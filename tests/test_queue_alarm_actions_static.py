"""Regression coverage for the queue staffing-alarm actions."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestQueueAlarmActionsStatic(unittest.TestCase):
    def test_alarm_buttons_name_the_actions_they_execute(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        alarm_start = app_js.index('data-alarm-retry=')
        alarm_end = app_js.index("const watchHtml", alarm_start)
        alarm = app_js[alarm_start:alarm_end]

        self.assertIn(">Spawn worker now</button>", alarm)
        self.assertIn(">Open queue activity log</button>", alarm)
        self.assertNotIn(">Retry reconcile</button>", alarm)
        self.assertNotIn(">Inspect worker</button>", alarm)
