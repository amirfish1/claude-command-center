# CCC content source pack: 12 pain-first story units

First draft for lead edit. Companion to `pain-feature-proof.md` (owns the
claims) and `message-architecture.md` (owns the voice). This file is raw
material for the growth system: each unit is a complete story that channel
adapters may cut down but never inflate.

Drafted: 2026-07-10, against pain-feature-proof.md audited 2026-07-10 (v5.6.0
released, v5.7.0-dev source).

## Rules of use

1. Every capability claim in this pack traces to a **Built** row in
   `pain-feature-proof.md` section 2, or carries its Partial qualification
   inline. If a claim is not in that table, it does not ship, no matter how
   good the sentence is.
2. Every post starts with the painful moment, in second person or founder
   first person. Never "CCC now has X." Never a feature announcement frame.
3. Real-user quotes are verbatim and attributed generically as "from public
   issues/threads." Founder quotes are first person. Never fabricate a quote,
   never tighten one silently.
4. No em-dashes anywhere. Periods, commas, colons.
5. Banned vocabulary (per message-architecture.md section 10): AI
   orchestration, agentic workflows, revolutionary, supercharge, 10x,
   blazingly fast, seamless, magic, game-changer, copilot for X,
   mission-critical, enterprise-grade, "the future of."
6. Channel versions are genuinely different. Reddit invites discussion and
   never pushes a link in the body. LinkedIn carries the operational lesson.
   X carries the single sharpest frame plus the clip.
7. Every visual is a real capture from the seeded demo bundle or a
   synthetic-HOME local server (asset IDs resolve in pain-feature-proof.md
   section 4). No mockups presented as product.
8. One CTA per piece, matched to awareness. Cold: "Tour the live demo. All
   data fake, nothing to install." (ccc.amirfish.ai/demo). Warm: the install
   line: `curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`
9. Nothing private: no client names, no real repo names besides
   claude-command-center, no local filesystem paths, no unreleased or
   private-only features (see the "Never claim" list in
   message-architecture.md section 7).

---

## SU-01: Nine tabs, zero picture

**Family:** F1 (See everything). Pain row 1.

**The painful moment.** You have nine terminal tabs open and each one holds a
coding agent mid-task. Ask yourself which one finished, which one is stuck,
and which one you started before lunch. You cannot answer without clicking
through all nine, and by the time you finish the tour, the first one has
changed state.

**Why the obvious workaround fails.** More discipline means naming tabs and
checking them on a rotation. That holds until session four. The whole point
of running agents in parallel was to stop babysitting processes, and the
rotation is babysitting with extra steps.

**The CCC solution.** One local board lists every coding-agent session on
your machine with live status, so you scan the whole fleet in seconds and go
where you are needed.

**Visible proof.** S-OVR (full dashboard, mixed live/waiting rows, 3 repos),
V-01 (scan the fleet: list, board, live rows).

**LinkedIn version.**

A user of a competing tool said it better than I ever could: "Parallel
sessions are the single biggest productivity unlock of this product, and
today they're also the single biggest source of confusion." (From a public
issue thread.)

I hit the same wall. Nine terminal tabs, each holding a coding agent
mid-task. I could not say which one had finished, which one was stuck, or
which one I had started before lunch. tmux stopped scaling.

The lesson: parallel agents do not fail on capability. They fail on
visibility. No human holds nine invisible processes in their head, and
discipline does not fix that. Naming conventions do not fix it. A board
fixes it.

So I built one: a local dashboard that lists every coding-agent session on
my machine with live status. Scan the fleet in seconds, then go where you
are needed.

If you run more than three sessions at once, I would genuinely like to know
how you keep track today.

#ClaudeCode #DevTools

**X version (single post).**

Nine terminals. Nine coding agents. Zero idea which one needs you.

Parallel agents don't fail on capability. They fail on visibility.

Every session on your machine, one local board, live status. 20 seconds:
[V-01 clip]

**Reddit-native angle.** r/ClaudeAI. Title: "People running 4+ parallel
Claude Code sessions: how do you actually keep track of them?" Body
approach: describe the nine-tab moment honestly and the failed
naming-convention phase, mention that the author ended up building a local
board for themselves, then ask what setups others use (tmux panes, scripts,
sticky notes). No link in the body; share the repo only if asked in
comments.

**Video outline (30-60s).**
1. Painful start: a wall of terminal tabs, cursor hovers tab to tab, nothing
   tells you anything.
2. Smallest flow: open the CCC board; rows appear for every session with
   repo, status, and last activity.
3. Resolved state: eyes land on the one live row and the one waiting row;
   click into the waiting one and answer it.

**CTA.** Cold audience (this is a first-touch story): "Tour the live demo.
All data fake, nothing to install." ccc.amirfish.ai/demo

