# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Hard redaction for shared runs. Local, stdlib-only, no network.

A pattern list only hides what it anticipated. This module works the other
way round: a word survives only if it is on an allow-list of ordinary English
and programming vocabulary (``share_wordlist.txt``). Everything else, whether
a name, client, project, hostname or token, is masked without needing a
pattern for it. Detectors run first only so that the placeholder is
informative (``[path]`` rather than ``[word-3]``), not because anything
depends on them for safety.

``Redactor.verify`` is the second, independent layer: it rescans finished text
for anything that must never appear. Callers refuse to export on any hit.

Known limit, stated in docs/sharing.md: a private name that is also an
ordinary word ("Stripe", "Target") can survive unless it is one of the
machine's identity tokens or the user lists it under "also hide". That is why
the export always shows a preview first.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

_WORDLIST = Path(__file__).with_name("share_wordlist.txt")

# Tool and product names that the public corpus does not cover.
_EXTRA_WORDS = frozenset("""
    agent agents anthropic api apis async auth backend backlog bash bugfix
    changelog chrome claude cli codebase codex commits config css cursor
    deduplicate devin dockerfile dropdown e2e fastapi flaky frontend gemini
    git github gitlab golang gpt graphql grok haiku homebrew html http https
    ide ios javascript json jsonl jwt kimi kubernetes linter llm llms localhost
    macos markdown mcp middleware mobile monorepo mysql nextjs nginx npm oauth
    onboarding openai opus pagination postgres postgresql prod puppeteer pydantic
    pytest python react readme redis refactor refactored refactoring regex
    regression repo repos rollback rust saas schema sdk sidebar signup sonnet
    sql sqlite ssh stdlib sqlalchemy subagent subagents svelte svg tailwind
    terraform timeout timeouts tmux todo tooltip typescript ubuntu ui unittest
    url urls ux vercel vite vscode vue webhook webhooks websocket windows
    worktree worktrees wsl xcode yaml

    admission admit adversarial affirmative alike allowance analytics
    appointment assessment asymmetry attend attendance attended auditor
    autonomy autopilot awake backfill backstop beside bets bill billed blast
    blip blocker booking breach breakfast broader brother burn burst bury buyers
    calculator campaign cash categorical census charging chatter checklist chips
    citing clarification cleanest coffee commitment committing confined
    consolidation contradicting controversial coordination country cutover
    daughter debit decline dedup defensible deliverable deliveries dense desk
    dial dinner dip disagreement discuss dispute doctor dramatic drove dumb
    eligibility enrollment episode escalate escalation excuse exemption expiring
    facet family father feasibility fight film founder fresher friend friday
    funnel game gift grader grew guardrail habit handoff hardening headline hear
    hedge herself hero honestly hostile hospital hotfix house husband hypotheses
    improvise inbox infra ingest injuries inquiries inquiry instructor intake
    invoice irreversible judge kit knew ladder lane laptop lawyer ledger lesson
    loan loud lunch marathon marketing meeting meetings mic microphone mishap
    misread momentum monday money moot morning mother motion night nudge
    objection occupancy office outage outreach overnight pace park parent
    partner payable payroll pickup planner plateau playbook poison precedent
    premise pretty price pricing productive projection promo promotion propose
    proportionate prospects pushback reaction rebalance recap recheck reconciliation
    recurring recycle refund reminder repro retirement retro revenue reviewer revise
    rollout roof runaway runbook salary saturday scoring screenshot seat
    selective senior settlement sister skim slip smoke snag social softer son
    speaker speech spike sprint steady steer stranger streak street student
    studio substantive sunday supervision suspicion sweep swept symptom taste
    taught teacher teaching tech tenant tenth throwaway thursday tomorrow tonight
    truthful tuesday typecheck unanswered unbuilt uncertain uncommitted undecided
    undelivered unmerged unnoticed unpaid unproven untracked unwind urgent vault
    verdict voicemail waitlist waiver wednesday weaker wedding wholesale widen
    wife wiring withdrawal wow wrinkle yep yesterday
    january february march april june july august september october november
    december
""".split())

