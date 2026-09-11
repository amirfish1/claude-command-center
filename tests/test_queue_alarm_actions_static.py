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

    def test_staffing_alarm_banner_is_gone(self):
        # The owner never wants the red "Queue X has no effective worker /
        # is stuck — no progress in Nm" banner again; only an invalid worker
        # config may still raise an in-panel notice.
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("' has no effective worker'", app_js)
        self.assertNotIn("' is stuck'", app_js)
        self.assertNotIn("the reconciler has not staffed this queue", app_js)
        self.assertNotIn("staffingAlarm", app_js)
