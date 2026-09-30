"""The experiment runner's pure parts: preconditions, USB tagging and the verdict text."""

from __future__ import annotations

import unittest

from scripts import link_experiment as exp


class PreconditionTests(unittest.TestCase):
    def test_every_experiment_names_a_known_workload(self):
        for name, spec in exp.EXPERIMENTS.items():
            for kind in spec["workload"].split(","):
                self.assertIn(kind, exp.WORKLOADS, f"{name}: {kind}")

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
