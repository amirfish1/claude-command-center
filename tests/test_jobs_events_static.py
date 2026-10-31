"""Job activity remains accessible in the redesigned job details."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "jobs-tab.js").read_text()


class JobsActivityTest(unittest.TestCase):
    def test_details_preserve_activity_and_links(self):
        self.assertIn('Recent activity', JS)
        self.assertIn('j.events.slice(-8).reverse()', JS)
        self.assertIn('esc(nodash(event.text))', JS)
        self.assertIn('esc(fmtLocal(event.at))', JS)
        self.assertIn('chipsHtml(j.tickets, 50)', JS)

    def test_rows_use_accessible_disclosure(self):
        self.assertIn('data-job-toggle aria-expanded=', JS)
        self.assertIn('class="job-status"', JS)
        self.assertIn('data-jobs-search aria-label="Search jobs"', JS)


if __name__ == "__main__":
    unittest.main()
