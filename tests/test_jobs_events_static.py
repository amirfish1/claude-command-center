"""Jobs tab day view renders CCC_EVENT rows (static wiring checks)."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "jobs-tab.js").read_text()
CSS = (ROOT / "static" / "app.css").read_text()


class JobsDayEventsTest(unittest.TestCase):
    def test_day_view_merges_events_into_slots(self):
        self.assertIn("(j.events || []).forEach", JS)
        self.assertIn("slots.push({ j: j, min: minuteOf(d), sec: d.getSeconds(), event: ev })", JS)
        self.assertIn("sl.event", JS)

    def test_event_rows_have_key_label_and_state(self):
        self.assertIn("key: sl.j.id + '#ev'", JS)
        self.assertIn("state: 'event'", JS)
        self.assertIn("opt.event", JS)
        self.assertIn('class="job-event"', JS)

    def test_event_row_styles_exist(self):
        self.assertIn(".job-row.slot-event .job-dot", CSS)
        self.assertIn(".job-event {", CSS)


if __name__ == "__main__":
    unittest.main()