**Claims requiring qualification.** None, provided the copy says
"coding-agent sessions" and does not enumerate the five engines. If the
adaptation names engines, it must carry the row 4 qualification: Claude Code
is first-class; Codex, Cursor, Antigravity, and Kilo Code spawn and ingest
with documented gaps. Say "one board for five engines," never "identical
support for five engines."

---

## SU-02: The dashboard that goes blind

**Family:** F1 (See everything). Pain row 2.

**The painful moment.** You installed a session manager, liked it for a
week, then resumed one session by hand from a terminal because that was
faster in the moment. The tool never saw that session again. Now your
dashboard shows four sessions and your machine is running six, and the tool
you adopted to end the guessing is itself part of the guessing.

**Why the obvious workaround fails.** "Just launch everything through the
tool" is a rule you will break the first time muscle memory types the resume
command. Tools that own execution go blind the moment you touch a terminal,
and you will always touch a terminal.

**The CCC solution.** CCC attaches instead of owning: it reads each engine's
on-disk state as the source of truth, so hand-launched and hand-resumed
sessions appear on the board automatically, and closing the board changes
nothing about the sessions.

**Visible proof.** V-02 (close board, sessions continue, reopen and
reattach), S-F1a (terminal-launched and dashboard-launched rows coexisting).

**LinkedIn version.**

There is a whole category of agent dashboards with the same silent failure
mode: they only see what you launch through them.

Resume a session by hand, just once, because it was faster in the moment,
and that session vanishes from the board forever. The tool you adopted to
end the guessing becomes part of the guessing. Every launch-through-me tool
has this flaw baked into its architecture.

The design lesson I took from it: a supervision layer must read ground
truth, not maintain its own registry. CCC never owns execution. It reads the
session state each engine already writes to disk. Launch from a terminal,
from the dashboard, from a script: the board sees it either way. Kill the
dashboard entirely and every session keeps running; reopen it tomorrow and
everything reattaches.

A dashboard should be a lens, not a runtime. If your current setup goes
blind when you bypass it, that is not a discipline problem. It is an
architecture problem.

#DevTools

**X version (3-post thread).**

1/ Every agent dashboard I tried had the same failure mode: resume a session
by hand, once, and the tool never sees it again.

The board says 4 sessions. The machine runs 6.

2/ The fix is architectural, not behavioral. Don't own execution. Read the
on-disk state the engines already write. Then hand-launched sessions show up
like everything else.

3/ Kill the dashboard, sessions keep running. Reopen it, everything
reattaches. A lens, not a runtime. [V-02 clip]

**Reddit-native angle.** r/ChatGPTCoding. Title: "Session managers that go
blind when you launch outside them: how do you deal with this?" Body
approach: describe the resume-by-hand failure mode neutrally, ask whether
others have hit it with squad-style or wrapper tools, and float
reading-engine-state-from-disk as an approach without naming the project
unless asked.

**Video outline (30-60s).**
1. Painful start: board shows sessions; a terminal resumes one by hand
   off-board.
2. Smallest flow: the hand-resumed session appears on the board on its own;
   then the board tab is closed entirely.
3. Resolved state: reopen the board; every session is back, states intact,
   nothing was lost.

**CTA.** Cold audience (differentiator story for people burned by other
tools): "Tour the live demo. All data fake, nothing to install."
ccc.amirfish.ai/demo

**Claims requiring qualification.** None.

---

## SU-03: The eight forgotten sessions

**Family:** F1 (See everything). Pain row 3.

**The painful moment.** Founder first person: I used to audit my machine
every couple of weeks and find work I had completely forgotten. "Used to
find 8 orphaned sessions I'd forgotten about." Eight half-finished tasks,
some with good work in them, all invisible because nothing kept an
inventory.

**Why the obvious workaround fails.** Memory does not scale past a handful
of parallel tracks, and terminal history is not an inventory: it tells you
what you typed, not what is still alive or what was abandoned mid-thought.

**The CCC solution.** The board keeps a full inventory including dormant and
archived sessions, with time-gap markers, so nothing is silently lost.

**Visible proof.** S-F1b (time-gap markers and dormant/archived rows).

**LinkedIn version.**

Embarrassing operational confession: before I fixed this, I used to find 8
orphaned sessions I'd forgotten about. Eight. Half-finished refactors, an
abandoned bug hunt, a research thread with genuinely useful findings in it.
All invisible, because nothing on my machine kept an inventory of
agent work.

Here is the thing about parallel agent sessions: starting them is so cheap
that you start more than you can remember. The cost is not the tokens. The
cost is the work that silently evaporates because you forgot it exists.

Human memory is not an inventory system. Terminal history is not an
inventory system either: it records what you typed, not what is still alive.