# Names, surnames and sample-data names, including those the corpus let in.
# Masked in any letter case.
NAME_WORDS = frozenset("""
    aaron abby abdul abigail adam adrian ahmed aisha alan albert alberto alex
    alexander alexandra alexis alfred ali alice alicia alison allen allison
    alvin amanda amir amit amy ana anderson andre andrea andres andrew andy
    angela angelica anita ann anna anne annie anthony antonio anya arjun arnold
    arthur ashley asher aviv avi barbara barry ben benjamin bernard bert beth
    betty beverly bianca bob bobby boris brad bradley brandon brenda brent brett
    brian brianna bruce bryan caleb calvin cameron candice carl carla carlos
    carmen carol carolina caroline carrie casey catherine cathy cecilia chad
    chandra charles charlie charlotte chen cheryl chloe chris christian
    christina christine christopher chuck cindy claire clara clark claudia
    clifford colin connie connor conor craig crystal curtis cynthia dale damian
    dan dana daniel daniela danielle danny darius darren dave david dawn dean
    deborah debra denis dennis derek diana diane diego dmitri dominic don donald
    donna doris dorothy doug douglas duncan dylan eddie edgar edith edward edwin
    eileen elaine eli elena eli elias elijah elizabeth ella ellen elliot emily
    emma emmanuel enrique eric erica erik erin ernest esther ethan eugene eva
    evan evelyn fatima felix fernando fiona florence francesca francis francisco
    frank fred freddie frederick gabriel gabriela gail gareth gary gavin george
    georgia gerald gerard gina giovanni gloria gordon graham greg gregory gretchen
    guido gustavo guy hannah hans harold harriet harry harvey hassan heather
    hector heidi helen henry herbert hermes hiroshi howard hugh hugo hussein ian
    ibrahim igor ilya irene iris isaac isabel isabella ivan jack jackie jacob
    jacqueline jake james jamie jane janet janice jared jason javier jay jean
    jeff jeffrey jen jenna jennifer jenny jeremy jerome jerry jesse jessica jesus
    jill jim jimmy joan joanna joanne joe joel johan john johnny jon jonah
    jonathan jordan jorge jose joseph josh joshua joy joyce juan judith judy
    julia julian juliana julie julio justin kaitlyn kamal karen karl kate
    katherine kathleen kathryn kathy katie katrina kayla keith kelly ken kenneth
    kenny kevin khalid kim kimberly kirk kristen kristin kurt kyle lana lance
    larry laura lauren laurence laurie lawrence leah lee leia lena leo leon
    leonard leslie levi lewis liam lily linda lindsay lisa liz logan lois lola
    lorenzo lori louis louise lucas lucia lucy luis luke luna lydia lynn madison
    maggie mahmoud malcolm manuel marc marcel marco marcus margaret maria marie
    marilyn mario marion marisa marissa marjorie marlene marsha marta martha
    martin marvin mary mason mateo matt matthew maureen maurice max maxim maya
    megan meghan melanie melissa melvin meredith mia michael michele michelle
    miguel mike mikhail mila miles milton mina miranda miriam mitchell mohamed
    mohammad mohammed molly monica morgan moses muhammad nadia nancy naomi natalie
    natasha nathan nathaniel neal ned neil nelson nicholas nick nicolas nicole
    nikhil nikita nina noah noel nora norman oleg oliver olivia omar oscar otto
    owen pablo paige pamela paolo patricia patrick paul paula pavel pedro peggy
    penelope percy pete peter phil philip phillip phoebe pierre priya rachel
    rafael rahul raj rajesh ralph ramon randall randy raphael rashid raul ravi
    ray raymond rebecca regina reginald renee reza ricardo richard rick ricky rita
    rob robert roberta roberto robin rodney rodrigo roger roland ron ronald ronnie
    rosa rosemary ross roy ruben rudy russell ruth ryan sabrina sally salma salman
    salvador sam samantha samir samuel sandra sanjay santiago sara sarah saul
    scott sean sebastian selena serena sergei sergio seth shane shannon sharon
    shaun shawn sheila shelly sheryl shirley sidney silvia simon sofia sonia sophia
    sophie stacey stacy stan stanley stefan stella stephanie stephen steve steven
    stuart sue sunil susan suzanne sylvia tamara tanya tara tariq tatiana taylor
    ted teresa terrence terri terry tessa theodore theresa thomas tiffany tim
    timothy tina tobias todd tom tommy toni tony tracey tracy travis trevor tricia
    tristan troy tyler uri valentina valerie vanessa vera veronica vicki vicky
    victor victoria vijay vikram vincent viktor vinay violet virginia vivian
    vladimir wade wallace walter wanda warren wayne wendy wesley whitney william
    willie wilson winston xavier yael yana yasmin yolanda yosef yusuf yuri
    yvette yvonne zachary zack zane zoe zoey

    abbott adams ahmad alvarez andersen armstrong bailey barnes bauer beck bell
    bennett bianchi bloom blum boyd brooks bryant burke burns butler campbell
    carlson carter castillo castro chan chang chavez cheng cho chopra chung clarke
    cohen coleman collins contreras cooper cruz cunningham das davidson davies
    davis delgado diaz dixon dominguez doyle dubois duffy dunn edwards ellis
    erickson estrada evans ferguson fernandez fischer fisher fitzgerald flores
    foster fox franklin freeman friedman fuller garcia gardner garza gibson gomez
    gonzales gonzalez goldberg goldman gordon grant greenberg griffin gupta
    gutierrez hall hamilton hansen harper harris harrison hart hawkins hayes
    henderson henry hernandez herrera hoffman holmes howell huang hudson hughes
    hunt iyer jackson jacobs jain jensen jimenez johnson johnston jones joshi kang
    kapoor katz kaufman kelley kennedy khan kim klein knight koch kowalski krause
    kumar lambert lane larsen larson lawson le lewis li lin liu lopez lowe lu lynch
    maldonado mann marshall martinez mccarthy mcdonald mehta mendez mendoza meyer
    miller mills mishra mitchell mohan molina montgomery moore morales moreno
    morris morrison mueller muller munoz murphy murray myers nakamura nair nash
    navarro nguyen nichols nielsen nunez obrien oconnor olsen olson ortega ortiz
    owens padilla palmer park parker patel patterson payne pearson pena perez
    perry peters petersen peterson pham phillips pierce porter powell price pyle
    qureshi ramirez ramos rao reddy reed reyes reynolds rhodes richards richardson
    riley rivera roberts robertson robinson rodriguez rogers romero rose rosen
    rosenberg ross rossi roth ruiz russo ryan sanchez sanders santiago santos
    schmidt schneider schroeder schultz schwartz scott sharma shah shapiro shaw
    shen silva simmons simpson singh smith snyder sokolov soto spencer stein
    stevens stewart sullivan sun suzuki tanaka tan taylor thompson thornton tran
    tucker turner valdez vargas vasquez vazquez verma wagner walsh walters wang
    ward warner washington watson watts webb weber weiss wells wheeler whitaker
    williams williamson willis wilson wong woods wright wu xu yamamoto yang yates
    yu zhang zhao zhou zimmerman

    acme bigcorp contoso foobar initech northwind
""".split())

