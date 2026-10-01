"""The experiment runner's pure parts: preconditions, USB tagging and the verdict text."""

from __future__ import annotations

import unittest

from scripts import link_experiment as exp


class PreconditionTests(unittest.TestCase):
    def test_every_experiment_names_a_known_workload(self):
        for name, spec in exp.EXPERIMENTS.items():
            for kind in spec["workload"].split(","):
                self.assertIn(kind, exp.WORKLOADS, f"{name}: {kind}")

    def test_isolation_arms_differ_only_in_the_reader(self):
        # pixels never touches WDA; ax and wda-nosnap put the same video on screen with WDA readers.
        self.assertEqual(exp.EXPERIMENTS["pixels"]["workload"], "tiktok-pixels,youtube-feed-pixels")
        self.assertTrue(exp.EXPERIMENTS["ax"]["workload"].startswith("tiktok,"))
        self.assertEqual(exp.EXPERIMENTS["wda-nosnap"]["workload"], "tiktok-status,tiktok-shot")
        self.assertEqual(exp.EXPERIMENTS["wda-strip"]["env"]["MJPEG_SETTINGS"], "0")
        for name in ("pixels", "ax", "wda-nosnap", "wda-strip"):
            self.assertEqual(exp.EXPERIMENTS[name]["tunnel_mode"], "userspace", name)
            self.assertEqual(exp.supervisor_matches(
                {"alive": True, "tunnelMode": "userspace", "mjpegForward": True}, exp.EXPERIMENTS[name]), [])
        for app in ("tiktok", "youtube", "photos"):
            self.assertIn(app, exp.VIDEO_APPS)

    def test_supervisor_mode_mismatch_is_named(self):
        status = {"alive": True, "state": "ready", "tunnelMode": "userspace", "mjpegForward": True}
        self.assertEqual(exp.supervisor_matches(status, exp.EXPERIMENTS["baseline"]), [])
        problems = exp.supervisor_matches(status, exp.EXPERIMENTS["kernel"])
        self.assertEqual(len(problems), 1)
        self.assertIn("'kernel'", problems[0])
        problems = exp.supervisor_matches(status, exp.EXPERIMENTS["no-mjpeg"])
        self.assertIn("MJPEG", problems[0])
        self.assertEqual(exp.supervisor_matches(status, exp.EXPERIMENTS["idle"]), [])

    def test_dead_supervisor_is_the_only_problem_reported(self):
        self.assertEqual(exp.supervisor_matches({"alive": False}, exp.EXPERIMENTS["kernel"]),
                         ["the link supervisor is not running"])

    def test_supervisor_command_carries_the_mode_flags(self):
        self.assertIn("--tunnel-mode kernel", exp.supervisor_command(exp.EXPERIMENTS["kernel"]))
        self.assertIn("--no-mjpeg-forward", exp.supervisor_command(exp.EXPERIMENTS["no-mjpeg"]))
        self.assertNotIn("--no-mjpeg-forward", exp.supervisor_command(exp.EXPERIMENTS["baseline"]))


class BatteryTests(unittest.TestCase):
    def test_envelope_summarises_charging_fields(self):
        rows = [{"t": "1", "battery": {"CurrentCapacity": 20, "InstantAmperage": -300, "IsCharging": True,
                                       "Temperature": 3100}},
                {"t": "2"},
                {"t": "3", "battery": {"CurrentCapacity": 21, "InstantAmperage": 900, "IsCharging": True,
                                       "Temperature": 3400}},
                {"t": "4", "battery": {"error": "no go-ios"}}]
        env = exp.battery_envelope(rows)
        self.assertEqual(env["reads"], 2)
        self.assertEqual(env["InstantAmperage"], {"min": -300, "max": 900})
        self.assertEqual(env["Temperature"], {"min": 3100, "max": 3400})
        self.assertEqual(env["IsCharging"], {"always": True, "ever": True})
        self.assertEqual(exp.battery_envelope([{"t": "1"}]), {"reads": 0})


class VerdictTests(unittest.TestCase):
    STALL = {"at": "13:12:07.00", "kind": "pipe-stall", "order": ["lockdown", "wda", "tunnel"],
             "first_failure_offsets": {"lockdown": 0.0, "wda": 1.0, "tunnel": 4.0},
             "recovered_after": None, "device_listed_throughout": True}

    def test_usb_event_within_slack_retags_an_incident(self):
        tagged = exp.tag_usb([self.STALL], ["13:12:09.500"])
        self.assertEqual(tagged[0]["kind"], "usb-drop")
        self.assertEqual(tagged[0]["usb_event"], "13:12:09.500")
        untouched = exp.tag_usb([self.STALL], ["13:20:00.000"])
        self.assertEqual(untouched[0]["kind"], "pipe-stall")
        self.assertIsNone(untouched[0]["usb_event"])

    def test_verdict_names_the_order_and_the_ladder_layer(self):
        text = exp.verdict("baseline", [self.STALL], 12.0, [{"step": "amds-restart", "lockdown_back_after": None},
                                                            {"step": "pnputil-restart", "lockdown_back_after": 6.0}],
                           "safe,tiktok")
        self.assertIn("1 pipe stall(s)", text)
        self.assertIn("lockdown+0s -> wda+1s -> tunnel+4s", text)
        self.assertIn("pnputil-restart brought lockdown back after 6.0s", text)
        self.assertIn("Windows USB stack", text)

    def test_power_warning_rides_with_the_verdict(self):
        text = exp.verdict("pixels", [], 8.0, [], "tiktok-pixels",
                           power="The USB port cannot power the phone under load: x")
        self.assertIn("power: The USB port cannot power", text)

    def test_clean_run_says_so_without_overclaiming(self):
        text = exp.verdict("kernel", [], 30.0, [], "safe")
        self.assertIn("0 pipe stall(s)", text)
        self.assertIn("no incident", text)
        self.assertIn("before calling it fixed", text)

    def test_ladder_that_never_recovers_is_reported(self):
        text = exp.verdict("idle", [self.STALL], 5.0, [{"step": "amds-restart", "lockdown_back_after": None},
                                                       {"step": "pnputil-restart", "lockdown_back_after": None},
                                                       {"step": "replug", "lockdown_back_after": None}], "idle")
        self.assertIn("no step brought lockdown back", text)


if __name__ == "__main__":
    unittest.main()


class ReplugTests(unittest.TestCase):
    def test_note_names_the_incident_and_the_action(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            inc = {"kind": "pipe-stall", "at": "17:30:36.67", "order": ["lockdown", "wda", "tunnel"]}
            path = exp.note_replug_needed(Path(td), inc, now=datetime(2026, 9, 30, 17, 32, 15))
            text = path.read_text(encoding="utf-8")
            self.assertEqual(path.name, "NEEDS_REPLUG.txt")
            self.assertIn("2026-09-30T17:32:15 pipe-stall at 17:30:36.67 (lockdown -> wda -> tunnel)", text)
            self.assertIn("plug it back in", text)

    def test_wait_for_phone_polls_until_listed_or_gives_up(self):
        answers = iter([False, False, True])
        naps = []
        self.assertTrue(exp.wait_for_phone(None, minutes=10, poll=30, listed=lambda: next(answers),
                                           sleep=naps.append))
        self.assertEqual(naps, [30, 30])
        clock = iter([0.0, 0.0, 700.0, 700.0])
        original = exp.time.monotonic
        exp.time.monotonic = lambda: next(clock)
        try:
            self.assertFalse(exp.wait_for_phone(None, minutes=10, poll=30, listed=lambda: False, sleep=lambda s: None))
        finally:
            exp.time.monotonic = original
