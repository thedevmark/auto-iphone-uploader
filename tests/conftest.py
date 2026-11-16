"""Pin this PC's time zone to New York so slot tests read the same on every machine.

Tests that need another zone patch video_drop.timezones.pc_zone_name themselves.
"""

import os
import tempfile
from pathlib import Path

# Before any app module is imported: the suite never reads the owner's real .env (it holds the
# phone passcode). Tests that need .env values set them in the process environment.
os.environ["VIDEO_DROP_ENV_FILE"] = str(Path(tempfile.gettempdir()) / "video-drop-tests-no.env")

import pytest  # noqa: E402

from video_drop import timezones  # noqa: E402


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


@pytest.fixture(autouse=True)
def timings_off_the_real_state(monkeypatch, tmp_path):
    """Flow tests mark steps; those marks belong in a temp file, never in .state/run-timings.jsonl."""
    from scripts import phone_youtube

    monkeypatch.setattr(phone_youtube, "TIMINGS", tmp_path / "run-timings.jsonl")


@pytest.fixture(autouse=True)
def no_usb_copy(monkeypatch):
    """No test asks the real phone whether its driver shares files: the cloud route is used.

    tests/test_usb_copy.py drives the USB copy itself with a fake go-ios.
    """
    from video_drop import usb_copy

    monkeypatch.setattr(usb_copy, "driver", lambda: None)


@pytest.fixture(autouse=True)
def no_pause_between_space_reads(monkeypatch):
    """A test that makes the free-space read fail does not sit through the real retries' pauses."""
    from video_drop import server

    monkeypatch.setattr(server, "SPACE_READ_PAUSE", 0.0)
