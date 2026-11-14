"""Pacific conversions use IANA daylight-saving rules, not fixed offsets."""
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / "pt"
convert = runpy.run_path(str(HELPER))["pacific_time"]


@pytest.mark.parametrize("stamp, expected", [
    ("2026-11-14T16:26:00Z", "2026-11-14 8:26:00 AM PST (UTC-08:00)"),
    ("2026-11-14 16:26 UTC", "2026-11-14 8:26:00 AM PST (UTC-08:00)"),
    ("2026-03-08T09:59:59Z", "2026-03-08 1:59:59 AM PST (UTC-08:00)"),
    ("2026-03-08T10:00:00Z", "2026-03-08 3:00:00 AM PDT (UTC-07:00)"),
    ("2026-11-01T08:59:59Z", "2026-11-01 1:59:59 AM PDT (UTC-07:00)"),
    ("2026-11-01T09:00:00Z", "2026-11-01 1:00:00 AM PST (UTC-08:00)"),
    ("2026-11-14T01:00:00", "2026-11-13 5:00:00 PM PST (UTC-08:00)"),
    ("2026-11-14T18:26:00+02:00", "2026-11-14 8:26:00 AM PST (UTC-08:00)"),
])
def test_dst_and_date_rollover(stamp, expected):
    assert convert(stamp) == expected


def test_cli_accepts_split_utc_input_and_rejects_invalid_input():
    result = subprocess.run([sys.executable, str(HELPER), "2026-11-14", "16:26", "UTC"],
                            text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "2026-11-14 8:26:00 AM PST (UTC-08:00)"
    bad = subprocess.run([sys.executable, str(HELPER), "not-a-time"], text=True, capture_output=True)
    assert bad.returncode == 2 and not bad.stdout
    assert "error:" in bad.stderr