The fix was boring and structural: a board that lists everything, including
dormant and archived sessions, with time-gap markers showing how long each
one has sat idle. Forgotten work stopped being possible, because forgetting
requires invisibility.

What is the oldest half-finished agent session you have rediscovered on
your machine?

**X version (single post).**

Audited my own machine and found 8 orphaned agent sessions I'd forgotten
about. Half-finished work, just sitting there.

Starting sessions is cheap. Remembering them is not.

Full inventory, dormant included, time-gap markers: [S-F1b]

**Reddit-native angle.** r/ClaudeAI. Title: "Just found a pile of old Claude
Code sessions I completely forgot existed. Anyone else?" Body approach: tell
the eight-orphaned-sessions story as a confession, ask how others rediscover
or garbage-collect forgotten sessions, and let the inventory-tooling
discussion emerge in comments rather than pitching anything.

**Video outline (30-60s).**
1. Painful start: a bare terminal; nothing suggests any past work exists.
2. Smallest flow: open the board and scroll: live rows first, then dormant
   rows with time-gap markers, then archived.
3. Resolved state: click a weeks-old dormant session; the full transcript is
   right there, work recovered.

**CTA.** Cold audience: "Tour the live demo. All data fake, nothing to
install." ccc.amirfish.ai/demo

**Claims requiring qualification.** None.

---

## SU-04: The agent asked. Nobody heard.

**Family:** F2 (Know what needs you). Pain row 6.

**The painful moment.** An agent asked "want me to proceed?" 40 minutes ago.
You were in another window, shipping something else. The agent has been
sitting at a prompt the entire time, patient and useless, while you assumed
it was working. The bottleneck stopped being the models pretty quickly. It
became me.

**Why the obvious workaround fails.** Checking every tab on a rotation makes
you the polling loop, and the interval is always wrong: too short and you
get nothing else done, too long and agents idle for most of an hour.

**The CCC solution.** Attention detection reads the actual transcript and
flags sessions that ended on a real question, including plain-prose ones
with no question mark, so waiting-on-you work surfaces itself (with desktop
notifications on macOS).

**Visible proof.** S-F2a (needs-attention lane with question-waiting
session), V-03 (spot the waiting session, open it, answer).

**LinkedIn version.**

The bottleneck stopped being the models pretty quickly. It became me.

Here is the shape of the failure: an agent asks "want me to proceed?" and
then waits. It does not retry, does not escalate, does not time out. It just
sits there. If you are in another window, that session produces nothing for
40 minutes while you believe it is working.

Multiply by five parallel sessions and the math is brutal. Capability isn't
the bottleneck anymore, supervision is. The expensive resource in the loop
is no longer model output. It is your attention, and it is being allocated
blind.

The fix that worked for me: stop polling, start getting flagged. CCC reads
each transcript and detects when a session ended on a real question,
including plain-prose questions with no question mark, and surfaces those
sessions on the board (plus a desktop notification on macOS). Waiting work
announces itself instead of hiding.

How much agent idle time do you think you eat per day without noticing?

#ClaudeCode

**X version (3-post thread).**

1/ Your agent asked "want me to proceed?" 40 minutes ago.

It's still waiting. You thought it was working.

2/ This is the real cost of parallel sessions. Not tokens. Idle time you
never see, because a blocked agent looks exactly like a busy one from the
outside.

3/ Fix: read the transcripts, flag every session that ended on a question,
put them in one lane. Waiting work surfaces itself. [V-03 clip]

**Reddit-native angle.** r/ExperiencedDevs. Title: "Running multiple coding
agents: supervision, not capability, became my bottleneck. Anyone else
measuring idle time?" Body approach: lay out the blocked-agent-looks-busy
problem and the founder's polling-loop failure in plain engineering terms,
ask how others detect a waiting agent, and keep tooling mentions to comment
replies only.

**Video outline (30-60s).**
1. Painful start: five sessions on the board; one has been ending on a
   question for 40 minutes and nothing in a terminal would tell you.
2. Smallest flow: the needs-attention lane flags it; click the row; the
   question is the last line of the transcript.
3. Resolved state: type the answer from the board; the session goes back to
   working.

**CTA.** Warm audience (reader already runs parallel sessions and feels
this): install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** Desktop notifications are macOS-only;
keep the "on macOS" qualifier inline wherever notifications are mentioned.
Attention detection itself is Built with no qualification.

---

## SU-05: Death by full context

**Family:** F2 (Know what needs you). Pain row 7.

**The painful moment.** A session dies mid-refactor because its context
window silently filled. The agent was three files into a five-file change,
and now the thread that held all that state is unusable. You get to
reconstruct the plan from a truncated transcript.

