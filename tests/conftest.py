"""Pin this PC's time zone to New York so slot tests read the same on every machine.

Tests that need another zone patch video_drop.timezones.pc_zone_name themselves.
"""

import pytest

from video_drop import timezones


@pytest.fixture(autouse=True)
def pc_in_new_york(monkeypatch):
    monkeypatch.setattr(timezones, "pc_zone_name", lambda: "America/New_York")


@pytest.fixture(autouse=True)
def phone_link_ready(monkeypatch):
    """No test waits on (or probes) the real phone link: the supervisor reports ready.

    Tests of the waiting itself put server.wait_for_link back (see test_release_run_routes).
    """
    from video_drop import server

    monkeypatch.setattr(server, "wait_for_link",
                        lambda on_wait=None, timeout=0.0: {"state": "ready", "message": "Phone link ready"})