# Ordinary words that are also common names. Masked when capitalized, so
# "Mark said" hides but "mark the task" stays. The first word of a sentence is
# over-masked; that is the safe direction.
AMBIGUOUS_NAMES = frozenset("""
    amber april art august autumn baker bill black blake bond brown bush
    carpenter chase christian cook crane dawn drew faith fish ford forest gray
    green grey hall hazel heath hill holly hope hunter ivy jasmine jewel king
    lily long love major mark marsh may mercedes miller moon page paris pat
    pearl penny pierce rich river rock rocky rose rusty sage savannah shepherd
    sky snow stone storm summer swift violet wade walker ward waters white will
    winter wolf wood young
    bell butler crystal dean don fox foster grant guy hunt jack joy knight lane
    marshall max miles mills nick park porter price ray reed sun tan uri wells
    woods
""".split())
NAME_WORDS = NAME_WORDS - AMBIGUOUS_NAMES

_ACRONYM_OK = frozenset("""
    ai api apis ci cd cli cpu css csv cto ceo dns e2e faq gpu html http https id
    ide ids io ip json jwt llm mcp oauth orm os pdf png pr prs qa ram rest sdk
    sql ssh ssl svg tcp tls ts ui url uri usb ux vm vpn xml yaml
""".split())

FILE_EXTENSIONS = frozenset("""
    bash c cc cfg cjs conf cpp cs css csv dart db diff env ex exs gif go gql
    gradle h hpp html ini ipynb java jpeg jpg js json jsonl jsx kt lock log lua
    m md mjs mm mp4 pdf php pl png proto ps1 py pyi rb rs sass scala scss sh sql
    sqlite svelte svg swift tf toml ts tsx txt vue wasm webp xml yaml yml zig
""".split())