**Why the obvious workaround fails.** You cannot see context pressure from a
terminal until the engine warns you, which is usually moments before the
ceiling, and with five sessions open you will not be watching the right one
when it happens.

**The CCC solution.** Every session on the board carries a context meter
with warning and danger levels, so you see exhaustion coming across the
whole fleet and can click through to compact before it costs you the
session.

**Visible proof.** S-F2b (context meters including one in the danger zone),
V-04 (find the session nearly out of context via meters).

**LinkedIn version.**

The most expensive way to lose an afternoon with coding agents: a session
dies mid-refactor because its context window filled up, silently, while you
were watching a different terminal.

The state that session held (the plan, the constraints you negotiated, the
three files already changed) does not transfer. You reconstruct it from a
truncated transcript and start again, warier and slower.

What made this unfixable for me in a terminal workflow: context pressure is
invisible until the engine complains, and by then you are moments from the
ceiling. With five sessions running, the odds you are watching the right one
at that moment round to zero.

The operational fix is not smarter prompting. It is instrumentation. Every
session on my board shows a context meter, with warning and danger
thresholds, visible fleet-wide at a glance. The session at 88% gets
compacted on my schedule, not discovered dead on its own.

Instrument first. Everything else is hoping.

#ClaudeCode

**X version (single post).**

Worst agent failure mode: context window fills silently, session dies
mid-refactor, all negotiated state gone.

You can't see it coming from a terminal. You can from a meter.

Fleet-wide context meters, warning and danger levels: [V-04 clip]

**Reddit-native angle.** r/ClaudeAI. Title: "How do you avoid losing
sessions to context exhaustion when running several at once?" Body approach:
describe the mid-refactor death and the reconstruction cost, ask what
compaction habits and monitoring approaches others use, and compare notes
rather than promote.

**Video outline (30-60s).**
1. Painful start: board with several sessions; one context meter sits deep
   in the danger zone, unnoticed in any terminal.
2. Smallest flow: scan the meters, spot the red one, click through.
3. Resolved state: compact the session; the meter drops; the refactor
   continues in the same thread.

**CTA.** Warm audience: install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** None.

---

## SU-06: Thirty rows that mean nothing

**Family:** F3 (Organize work that outgrew a flat list). Pain row 12.

**The painful moment.** Your session list hit thirty rows and stopped
meaning anything. Which of these are being reviewed? Which are done but
unverified? Which are safe to archive? A flat list answers none of it; it
just scrolls.

**Why the obvious workaround fails.** Mental state-tracking caps out fast,
and a naming scheme ("REVIEW-", "DONE-") is a database you maintain by hand
in the worst editor available: session titles.

**The CCC solution.** The board has kanban states (Working, Review, In
Testing, Verified, Archived) with drag-and-drop and multi-select, so the
state of the whole operation is readable at a glance.

**Visible proof.** S-F3b (kanban with populated columns), V-06 (drag a card
across states).

**LinkedIn version.**

Nobody warns you about the second scaling wall with coding agents. The first
is running them in parallel at all. The second is quieter: your session list
hits thirty rows and stops meaning anything.

Thirty rows in a flat list is not a view of your operation. It is a scroll
of guilt. Which ones are awaiting review? Which are done but unverified?
Which are safe to archive? The list cannot say, so you re-derive it in your
head, several times a day, badly.

I tried the naming-scheme workaround (REVIEW- prefixes, DONE- prefixes) and
learned what it really is: a state database maintained by hand in the worst
editor available.

Work-in-progress state deserves structure. My board went kanban: Working,
Review, In Testing, Verified, Archived. Drag a card when its state changes,
multi-select when a batch lands. Now "how is the operation doing" is a
glance, not an interrogation.

Old lesson, new domain: when the list stops meaning anything, add states.

**X version (single post).**

30 agent sessions in a flat list is not visibility. It's a scroll of guilt.

Working / Review / In Testing / Verified / Archived. Drag cards,
multi-select batches. The whole operation, one glance. [V-06 clip]

**Reddit-native angle.** r/ChatGPTCoding. Title: "At what session count did
your flat list stop working, and what did you switch to?" Body approach:
describe the thirty-row wall and the failed prefix-naming phase, ask what
structures others impose (folders, boards, spreadsheets, nothing), and treat
it as a workflow survey rather than a pitch.

**Video outline (30-60s).**
1. Painful start: a long flat list of sessions, scrolling and scrolling,
   no state visible.
2. Smallest flow: switch to board view; columns appear populated; drag one
   card from Working to Review.
3. Resolved state: the full kanban, whole operation readable in one frame;
   multi-select archives a finished batch.

**CTA.** Cold audience (organization pain draws list-tool switchers): "Tour
the live demo. All data fake, nothing to install." ccc.amirfish.ai/demo

**Claims requiring qualification.** None.

---

