import tempfile
import unittest
from pathlib import Path
from unittest import mock

from video_drop import link_supervisor
from video_drop.phone_link import recover, wait_ready


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class PhoneLinkRecoveryTests(unittest.TestCase):
    def run_recover(self, answers, *, releases=None, supervised=False, state=None, started=None):
        clock = Clock()
        answers = iter(answers)
        released = releases if releases is not None else []
        started = started if started is not None else []
        ok = recover(status=lambda: next(answers, False), release=lambda: released.append(clock.now),
                     sleep=clock.sleep, clock=clock.time, wait=10, supervised=supervised,
                     supervisor_state=state, start=lambda s: started.append(s))
        return ok, released, started

    def test_wait_ready_follows_the_supervisor_verdict(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            verdicts = iter(["wedged", "wedged", "ready"])
            with mock.patch.object(link_supervisor, "read_status",
                                   lambda s=None: {"state": next(verdicts, "ready"), "alive": True}):
                result = wait_ready(30, status=lambda: True, sleep=clock.sleep, clock=clock.time, state=state)
            self.assertEqual(result["state"], "ready")
            self.assertEqual(clock.now, 6.0)
            with mock.patch.object(link_supervisor, "read_status", lambda s=None: {"state": "needs-replug", "alive": True}):
                result = wait_ready(10, status=lambda: True, sleep=clock.sleep, clock=clock.time, state=state)
            self.assertEqual(result["state"], "needs-replug")

    def test_supervisor_is_asked_instead_of_restarting_from_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, released, started = self.run_recover([False] * 6 + [True], supervised=True, state=Path(tmp))
            self.assertTrue(ok)
            self.assertEqual((started, len(released)), ([], 1))
            self.assertTrue((Path(tmp) / "link-request").is_file())

    def test_answering_wda_is_left_alone(self):
        ok, released, started = self.run_recover([True])
        self.assertTrue(ok)
        self.assertEqual((started, released), ([], []))

    def test_frozen_app_is_released_without_restarting_wda(self):
        ok, released, started = self.run_recover([False, False, True])
        self.assertTrue(ok)
        self.assertEqual(len(released), 1)
        self.assertEqual(started, [])

    def test_unsupervised_link_starts_the_supervisor_once_and_only_after_waiting(self):
        # Nothing supervising the link is no license to restart WDA from the
        # script: the last resort is to start the one owner and ask it.
        with tempfile.TemporaryDirectory() as tmp:
            ok, released, started = self.run_recover([False] * 6 + [True], state=Path(tmp))
            self.assertTrue(ok)
            self.assertEqual((started, len(released)), ([Path(tmp)], 1))
            self.assertTrue((Path(tmp) / "link-request").is_file())

    def test_a_link_that_never_answers_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, _released, started = self.run_recover([False] * 50, state=Path(tmp))
            self.assertFalse(ok)
            self.assertEqual(started, [Path(tmp)])

    def test_scripts_may_still_pass_the_old_admin_argument(self):
        class Admin:
            calls = 0

            def up(self):
                Admin.calls += 1
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            ok = recover(Admin(), status=lambda: False, sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__,
                         wait=10, supervisor_state=Path(tmp), supervised=True)
        self.assertFalse(ok)
        self.assertEqual(Admin.calls, 0, "SideTap's up() must never run from a script")


if __name__ == "__main__":
    unittest.main()
