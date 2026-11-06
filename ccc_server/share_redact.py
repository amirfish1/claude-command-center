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

Known limit, stated in docs/sharing.md: ordinary English stays readable. A
private name that is also an ordinary word ("Stripe", "Target") survives unless
it is one of the machine's identity tokens or the user asks to hide it, and so
does a sensitive sentence made of plain words. The dashboard shows a preview
first; ``ccc share`` writes files directly and tells the user to read them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

_WORDLIST = Path(__file__).with_name("share_wordlist.txt")
# The public BIP-39 English list (github.com/bitcoin/bips, bip-0039/english.txt).
_BIP39_LIST = Path(__file__).with_name("share_bip39.txt")

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
    al bo cy di ed jo lu mo ty vi
""".split())
NAME_WORDS = NAME_WORDS - AMBIGUOUS_NAMES
# "ask mark": after a verb of contacting someone, even lowercase is a person.
_CONTACTED_NAME_RE = re.compile(
    r"(?i)\b((?:ask|asked|asking|call|called|cc|contact|dm|email|emailed|invite|"
    r"message|messaged|meet|met|ping|pinged|remind|slack|tell|text|texted|thank|told)"
    r"[ \t]+)(" + "|".join(sorted(AMBIGUOUS_NAMES)) + r")\b(?![\-_.])"
)

# A word glued to digits is how a weak password looks ("Welcome123"), so it is
# masked unless it is one of these or too short to be a password ("v2", "3d").
_DIGIT_WORDS = frozenset("""
    1password 2d 2fa 3d 3g 4g 4k 5g 8k 1080p 1440p 2160p 720p a100 a11y aarch64
    aes128 aes256 amd64 argon2 arm64 b2b b2c b64 base32 base64 c99 cp1252 crc32
    css3 e2e ec2 ed25519 es2015 es2020 es2022 es5 es6 gen2 gpt3 gpt4 gpt5 h100
    h264 h265 html5 http2 http3 i18n i386 i686 ipv4 ipv6 iso8601 k3s k8s l10n
    latin1 lib2to3 log10 log2 md4 md5 mp3 mp4 oauth1 oauth2 p2p pbkdf2 pip3 py2
    py3 python2 python3 rfc822 rsa2048 rsa4096 s3 sha1 sha2 sha256 sha3 sha384
    sha512 sqlite3 tls12 tls13 utf16 utf32 utf8 uuid4 uuid7 w3c web2 web3 win32
    win64 x11 x264 x265 x64 x86