## SU-07: One more instruction, one more terminal hunt

**Family:** F4 (Steer many agents without orchestration code). Pain row 18.

**The painful moment.** A session that went dormant an hour ago needs one
more instruction. One sentence. To deliver it you have to find the right
terminal, or worse, reopen one, cd to the repo, and resume the session by
hand. The instruction takes five seconds; the plumbing takes minutes, so you
put it off.

**Why the obvious workaround fails.** Keeping every terminal open forever
just to preserve a input path back into each session turns your machine into
a window museum, and it still breaks the first time you reboot.

**The CCC solution.** Type into any session directly from the browser;
dormant sessions auto-resume to receive the message, so steering happens
from one place with no terminal hunting.

**Visible proof.** V-10 (type into a dormant session from the browser),
S-F4a (composer typing into a dormant session).

**LinkedIn version.**

There is a category of work I kept postponing without noticing: the
five-second instruction to a dormant agent session.

"Also add a test for the empty case." That is the whole message. But the
session went dormant an hour ago, so delivering it means finding the right
terminal among nine, or reopening one, navigating to the repo, resuming the
session, and then typing the sentence. The instruction is five seconds; the
plumbing is minutes. So the instruction quietly does not happen, and the
work ships slightly worse.

The workaround, keeping every terminal open forever as a warm input path,
turns a laptop into a window museum and dies at the first reboot anyway.

What fixed it: making every session, dormant or not, accept input from one
place. On my board I type into any session from the browser; a dormant one
auto-resumes to receive the message. The five-second instruction became a
five-second action.

Friction does not block work. It silently deletes the small improvements.

#DevTools

**X version (single post).**

The instruction: 5 seconds. "Also add a test for the empty case."

The plumbing: find the terminal, resume the session, then type it.

So it doesn't happen.

Type into any session from the browser. Dormant ones auto-resume. [V-10
clip]

**Reddit-native angle.** r/ClaudeAI. Title: "How do you send a quick
follow-up to a Claude Code session that already ended?" Body approach: name
the tiny-instruction friction and the window-museum workaround, ask whether
others resume by hand, re-prompt from scratch, or have found something
better, and let tooling come up naturally in comments.

**Video outline (30-60s).**
1. Painful start: a dormant session row, hours idle; the fix it needs is one
   sentence long.
2. Smallest flow: click the row on the board, type the sentence into the
   composer, send.
3. Resolved state: the session resumes itself, receives the message, and
   starts working; no terminal was touched.

**CTA.** Warm audience: install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** None. Do not extend this into claims
about headless-session input surviving server restarts; the ledger
explicitly bars claiming durability across restarts.

---

## SU-08: You are the message bus

**Family:** F4 (Steer many agents without orchestration code). Pain row 19.

**The painful moment.** You have two agents whose work overlaps: one is
designing an API, the other is building against it. Every time one makes a
decision the other needs, you copy the relevant part out of one terminal and
paste it into the other, with commentary. You have become a message bus with
opinions, and it is the slowest component in the system.

**Why the obvious workaround fails.** Writing an orchestrator script to
connect them is a real project: process management, turn-taking, failure
handling. You wanted the agents to talk, not to build middleware this
weekend.

**The CCC solution.** Group chats: multiple sessions share one chat and are
auto-pinged to respond in turn, so coordination becomes a conversation
instead of code.

**Visible proof.** S-F4b (group chat with multiple agent participants), V-11
(group chat thread with agents responding).

**LinkedIn version.**

Job title I never wanted: message bus.

Two of my agent sessions had overlapping work. One designing an API, one
building against it. Every decision the first made, the second needed. So I
copied context out of one terminal and pasted it into the other, hourly,
with commentary. I was the integration layer, and I was the slowest and
least reliable component in the system.

The standard answer is "write an orchestrator." I looked at what that
actually means: process management, turn-taking logic, failure handling,
retries. A weekend of middleware to save minutes of pasting. The economics
only work if you enjoy building middleware, and I had a product to ship.

What I actually wanted was for the agents to talk. So that is what my board
does now: put several sessions in a group chat, and each is automatically
pinged to respond in turn. Coordination became a conversation I can read
and steer, not code I have to maintain.

Fire the message bus. Especially when it is you.

#ClaudeCode

**X version (3-post thread).**

1/ Two agents, overlapping work. One designs the API, one builds against it.

Guess who carries every decision between their terminals, by hand?

You. You're the message bus.

2/ The standard fix is "write an orchestrator." Process management,
turn-taking, failure handling. A weekend of middleware to save minutes of
pasting.

3/ Or: put the sessions in a group chat. Each gets auto-pinged to respond in
turn. Coordination as a conversation you can read and steer. [V-11 clip]

