"""The pipe-stall recovery ladder: detection, the Decider's steps with injected probes and a
fake clock, the raw probe classification, and the runner's helper plumbing."""

from __future__ import annotations

import json
import math
import plistlib
import socket
import struct
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from video_drop import link_probe, link_supervisor, pipe_stall
from video_drop.link_supervisor import (
    NEEDS_REPLUG, PIPE_STALL, PIPE_STALL_SERVICE, PIPE_STALL_USB, READY, RECOVERING_RUNNER, UNPLUGGED,
    WAITING_TUNNEL, WEDGED, Decider, Observation, Policy,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


HEALTHY = Observation(device_present=True, tunnel_alive=True, tunnel_entry=True, route_ok=True,
                      runwda_alive=True, forwards_alive=True, wda="up")
# What the runner observes during a stall: the forward accepts and never answers, the runner is
# soon dead, the tunnel entry may or may not be listed, and the raw probes say "stalled".
STALLED = Observation(device_present=True, tunnel_alive=True, tunnel_entry=True, runwda_alive=True,
                      forwards_alive=True, wda="wedged", pipe="stalled", helper_available=True)
STALLED_NO_HELPER = Observation(**{**STALLED.__dict__, "helper_available": False})
# Lockdown answers again but nothing above it does yet.
PIPE_BACK = Observation(device_present=True, tunnel_alive=True, tunnel_entry=False, runwda_alive=False,
                        forwards_alive=True, wda="down", pipe="ok", helper_available=True)


def bookkeeping(decider):
    """What the runner reports back after each action (the ladder tests report helper results themselves)."""
    table = {"start_tunnel": decider.tunnel_started, "restart_tunnel": decider.tunnel_started,
             "start_runwda": decider.runner_started, "restart_runwda": decider.runner_started,
             "release_springboard": decider.released,
             "restart_apple_service": lambda: decider.ladder_step_result(None, ""),
             "restart_usb_device": lambda: decider.ladder_step_result(None, "")}
    return lambda action: table.get(action, lambda: None)()


def steps(decider, clock, obs, ticks):
    decisions = []
    apply = bookkeeping(decider)
    for _ in range(ticks):
        decision = decider.step(obs)
        for action in decision.actions:
            apply(action)
        decisions.append(decision)
        clock.advance(decider.policy.tick)
    return decisions


def actions(decisions):
    return [a for d in decisions for a in d.actions]


class LadderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.policy = Policy()
        self.decider = Decider(self.policy, clock=self.clock)

    def within(self, seconds):
        """Ticks that fall strictly inside a window that opened on the previous tick."""
        return math.ceil(seconds / self.policy.tick) - 1

    def test_stall_needs_consecutive_probes_and_never_presses_home_into_it(self):
        two = steps(self.decider, self.clock, STALLED, self.policy.stall_probes - 1)
        self.assertEqual({d.state for d in two}, {WEDGED})
        self.assertEqual(actions(two), [])  # no Home press: `ios launch` would only hang on a dead pipe
        self.assertIn("pipe stall", two[-1].detail)
        third = steps(self.decider, self.clock, STALLED, 1)[0]
        self.assertEqual(third.state, PIPE_STALL)
        self.assertIn("USB data link stopped answering", third.message)

    def test_one_stalled_probe_between_good_ones_starts_nothing(self):
        for _ in range(3):
            steps(self.decider, self.clock, STALLED, 1)
            steps(self.decider, self.clock, HEALTHY, 1)
        self.assertIsNone(self.decider.ladder)
        self.assertEqual(self.decider.stall_ticks, 0)

    def test_wda_only_wedge_with_a_live_pipe_is_still_a_home_press(self):
        wedged = Observation(**{**STALLED.__dict__, "pipe": "ok", "route_ok": None})
        decisions = steps(self.decider, self.clock, wedged, 3)
        self.assertEqual(actions(decisions), ["release_springboard"])
        self.assertIsNone(self.decider.ladder)

    def test_unplugged_phone_is_not_a_stall(self):
        gone = Observation(device_present=False, tunnel_alive=True)
        decisions = steps(self.decider, self.clock, gone, 6)
        self.assertEqual({d.state for d in decisions}, {UNPLUGGED})
        self.assertIsNone(self.decider.ladder)

    def test_ladder_walks_wait_service_usb_then_replug_with_timings(self):
        policy = self.policy
        # (a) the stall gets a short bounded wait
        steps(self.decider, self.clock, STALLED, policy.stall_probes)
        waiting = steps(self.decider, self.clock, STALLED, self.within(policy.stall_wait))
        self.assertEqual({d.state for d in waiting}, {PIPE_STALL})
        self.assertEqual(actions(waiting), [])
        # (b) Apple Mobile Device Service, then lockdown is given stall_verify
        service = steps(self.decider, self.clock, STALLED, 1)[0]
        self.assertEqual(service.state, PIPE_STALL_SERVICE)
        self.assertEqual(service.actions, ["restart_apple_service"])
        self.assertIn("Apple Mobile Device Service", service.message)
        self.decider.ladder_step_result(True, "started: Running")
        verifying = steps(self.decider, self.clock, STALLED, self.within(policy.stall_verify))
        self.assertEqual({d.state for d in verifying}, {PIPE_STALL_SERVICE})
        self.assertEqual(actions(verifying), [])
        # (c) the USB device node
        usb = steps(self.decider, self.clock, STALLED, 1)[0]
        self.assertEqual(usb.state, PIPE_STALL_USB)
        self.assertEqual(usb.actions, ["restart_usb_device"])
        self.assertIn("USB port", usb.message)
        self.decider.ladder_step_result(True, "restarted USB node")
        # The reset re-enumerates the phone: a brief absence is the step working, not a replug.
        steps(self.decider, self.clock, Observation(device_present=False, tunnel_alive=True), 1)
        self.assertIsNotNone(self.decider.ladder)
        verifying = steps(self.decider, self.clock, STALLED, self.within(policy.stall_usb_verify) - 1)
        self.assertEqual(actions(verifying), [])
        self.assertEqual({d.state for d in verifying}, {PIPE_STALL_USB})
        # (d) only now the replug message, with every step's seconds in the record
        failed = steps(self.decider, self.clock, STALLED, 1)[-1]
        self.assertEqual(failed.state, NEEDS_REPLUG)
        self.assertIn("replug", failed.message.lower())
        self.assertNotIn("helper is not installed", failed.message)
        report = self.decider.stall_reports[-1]
        self.assertFalse(report["recovered"])
        self.assertEqual([s["step"] for s in report["steps"]], ["wait", "service", "usb"])
        self.assertGreaterEqual(report["steps"][0]["seconds"], policy.stall_wait)
        self.assertGreaterEqual(report["steps"][1]["seconds"], policy.stall_verify)
        self.assertGreaterEqual(report["steps"][2]["seconds"], policy.stall_usb_verify)
        self.assertIn("did not clear", report["steps"][0]["outcome"])
        for step in report["steps"][1:]:
            self.assertIn("lockdown still silent", step["outcome"])
        self.assertGreater(report["stallSeconds"], policy.stall_wait + policy.stall_verify + policy.stall_usb_verify)
        # The ladder is not restarted from scratch while the stall persists: no more helper calls.
        more = steps(self.decider, self.clock, STALLED, 20)
        self.assertEqual(actions(more), [])
        self.assertEqual({d.state for d in more}, {NEEDS_REPLUG})
        self.assertEqual(len(self.decider.stall_reports), 1)

    def test_service_restart_that_revives_lockdown_ends_the_ladder_and_rebuilds_the_link(self):
        policy = self.policy
        steps(self.decider, self.clock, STALLED, policy.stall_probes + self.within(policy.stall_wait) + 1)
        self.assertEqual(self.decider.ladder["step"], "service")
        self.decider.ladder_step_result(True, "stopped; started: Running")
        self.clock.advance(12)
        back = steps(self.decider, self.clock, PIPE_BACK, 1)[0]
        self.assertEqual(back.state, WAITING_TUNNEL)
        self.assertEqual(back.actions, ["start_forwards"])  # the forwards were bound to the stalled connection
        self.assertIsNotNone(back.ladder)
        self.assertTrue(back.ladder["recovered"])
        self.assertEqual(back.ladder["recoveredBy"], "service")
        self.assertEqual([s["step"] for s in back.ladder["steps"]], ["wait", "service"])
        self.assertIn("lockdown answered", back.ladder["steps"][-1]["outcome"])
        self.assertIn("pipe stall cleared after", back.detail)
        self.assertIsNone(self.decider.ladder)
        # No tunnel restart or replug follows: the runner is simply rebuilt once the tunnel is listed.
        listed = Observation(**{**PIPE_BACK.__dict__, "tunnel_entry": True, "route_ok": True})
        decisions = steps(self.decider, self.clock, listed, 2)
        self.assertIn("start_runwda", actions(decisions))
        self.assertNotIn("restart_tunnel", actions(decisions))
        self.assertEqual(steps(self.decider, self.clock, HEALTHY, 1)[0].state, READY)

    def test_stall_that_clears_during_the_wait_needs_no_helper_call(self):
        policy = self.policy
        steps(self.decider, self.clock, STALLED, policy.stall_probes + 2)
        back = steps(self.decider, self.clock, PIPE_BACK, 1)[0]
        self.assertEqual(back.actions, ["start_forwards"])
        self.assertEqual(back.ladder["recoveredBy"], "wait")
        self.assertEqual(len(self.decider.stall_reports), 1)

    def test_without_the_helper_the_ladder_skips_to_the_replug_message_and_says_how_to_install_it(self):
        policy = self.policy
        decisions = steps(self.decider, self.clock, STALLED_NO_HELPER,
                          policy.stall_probes + self.within(policy.stall_wait) + 1)
        self.assertEqual(actions(decisions), [])
        self.assertEqual(decisions[-1].state, NEEDS_REPLUG)
        self.assertIn("-InstallUsbHelper", decisions[-1].message)
        report = self.decider.stall_reports[-1]
        self.assertEqual([(s["step"], "skipped" in s["outcome"]) for s in report["steps"]],
                         [("wait", False), ("service", True), ("usb", True)])
        # Timings are still logged for the wait, and the replug is reached in well under the old 5 minutes.
        self.assertLess(report["stallSeconds"], 120)

    def test_helper_failure_moves_to_the_next_step_without_waiting_out_the_verification(self):
        policy = self.policy
        steps(self.decider, self.clock, STALLED, policy.stall_probes + self.within(policy.stall_wait) + 1)
        self.assertEqual(self.decider.ladder["step"], "service")
        self.decider.ladder_step_result(False, "service 'Apple Mobile Device Service' is not installed")
        nxt = steps(self.decider, self.clock, STALLED, 1)[0]
        self.assertEqual(nxt.state, PIPE_STALL_USB)
        self.assertEqual(nxt.actions, ["restart_usb_device"])
        self.assertIn("helper failed", self.decider.ladder["steps"][-1]["outcome"])

    def test_a_replug_during_the_replug_message_clears_it(self):
        policy = self.policy
        steps(self.decider, self.clock, STALLED_NO_HELPER, policy.stall_probes + self.within(policy.stall_wait) + 2)
        self.assertEqual(self.decider.state, NEEDS_REPLUG)
        steps(self.decider, self.clock, Observation(device_present=False, tunnel_alive=True), 2)
        self.assertIsNone(self.decider.ladder)
        back = steps(self.decider, self.clock, PIPE_BACK, 1)[0]
        self.assertEqual(back.state, WAITING_TUNNEL)
        self.assertEqual(back.actions, ["start_forwards"])

    def test_busy_window_stretches_detection_and_the_wait_only(self):
        busy = Observation(**{**STALLED.__dict__, "busy": True})
        policy = self.policy
        decisions = steps(self.decider, self.clock, busy, policy.stall_probes * policy.busy_multiplier - 1)
        self.assertIsNone(self.decider.ladder)
        steps(self.decider, self.clock, busy, 1)
        self.assertEqual(self.decider.ladder["step"], "wait")
        decisions = steps(self.decider, self.clock, busy, self.within(policy.stall_wait * policy.busy_multiplier))
        self.assertEqual(actions(decisions), [])
        self.assertEqual(steps(self.decider, self.clock, busy, 1)[0].actions, ["restart_apple_service"])

    def test_dead_runner_with_a_live_pipe_is_a_cheap_runner_restart_never_a_tunnel_action(self):
        # A route probe missed once (a slow phone), then the runner died: lockdown answering through
        # the pipe probe lifts the route hold, so the runner comes back in seconds.
        slow = Observation(**{**HEALTHY.__dict__, "route_ok": False})
        steps(self.decider, self.clock, slow, 1)
        self.assertIs(self.decider.last_route_ok, False)
        dead = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False, "pipe": "ok", "route_ok": None})
        decisions = steps(self.decider, self.clock, dead, 1)
        self.assertEqual(actions(decisions), ["start_runwda"])
        self.assertEqual(decisions[0].state, RECOVERING_RUNNER)
        self.assertIs(self.decider.last_route_ok, True)
        # Without the pipe probe's verdict the runner would have waited for the next route probe.
        held = Decider(self.policy, clock=self.clock)
        steps(held, self.clock, slow, 1)
        waiting = steps(held, self.clock, Observation(**{**dead.__dict__, "pipe": None}), 1)
        self.assertEqual(actions(waiting), [])
        self.assertEqual(waiting[0].state, WAITING_TUNNEL)


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        cases = [
            (("ok", True, "ok", None), "ok"),
            (("ok", True, "TimeoutError", "ok"), "ok"),  # lockdown slow, WDA answered over the same pipe
            (("ok", True, "TimeoutError", "TimeoutError"), "stalled"),
            (("ok", True, "timed out", "timeout"), "stalled"),
            (("ok", True, "ConnectionError", "TimeoutError"), "unknown"),  # a refused connect is not a stall
            (("ok", True, "TimeoutError", "empty"), "unknown"),
            (("ok", False, None, None), "no-device"),
            (("ConnectionRefusedError", False, None, None), "unknown"),  # usbmuxd itself is down
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(pipe_stall.classify_pipe(*args), expected)


class FakeUsbmux(threading.Thread):
    """A usbmuxd on loopback that lists one phone and either answers or stalls on :62078 and :8100."""

    def __init__(self, *, lockdown_answers=True, wda_answers=True):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.addr = self.server.getsockname()
        self.lockdown_answers = lockdown_answers
        self.wda_answers = wda_answers
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        with conn:
            conn.settimeout(3)
            try:
                length, _v, _k, tag = struct.unpack("<IIII", link_probe._recv_exact(conn, 16))
                request = plistlib.loads(link_probe._recv_exact(conn, length - 16))
                if request["MessageType"] == "ListDevices":
                    self.reply(conn, tag, {"DeviceList": [{"DeviceID": 3, "Properties": {"SerialNumber": "X",
                                                                                          "ConnectionType": "USB"}}]})
                    return
                port = socket.ntohs(request["PortNumber"])
                self.reply(conn, tag, {"MessageType": "Result", "Number": 0})
                if port == 62078 and self.lockdown_answers:
                    (size,) = struct.unpack(">I", link_probe._recv_exact(conn, 4))
                    plistlib.loads(link_probe._recv_exact(conn, size))
                    body = plistlib.dumps({"Request": "QueryType", "Type": "com.apple.mobile.lockdown"})
                    conn.sendall(struct.pack(">I", len(body)) + body)
                elif port == 8100 and self.wda_answers:
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                else:
                    self.stop.wait(4)  # stall: never answer
            except OSError:
                return

    @staticmethod
    def reply(conn, tag, payload):
        body = plistlib.dumps(payload)
        conn.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body)

    def close(self):
        self.stop.set()
        self.server.close()