""".split())
_SPLIT_DIGITS_RE = re.compile(r"[^\W\d_][_\-]\d{3,}")
_SHORT_DIGIT_WORD_RE = re.compile(r"(?i)^(?:[a-z]\d{1,3}|\d{1,4}(?:[a-z]|ms|gb|mb|kb|px|th|st|nd|rd))$")

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
    ai app bank biz box ca cloud co com corp de dev edu eu fr gov home il in info
    internal intranet io jp lan local localdomain me net nz org private ru tech tv
    uk us xyz
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
    r"|environment_context|user_instructions|instructions|pasted_content[^>]{0,80}"
    # A dash or underscore marks a tool's wrapper, not HTML: <ide_selection>,
    # <task-notification>, <user-prompt-submit-hook>, and ones not invented yet.
    r"|[a-z][a-z0-9]*[-_][a-z0-9_\-]*)(?![\w\-])[^>]{0,200}>"
    r".*?(?:</\1>|\Z)",
    re.S | re.I,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}-----.*?(?:-----END [A-Z0-9 ]{0,40}-----|\Z)", re.S
)
_SECRET_NAME = (
    r"[A-Za-z0-9_.\-]{0,40}(?:pass(?:word|wd|phrase|code)?|pwd|pw|secret|token|"
    r"api[_\- ]?key|apikey|access[_\- ]?key|private[_\- ]?key|credential|auth|"
    r"bearer|cookie|session[_\- ]?id|otp|pin)[A-Za-z0-9_.\-]{0,40}"
)
_HELD = "(?![])"
_QUOTED = (
    r"\"[^\"\n]{0,500}\"|'[^'\n]{0,500}'|“[^”\n]{0,500}”|‘[^’\n]{0,500}’|`[^`\n]{0,500}`"
)
# A value may be several words, so after a label the mask runs to the end of the
# clause (at most 8 words). Over-masking a sentence is the safe direction.
_PHRASE_VALUE = (
    _HELD + r"(?:" + _QUOTED + r"|\S{1,500}?(?=[,;.!?]?(?:\s|$))"
    r"(?:[ \t]+[^\s.,;:!?]{1,60}){0,7})"
)
_ONE_VALUE = _HELD + r"(?:" + _QUOTED + r"|\S{1,500})"
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + _SECRET_NAME + r"[\"']?[ \t]{0,3}(?:=>|:=|=|:)[ \t]{0,3})"
    + _HELD + r"(\"[^\"\n]{0,500}\"|'[^'\n]{0,500}'|[^\s,;&\"')\]}]{1,500})"
)
_SECRET_PROSE_RE = re.compile(
    r"(?i)\b((?:password|passphrase|passcode|secret|token|api key|pin)"
    r"(?: is| was| would be| to)[ \t]{1,3})" + _HELD + r"(\S{1,500})"
)
_CRED_LABEL = (
    r"(?:pass(?:word|wd|[ _\-]?phrase|code|key)|pwd|pw|secret|token|otp|cvv|cvc|"
    r"api[ _\-]?key|access[ _\-]?key|pin(?:[ _\-]?code)?|security[ _\-]?code)"
)
# "the password for prod is X", "password: X", "wifi pass was X".
_LABEL_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:" + _CRED_LABEL + r"|pass)"
    r"(?:[ \t]+(?:for|of|on|to|at|in)(?:[ \t]+[^\s:=]{1,30}){1,4}?)?"
    r"[ \t]*(?:\b(?:is|was|are|were|would[ \t]+be|will[ \t]+be|should[ \t]+be)\b|=>|:=|=|:)"
    r"[ \t]*)(" + _PHRASE_VALUE + r")"
)
# "--password X", "--api-key=X". Plain "-p" is handled apart: it is often a port.
_SECRET_FLAG_RE = re.compile(
    r"(?i)(?<![\w\-])(--?(?:[a-z0-9]+-){0,3}"
    r"(?:password|passwd|passphrase|pass|pwd|pw|secret|token|api-?key|auth|key)"
    r"(?:[ \t]*=[ \t]*|[ \t]+))(" + _ONE_VALUE + r")"
)
_USER_THEN_P_RE = re.compile(r"(?<![\w\-])(-u[ \t]*\S{1,100}[ \t]+-p[ \t]*)(" + _ONE_VALUE + r")")
_SHORT_P_RE = re.compile(r"(?<!mkdir )(?<![\w\-])(-p[ \t]+)(" + _ONE_VALUE + r")")
_PORT_RE = re.compile(r"^\d{1,5}(?::\d{1,5})*$")
_USER_COLON_RE = re.compile(r"(?<![\w\-])((?:-u|--user)[ \t=]+[^\s:]{1,100}:)(" + _ONE_VALUE + r")")
# "login: admin / Server123"
_LOGIN_PAIR_RE = re.compile(
    r"(?i)\b((?:logins?|creds?|credentials?|user(?:name)?|account)[ \t]*[:=][ \t]*)"
    r"([^\s/|]{1,100})[ \t]*[/|][ \t]*(" + _ONE_VALUE + r")"
)
_LOGIN_VALUE_RE = re.compile(r"(?i)\b(login[ \t]*[:=][ \t]*)(" + _ONE_VALUE + r")")
# "use X as the password", "X is the wifi password"
_VALUE_AS_LABEL_RE = re.compile(
    r"(?i)\b((?:use|using|set|try|with)[ \t]+)(" + _ONE_VALUE + r")"
    r"([ \t]+as[ \t]+(?:(?:the|my|our|your|a|an|new)[ \t]+){0,2}(?:[a-z]{2,20}[ \t]+)?"
    r"(?:password|passphrase|passcode|pin|secret|token|api[ \t]key)\b)"
)
_VALUE_IS_LABEL_RE = re.compile(
    r"(?i)(?<![\w])(" + _ONE_VALUE + r")"
    r"([ \t]+(?:is|was)[ \t]+(?:the|my|our|your)[ \t]+(?:new[ \t]+)?(?:[a-z]{2,20}[ \t]+)?"
    r"(?:password|passphrase|passcode|pin)\b)"
)
# "wifi password X", with no verb. Words that can follow "password" in ordinary
# talk about passwords stay; any other word is taken for the value.
_BARE_PASSWORD_RE = re.compile(
    r"(?i)\b((?:pass(?:word|wd|phrase|code)|pwd|pw)[ \t]+)(?!(?:"
    r"a|about|after|all|an|and|any|are|as|at|auth|authentication|based|be|before|"
    r"box|but|by|can|cannot|change|changed|changes|check|checks|complexity|could|"
    r"did|does|entry|expired|expires|expiry|field|fields|file|files|flag|flow|for|"
    r"form|from|gate|handling|has|hash|hashes|hashing|have|here|hint|if|in|input|"
    r"inputs|into|is|isn|it|length|login|logins|manager|managers|may|might|must|"
    r"need|needs|not|of|on|only|option|or|page|param|parameter|policy|prompt|"
    r"prompts|protected|protection|recovery|required|requirement|requirements|"
    r"reset|resets|rotation|rule|rules|screen|should|so|storage|store|stored|"
    r"strength|than|that|the|then|there|this|to|update|updated|updates|used|"
    r"validation|value|values|var|variable|was|we|were|when|where|which|will|with|"
    r"would|you)\b)(" + _ONE_VALUE + r")"
)
_OTHER_LANGUAGE_LABEL_RE = re.compile(
    r"(?i)((?:密码|密碼|口令|パスワード|暗証番号|비밀번호|암호|סיסמה|סיסמא|كلمة المرور|كلمة السر|पासवर्ड|"
    r"(?<![^\W\d_])(?:пароль|парола|passwort|kennwort|contraseña|contrasena|clave|"
    r"mot de passe|senha|wachtwoord|lösenord|losenord|adgangskode|passord|salasana|"
    r"hasło|haslo|heslo|jelszó|parola|şifre|sifre)(?![^\W\d_]))"
    r"[ \t]*(?:(?:ist|lautet|es|est|era|é|är|er|是|为|為|は|は、|는|:|=|-)[ \t]*){0,2})"
    r"(" + _PHRASE_VALUE + r")"
)
# Shell assignments: an unknown variable name may still hold a secret, so only
# values that are plainly settings stay.
_ENV_ASSIGN_RE = re.compile(
    r"(?<![\w.\-])((?:export[ \t]+[A-Za-z_][A-Za-z0-9_]{0,80}|[A-Z][A-Z0-9_]{2,80})=)"
    r"(" + _ONE_VALUE + r")"
)
_SAFE_SETTING_RE = re.compile(
    r"(?i)^(?:\d[\d.]{0,20}|true|false|yes|no|on|off|none|null|debug|info|warn|warning|"
    r"error|production|development|dev|staging|test|prod|local|utf-?8)?$"
)
# Digits after a pin-style label. Status and exit codes are not secrets.
_PIN_RE = re.compile(
    r"(?i)(?<!status )(?<!exit )(?<!error )(?<!response )(?<!return )(?<!http )(?<!line )"
    r"(?<![A-Za-z0-9])((?:pin|passcode|code|cvv|cvc|cvn|otp|2fa)\b"
    r"(?:[^\n\d]{0,12}?|[^\n\d]{0,40}?\b(?:is|was)\b[ \t]*[:=]?[ \t]*))"
    r"(?<![\w.\-])(\d{3,8})(?![\w\-])"
)
_SEED_LABEL_RE = re.compile(
    r"(?i)\b((?:seed(?=[ \t]*(?:[:=]|is\b))|(?:seed|mnemonic|recovery|backup|wallet)"
    r"[ _\-]?(?:phrase|words?|code|key)|mnemonic|secret[ _\-]?(?:recovery[ _\-]?)?phrase|"
    r"pass[ _\-]?phrase)\b(?:[ \t]+(?:is|was|are|were))?[ \t]*[:=\-]?\s*)"
    r"((?:\d{1,2}[.)][ \t]*)?[a-z]{3,10}(?:[\s,]+(?:\d{1,2}[.)][ \t]*)?[a-z]{3,10}){2,29})"
)
_SEED_WORD_RE = re.compile(r"[A-Za-z]{3,8}")
_SEED_GAP_RE = re.compile(r"^[\s,;.\-:)\d]{1,8}$")
# Shortest seed phrase is 12 words; six catches a phrase pasted in halves.
_SEED_RUN = 6
_VERIFY_SEED_RUN = 8
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
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DAY = r"(?:[0-3]?\d)(?:st|nd|rd|th)?"
# "March 14, 1985", "14th of March 1985", "03/14/1985", and a bare "March 14"
# when a birth word sits just before it.
_WORD_DATE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:"
    + _MONTH + r"[ \t]+" + _DAY + r"(?:[ \t]*,)?[ \t]+\d{4}"
    + r"|" + _DAY + r"[ \t]+(?:of[ \t]+)?" + _MONTH + r"(?:[ \t]*,)?[ \t]+\d{4}"
    + r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"
    + r")(?![A-Za-z0-9])"
)
_BIRTH_DATE_RE = re.compile(
    r"(?i)\b((?:born|birthday|birthdate|birth[ \t]*date|date[ \t]+of[ \t]+birth|dob|d\.o\.b\.)"
    r"(?:[ \t]+(?:is|was|on|in))?[ \t]*:?[ \t]*)(?:" + _MONTH + r"[ \t]+" + _DAY
    + r"|" + _DAY + r"[ \t]+(?:of[ \t]+)?" + _MONTH + r")(?![A-Za-z0-9])"
)
_BIRTH_YEAR_RE = re.compile(
    r"(?i)\b((?:born|birthday|birthdate|birth[ \t]*date|date[ \t]+of[ \t]+birth|dob|d\.o\.b\.|"
    r"geboren|n[ée]e?|nacido|nata|nato)"
    r"(?:[ \t]+(?:is|was|on|in|am|le|en|el))?[ \t]*:?[ \t]*)"
    r"(?:\d{1,2}\.?[ \t]*(?:[A-Za-z\u00c0-\u00ff]{3,9}|[IVX]{1,4})\.?[ \t]*\d{2,4}"
    r"|[A-Za-z\u00c0-\u00ff]{3,9}\.?[ \t]+\d{4}"
    r"|\d{4})(?![A-Za-z0-9])"
)
_HOSTNAME_SERIAL_RE = re.compile(r"(?i)^[a-z]+(?:-[a-z]+)+-\d{1,3}$")
_DATE_RE = re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?[^\s,;]{0,10})?")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d \-().]{6,22}\d(?![\w])")
# Three or more digit groups under any separator: cards and phone numbers typed
# with commas, underscores or line breaks.
_DIGIT_GROUPS_RE = re.compile(
    r"(?<![\w.])\d{2,6}(?:[ \t]{0,2}[,_:;\n\r\-./][ \t]{0,2}\d{2,6}|[ \t]{1,2}\d{2,6}){2,12}(?![\w])"
)
_DIGIT_GROUP_MIN = 7
# Dashes, pipes and bullets between digit groups become a plain hyphen before
# matching, so " – ", " | " and " • " cannot hide a card number.
_ODD_SEP_RE = re.compile(r"(?<=\d)[ \t]*[\u2010-\u2015\u2212\u2022\u00b7\u2219|][ \t]*(?=\d)|(?<=\d)[ \t]+-[ \t]+(?=\d)")
# verify(): independent of the masking pattern. Twelve or more digits across
# anything that is not a letter is a card-length number.
_VERIFY_DIGITS_RE = re.compile(r"\d(?:[^A-Za-z\d\n]{0,4}\d){11,}")
_ADDRESS_RE = re.compile(
    r"(?<![\w.])\d{1,6}[ \t]+(?:[A-Za-z][A-Za-z.\-]{0,30}[ \t]+){1,3}"
    r"(?:[Ss]treet|[Ss]t|[Aa]venue|[Aa]ve|[Rr]oad|[Rr]d|[Bb]oulevard|[Bb]lvd|[Ll]ane|[Ll]n|"
    r"[Dd]rive|[Dd]r|[Cc]ourt|[Cc]t|[Pp]lace|[Pp]l|[Tt]errace|[Hh]ighway|[Hh]wy)\b\.?"
)
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
_ANY_PLACEHOLDER_RE = re.compile(r"\[(?:[a-z]+(?:-\d+)?|code, \d+ lines?)\]")
_ADJACENT_AFTER_RE = re.compile(r"(\[name-\d+\])([ \t]+)([A-Z][a-z]{1,30})\b")
_ADJACENT_BEFORE_RE = re.compile(r"(?<=[a-z,;] )([A-Z][a-z]{1,30})([ \t]+)(\[name-\d+\])")

# The verifier flags these in finished output.
_VERIFY_PATH_RE = re.compile(
    r"(?:(?<![\w.\-])~/[\w.\-]|(?<![\w.\-/])/(?:home|Users|root|tmp|var|etc|opt|mnt|srv|private|Volumes)/)"
)
_VERIFY_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]{1,20}://")
_VERIFY_LABEL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])pass(?:word|wd|phrase|code)[ \t]*(?:\bis\b|=|:)[ \t]*(\S)"
)


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


@lru_cache(maxsize=1)
def _bip39_words() -> frozenset:
    try:
        return frozenset(_BIP39_LIST.read_text("utf-8").split())
    except OSError:
        return frozenset()


def _seed_runs(text: str, minimum: int) -> list:
    """(start, end) of runs of BIP-39 words, allowing one miss for a typo.

    Ordinary sentences break a run quickly: "the", "of" and "is" are not on
    the list.
    """
    words = _bip39_words()
    runs = []
    start = end = None
    count = misses = 0
    previous_end = None

    def close():
        if start is not None and count >= minimum:
            runs.append((start, end))

    for match in _SEED_WORD_RE.finditer(text):
        joined = previous_end is not None and _SEED_GAP_RE.match(text[previous_end:match.start()])
        previous_end = match.end()
        known = match.group(0).casefold() in words
        if not joined:
            close()
            start, count, misses = None, 0, 0
        if known:
            start = match.start() if start is None else start
            end = match.end()
            count += 1
            misses = 0
        elif start is not None:
            misses += 1
            if misses > 1:
                close()
                start, count, misses = None, 0, 0
    close()
    return runs


def split_identity(value) -> set:
    """Lowercase word parts of an identity value, minus generic account names."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    parts = {p.casefold() for p in re.split(r"[^\w]+|_", text) if len(p) >= 3}
    return parts - _GENERIC_IDENTITY