**Reddit-native angle.** r/ChatGPTCoding. Title: "Those of you running
multiple agents on one project: how do they share decisions?" Body approach:
describe the human-message-bus experience concretely, ask whether people
copy-paste, use shared files, or wrote their own coordination scripts, and
frame it as comparing coordination patterns rather than presenting an
answer.

**Video outline (30-60s).**
1. Painful start: two transcripts side by side; the same decision text
   visibly copy-pasted from one into the other.
2. Smallest flow: create a group chat, add both sessions, post one message
   to the group.
3. Resolved state: the agents respond in turn in the shared thread; the
   human reads and steers instead of ferrying.

**CTA.** Warm audience: install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** None. (If an adaptation adds the
synchronous sibling-ask API as a power-user aside, that is also Built, pain
row 20, asset S-F4c.)

---

## SU-09: Wake up to closed tickets

**Family:** F5 (Let work run unattended). Pain row 23, with the row 24
watcher folded in.

**The painful moment.** It is 11pm and you have a list of nine small,
well-understood fixes. Each is twenty minutes of agent work. You are the
only thing standing between that list and a clean morning, because someone
has to feed the next task to an agent when the previous one finishes, and
that someone goes to sleep.

**Why the obvious workaround fails.** Kicking off nine sessions at bedtime
is not a pipeline, it is a prayer: nothing hands out the next task, nothing
notices a stuck worker, and nothing checks the fixes actually landed.

**The CCC solution.** A work queue with a claim, fix, verify, close
lifecycle and bound agent workers drains the list without you, and a
queue-health watcher flags stuck queues from ground truth and nudges workers
automatically.

**Visible proof.** S-F5a (queue board with tickets in states), S-F5b (health
strip including a stuck flag), V-12 (ticket claimed, fixed, verified).

**LinkedIn version.**

The 11pm problem: nine small, well-understood fixes on a list. Each one is
twenty minutes of agent work. The agents do not sleep. I do. And the naive
version of "let them run overnight" is just launching nine sessions at
bedtime and hoping, because nothing hands out the next task, nothing notices
a worker that got stuck at 1am, and nothing verifies the fixes actually
landed.

Hope is not a pipeline. A queue is.

So the overnight setup I run now looks like infrastructure, not prayer:
tickets go into a work queue, agent workers claim them, and every ticket
moves through a claim, fix, verify, close lifecycle. A health watcher judges
the queue from ground truth, meaning actual ticket and worker state, not an
agent's self-report of "done," and nudges stuck workers automatically.

The part that matters most is verify. Trust the queue's evidence, not the
agent's confidence.

Waking up to closed tickets is a real thing. It just takes a queue, not a
prayer.

#ClaudeCode #BuildInPublic

**X version (3-post thread).**

1/ 9 small fixes on the list at 11pm. Each is 20 min of agent work.

Launching 9 sessions at bedtime is not a pipeline. It's a prayer. Nothing
assigns the next task, notices a stuck worker, or checks the fix landed.

2/ A queue is a pipeline: tickets in, workers claim, fix, verify, close. A
health watcher judges the queue from ground truth, not from an agent saying
"done," and nudges stuck workers.

3/ Woke up to a drained queue. [V-12 clip]

**Reddit-native angle.** r/SideProject. Title: "Solo builders: has anyone
actually made overnight agent runs reliable?" Body approach: contrast the
launch-and-pray approach with a queue lifecycle honestly, including what
still needs a human in the morning, and ask what failure modes others hit
running agents unattended.

**Video outline (30-60s).**
1. Painful start: a queue board with a stack of open tickets, evening
   timestamp.
2. Smallest flow: a worker claims a ticket; it moves through fix and verify;
   the health strip stays green.
3. Resolved state: morning: the queue is drained, tickets closed with
   verification, one flagged item waiting for human review.

**CTA.** Warm audience (automation users are past the demo stage): install
line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** None, but naming discipline applies: the
public name is "work queue." Use "Watchtower" sparingly if at all; it is a
separate product name per the ledger. Do not frame this as scheduled or cron
jobs; the queue drains when workers are bound, and no scheduling claim is
permitted.

---

## SU-10: Two agents, one working tree

**Family:** F5 (Let work run unattended). Pain row 27.

**The painful moment.** You put two agents on the same repo and they
clobbered each other inside an hour: one ran the formatter while the other
was mid-edit, and a third of the diff belongs to nobody. As a public issue
thread puts it, "parallel sessions currently conflict on the working tree."

**Why the obvious workaround fails.** "Be careful about which files each
agent touches" is not a boundary, it is a wish; agents run tests, format
code, and touch shared files you did not anticipate.

**The CCC solution.** One click gives each task a fresh git worktree, with
optional per-repo init scripts, so parallel agents get true isolation
without ceremony.