class ProbeTests(unittest.TestCase):
    def probe(self, **kw):
        mux = FakeUsbmux(**kw)
        mux.start()
        try:
            return pipe_stall.probe_pipe(timeout=0.4, mux=mux.addr)
        finally:
            mux.close()

    def test_live_pipe_is_one_lockdown_round_trip(self):
        row = self.probe()
        self.assertEqual(row["pipe"], "ok")
        self.assertEqual(row["lockdown"][0], "ok")
        self.assertIsNone(row["wda"])  # never asked: lockdown was the whole answer

    def test_stalled_pipe_times_out_on_both_hops_within_the_bound(self):
        row = self.probe(lockdown_answers=False, wda_answers=False)
        self.assertEqual(row["pipe"], "stalled")
        self.assertTrue(pipe_stall.timed_out(row["lockdown"][0]))
        self.assertTrue(pipe_stall.timed_out(row["wda"][0]))
        self.assertLess(row["ms"], 2500)

    def test_wda_answering_beside_a_slow_lockdown_is_not_a_stall(self):
        row = self.probe(lockdown_answers=False, wda_answers=True)
        self.assertEqual(row["pipe"], "ok")

    def test_timeout_is_capped_at_two_seconds(self):
        with mock.patch.object(link_probe, "mux_list_devices", lambda timeout, mux: ("ok", 1, [])) as _:
            row = pipe_stall.probe_pipe(timeout=9.0)
        self.assertEqual(row["pipe"], "no-device")


