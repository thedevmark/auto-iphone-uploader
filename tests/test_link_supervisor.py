"""Decision logic of the phone-link supervisor, driven by injected observations."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from video_drop.link_supervisor import (
    NEEDS_REPLUG, READY, RECOVERING_RUNNER, RECOVERING_TUNNEL, UNPLUGGED, WAITING_TUNNEL, WEDGED,
    Decider, Observation, Policy, clear_busy, declare_busy, read_busy, read_status, write_status, Decision,
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


def run(decider, clock, obs, ticks, *, apply=None):
    """Feed `obs` for `ticks` ticks; return every decision. `apply` mimics the runner's bookkeeping."""
    decisions = []
    for _ in range(ticks):
        decision = decider.step(obs)
        for action in decision.actions:
            if apply:
                apply(action)
        decisions.append(decision)
        clock.advance(decider.policy.tick)
    return decisions


def ticks(policy, seconds):
    """How many ticks cover `seconds` of the policy's clock, plus one."""
    return int(seconds / policy.tick) + 1


def bookkeeping(decider):
    def apply(action):
        if action in ("start_tunnel", "restart_tunnel"):
            decider.tunnel_started()
        elif action in ("start_runwda", "restart_runwda"):
            decider.runner_started()
        elif action == "release_springboard":
            decider.released()
    return apply


class DeciderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.decider = Decider(Policy(), clock=self.clock)
        self.apply = bookkeeping(self.decider)

    def actions(self, decisions):
        return [a for d in decisions for a in d.actions]

    def test_healthy_link_is_left_alone(self):
        decisions = run(self.decider, self.clock, HEALTHY, 20)
        self.assertEqual({d.state for d in decisions}, {READY})
        self.assertEqual(self.actions(decisions), [])

    def test_single_slow_route_probe_never_restarts_the_tunnel(self):
        slow = Observation(**{**HEALTHY.__dict__, "route_ok": False})
        run(self.decider, self.clock, slow, 2, apply=self.apply)
        decisions = run(self.decider, self.clock, HEALTHY, 3, apply=self.apply)
        self.assertEqual(self.actions(decisions), [])
        self.assertEqual(decisions[-1].state, READY)

    def test_wedge_by_video_feed_is_released_via_home_within_the_runner_budget(self):
        wedged = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "route_ok": None})
        decisions = run(self.decider, self.clock, wedged, 3, apply=self.apply)
        self.assertEqual(self.actions(decisions), ["release_springboard"])
        self.assertEqual(decisions[-1].state, WEDGED)
        # iOS kills the runner ~39s after a wedge; the Home press must land well before that.
        pressed_at = next(i for i, d in enumerate(decisions) if d.actions) * self.decider.policy.tick
        self.assertLessEqual(pressed_at + self.decider.policy.wda_timeout, 20)
        # Recovered 20s later: no restart happened.
        recovered = run(self.decider, self.clock, HEALTHY, 1, apply=self.apply)
        self.assertEqual(recovered[0].state, READY)
        self.assertNotIn("restart_runwda", self.actions(decisions))

    def test_wedge_that_survives_two_home_presses_restarts_only_the_runner(self):
        wedged = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "route_ok": None})
        decisions = run(self.decider, self.clock, wedged, 40, apply=self.apply)
        acts = self.actions(decisions)
        # Two Home presses two minutes apart, then the runner (never the tunnel), then the cycle repeats.
        self.assertEqual(acts[:3], ["release_springboard", "release_springboard", "restart_runwda"])
        self.assertNotIn("restart_tunnel", acts)
        self.assertNotIn("start_tunnel", acts)
        releases = [i for i, d in enumerate(decisions) if "release_springboard" in d.actions]
        self.assertGreaterEqual((releases[1] - releases[0]) * self.decider.policy.tick,
                                self.decider.policy.release_interval)

    def test_busy_queue_is_not_a_wedge(self):
        busy = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "activity_landed": True})
        decisions = run(self.decider, self.clock, busy, 10, apply=self.apply)
        self.assertEqual(self.actions(decisions), [])

    def test_declared_busy_window_does_not_delay_the_wedge_release(self):
        # A wedge happens inside a busy window by construction (the script is on the
        # video surface); the 39s runner budget does not stretch with it.
        wedged = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "route_ok": None, "busy": True})
        decisions = run(self.decider, self.clock, wedged, 3, apply=self.apply)
        self.assertEqual(self.actions(decisions), ["release_springboard"])

    def test_dead_runner_with_healthy_tunnel_restarts_only_the_runner(self):
        dead = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        decisions = run(self.decider, self.clock, dead, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_runwda"])
        self.assertEqual(decisions[0].state, RECOVERING_RUNNER)
        # While the fresh runner boots (still refusing), nothing else is touched.
        booting = Observation(**{**HEALTHY.__dict__, "wda": "down"})
        decisions = run(self.decider, self.clock, booting, 5, apply=self.apply)
        self.assertEqual(self.actions(decisions), [])
        self.assertEqual(run(self.decider, self.clock, HEALTHY, 1)[0].state, READY)

    def test_runner_that_never_answers_is_restarted_after_the_grace(self):
        dead = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        run(self.decider, self.clock, dead, 1, apply=self.apply)
        stuck = Observation(**{**HEALTHY.__dict__, "wda": "down"})
        decisions = run(self.decider, self.clock, stuck, ticks(self.decider.policy, self.decider.policy.wda_start_grace) + 1,
                        apply=self.apply)
        # A live runner that never answers may sit behind a stale forward: both are refreshed.
        self.assertEqual(self.actions(decisions), ["start_forwards", "restart_runwda"])

    def test_stale_tunnel_record_is_refreshed_in_place_before_any_restart(self):
        # 2026-09-30 12:37: after a replug the daemon still listed port 60107 with nothing listening.
        # go-ios's per-device refresh rebuilt it in under 5s; the daemon is never killed first.
        stale = Observation(**{**HEALTHY.__dict__, "wda": "down", "tunnel_listening": False})
        decisions = run(self.decider, self.clock, stale, ticks(self.decider.policy, 60), apply=self.apply)
        actions = self.actions(decisions)
        self.assertEqual(actions.count("refresh_tunnel"), self.decider.policy.refreshes_before_restart)
        self.assertEqual(actions.index("refresh_tunnel"), 0)  # after two refused probes, never on one
        self.assertIn("restart_tunnel", actions)
        self.assertLess(actions.index("refresh_tunnel"), actions.index("restart_tunnel"))

    def test_a_refused_probe_while_wda_answers_never_refreshes(self):
        noisy = Observation(**{**HEALTHY.__dict__, "tunnel_listening": False})
        decisions = run(self.decider, self.clock, noisy, 5, apply=self.apply)
        self.assertNotIn("refresh_tunnel", self.actions(decisions))

    def test_a_listening_tunnel_clears_the_refresh_count(self):
        stale = Observation(**{**HEALTHY.__dict__, "wda": "down", "tunnel_listening": False})
        run(self.decider, self.clock, stale, 1, apply=self.apply)
        run(self.decider, self.clock, Observation(**{**HEALTHY.__dict__, "tunnel_listening": True}), 1, apply=self.apply)
        self.assertEqual(self.decider.tunnel_refreshes, 0)

    def test_missing_forwards_are_restarted_without_touching_the_runner(self):
        obs = Observation(**{**HEALTHY.__dict__, "wda": "down", "forwards_alive": False})
        decisions = run(self.decider, self.clock, obs, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_forwards"])

    def test_usb_drop_waits_for_the_daemon_instead_of_killing_it(self):
        gone = Observation(device_present=False, tunnel_alive=True)
        decisions = run(self.decider, self.clock, gone, 6, apply=self.apply)
        self.assertEqual({d.state for d in decisions}, {UNPLUGGED})
        self.assertEqual(self.actions(decisions), [])
        # Back on the bus: the daemon gets its grace to list a new tunnel.
        negotiating = Observation(device_present=True, tunnel_alive=True, tunnel_entry=False, wda="down")
        decisions = run(self.decider, self.clock, negotiating, ticks(self.decider.policy, 30), apply=self.apply)
        self.assertEqual({d.state for d in decisions}, {WAITING_TUNNEL})
        # Forwards are bound to the dropped USB connection, so they are refreshed once on return
        # (2026-09-30: stale forwards kept WDA silent for 15 minutes); the daemon is left alone.
        self.assertEqual(self.actions(decisions), ["start_forwards"])
        # Tunnel listed again, runner orphaned: only runner + forwards come back.
        orphaned = Observation(device_present=True, tunnel_alive=True, tunnel_entry=True, route_ok=True,
                               runwda_alive=False, forwards_alive=False, wda="down")
        decisions = run(self.decider, self.clock, orphaned, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_forwards", "start_runwda"])

    def test_daemon_that_never_lists_a_tunnel_is_restarted_with_backoff(self):
        obs = Observation(device_present=True, tunnel_alive=True, tunnel_entry=False, wda="down")
        policy = self.decider.policy
        decisions = run(self.decider, self.clock, obs, ticks(policy, policy.entry_grace) + policy.entry_ticks + 1,
                        apply=self.apply)
        acts = self.actions(decisions)
        self.assertEqual(acts.count("restart_tunnel"), 1)
        # A second failure while the phone stays on the bus is a replug, not a loop.
        decisions = run(self.decider, self.clock, obs, ticks(policy, policy.entry_grace) + policy.entry_ticks + 5,
                        apply=self.apply)
        self.assertNotIn("restart_tunnel", self.actions(decisions))
        self.assertEqual(decisions[-1].state, NEEDS_REPLUG)

    def test_dead_route_with_phone_on_bus_waits_then_restarts_once_then_asks_for_a_replug(self):
        dead_route = Observation(**{**HEALTHY.__dict__, "route_ok": False, "wda": "down", "runwda_alive": False})
        policy = self.decider.policy
        # Five minutes of a dead route with the daemon left alone: it may recover by itself.
        decisions = run(self.decider, self.clock, dead_route, ticks(policy, policy.route_dead_grace) - 1, apply=self.apply)
        self.assertEqual(self.actions(decisions), [])
        self.assertEqual(decisions[-1].state, WAITING_TUNNEL)
        decisions = run(self.decider, self.clock, dead_route, 2, apply=self.apply)
        acts = self.actions(decisions)
        self.assertEqual(acts, ["restart_tunnel"])
        decisions = run(self.decider, self.clock, dead_route, ticks(policy, policy.route_dead_grace) + 5, apply=self.apply)
        acts = self.actions(decisions)
        self.assertNotIn("restart_tunnel", acts)
        self.assertNotIn("start_runwda", acts)  # a runner cannot reach testmanagerd over a dead route
        self.assertEqual(decisions[-1].state, NEEDS_REPLUG)
        self.assertIn("replug", decisions[-1].message.lower())
        # The replug: device leaves and returns, route answers, runner is rebuilt.
        run(self.decider, self.clock, Observation(device_present=False, tunnel_alive=True), 1, apply=self.apply)
        back = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        run(self.decider, self.clock, back, 1, apply=self.apply)
        decisions = run(self.decider, self.clock, back, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_runwda"])

    def test_route_that_recovers_by_itself_needs_no_restart_or_replug(self):
        dead_route = Observation(**{**HEALTHY.__dict__, "route_ok": False, "wda": "down", "runwda_alive": False})
        run(self.decider, self.clock, dead_route, 20, apply=self.apply)
        back = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        decisions = run(self.decider, self.clock, back, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_runwda"])
        self.assertEqual(run(self.decider, self.clock, HEALTHY, 1)[0].state, READY)

    def test_dead_runner_behind_a_live_forward_is_down_not_wedged(self):
        # The forward accepts the socket and nothing answers: with no runner that is
        # a dead runner, not a video-feed wedge, so no Home press.
        obs = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "runwda_alive": False})
        decisions = run(self.decider, self.clock, obs, 4, apply=self.apply)
        acts = self.actions(decisions)
        self.assertIn("start_runwda", acts)
        self.assertNotIn("release_springboard", acts)

    def test_dead_route_while_busy_waits_three_times_as_long(self):
        dead_route = Observation(**{**HEALTHY.__dict__, "route_ok": False, "busy": True})
        policy = self.decider.policy
        decisions = run(self.decider, self.clock, dead_route,
                        ticks(policy, policy.route_dead_grace * policy.busy_multiplier) - 1, apply=self.apply)
        self.assertEqual(self.actions(decisions), [])
        decisions = run(self.decider, self.clock, dead_route, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["restart_tunnel"])

    def test_missing_daemon_is_started_with_backoff(self):
        obs = Observation(device_present=True, tunnel_alive=False, wda="down")
        decisions = run(self.decider, self.clock, obs, 2, apply=self.apply)
        self.assertEqual(self.actions(decisions), ["start_tunnel"])
        self.assertEqual(decisions[-1].state, RECOVERING_TUNNEL)
        # A daemon that keeps dying is retried, later each time (20 s, 60 s, 180 s ...).
        decisions = run(self.decider, self.clock, obs, ticks(self.decider.policy, 300), apply=self.apply)
        starts = [i for i, d in enumerate(decisions) if d.actions]
        self.assertEqual(len(starts), 3)
        self.assertGreater(starts[2] - starts[1], starts[1] - starts[0])

    def test_dead_runner_error_becomes_a_plain_language_hint(self):
        dead = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False,
                              "runner_error": "Timed out while enabling automation mode. (Error code: 1000)"})
        decision = run(self.decider, self.clock, dead, 1, apply=self.apply)[0]
        self.assertEqual(decision.actions, ["start_runwda"])
        self.assertIn("Unlock the phone", decision.message)
        self.assertIn("passcode", decision.message)

    def test_unknown_device_probe_does_not_count_as_unplugged(self):
        obs = Observation(**{**HEALTHY.__dict__, "device_present": None})
        decisions = run(self.decider, self.clock, obs, 3)
        self.assertEqual({d.state for d in decisions}, {READY})


class FilesTests(unittest.TestCase):
    def test_busy_window_expires(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            declare_busy(state, "youtube upload", 100)
            self.assertEqual(read_busy(state), "youtube upload")
            self.assertIsNone(read_busy(state, now=__import__("time").time() + 200))
            clear_busy(state)
            self.assertIsNone(read_busy(state))

    def test_status_round_trip_reports_supervisor_liveness(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            write_status(state, Decision(NEEDS_REPLUG, "Unplug and replug the phone"), since="now")
            status = read_status(state)
            self.assertEqual(status["state"], NEEDS_REPLUG)
            self.assertFalse(status["alive"])
            self.assertEqual(json.loads((state / "link-status.json").read_text())["message"],
                             "Unplug and replug the phone")


class RunnerOwnershipTests(unittest.TestCase):
    """The runner drives the vendored driver and owns its files under .state/phone."""

    def runner(self, state):
        from unittest import mock
        from video_drop import link_supervisor
        from video_drop.phone import device

        with mock.patch.object(device, "ios_path", lambda: "C:/fake/ios.exe"):
            runner = link_supervisor.Runner(state)
        return runner

    def test_processes_are_recorded_in_the_apps_phone_state_folder(self):
        from unittest import mock
        from video_drop import link_supervisor
        from video_drop.phone import config, device

        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            runner = self.runner(state)
            self.assertEqual(config.STATE_DIR, state / "phone")
            self.assertTrue((state / "phone").is_dir())
            spawned = []

            def spawn(command, log, **kwargs):
                spawned.append((command, log))
                return 4242

            with mock.patch.object(link_supervisor, "spawn_detached", spawn), \
                    mock.patch.object(device, "_pid_alive", lambda pid: pid == 4242):
                self.assertIn("4242", runner.start_tunnel())
                self.assertEqual(device.proc_status("tunnel"), "running")
            self.assertEqual((state / "phone" / "tunnel.pid").read_text(), "4242")
            self.assertEqual(spawned[0][0], ["C:/fake/ios.exe", "tunnel", "start", "--userspace"])
            self.assertEqual(spawned[0][1], state / "phone" / "tunnel.log")
            self.assertEqual(runner.runner_error(), "")

    def test_a_tick_asks_for_each_process_status_once(self):
        # proc_status is one `tasklist` spawn per process; observe() asked for four and the status
        # payload asked for the same four again, eight spawns every 5 s tick.
        from unittest import mock
        from video_drop.phone import device

        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            runner = self.runner(state)
            asked = []
            runner.wda_state = lambda timeout: "up"
            runner.device_present = lambda: True
            runner.tunnel_entry = lambda: {"udid": "X", "address": "fd11::1", "rsdPort": 1, "userspaceTun": True}
            runner.route_ok = lambda timeout=15: True
            runner.activity_landed = lambda: False
            runner.helper_available = lambda refresh=False: False
            runner.viewer_running = lambda: False
            with mock.patch.object(device, "proc_status", lambda name: asked.append(name) or "running"):
                decision = runner.tick()
            self.assertEqual(decision.state, READY)
            self.assertEqual(sorted(asked), ["forward8100", "forward9100", "runwda", "tunnel"])
            status = json.loads((state / "link-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["pids"], {name: "running" for name in asked})

    def test_only_the_supervisor_can_start_a_go_ios_process(self):
        from video_drop.phone import device

        for name in ("_spawn", "start_tunnel", "start_wda", "start_forwards"):
            self.assertFalse(hasattr(device, name), f"device.{name} would be a second starter beside the supervisor")

    def test_sidetap_viewer_is_detected_by_its_port(self):
        import socket
        from unittest import mock
        from video_drop import link_supervisor

        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp))
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            try:
                with mock.patch.object(link_supervisor, "SIDETAP_VIEWER_PORT", port):
                    self.assertTrue(runner.viewer_running())
            finally:
                listener.close()
            with mock.patch.object(link_supervisor, "SIDETAP_VIEWER_PORT", port):
                self.assertFalse(runner.viewer_running())


if __name__ == "__main__":
    unittest.main()


class RunnerStartGuardTests(unittest.TestCase):
    def test_dead_route_starts_nothing_and_never_mounts(self):
        # 2026-09-30 12:20-12:24: `ios image auto` on a dead route blocked the loop for 180s.
        import tempfile
        from video_drop import link_supervisor as sup
        runner = sup.Runner(Path(tempfile.mkdtemp()))
        calls = []
        runner._other_runners = lambda: []
        runner.route_ok = lambda timeout=15: calls.append(("route", timeout)) or False
        runner.device = type("D", (), {"detect_wda_bundle": lambda self: calls.append("bundle"),
                                       "ddi_mounted": lambda self: calls.append("ddi"),
                                       "mount_ddi": lambda self: calls.append("mount")})()
        self.assertIn("route not answering", runner.start_runwda())
        self.assertEqual(calls, [("route", 6)])
        self.assertIs(runner.decider.last_route_ok, False)


class ExternalTunnelDeciderTests(unittest.TestCase):
    """LINK_TUNNEL_MODE=kernel/pmd3: the tunnel is adopted, reported on, never started or killed."""

    def setUp(self):
        from video_drop.link_supervisor import EXTERNAL_TUNNEL_DOWN
        self.EXTERNAL = EXTERNAL_TUNNEL_DOWN
        self.clock = Clock()
        self.decider = Decider(Policy(manage_tunnel=False), clock=self.clock)
        self.apply = bookkeeping(self.decider)

    def actions(self, decisions):
        return [a for d in decisions for a in d.actions]

    def test_missing_external_daemon_is_reported_never_started(self):
        obs = Observation(device_present=True, tunnel_alive=False, wda="down")
        decisions = run(self.decider, self.clock, obs, 10, apply=self.apply)
        self.assertEqual({d.state for d in decisions}, {self.EXTERNAL})
        self.assertEqual(self.actions(decisions), [])
        self.assertIn("admin terminal", decisions[0].message)

    def test_dead_route_never_restarts_or_asks_for_a_replug_first(self):
        dead_route = Observation(**{**HEALTHY.__dict__, "route_ok": False, "wda": "down", "runwda_alive": False})
        policy = self.decider.policy
        decisions = run(self.decider, self.clock, dead_route, ticks(policy, policy.route_dead_grace) + 5,
                        apply=self.apply)
        acts = self.actions(decisions)
        self.assertNotIn("restart_tunnel", acts)
        self.assertNotIn("start_tunnel", acts)
        self.assertNotIn("refresh_tunnel", acts)
        self.assertNotIn("start_runwda", acts)  # a runner cannot reach testmanagerd over a dead route
        self.assertEqual(decisions[-1].state, self.EXTERNAL)
        # The operator restarted their daemon: the route answers, the runner is rebuilt, ready again.
        back = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        decisions = run(self.decider, self.clock, back, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_runwda"])
        self.assertEqual(run(self.decider, self.clock, HEALTHY, 1)[0].state, READY)

    def test_stale_listener_is_reported_not_refreshed(self):
        stale = Observation(**{**HEALTHY.__dict__, "wda": "down", "tunnel_listening": False})
        decisions = run(self.decider, self.clock, stale, 6, apply=self.apply)
        self.assertNotIn("refresh_tunnel", self.actions(decisions))
        self.assertEqual(decisions[-1].state, self.EXTERNAL)

    def test_wedge_and_runner_recovery_still_work_with_an_external_tunnel(self):
        wedged = Observation(**{**HEALTHY.__dict__, "wda": "wedged", "route_ok": None})
        decisions = run(self.decider, self.clock, wedged, 3, apply=self.apply)
        self.assertEqual(self.actions(decisions), ["release_springboard"])
        dead = Observation(**{**HEALTHY.__dict__, "wda": "down", "runwda_alive": False})
        decisions = run(self.decider, self.clock, dead, 1, apply=self.apply)
        self.assertEqual(decisions[0].actions, ["start_runwda"])


class ExternalTunnelRunnerTests(unittest.TestCase):
    def runner(self, state, **kwargs):
        from unittest import mock
        from video_drop import link_supervisor
        from video_drop.phone import device

        with mock.patch.object(device, "ios_path", lambda: "C:/fake/ios.exe"):
            return link_supervisor.Runner(state, **kwargs)

    def test_kernel_mode_never_spawns_or_stops_a_tunnel_daemon(self):
        from unittest import mock
        from video_drop import link_supervisor

        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp), tunnel_mode="kernel")
            self.assertFalse(runner.policy.manage_tunnel)
            spawned, stopped = [], []
            with mock.patch.object(link_supervisor, "spawn_detached", lambda *a, **k: spawned.append(a) or 1), \
                    mock.patch.object(runner, "_stop", lambda names: stopped.append(names) or []):
                self.assertIn("external", runner.start_tunnel())
                self.assertIn("external", runner.restart_tunnel())
                self.assertIn("external", runner.refresh_tunnel())
            self.assertEqual(spawned, [])
            self.assertEqual(stopped, [])

    def test_mode_is_read_from_the_environment(self):
        from unittest import mock

        with TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"LINK_TUNNEL_MODE": "kernel",
                                                                          "LINK_SKIP_MJPEG_FORWARD": "1"}):
            runner = self.runner(Path(tmp))
        self.assertEqual(runner.tunnel_mode, "kernel")
        self.assertFalse(runner.mjpeg_forward)
        self.assertEqual(runner.forward_names, ("forward8100",))
        with TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"LINK_TUNNEL_MODE": "bogus"}):
            with self.assertRaises(ValueError):
                self.runner(Path(tmp))

    def test_kernel_mode_rejects_a_userspace_tunnel_entry(self):
        from unittest import mock

        userspace = ('{"address":"fd11::1","rsdPort":54176,"udid":"X","userspaceTun":true,"userspaceTunPort":60107}')
        kernel = '{"address":"fd11::1","rsdPort":54176,"udid":"X","userspaceTun":false}'
        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp), tunnel_mode="kernel")
            fake = type("P", (), {"stdout": userspace, "stderr": ""})()
            with mock.patch.object(runner, "_ios", lambda args, timeout: fake):
                self.assertIsNone(runner.tunnel_entry())
            self.assertIn("listed tunnel is userspace", runner.mode_mismatch)
            fake.stdout = kernel
            with mock.patch.object(runner, "_ios", lambda args, timeout: fake):
                self.assertEqual(runner.tunnel_entry()["rsdPort"], 54176)
            self.assertEqual(runner.mode_mismatch, "")
            # And the default mode rejects a kernel entry the same way.
            default = self.runner(Path(tmp))
            with mock.patch.object(default, "_ios", lambda args, timeout: fake):
                self.assertIsNone(default.tunnel_entry())

    def test_listening_probe_targets_the_right_end(self):
        from unittest import mock
        from video_drop import link_supervisor

        seen = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def connect(addr, timeout):
            seen.append(addr)
            return Conn()

        with mock.patch.object(link_supervisor.socket, "create_connection", connect):
            self.assertTrue(link_supervisor.Runner.tunnel_listening({"userspaceTunPort": 60107, "address": "fd11::1",
                                                                      "rsdPort": 54176}))
            self.assertTrue(link_supervisor.Runner.tunnel_listening({"address": "fd11::1", "rsdPort": 54176}))
        self.assertEqual(seen, [("127.0.0.1", 60107), ("fd11::1", 54176)])

    def test_pmd3_entry_feeds_go_ios_the_rsd_address(self):
        from unittest import mock
        from video_drop import link_probe
        from video_drop.phone import config, device

        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp), tunnel_mode="pmd3")
            entries = [{"udid": "X", "address": "fd42::1", "rsdPort": 61000, "userspaceTun": False}]
            with mock.patch.object(link_probe, "pmd3_entries", lambda: ("ok", 2, entries)), \
                    mock.patch.object(config, "SIDETAP_UDID", None):
                self.assertEqual(runner.tunnel_entry()["address"], "fd42::1")
                self.assertTrue(runner.pmd3_alive())
            try:
                self.assertEqual(device.pin_udid(["runwda", "--bundleid=x"])[-2:],
                                 ["--address=fd42::1", "--rsd-port=61000"])
                self.assertEqual(device.pin_udid(["image", "list"]), ["image", "list"])  # lockdown, not RSD
                self.assertEqual(device.pin_udid(["list"]), ["list"])
            finally:
                config.GO_IOS_RSD_ADDRESS = config.GO_IOS_RSD_PORT = None

    def test_no_mjpeg_forward_starts_only_the_wda_forward(self):
        from unittest import mock
        from video_drop import link_supervisor
        from video_drop.phone import device

        with TemporaryDirectory() as tmp:
            runner = self.runner(Path(tmp), mjpeg_forward=False)
            spawned = []
            with mock.patch.object(link_supervisor, "spawn_detached", lambda cmd, log, **k: spawned.append(cmd) or 5), \
                    mock.patch.object(device, "_free_port", lambda port: None), \
                    mock.patch.object(device, "_pid_alive", lambda pid: pid == 5):
                text = runner.start_forwards()
            self.assertIn("no MJPEG forward", text)
            self.assertEqual([c[1:] for c in spawned], [["forward", "8100", "8100"]])
            self.assertEqual(runner.forward_names, ("forward8100",))


