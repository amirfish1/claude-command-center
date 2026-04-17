"""Morning view module for Claude Command Center.

Phase 1: sample data only. No filesystem reads, no MCP calls.
The public API here is what server.py wires to HTTP routes; later phases
will swap the constant-backed implementations for real ingestion without
changing these signatures.
"""

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Sample data (Phase 1 only — replaced by real ingestion in Phase 2+)
# ---------------------------------------------------------------------------

_SAMPLE_GOALS = [
    {
        "slug": "bym-growth",
        "name": "BYM growth",
        "life_area": "The Initiatives",
        "accent": "#27ae60",
        "ribbon": {
            "date": "Apr 17",
            "text": "5 commits · 3 issues closed · demo mode shipped",
            "source": "auto",
        },
    },
    {
        "slug": "nvidia-course",
        "name": "Nvidia course",
        "life_area": "The Initiatives",
        "accent": "#f39c12",
        "ribbon": {
            "date": "Apr 17",
            "text": "3 commits · spec draft landed · Eran aligned",
            "source": "auto",
        },
    },
    {
        "slug": "ai-forms",
        "name": "AI forms",
        "life_area": "The Initiatives",
        "accent": "#3498db",
        "ribbon": {
            "date": "Apr 17",
            "text": "no activity 4 days · \"$5 MCP\" still parked",
            "source": "auto",
        },
    },
    {
        "slug": "taxes",
        "name": "Taxes",
        "life_area": "HOME/FAMILY",
        "accent": "#9b59b6",
        "ribbon": {
            "date": "Apr 17",
            "text": "URGENT — deadline Apr 15 passed",
            "source": "manual",
        },
    },
]

_SAMPLE_STRATEGIC = [
    {"priority": "P0", "goal_slug": "nvidia-course",
     "text": "Come up with structure: raw material + workshop",
     "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Push/promote BYM (advance growth)",
     "source": "Notion", "age_days": 3},
    {"priority": "P0", "goal_slug": "taxes",
     "text": "Taxes",
     "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "ai-forms",
     "text": "Push AI forms (decide: launch / marketing / sales)",
     "source": "Notion", "age_days": 3},
]

_SAMPLE_TACTICAL = [
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "Re-run migration for Joyce after fixes",
     "source": "TODO.md", "age_days": 2},
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "#114 — same-day swap instructors fails",
     "source": "GH", "age_days": 0},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "ICS email invitations instead of GCal invites",
     "source": "TODO.md", "age_days": 5},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Verify new Calendly token holds all 7 scopes",
     "source": "TODO.md", "age_days": 4},
    {"priority": "P2", "goal_slug": "bym-growth",
     "text": "Test passkey auth end-to-end on production",
     "source": "PARKING", "age_days": 13},
]

_SAMPLE_INBOX = [
    {"source": "Apple Notes", "age_days": 2,
     "text": "Try using a local LLM for the Wispr transcription cleanup instead of sending to cloud",
     "suggested_goal": None},
    {"source": "Google Doc", "age_days": 1,
     "text": "Should explore putting the command center behind proper auth so I can share it with Eran",
     "suggested_goal": None},
    {"source": "Wispr", "age_days": 0,
     "text": "Idea: morning dashboard that aggregates all my todos so I don't have to remember where things are",
     "suggested_goal": None},
    {"source": "Apple Notes", "age_days": 4,
     "text": "Find a decent mattress finally",
     "suggested_goal": None},
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_morning_state():
    """Return the full state needed to render /morning.

    Phase 1: sample data. Phase 2 will replace with real aggregation from
    ingestion workers without changing this shape.
    """
    return {
        "goals": list(_SAMPLE_GOALS),
        "strategic": list(_SAMPLE_STRATEGIC),
        "tactical": list(_SAMPLE_TACTICAL),
        "inbox": list(_SAMPLE_INBOX),
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Sample goal-detail data (Phase 1 only)
# ---------------------------------------------------------------------------

_SAMPLE_GOAL_DETAILS = {
    "bym-growth": {
        "slug": "bym-growth",
        "name": "BYM growth",
        "life_area": "The Initiatives",
        "accent": "#27ae60",
        "intent_markdown": (
            "1 paying studio (Joyce, LCPP) → 10 by end of Q2. "
            "Growth is the gating constraint on proving BYM is a real business "
            "vs. a one-customer project.\n\n"
            "**Success:** 10 active paying studios · $5k MRR · 3 referrals from existing customers."
        ),
        "strategies": [
            {"id": "demo-mode", "text": "Ship demo mode (anonymized data for prospect tours)",
             "status": "done", "session_state": "dormant",
             "session_summary": "session 01HK...7fA2 · last active Apr 17 · 2h, 12 commits, 14 files touched"},
            {"id": "affiliates", "text": "Find 3 pilates-studio affiliates",
             "status": "active", "session_state": "alive",
             "session_summary": "session 01HK...B9C1 · alive in iTerm tab \"affiliates\" · last input 14m ago"},
            {"id": "fb-groups", "text": "Post in 5 Facebook pilates-instructor groups (one per week)",
             "status": "active", "session_state": "dormant",
             "session_summary": "session 01HK...D4E7 · dormant since Apr 10 · 0/5 posts"},
            {"id": "video-ad", "text": "Create 60s demo video walking through booking flow",
             "status": "active", "session_state": "never",
             "session_summary": "no session yet · click Start to spawn"},
            {"id": "linkedin", "text": "LinkedIn post series: \"I built a pilates booking system\" (3 posts)",
             "status": "active", "session_state": "alive",
             "session_summary": "session 01HK...F1B3 · headless (pid 48721) · 1/3 drafted"},
            {"id": "youtube-ad", "text": "YouTube ad buy ($500)",
             "status": "dropped", "session_state": "dropped",
             "session_summary": "claude: dropped Apr 13, too early"},
        ],
        "tactical_tagged": [
            {"text": "Push/promote BYM", "source": "Notion P1", "strategy_id": "affiliates"},
            {"text": "ICS email invitations", "source": "TODO.md", "strategy_id": None},
            {"text": "#114 instructor swap bug", "source": "GH", "strategy_id": None},
        ],
        "deliverables": [
            {"type": "COMMIT", "label": "demo mode shipped · a3f8c21", "source": "demo-mode session"},
            {"type": "FILE", "label": "apps/bookyourmat/.../DemoModeProvider.tsx", "source": "Write · demo-mode"},
            {"type": "DRAFT", "label": "~/Drive/BYM/linkedin/post-1.md", "source": "Write · linkedin"},
            {"type": "LIST", "label": "~/Drive/BYM/fb-groups.md (12 groups)", "source": "Write · fb-groups"},
        ],
        "context_library": [],  # populated in Phase 4
        "recent_sessions": [
            {"summary": "Ship demo mode", "when": "Apr 17 · 2h · 12 commits"},
            {"summary": "Fix Joyce swap-instructor bug", "when": "Apr 17 · 45m"},
            {"summary": "Reach out to pilates studios (affiliates)", "when": "Apr 17 · still alive"},
            {"summary": "LinkedIn post #1 draft", "when": "Apr 14 · 30m"},
        ],
    },
}


def get_goal_detail(slug):
    """Return the full detail for a single goal, or None if slug is unknown.

    Phase 1: sample data only.
    """
    data = _SAMPLE_GOAL_DETAILS.get(slug)
    if data is None:
        return None
    # Shallow copy so callers can't mutate our constant.
    return dict(data)
