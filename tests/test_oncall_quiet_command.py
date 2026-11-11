"""`ccc oncall quiet`: set, clear, zone handling and the status line."""
import calendar

from ccc_server.oncall import alerts, cli
from tests.test_oncall_cli import Box, box  # noqa: F401



def test_set_off_and_status_line(box, capsys):
    box.flag = True
    assert box.run("quiet", "23:00", "07:00") == 0
    assert box.eng.config()["quiet_hours"] == {"start": "23:00", "end": "07:00", "tz": ""}
    assert box.run("quiet", "22:30", "06:15", "--tz", "America/Los_Angeles") == 0
    assert box.eng.config()["quiet_hours"]["tz"] == "America/Los_Angeles"
    lines = []
    cli._print_status({"config": box.eng.config()}, cli.IO(out=lines.append))
    assert "Quiet hours: 22:30 to 06:15, America/Los_Angeles." in lines
    assert box.run("quiet", "off") == 0
    assert box.eng.config()["quiet_hours"] is None
    assert box.text.count("Quiet hours are off.") == 1


def test_bad_time_and_unknown_zone_are_one_line(box, capsys):
    box.flag = True
    for argv in (("quiet", "25:00", "07:00"), ("quiet", "23:00"), ("quiet", "23:00", "07:00", "--tz", "Mars/Base")):
        capsys.readouterr()
        assert box.run(*argv) == 1
        assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert not box.eng.config()["quiet_hours"]


def _utc(*parts):
    return calendar.timegm(parts + (0,))


def test_zone_follows_daylight_saving():
    cfg = {"quiet_hours": {"start": "23:00", "end": "07:00", "tz": "America/Los_Angeles"}}
    # 2026-03-08 is the spring-forward day. 06:30 UTC on 03-07 is 22:30 PST (not quiet);
    # 07:30 UTC is 23:30 PST (quiet). After the change, the same UTC clock reads one hour later.
    assert not alerts.in_quiet_hours(cfg, _utc(2026, 3, 7, 6, 30))
    assert alerts.in_quiet_hours(cfg, _utc(2026, 3, 7, 7, 30))
    assert not alerts.in_quiet_hours(cfg, _utc(2026, 3, 10, 5, 30))   # 22:30 PDT
    assert alerts.in_quiet_hours(cfg, _utc(2026, 3, 10, 6, 30))       # 23:30 PDT
    assert alerts.in_quiet_hours(cfg, _utc(2026, 3, 10, 13, 30))      # 06:30 PDT
    assert not alerts.in_quiet_hours(cfg, _utc(2026, 3, 10, 14, 30))  # 07:30 PDT
