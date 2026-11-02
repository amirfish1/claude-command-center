"""Read-only historical inventory and server-owned legacy action policy."""
import sqlite3
from pathlib import Path

from ccc_server.decision_answer_settings import answer_environment
from ccc_server.decision_legacy_reconciliation import reconcile_legacy_cards, ticket_refs


def legacy_requires_canonical(card):
    source = card.get('source')
    source = source if isinstance(source, dict) else {}
    return bool(ticket_refs(card) or 'source_request' in source
                or source.get('source') == 'routine'
                or str(card.get('source_id', '')).startswith('routine:'))


def canonical_mode(root):
    # Disabling new replies must not reactivate historical options. An
    # installation with persisted answer policy keeps its legacy replay fence.
    return (answer_environment(root).get('CCC_DECISION_ANSWERS_ENABLED') == '1'
            or (Path(root) / 'decision-inbox' / 'answer-settings.json').exists())


def reconciliation_payload(*, state_root=None, source_loader=None):
    from ccc_server import decision_inbox
    from ccc_server.decision_sources import owner_source_payload
    root = Path(state_root) if state_root else decision_inbox._state_root()
    cards = decision_inbox.load_cards(root / 'decision-inbox' / 'cards.json')
    try:
        observation = (source_loader or owner_source_payload)(state_root=root)
    except Exception:
        observation = {'ok': False}
    records, owners = None, None
    try:
        database = root / 'decision-inbox' / 'requests.sqlite3'
        db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            from ccc_server.decision_registry import DecisionRegistry
            records = [DecisionRegistry._record(row) for row in db.execute('SELECT * FROM requests')]
            owners = {row['key'].removeprefix('delivery_owner:'): row['value']
                      for row in db.execute("SELECT * FROM metadata WHERE key LIKE 'delivery_owner:%'")}
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError):
        pass  # Missing registry evidence cannot verify a producer's owner claim.
    result = reconcile_legacy_cards(cards, observation, canonical_requests=records,
                                    delivery_owners=owners)
    enabled = canonical_mode(root)
    for row in result['cards']:
        row['canonical_required'] = enabled and legacy_requires_canonical(row['card'])
    return dict(result, ok=True,
                answers_enabled=answer_environment(root).get('CCC_DECISION_ANSWERS_ENABLED') == '1')