_TLDS = frozenset("""
    ai app biz ca cloud co com de dev edu eu fr gov il in info internal intranet
    io jp lan local me net nz org ru tech tv uk us xyz
""".split())
_HOST_RE = re.compile(
    r"(?i)(?<![\w.\-])[a-z0-9\-]{1,63}(?:\.[a-z0-9\-]{1,63}){0,6}\.(?:"
    + "|".join(sorted(_TLDS))
    + r")(?![\w\-])"
)

# Generic account names that identify nobody; masking them would only garble
# ordinary prose.
_GENERIC_IDENTITY = frozenset("""
    admin administrator app apps code default desktop dev developer documents
    downloads home localhost main master mac macbook me ops pc project projects
    repos sites
    root runner src ubuntu user users work workspace
""".split())

# One message's worth. Keeps redaction time bounded on a pasted log; the replay
# shows far less than this anyway.
MAX_INPUT_CHARS = 20_000

_PLACEHOLDER_OPEN = "\ue000"
_PLACEHOLDER_CLOSE = "\ue001"

_INVISIBLE_RE = re.compile(
    "[\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff"
    "\ue000-\uf8ff]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_DROP_BLOCK_RE = re.compile(
    r"<(system-reminder|session-state|command-[a-z]+|local-command-[a-z]+"
    r"|environment_context|user_instructions|pasted_content[^>]{0,80})\b[^>]{0,200}>"
    r".*?(?:</\1>|\Z)",
    re.S | re.I,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}-----.*?(?:-----END [A-Z0-9 ]{0,40}-----|\Z)", re.S
)
_SECRET_NAME = (
    r"[A-Za-z0-9_.\-]{0,40}(?:pass(?:word|wd|phrase|code)?|pwd|secret|token|"
    r"api[_\- ]?key|apikey|access[_\- ]?key|private[_\- ]?key|credential|auth|"
    r"bearer|cookie|session[_\- ]?id|otp|pin)[A-Za-z0-9_.\-]{0,40}"
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + _SECRET_NAME + r"[\"']?[ \t]{0,3}(?:=>|:=|=|:)[ \t]{0,3})"
    r"(\"[^\"\n]{0,500}\"|'[^'\n]{0,500}'|[^\s,;&\"')\]}]{1,500})"
)
_SECRET_PROSE_RE = re.compile(
    r"(?i)\b((?:password|passphrase|passcode|secret|token|api key|pin)"
    r"(?: is| was| would be| to)[ \t]{1,3})(\S{1,500})"
)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(bearer|basic|token)[ \t]{1,3}[A-Za-z0-9._~+/=\-]{8,}")