def _digit_word_ok(piece: str) -> bool:
    """False for letters mixed with digits, unless a known term or tiny."""
    if not (any(c.isdigit() for c in piece) and any(c.isalpha() for c in piece)):
        return True
    return piece.casefold() in _DIGIT_WORDS or bool(_SHORT_DIGIT_WORD_RE.match(piece))


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
        for value in identity:
            value = unicodedata.normalize("NFKC", str(value or "")).strip()
            if not value:
                continue
            self.deny |= split_identity(value)
            words = [w for w in re.split(r"[^\w]+|_", value) if w]
            if len(value) >= 3 and value.casefold() not in _GENERIC_IDENTITY:
                self.phrases.append(words)
        # The user asked for these by name, so unlike machine identity they
        # always apply, however short or generic.
        for value in hide:
            value = unicodedata.normalize("NFKC", str(value or "")).strip()
            words = [w for w in re.split(r"[^\w]+|_", value) if w]
            if not words:
                continue
            self.phrases.append(words)
            if len(words) == 1:
                self.deny.add(words[0].casefold())
            else:
                self.deny |= split_identity(value)
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
                r"(?i)(?<![^\W_])(?<!\ue000)(?:" + "|".join(alternatives) + r")(?![^\W_])(?!\ue001)"
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
        if piece.casefold() in _DIGIT_WORDS and piece.casefold() not in self.deny:
            return True
        if not _digit_word_ok(piece):
            return False
        # sha256, 3d and h264 are one word and one number. Anything more
        # interleaved than that is a token, even if each letter "passes".
        if len(_RUNS_RE.findall(piece)) > 2:
            return False
        parts = _CAMEL_RE.findall(piece)
        if len(parts) >= 3 and any(len(p) <= 2 and not p.isdigit() for p in parts):
            return False
        return all(self._word_ok(part) for part in parts)

    def _parts_ok(self, core: str) -> bool:
        # "Welcome-123" is "Welcome123" with a separator; "sha-256" stays.
        if _SPLIT_DIGITS_RE.search(core) and not _digit_word_ok(re.sub(r"[_\-]", "", core)):
            return False
        return all(self._piece_ok(piece) for piece in re.split(r"[_\-'’]+", core))

    def _mask_word(self, core: str) -> str:
        if any(ch.isdigit() for ch in core) and len(core) >= 12:
            return self._mask("secret")
        category = "name" if core[:1].isupper() or not core.isascii() else "word"
        return self._alias(category, core)

    def _classify(self, core: str) -> str:
        if core.casefold() in self.keep:
            return core
        if core.casefold() in self.deny:
            return self._mask_word(core)
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
        if _HOSTNAME_SERIAL_RE.match(core) and core.casefold() not in self.keep:
            return self._mask("host")
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
        # The digits between hold markers index the stash; they are not text.
        if match.start() and match.string[match.start() - 1] == _PLACEHOLDER_OPEN:
            return raw
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

    def _after_label(self, match) -> str:
        """Keep the label in group 1, mask the value in group 2."""
        return match.group(1) + self._mask("secret")

    def _short_p(self, match) -> str:
        if _PORT_RE.match(match.group(2)):
            return match.group(0)
        return self._after_label(match)

    def _setting(self, match) -> str:
        if _SAFE_SETTING_RE.match(match.group(2).strip("\"'")):
            return match.group(0)
        return self._after_label(match)

    def _digit_groups(self, match) -> str:
        if sum(c.isdigit() for c in match.group(0)) < _DIGIT_GROUP_MIN:
            return match.group(0)
        return self._mask("number")

    def _seed_phrases(self, text: str) -> str:
        text = _SEED_LABEL_RE.sub(self._after_label, text)
        for start, end in reversed(_seed_runs(text, _SEED_RUN)):
            text = text[:start] + self._mask("secret") + text[end:]
        return text

    def _credentials(self, text: str) -> str:
        text = self._seed_phrases(text)
        text = _USER_THEN_P_RE.sub(self._after_label, text)
        text = _USER_COLON_RE.sub(self._after_label, text)
        text = _SECRET_FLAG_RE.sub(self._after_label, text)
        text = _SHORT_P_RE.sub(self._short_p, text)
        text = _LOGIN_PAIR_RE.sub(
            lambda m: m.group(1) + self._mask("secret") + " / " + self._mask("secret"), text
        )
        text = _LOGIN_VALUE_RE.sub(self._after_label, text)
        text = _VALUE_AS_LABEL_RE.sub(
            lambda m: m.group(1) + self._mask("secret") + m.group(3), text
        )
        text = _VALUE_IS_LABEL_RE.sub(lambda m: self._mask("secret") + m.group(2), text)
        text = _OTHER_LANGUAGE_LABEL_RE.sub(self._after_label, text)
        text = _LABEL_VALUE_RE.sub(self._after_label, text)
        text = _SECRET_ASSIGN_RE.sub(self._after_label, text)
        text = _SECRET_PROSE_RE.sub(self._after_label, text)
        text = _BARE_PASSWORD_RE.sub(self._after_label, text)
        text = _ENV_ASSIGN_RE.sub(self._setting, text)
        return _PIN_RE.sub(self._after_label, text)

    def _detectors(self, text: str) -> str:
        text = self._sub(_PEM_RE, "secret", text)
        text = self._credentials(text)
        text = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + " " + self._mask("secret"), text)
        for pattern in _SECRET_TOKEN_RES:
            text = self._sub(pattern, "secret", text)
        text = self._sub(_URL_RE, "url", text)
        text = self._sub(_SCP_RE, "url", text)
        text = self._sub(_EMAIL_RE, "email", text)
        text = self._sub(_UUID_RE, "id", text)
        text = self._sub(_DATE_RE, "date", text)
        text = self._sub(_WORD_DATE_RE, "date", text)
        text = _BIRTH_YEAR_RE.sub(lambda m: m.group(1) + self._mask("date"), text)
        text = _BIRTH_DATE_RE.sub(lambda m: m.group(1) + self._mask("date"), text)
        text = self._sub(_IPV4_RE, "ip", text)
        text = self._sub(_MAC_RE, "ip", text)
        text = self._sub(_IPV6_RE, "ip", text)
        text = self._sub(_WIN_PATH_RE, "path", text)
        text = self._sub(_UNC_RE, "path", text)
        text = self._sub(_POSIX_PATH_RE, "path", text)
        text = self._sub(_RELATIVE_PATH_RE, "path", text)
        text = self._sub(_HOST_RE, "host", text)
        text = self._sub(_MONEY_RE, "amount", text)
        text = self._sub(_ADDRESS_RE, "address", text)
        text = self._sub(_PHONE_RE, "number", text)
        text = _DIGIT_GROUPS_RE.sub(self._digit_groups, text)
        text = self._sub(_HANDLE_RE, "handle", text)
        text = self._sub(_TICKET_RE, "ref", text)
        text = self._sub(_ISSUE_RE, "ref", text)
        if self._phrase_re is not None:
            text = self._phrase_re.sub(lambda m: self._alias("name", m.group(0)), text)
        return _CONTACTED_NAME_RE.sub(
            lambda m: m.group(1) + self._alias("name", m.group(2)), text
        )

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
        text = _ODD_SEP_RE.sub("-", text)
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

    def is_hidden(self, value: str) -> bool:
        """True if ``value`` holds a word or phrase this export must not show."""
        if self._phrase_re is not None and self._phrase_re.search(value):
            return True
        return any(w.casefold() in self.deny for w in re.findall(r"[^\W_]+", value))

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
        # A placeholder's own text is not content: hiding "name" must not
        # refuse "[name-1]".
        bare = _ANY_PLACEHOLDER_RE.sub(" ", value)
        for word in re.findall(r"[^\W_]+", bare):
            if word.casefold() in self.deny:
                raise RedactionError("identity token in export")
            if not _digit_word_ok(word) and not any(word.casefold() in k for k in self.keep):
                raise RedactionError("unredacted secret in export")
        if self._phrase_re is not None and self._phrase_re.search(bare):
            raise RedactionError("identity phrase in export")
        if _seed_runs(bare, _VERIFY_SEED_RUN):
            raise RedactionError("unredacted secret in export")
        if _VERIFY_DIGITS_RE.search(bare):
            raise RedactionError("unredacted number in export")
        for match in _DIGIT_GROUPS_RE.finditer(bare):
            if sum(c.isdigit() for c in match.group(0)) >= _DIGIT_GROUP_MIN:
                raise RedactionError("unredacted number in export")
        for match in _VERIFY_LABEL_RE.finditer(value):
            if not match.group(1).startswith("["):
                raise RedactionError("unredacted secret in export")
        for piece in re.findall(r"[^\W_]{20,}", bare):
            if (
                any(c.isdigit() for c in piece)
                and any(c.isalpha() for c in piece)
                and piece.casefold() not in self.keep
            ):
                raise RedactionError("unredacted secret in export")
