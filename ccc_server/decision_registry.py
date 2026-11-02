"""Local, passive decision identities; importing a record never executes it.

The caller supplies the database path (normally decision-inbox/requests.sqlite3)
and source identity: adapter, origin_id and generation. Identity never depends
on text or display numbers. Generations may be strings or integers; integers
are normalized to strings. A new generation always means a new request.

Each method opens its own SQLite connection, so instances can be shared across
threads. Batches are atomic. Returned records and JSON content are detached
copies. No source files, services, tickets or action handlers are accessed.
"""

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _identity(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _aliases(record):
    aliases = record.get("aliases", [])
    if not isinstance(aliases, (list, tuple)) or any(
        not isinstance(alias, str) or not alias for alias in aliases
    ):
        raise ValueError("aliases must be nonempty strings in a list")
    result = list(aliases)
    result.extend(re.findall(r'\[Q\d+:[a-z0-9-]+\]', str(record.get('title') or '')))
    for key in ("q_alias", "question_id", "decision_ref"):
        value = record.get(key)
        if isinstance(value, str) and value:
            result.append(value)
    for key, prefix in (("decision_number", "D"), ("q_number", "Q"),
                        ("question_number", "Q")):
        value = record.get(key)
        if type(value) is int and value > 0:
            result.append(f"{prefix}{value}")
            if prefix == 'D':
                result.append(f"D-{value}")
        elif isinstance(value, str) and value:
            result.append(value if value.startswith(prefix) else prefix + value)
    return sorted(set(result))


class DecisionRegistry:
    """Persistent identities within one installation, located only at db_path.

    Public records contain request_id (UUID), installation_id, adapter,
    origin_id, generation, revision (starts at 1), content (the entire input),
    aliases (exact strings scoped to this record's installation and origin),
    status, answer_state and delivery_state. Aliases are never lookup keys for
    deduplication. Updates preserve historical aliases.
    Supplied kind/title/body/source_revision/visibility fields are also exposed
    directly on returned records for projection consumers.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('installation_id', ?)",
                       (str(uuid.uuid4()),))
            self.installation_id = db.execute(
                "SELECT value FROM metadata WHERE key = 'installation_id'"
            ).fetchone()[0]
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                installation_id TEXT NOT NULL,
                adapter TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                generation TEXT NOT NULL,
                revision INTEGER NOT NULL,
                content TEXT NOT NULL,
                aliases TEXT NOT NULL,
                status TEXT NOT NULL,
                answer_state TEXT NOT NULL DEFAULT 'none',
                delivery_state TEXT NOT NULL DEFAULT 'none',
                UNIQUE (installation_id, adapter, origin_id, generation)
            )""")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row):
        record = dict(row)
        record["content"] = json.loads(record["content"])
        record["aliases"] = json.loads(record["aliases"])
        for field in ("kind", "title", "body", "source_revision", "visibility"):
            if field in record["content"]:
                record[field] = record["content"][field]
        return record

    def _upsert(self, db, *, adapter, origin_id, generation, content, aliases,
                status, answer_state="none", delivery_state="none"):
        key = (self.installation_id, adapter, origin_id, generation)
        previous = db.execute("""SELECT * FROM requests WHERE
            installation_id=? AND adapter=? AND origin_id=? AND generation=?""", key).fetchone()
        serialized = _json(content)
        if previous:
            aliases = sorted(set(aliases) | set(json.loads(previous["aliases"])))
            changed = (serialized, _json(aliases), status, answer_state, delivery_state) != (
                previous["content"], previous["aliases"], previous["status"],
                previous["answer_state"], previous["delivery_state"])
            if changed:
                db.execute("""UPDATE requests SET content=?, aliases=?, status=?,
                    answer_state=?, delivery_state=?, revision=revision+1 WHERE request_id=?""",
                    (serialized, _json(aliases), status, answer_state, delivery_state,
                     previous["request_id"]))
            request_id = previous["request_id"]
        else:
            request_id = str(uuid.uuid4())
            db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (request_id, *key, 1, serialized, _json(aliases), status,
                        answer_state, delivery_state))
        return self._record(db.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def upsert_requests(self, requests, *, complete=False):
        """Atomically import explicit source identities and return touched records.

        Each mapping requires adapter, origin_id, generation. All other fields
        are preserved verbatim in content. complete documents snapshot coverage
        only: missing requests are never withdrawn, even for complete snapshots.
        Source statuses are evidence only; they never imply answer delivery.
        """
        if type(complete) is not bool:
            raise ValueError("complete must be a boolean")
        results = []
        with self._transaction() as db:
            for request in requests:
                adapter = _identity(request.get("adapter"), "adapter")
                origin_id = _identity(request.get("origin_id"), "origin_id")
                generation = request.get("generation")
                if type(generation) is int:
                    generation = str(generation)
                generation = _identity(generation, "generation")
                results.append(self._upsert(
                    db, adapter=adapter, origin_id=origin_id, generation=generation,
                    content=request, aliases=_aliases(request),
                    status=_identity(request.get("status", "open"), "status")))
        return results

    def import_cards(self, cards):
        """Import a legacy id->card mapping or iterable of cards with explicit id.

        The mapping key is the legacy origin identity; an explicit card id is
        also preserved as an alias. Never merge different cards by source_id,
        Q number or content. A decided card records an answer but delivery is
        unknown regardless of any historical action result stored in content.
        """
        items = cards.items() if isinstance(cards, Mapping) else ((c.get("id"), c) for c in cards)
        results = []
        with self._transaction() as db:
            for legacy_id, card in items:
                origin_id = _identity(legacy_id, "legacy card id")
                aliases = _aliases(card) + [origin_id]
                if isinstance(card.get("id"), str) and card["id"]:
                    aliases.append(card["id"])
                status = _identity(card.get("status", "open"), "status")
                results.append(self._upsert(
                    db, adapter="legacy-card", origin_id=origin_id, generation="0",
                    content=card, aliases=sorted(set(aliases)), status=status,
                    answer_state="recorded" if status == "decided" or card.get("decided") else "none",
                    delivery_state="unknown"))
        return results

    def list_requests(self):
        """Return all persisted records without fetching or modifying sources."""
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        try:
            return [self._record(row) for row in db.execute(
                "SELECT * FROM requests ORDER BY adapter, origin_id, generation")]
        finally:
            db.close()


# Short public spelling for callers composing registry and source projections.
Registry = DecisionRegistry