class RunnerPlumbingTests(unittest.TestCase):
    def runner(self, state):
        from video_drop.phone import device

        with mock.patch.object(device, "ios_path", lambda: "C:/fake/ios.exe"):
            return link_supervisor.Runner(state)

    def test_pipe_is_only_probed_while_wda_is_silent(self):
        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp))
            probes = []
            runner.wda_state = lambda timeout=4.0: "up"
            runner.pipe_state = lambda: probes.append(1) or "ok"
            runner.device_present = lambda: True
            runner.proc_alive = lambda name: True
            runner.tunnel_entry = lambda: {"address": "fd11::1", "rsdPort": 1, "userspaceTun": True}
            runner.helper_available = lambda refresh=False: True
            runner.route_ok = lambda timeout=15: True
            self.assertEqual(runner.observe().pipe, "ok")
            self.assertEqual(probes, [])
            runner.wda_state = lambda timeout=4.0: "wedged"
            runner.pipe_state = lambda: probes.append(1) or "stalled"
            obs = runner.observe()
            self.assertEqual((obs.pipe, obs.helper_available, len(probes)), ("stalled", True, 1))

    def test_pipe_state_maps_unknown_and_no_device_to_not_probed(self):
        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp))
            for kind in ("unknown", "no-device"):
                with mock.patch.object(pipe_stall, "probe_pipe", lambda **kw: {"pipe": kind}):
                    self.assertIsNone(runner.pipe_state())
            with mock.patch.object(pipe_stall, "probe_pipe", lambda **kw: {"pipe": "stalled", "ms": 4001}):
                self.assertEqual(runner.pipe_state(), "stalled")
            self.assertEqual(runner.last_pipe["ms"], 4001)

    def test_helper_steps_go_through_the_helper_and_report_to_the_decider(self):
        from video_drop import usb_helper

        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp))
            runner.decider.ladder = {"step": "service", "since": 0.0, "sent": True, "steps": []}
            calls = []
            with mock.patch.object(usb_helper, "request", lambda command, **kw: calls.append(command) or
                                   {"ok": True, "detail": "started: Running", "ms": 900}):
                text = runner.apply("restart_apple_service")
            self.assertEqual(calls, ["restart-apple-service"])
            self.assertIn("done", text)
            self.assertEqual(runner.decider.ladder["result"], "sent")
            with mock.patch.object(usb_helper, "request", lambda command, **kw: {"ok": False, "detail": "refused"}):
                runner.apply("restart_usb_device")
            self.assertEqual(runner.decider.ladder["result"], "failed")
            self.assertEqual(runner.decider.ladder["result_detail"], "refused")

    def test_a_finished_ladder_is_written_to_the_events_log(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            runner = self.runner(state)
            runner.viewer_running = lambda: False
            runner.observe = lambda: PIPE_BACK
            runner.start_forwards = lambda: "forwards 1/2"
            runner.decider.ladder = {"step": "service", "since": runner.decider.clock() - 10, "sent": True, "steps": [
                {"step": "wait", "seconds": 30.0, "outcome": "did not clear on its own"}]}
            runner.decider.stall_since = runner.decider.clock() - 70
            decision = runner.tick()
            self.assertEqual(decision.state, WAITING_TUNNEL)
            events = [json.loads(line) for line in (state / "link-events.jsonl").read_text().splitlines()]
            ladder = events[-1]["ladder"]
            self.assertTrue(ladder["recovered"])
            self.assertEqual(ladder["recoveredBy"], "service")
            self.assertIn("start_forwards: forwards 1/2", events[-1]["actions"])
            status = json.loads((state / "link-status.json").read_text())
            self.assertEqual(status["lastStall"]["recoveredBy"], "service")


if __name__ == "__main__":
    unittest.main()