_SECRET_TOKEN_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{6,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bxox[abeoprs]-[A-Za-z0-9\-]{6,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{8,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{10,}"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{10,}"),
    re.compile(r"\bSG\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bAC[0-9a-f]{32}\b"),
)

_URL_RE = re.compile(
    r"(?i)\b(?:[a-z][a-z0-9+.\-]{1,20}://[^\s<>\"'`\\]{0,2000}|www\.[^\s<>\"'`\\]{1,2000})"
)
_SCP_RE = re.compile(r"\b[\w.\-]{1,64}@[\w.\-]{1,120}:[\w./~\-]{1,500}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,6}")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(
    r"(?<![\w:])(?=[0-9A-Fa-f:]{0,40}::|(?:[0-9A-Fa-f]{1,4}:){4})[0-9A-Fa-f:]{3,45}(?![\w:])"
)
_MAC_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}(?![\w:])")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_DATE_RE = re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?[^\s,;]{0,10})?")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d \-().]{6,22}\d(?![\w])")
_MONEY_RE = re.compile(r"[$€£¥₪]\s?\d[\d,.]{0,20}(?:[kKmMbB]\b)?")
_HANDLE_RE = re.compile(r"(?<![\w/@.])@[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,60}")
_TICKET_RE = re.compile(r"(?<![\w.\-])[A-Z][A-Z0-9]{1,14}-\d{1,8}\b")
_ISSUE_RE = re.compile(r"(?<![\w#])#\d{1,8}\b")
_WIN_PATH_RE = re.compile(r"(?i)(?<![a-z0-9])[a-z]:\\[^\s\"'<>|]{0,500}")
_UNC_RE = re.compile(r"\\\\[^\s\"'<>|]{1,500}")
_POSIX_PATH_RE = re.compile(
    r"(?<![\w/.\-])(?:~|\.\.?)?/[\w.\-@+~%]{1,255}(?:/[\w.\-@+~%]{0,255}){0,40}"
)
_RELATIVE_PATH_RE = re.compile(r"(?<![\w/.\-])[\w.\-]{1,255}(?:/[\w.\-]{1,255}){2,40}/?")

# A chunk is a run without whitespace or the punctuation that separates tokens
# in prose, code and tables.
_CHUNK_RE = re.compile(r"[^\s,;|()<>{}\[\]\"`*=\ue000\ue001]+")
_NUMBER_RE = re.compile(r"^[+\-~]?\d{1,4}(?:[.,]\d{1,3})?(?:%|x|k|m|s|ms|h|gb|mb|kb|px|th|st|nd|rd)?$", re.I)
_VERSION_RE = re.compile(r"^v?\d{1,3}(?:\.\d{1,4}){1,3}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,}$")
_CAMEL_RE = re.compile(r"[A-Z]{2,}(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W\d_]+")
_RUNS_RE = re.compile(r"\d+|[^\W\d_]+")
_ANY_PLACEHOLDER_RE = re.compile(r"\[[a-z]+(?:-\d+)?\]")
_ADJACENT_AFTER_RE = re.compile(r"(\[name-\d+\])([ \t]+)([A-Z][a-z]{1,30})\b")
_ADJACENT_BEFORE_RE = re.compile(r"(?<=[a-z,;] )([A-Z][a-z]{1,30})([ \t]+)(\[name-\d+\])")

# The verifier flags these in finished output.
_VERIFY_PATH_RE = re.compile(
    r"(?:(?<![\w.\-])~/[\w.\-]|(?<![\w.\-/])/(?:home|Users|root|tmp|var|etc|opt|mnt|srv|private|Volumes)/)"
)
_VERIFY_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]{1,20}://")


@lru_cache(maxsize=1)
def _words() -> frozenset:
    try:
        base = _WORDLIST.read_text("utf-8").split()
    except OSError:
        # A missing list must fail safe: fewer words survive, none leak.
        base = []
    # The curated list is singular nouns and base verbs, so its plurals are
    # safe to add. Corpus words do not get this: "Banks" and "Rivers" are names.
    return frozenset(base) | _EXTRA_WORDS | {f"{w}s" for w in _EXTRA_WORDS}