class OneSupervisorTests(unittest.TestCase):
    """Only one supervisor may own the link; two would fight over the phone."""

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = Path(self.folder.name)

    def test_the_lock_is_exclusive_and_released(self):
        from video_drop.link_supervisor import release_supervisor_lock, supervisor_alive, try_supervisor_lock
        self.assertFalse(supervisor_alive(self.state))
        held = try_supervisor_lock(self.state)
        self.assertIsNotNone(held)
        try:
            self.assertIsNone(try_supervisor_lock(self.state))
            self.assertTrue(supervisor_alive(self.state))
        finally:
            release_supervisor_lock(held)
        self.assertFalse(supervisor_alive(self.state))

    def test_a_stale_pid_record_is_not_a_running_supervisor(self):
        from video_drop.link_supervisor import PID_FILE, supervisor_alive
        import os
        # The record names a live python process (this one) that holds no lock.
        (self.state / PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
        self.assertFalse(supervisor_alive(self.state))

    def test_a_second_supervisor_exits_without_running(self):
        from unittest import mock
        from video_drop import link_supervisor
        held = link_supervisor.try_supervisor_lock(self.state)
        self.addCleanup(link_supervisor.release_supervisor_lock, held)
        with mock.patch.dict("os.environ", {"VIDEO_DROP_STATE": str(self.state)}), \
                mock.patch.object(link_supervisor, "Runner") as runner:
            self.assertEqual(link_supervisor.main([]), 0)
        runner.assert_not_called()

    def test_the_os_drops_the_lock_when_the_holder_dies(self):
        import subprocess
        import sys
        import time
        from video_drop.link_supervisor import supervisor_alive
        root = Path(__file__).resolve().parents[1]
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; from pathlib import Path; "
             "from video_drop.link_supervisor import try_supervisor_lock; "
             "h = try_supervisor_lock(Path(sys.argv[1])); print('held' if h else 'busy', flush=True); time.sleep(60)",
             str(self.state)],
            cwd=root, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertTrue(supervisor_alive(self.state))
        finally:
            holder.kill()
            holder.wait(timeout=10)
            holder.stdout.close()
        for _ in range(20):
            if not supervisor_alive(self.state):
                break
            time.sleep(0.1)
        self.assertFalse(supervisor_alive(self.state))