**Visible proof.** S-F5e (worktree spawn modal).

**LinkedIn version.**

Cheapest catastrophic mistake in agent-assisted development: two agents, one
working tree.

I did it the naive way first. Two sessions on the same repo, "they're
touching different files, it's fine." Within the hour one agent ran the
formatter across the codebase while the other was mid-edit, and I had a diff
that belonged to nobody. This is not an exotic failure. Public issue threads
describe exactly it: parallel sessions conflicting on the working tree. It
is the default outcome, not the unlucky one.

File-level discipline does not work because agents do not stay in their
lane: they run tests, format, touch lockfiles and shared configs you never
listed.

Git already solved isolation years ago: worktrees. Each task gets its own
checkout, same repo, zero interference. The only real complaint was
ceremony, so I reduced it to one click: fresh worktree per task, init
scripts run automatically, agent starts inside it.

Rule of thumb: one agent, one working tree. No exceptions you will not
regret.

#Git

**X version (single post).**

Two agents, one working tree: one ran the formatter mid-edit of the other.
The diff belonged to nobody.

Public issues call this out: parallel sessions conflict on the working tree.

One click, fresh worktree per task, init scripts included. [S-F5e]

**Reddit-native angle.** r/git. Title: "Worktrees turned out to be the
answer to parallel coding agents clobbering each other. What's your setup?"
Body approach: tell the formatter-collision story, credit worktrees as
long-standing git functionality rather than anything novel, and ask how
others structure worktree naming, init, and cleanup for short-lived task
checkouts.

**Video outline (30-60s).**
1. Painful start: one repo, two sessions, a tangled diff neither agent
   owns.
2. Smallest flow: spawn a session with the worktree option; a fresh checkout
   is created and the init script runs.
3. Resolved state: two sessions working in two clean worktrees; each diff is
   coherent and attributable.

**CTA.** Warm audience: install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** None. Note for the lead: the full
verbatim quote from the public issue begins with a banned vocabulary word
("Seamless local multi-branching: parallel sessions currently conflict on
the working tree"), so this draft quotes only the second clause verbatim.
Decide whether the full quote is admissible since it is user speech, not our
copy.

---

## SU-11: Issues in, verified closures out

**Family:** F5 (Let work run unattended). Pain row 26.

**The painful moment.** You maintain an open-source repo and the issues
queue is the guilt pile: fifteen reproducible, well-scoped bugs that each
need an hour nobody has. Meanwhile turning even one of them into an agent
session means copying the issue text, setting up context, and later writing
the closing comment by hand.

**Why the obvious workaround fails.** Pasting issues at an agent does not
close the loop: the fix may or may not land, the issue may or may not get
closed, and an agent saying "done" is not evidence a maintainer can stand
behind.

**The CCC solution.** The issue board turns a GitHub issue into a working
session with one click, and verify closes the issue with a commit-SHA
comment, so what lands in the tracker is evidence, not a claim. (Requires
the `gh` CLI.)

**Visible proof.** S-F5d (issue cards with spawn action), V-14 (issue card
to spawned session).

**LinkedIn version.**

Every OSS maintainer knows the guilt pile: fifteen well-scoped, reproducible
issues that each need an hour nobody has.

Agents should be perfect for this, and the naive version disappoints in a
specific way. You paste an issue at an agent, it works, it says "done." Now
what? Did the fix land on the branch? Which commit? Is the issue closed with
anything a future reader can trust? An agent's "done" is a claim, and a
tracker full of claims is worse than a tracker full of open issues, because
it lies.

The loop only counts when it is closed with evidence. On my board, an issue
card becomes a working session in one click. When the work verifies, the
issue is closed with a comment carrying the commit SHA. Not "the agent
finished." Here is the commit, check it yourself. (It rides the gh CLI, so
setup is one install.)

Maintainers do not need agents that claim. They need closures they can
stand behind.

#OpenSource

**X version (single post).**

An agent saying "done" is a claim. A tracker full of claims lies.

One click: GitHub issue becomes a working session. On verify, the issue
closes with the commit SHA in the comment. Evidence, not vibes. [V-14 clip]

**Reddit-native angle.** r/opensource. Title: "Maintainers using coding
agents on the issue backlog: how do you keep closures trustworthy?" Body
approach: raise the agent-says-done trust problem from a maintainer's
perspective, propose commit-SHA-in-the-closing-comment as a norm worth
discussing, and ask what verification standards other maintainers hold
agent-assisted fixes to.

**Video outline (30-60s).**
1. Painful start: an issue backlog on the board, cards piling up.
2. Smallest flow: click spawn on one issue card; a session starts with the
   issue as its brief.
3. Resolved state: the work verifies; the issue closes with a commit-SHA
   comment visible on GitHub.