def split_identity(value) -> set:
    """Lowercase word parts of an identity value, minus generic account names."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    parts = {p.casefold() for p in re.split(r"[^\w]+|_", text) if len(p) >= 3}
    return parts - _GENERIC_IDENTITY


class RedactionError(RuntimeError):
    """Finished output failed verification. Never export it."""


class Redactor:
    """One instance per export, so a masked word keeps one placeholder."""

    def __init__(self, identity=(), hide=(), keep=(), phrases=()):
        """``identity``/``hide`` mask word by word; ``phrases`` only as a whole,
        so a repo named "command-center" does not ban the word "command".
        ``keep`` lifts word masks only; detectors still apply to kept words.
        """
        self.keep = {str(k).casefold().strip() for k in keep if str(k).strip()}
        self.deny = set()
        self.phrases = []
        for value in list(identity) + list(hide):
            value = unicodedata.normalize("NFKC", str(value or "")).strip()
            if not value:
                continue
            self.deny |= split_identity(value)
            words = [w for w in re.split(r"[^\w]+|_", value) if w]
            if len(value) >= 3 and value.casefold() not in _GENERIC_IDENTITY:
                self.phrases.append(words)
        for value in phrases:
            value = unicodedata.normalize("NFKC", str(value or "")).strip()
            words = [w for w in re.split(r"[^\w]+|_", value) if w]
            if not words or value.casefold() in _GENERIC_IDENTITY:
                continue
            if len(value) >= 3:
                self.phrases.append(words)
        self.deny -= self.keep
        self.phrases = [p for p in self.phrases if " ".join(p).casefold() not in self.keep]
        self._phrase_re = None
        if self.phrases:
            alternatives = sorted(
                {r"[\s_\-./]{0,3}".join(re.escape(w) for w in p) for p in self.phrases},
                key=len,
                reverse=True,
            )
            self._phrase_re = re.compile(
                r"(?i)(?<![^\W_])(?:" + "|".join(alternatives) + r")(?![^\W_])"
            )
        self.counts = Counter()
        self._aliases = {}
        self._alias_numbers = Counter()
        self._stash = []

    # Placeholders -----------------------------------------------------------

    def _hold(self, placeholder: str) -> str:
        self._stash.append(placeholder)
        return f"{_PLACEHOLDER_OPEN}{len(self._stash) - 1}{_PLACEHOLDER_CLOSE}"

    def _mask(self, category: str) -> str:
        self.counts[category] += 1
        return self._hold(f"[{category}]")

    def _alias(self, category: str, word: str) -> str:
        self.counts[category] += 1
        return self._hold(self._alias_for(category, word))

    def _alias_for(self, category: str, word: str) -> str:
        key = word.casefold()
        alias = self._aliases.get(key)
        if alias is None:
            self._alias_numbers[category] += 1
            alias = f"[{category}-{self._alias_numbers[category]}]"
            self._aliases[key] = alias
        return alias

    def _restore(self, text: str) -> str:
        def put(match):
            return self._stash[int(match.group(1))]

        return re.sub(f"{_PLACEHOLDER_OPEN}(\\d+){_PLACEHOLDER_CLOSE}", put, text)

    # Words ------------------------------------------------------------------

    def _word_ok(self, part: str) -> bool:
        if not part:
            return True
        if part.isdigit():
            return len(part) <= 4
        lower = part.casefold()
        if lower in self.keep:
            return True
        if not part.isascii() or not part.isalpha():
            return False
        if lower in self.deny or lower in NAME_WORDS:
            return False
        if part.isupper() and len(part) <= 5 and lower in _ACRONYM_OK:
            return True
        capitalized = part[0].isupper()
        if capitalized and lower in AMBIGUOUS_NAMES:
            return False
        if len(part) == 1:
            return True
        words = _words()
        if lower in words:
            return True
        if capitalized:
            return False
        for suffix in ("s", "es", "ed", "d", "ing", "ly"):
            if lower.endswith(suffix) and lower[: -len(suffix)] in words:
                return True
        return False

    def _piece_ok(self, piece: str) -> bool:
        if piece.casefold() in self.keep:
            return True
        # sha256, 3d and h264 are one word and one number. Anything more
        # interleaved than that is a token, even if each letter "passes".
        if len(_RUNS_RE.findall(piece)) > 2:
            return False
        parts = _CAMEL_RE.findall(piece)
        if len(parts) >= 3 and any(len(p) <= 2 and not p.isdigit() for p in parts):
            return False
        return all(self._word_ok(part) for part in parts)

    def _parts_ok(self, core: str) -> bool:
        return all(self._piece_ok(piece) for piece in re.split(r"[_\-'’]+", core))

    def _mask_word(self, core: str) -> str:
        if any(ch.isdigit() for ch in core) and len(core) >= 12:
            return self._mask("secret")
        category = "name" if core[:1].isupper() or not core.isascii() else "word"
        return self._alias(category, core)

    def _classify(self, core: str) -> str:
        if core.casefold() in self.keep:
            return core
        if _NUMBER_RE.match(core) or _VERSION_RE.match(core):
            return core
        if core.isdigit():
            return self._mask("number")
        if _HEX_RE.match(core) and any(c.isdigit() for c in core):
            return self._mask("id")
        if "/" in core or "\\" in core or core.startswith("~"):
            pieces = [p for p in re.split(r"[/\\]", core) if p]
            if (
                not core.startswith(("/", "~", "."))
                and len(pieces) == 2
                and all(p.isalpha() and self._word_ok(p) for p in pieces)
            ):
                return core
            return self._mask("path")
        if ":" in core:
            return ":".join(
                self._classify(p) if p else "" for p in core.split(":")
            )
        if "." in core:
            return self._classify_dotted(core)
        if "@" in core or "%" in core or "+" in core or "&" in core or "$" in core:
            return self._mask("id")
        if self._parts_ok(core):
            return core
        return self._mask_word(core)

    def _classify_dotted(self, core: str) -> str:
        pieces = core.split(".")
        last = pieces[-1].casefold()
        stem = ".".join(pieces[:-1])
        if last in _TLDS and len(pieces) >= 2 and stem and stem.casefold() not in self.keep:
            return self._mask("host")
        if last in FILE_EXTENSIONS and stem:
            if all(self._parts_ok(p) for p in pieces[:-1]):
                return core
            self.counts["file"] += 1
            return self._hold(f"{self._alias_for('file', stem)}.{pieces[-1]}")
        if all(self._parts_ok(p) for p in pieces):
            return core
        return self._mask("host")

    def _chunk(self, match) -> str:
        raw = match.group(0)
        start, end = 0, len(raw)
        edge = ".,:;!?'\"’‘“”_~#^+/\\-"
        while start < end and raw[start] in edge and raw[start] not in "/\\~":
            start += 1
        while end > start and raw[end - 1] in edge:
            end -= 1
        core = raw[start:end]
        if not core:
            return raw
        return raw[:start] + self._classify(core) + raw[end:]

    # Text -------------------------------------------------------------------

    def _sub(self, pattern, category, text):
        return pattern.sub(lambda _m: self._mask(category), text)

    def _strip_structure(self, text: str) -> str:
        text = _DROP_BLOCK_RE.sub(" ", text)
        text = _HTML_COMMENT_RE.sub(" ", text)

        def fence(match):
            lines = match.group(2).count("\n")
            self.counts["code"] += 1
            noun = "line" if lines <= 1 else "lines"
            return self._hold(f"[code, {max(lines, 1)} {noun}]") + "\n"

        return _FENCE_RE.sub(fence, text)

    def _detectors(self, text: str) -> str:
        text = self._sub(_PEM_RE, "secret", text)
        text = _SECRET_ASSIGN_RE.sub(lambda m: m.group(1) + self._mask("secret"), text)
        text = _SECRET_PROSE_RE.sub(lambda m: m.group(1) + self._mask("secret"), text)
        text = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + " " + self._mask("secret"), text)
        for pattern in _SECRET_TOKEN_RES:
            text = self._sub(pattern, "secret", text)
        text = self._sub(_URL_RE, "url", text)
        text = self._sub(_SCP_RE, "url", text)
        text = self._sub(_EMAIL_RE, "email", text)
        text = self._sub(_UUID_RE, "id", text)
        text = self._sub(_DATE_RE, "date", text)
        text = self._sub(_IPV4_RE, "ip", text)
        text = self._sub(_MAC_RE, "ip", text)
        text = self._sub(_IPV6_RE, "ip", text)
        text = self._sub(_WIN_PATH_RE, "path", text)
        text = self._sub(_UNC_RE, "path", text)
        text = self._sub(_POSIX_PATH_RE, "path", text)
        text = self._sub(_RELATIVE_PATH_RE, "path", text)
        text = self._sub(_HOST_RE, "host", text)
        text = self._sub(_MONEY_RE, "amount", text)
        text = self._sub(_PHONE_RE, "number", text)
        text = self._sub(_HANDLE_RE, "handle", text)
        text = self._sub(_TICKET_RE, "ref", text)
        text = self._sub(_ISSUE_RE, "ref", text)
        if self._phrase_re is not None:
            text = self._phrase_re.sub(lambda m: self._alias("name", m.group(0)), text)
        return text

    def _join_adjacent(self, text: str) -> str:
        def after(match):
            if match.group(3).casefold() in self.keep:
                return match.group(0)
            self.counts["name"] += 1
            return match.group(1) + match.group(2) + self._alias_for("name", match.group(3))

        def before(match):
            if match.group(1).casefold() in self.keep:
                return match.group(0)
            self.counts["name"] += 1
            return self._alias_for("name", match.group(1)) + match.group(2) + match.group(3)

        return _ADJACENT_BEFORE_RE.sub(before, _ADJACENT_AFTER_RE.sub(after, text))

    def text(self, value, limit: int = 0) -> str:
        """Return ``value`` with everything not known to be safe masked."""
        text = unicodedata.normalize("NFKC", str(value or "")[:MAX_INPUT_CHARS])
        text = _INVISIBLE_RE.sub("", text)
        text = _CONTROL_RE.sub(" ", text)
        self._stash = []
        text = self._strip_structure(text)
        text = self._detectors(text)
        text = _CHUNK_RE.sub(self._chunk, text)
        text = self._restore(text)
        text = self._join_adjacent(text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if limit and len(text) > limit:
            cut = text[:limit]
            # Never leave half a placeholder behind.
            bracket = cut.rfind("[")
            if bracket > cut.rfind("]"):
                cut = cut[:bracket]
            text = cut.rstrip() + " …"
        return text

    # Verification -----------------------------------------------------------

    def verify(self, strings) -> None:
        """Raise RedactionError if finished text holds anything forbidden.

        Independent of the masking path above. The error names only the
        category, so the failure itself cannot leak the value.
        """
        for raw in strings:
            value = unicodedata.normalize("NFKC", str(raw or ""))
            self._verify_one(value)

    def _verify_one(self, value: str) -> None:
        checks = (
            ("secret", _PEM_RE),
            ("email", _EMAIL_RE),
            ("path", _VERIFY_PATH_RE),
            ("path", _WIN_PATH_RE),
            ("url", _VERIFY_URL_RE),
            ("ip", _IPV4_RE),
            ("id", _UUID_RE),
        ) + tuple(("secret", p) for p in _SECRET_TOKEN_RES)
        for category, pattern in checks:
            if pattern.search(value):
                raise RedactionError(f"unredacted {category} in export")
        for word in re.findall(r"[^\W_]+", value):
            if word.casefold() in self.deny:
                raise RedactionError("identity token in export")
        if self._phrase_re is not None and self._phrase_re.search(value):
            raise RedactionError("identity phrase in export")
        for piece in re.findall(r"[^\W_]{20,}", _ANY_PLACEHOLDER_RE.sub(" ", value)):
            if (
                any(c.isdigit() for c in piece)
                and any(c.isalpha() for c in piece)
                and piece.casefold() not in self.keep
            ):
                raise RedactionError("unredacted secret in export")
