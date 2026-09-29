import unittest

from video_drop.phone_link import recover


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class Admin:
    def __init__(self, result=0):
        self.calls, self.result = 0, result

    def up(self):
        self.calls += 1
        return self.result


class PhoneLinkRecoveryTests(unittest.TestCase):
    def run_recover(self, answers, admin, releases=None):
        clock = Clock()
        answers = iter(answers)
        released = releases if releases is not None else []
        ok = recover(admin, status=lambda: next(answers, False), release=lambda: released.append(clock.now),
                     sleep=clock.sleep, clock=clock.time, wait=10)
        return ok, released

    def test_answering_wda_is_left_alone(self):
        admin = Admin()
        ok, released = self.run_recover([True], admin)
        self.assertTrue(ok)
        self.assertEqual((admin.calls, released), (0, []))

    def test_frozen_app_is_released_without_restarting_wda(self):
        admin = Admin()
        ok, released = self.run_recover([False, False, True], admin)
        self.assertTrue(ok)
        self.assertEqual(len(released), 1)
        self.assertEqual(admin.calls, 0)

    def test_restart_happens_once_and_only_after_waiting(self):
        admin = Admin()
        ok, released = self.run_recover([False] * 50, admin)
        self.assertTrue(ok)
        self.assertEqual((admin.calls, len(released)), (1, 1))

    def test_failed_restart_reports_failure(self):
        ok, _ = self.run_recover([False] * 50, Admin(result=1))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