**CTA.** Warm audience (maintainers evaluate by reading source anyway):
install line.
`curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash`

**Claims requiring qualification.** Requires the `gh` CLI; say so wherever
install-adjacent. Otherwise Built, no qualification.

---

## SU-12: The fleet keeps going. Your phone shows nothing.

**Family:** F6 (Work from anywhere). Pain row 29.

**The painful moment.** You stepped out for lunch with four sessions
running. They kept working; one hit a question ten minutes in and has been
idle since. Your phone, which can reach practically every system you care
about, shows you nothing about the machines doing your work at home.

**Why the obvious workaround fails.** SSH from a phone is technically
possible and practically miserable, and it still gives you a terminal
squint, not a fleet view.

**The CCC solution.** The dashboard is fully responsive: from a phone on
your network you can monitor the board, read transcripts, and steer
sessions, couch or kitchen included.

**Visible proof.** M-01 (session list on phone viewport), M-02 (open
conversation on phone viewport), V-15 (phone-width walkthrough).

**LinkedIn version.**

Odd gap in my setup until recently: my phone could reach my bank, my
infrastructure, and every service I pay for, but not the four coding agents
running on my own desk.

Step out for lunch and the fleet keeps going, which is the point. But one
session hits a question ten minutes in and idles for the rest of the hour,
and nothing in my pocket can tell me. The work I most wanted visibility into
was the least visible thing I owned.

SSH from a phone technically exists. It is a terminal squint through a
four-inch window, and it shows one session at a time when what I need is the
state of all of them.

Since the dashboard is a local web app, the fix was honest responsive
design: the full board, transcripts, and session input working at phone
width, served on my own network. Lunch now includes glancing at the fleet,
answering the one blocked session, and putting the phone away.

No cloud in the loop. The phone talks to my machine directly.

#DevTools

**X version (single post).**

Your phone can reach your bank, your servers, your doorbell. But not the 4
coding agents on your own desk.

Full board, transcripts, and steering at phone width, on your own network.
No cloud in the loop. [V-15 clip]

**Reddit-native angle.** r/selfhosted. Title: "Monitoring local coding-agent
sessions from my phone without any cloud relay: anyone else doing this?"
Body approach: frame it as a self-hosted, LAN-only monitoring story
(local server, phone on the same network, no external service), and ask how
others expose local dev dashboards to their phones safely, inviting setup
comparisons.

**Video outline (30-60s).**
1. Painful start: a desk with sessions running; the person leaves; a phone
   lock screen shows nothing.
2. Smallest flow: open the board on the phone, scroll the session list, tap
   the one flagged waiting.
3. Resolved state: read the question in the transcript, answer it from the
   phone, session resumes; phone goes back in the pocket.

**CTA.** Cold audience (mobile is a strong first hook and the demo works on
a phone): "Tour the live demo. All data fake, nothing to install."
ccc.amirfish.ai/demo

**Claims requiring qualification.** Keep "on your network" attached to every
remote-access phrasing; CCC binds to 127.0.0.1 by default and wider exposure
is explicit opt-in. Do not imply internet-wide access or a hosted relay. Do
not extend this unit into cross-machine handoff claims without pulling row
30 explicitly (Built, but its capture is pending re-release per the ledger).

---

## Appendix: selection notes for the lead

**Family weighting delivered:** F1 x3 (SU-01, SU-02, SU-03), F2 x2 (SU-04,
SU-05), F3 x1 (SU-06), F4 x2 (SU-07, SU-08), F5 x3 (SU-09, SU-10, SU-11),
F6 x1 (SU-12). Ten of twelve units sit in the requested F1/F2/F4/F5 weight.

**Considered and dropped:**
- Row 4 (one board, five engines): strong differentiator but Partial with a
  mandatory qualification; risky as a standalone pain-first unit in a first
  draft. Folded as a qualified footnote into SU-01 instead.
- Row 14 (Flow canvas): visually the best asset, but the pain ("I want a
  whiteboard") is a preference, not a wound; kanban (row 12) carries the F3
  family with a sharper moment.
- Row 15 (split pane): real but narrow (reviewers); lost the F3 slot to
  row 12.
- Row 25 (annotate to ticket): distinctive, but F5 already holds three
  units and the queue story (SU-09) subsumes its payoff.
- Row 28 (auto-fix Vercel deploys): Partial, needs qualification, and
  audience-narrow; weakest of the F5 candidates.
- Row 30 (cross-machine handoff): Built but new; ledger flags recapture on
  release. Mobile (row 29) is the broader F6 story today.
- Rows 8, 9, 10 (usage windows, model advisor, throughput): good depth
  content, but each is a dashboard-widget story rather than a sharp painful
  moment; better as follow-on posts than as first-wave units.
