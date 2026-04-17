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
