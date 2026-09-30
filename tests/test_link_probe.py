"""Layered link probes against a fake usbmuxd / lockdownd / WDA / tunnel, plus incident grouping."""

from __future__ import annotations

import plistlib
import socket
import struct
import threading
import unittest

from video_drop import link_probe


class FakeUsbmux(threading.Thread):
    """A usbmuxd on a loopback port: lists one device, connects to :62078 (lockdown QueryType)
    and :8100 (an HTTP /status), refuses every other port."""

    def __init__(self, *, lockdown_answers=True, wda_answers=True):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.addr = self.server.getsockname()
        self.lockdown_answers = lockdown_answers
        self.wda_answers = wda_answers
        self.connects: list[int] = []

    def run(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn: socket.socket):
        with conn:
            conn.settimeout(3)
            try:
                length, _v, _k, tag = struct.unpack("<IIII", link_probe._recv_exact(conn, 16))
                request = plistlib.loads(link_probe._recv_exact(conn, length - 16))
                kind = request["MessageType"]
                if kind == "ListDevices":
                    self.reply(conn, tag, {"DeviceList": [{"DeviceID": 7, "MessageType": "Attached",
                                                           "Properties": {"SerialNumber": "00008140-TEST",
                                                                          "ConnectionType": "USB"}}]})
                    return
                port = socket.ntohs(request["PortNumber"])
                self.connects.append(port)
                if port == 62078:
                    self.reply(conn, tag, {"MessageType": "Result", "Number": 0})
                    if not self.lockdown_answers:
                        conn.settimeout(10)
                        link_probe._recv_exact(conn, 4)
                        threading.Event().wait(5)  # stall: never answer
                        return
                    (size,) = struct.unpack(">I", link_probe._recv_exact(conn, 4))
                    req = plistlib.loads(link_probe._recv_exact(conn, size))
                    body = plistlib.dumps({"Request": req["Request"], "Type": "com.apple.mobile.lockdown"})
                    conn.sendall(struct.pack(">I", len(body)) + body)
                elif port == 8100:
                    self.reply(conn, tag, {"MessageType": "Result", "Number": 0})
                    if not self.wda_answers:
                        threading.Event().wait(5)
                        return
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                else:
                    self.reply(conn, tag, {"MessageType": "Result", "Number": 3})
            except OSError:
                return

    @staticmethod
    def reply(conn, tag, payload):
        body = plistlib.dumps(payload)
        conn.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body)

    def close(self):
        self.server.close()


class FakeUserspaceTunnel(threading.Thread):
    """go-ios's userspace listener: 20-byte preamble, then the phone's RSD (HTTP/2) answers SETTINGS."""

    def __init__(self):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.preambles: list[tuple[str, int]] = []

    def run(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(3)
                try:
                    preamble = link_probe._recv_exact(conn, 20)
                    self.preambles.append((socket.inet_ntop(socket.AF_INET6, preamble[:16]),
                                           struct.unpack("<I", preamble[16:])[0]))
                    link_probe._recv_exact(conn, len(link_probe.H2_PREFACE) + 9)
                    conn.sendall(b"\x00\x00\x06\x04\x00\x00\x00\x00\x00" + b"\x00\x03\x00\x00\x00\x64")
                except OSError:
                    pass

    def close(self):
        self.server.close()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.mux = FakeUsbmux()
        self.mux.start()

    def tearDown(self):
        self.mux.close()

    def test_list_devices_and_device_id(self):
        status, ms, devices = link_probe.mux_list_devices(mux=self.mux.addr)
        self.assertEqual(status, "ok")
        self.assertEqual(link_probe.usb_device_id(devices), 7)
        self.assertEqual(link_probe.usb_device_id(devices, "00008140-TEST"), 7)
        self.assertIsNone(link_probe.usb_device_id(devices, "other"))

    def test_lockdown_query_type_round_trip(self):
        status, ms = link_probe.lockdown_query_type(7, mux=self.mux.addr)
        self.assertEqual(status, "ok")
        self.assertIn(62078, self.mux.connects)

    def test_wda_status_through_usbmux_without_a_forward(self):
        status, ms = link_probe.wda_status_direct(7, mux=self.mux.addr)
        self.assertEqual(status, "ok")
        self.assertIn(8100, self.mux.connects)

    def test_refused_port_is_reported_not_raised(self):
        status, _ = link_probe.wda_status_direct(7, port=9999, mux=self.mux.addr)
        self.assertNotEqual(status, "ok")

    def test_silent_lockdown_times_out_quickly(self):
        stalled = FakeUsbmux(lockdown_answers=False)
        stalled.start()
        try:
            status, ms = link_probe.lockdown_query_type(7, timeout=0.5, mux=stalled.addr)
            self.assertEqual(status, "TimeoutError")
            self.assertLess(ms, 2000)
        finally:
            stalled.close()

    def test_tunnel_round_trip_through_userspace_port(self):
        tunnel = FakeUserspaceTunnel()
        tunnel.start()
        try:
            entry = {"address": "fd11:22fd:494f::1", "rsdPort": 54176, "userspaceTun": True,
                     "userspaceTunPort": tunnel.port}
            status, ms = link_probe.tunnel_rsd_roundtrip(entry)
            self.assertEqual(status, "ok")
            self.assertEqual(tunnel.preambles, [("fd11:22fd:494f::1", 54176)])
        finally:
            tunnel.close()

    def test_tunnel_probe_without_entry(self):
        self.assertEqual(link_probe.tunnel_rsd_roundtrip({})[0], "no entry")

    def test_recorder_probe_once_is_one_row_per_hop(self):
        rec = link_probe.LinkRecorder(__import__("pathlib").Path("unused.jsonl"), udid="00008140-TEST",
                                      entry_source=lambda: [], probe_timeout=1.0, mux=self.mux.addr)
        row = rec.probe_once(now=100.0)
        self.assertEqual(row["mux"][0], "ok")
        self.assertEqual(row["lockdown"][0], "ok")
        self.assertEqual(row["wda"][0], "ok")
        self.assertEqual(row["tunnel"], ["no entry", 0])


def rows(spec: str, start: float = 0.0):
    """Build rows from a spec: one char per hop (m,l,w,t) per second, '.'=ok, 'x'=fail, '-'=unprobed."""
    out = []
    for i, cell in enumerate(spec.split()):
        t = start + i
        row = {"t": "%02d:%02d:%05.2f" % (int(t // 3600), int(t % 3600 // 60), t % 60)}
        for layer, ch in zip(("mux", "lockdown", "wda", "tunnel"), cell):
            if ch == ".":
                row[layer] = ["ok", 3]
            elif ch == "x":
                row[layer] = ["TimeoutError", 1500]
            elif ch == "n":
                row[layer] = ["no device", 0]
        out.append(row)
    return out


class IncidentTests(unittest.TestCase):
    def test_pipe_stall_all_hops_die_phone_still_listed(self):
        spec = "...- ...- .... ...- ...- .x.- .xx- .xxx .xxx .xxx .xxx .xxx .xxx .xxx .xxx .xxx .xxx .xxx"
        incidents = link_probe.classify_incidents(rows(spec))
        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        self.assertEqual(inc["kind"], "pipe-stall")
        self.assertEqual(inc["order"], ["lockdown", "wda", "tunnel"])
        self.assertTrue(inc["device_listed_throughout"])
        self.assertIsNone(inc["recovered_after"])

    def test_tunnel_only_close_that_recovers(self):
        spec = "...- ...- .... ...- ...x ...x ...x ...x ...- ...- ...- ...- ...."
        incidents = link_probe.classify_incidents(rows(spec))
        self.assertEqual([i["kind"] for i in incidents], ["tunnel-only"])
        self.assertIsNotNone(incidents[0]["recovered_after"])

    def test_wda_only_wedge(self):
        spec = "...- .... ...- ...- ..x- ..x. ..x- ..x- ..x- ..x. ..x- ..x- ...- ...."
        incidents = link_probe.classify_incidents(rows(spec))
        self.assertEqual([i["kind"] for i in incidents], ["wda-only"])

    def test_usb_drop(self):
        spec = "...- .... ...- ...- nxx- nxx- nxx- nxx- ...- ...."
        incidents = link_probe.classify_incidents(rows(spec))
        self.assertEqual([i["kind"] for i in incidents], ["usb-drop"])
        self.assertFalse(incidents[0]["device_listed_throughout"])

    def test_a_single_slow_probe_is_not_an_incident_before_settling(self):
        spec = ".x.- ...- .... ...-"
        self.assertEqual(link_probe.classify_incidents(rows(spec)), [])


if __name__ == "__main__":
    unittest.main()
