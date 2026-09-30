"""Vendored from SideTap's working-tree tests/test_device.py (upstream 0c75c53
plus the uncommitted tunnel_running tests). ios_path fallbacks, the WDA bundle
and app-list caches, DDI probes and the safe kill. No phone needed."""

import contextlib
import json
import subprocess
from types import SimpleNamespace


from video_drop.phone import device


def test_tunnel_running_rejects_stale_userspace_listener(monkeypatch):
    record = '[{"address":"fd00::1","rsdPort":64313,"userspaceTunPort":60106}]'
    monkeypatch.setattr(device, "_run", lambda *_args, **_kwargs: SimpleNamespace(stdout=record, stderr=""))

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(device.socket, "create_connection", refused)
    assert device.tunnel_running() is False


def test_tunnel_running_accepts_live_userspace_listener(monkeypatch):
    record = '[{"address":"fd00::1","rsdPort":64313,"userspaceTunPort":60106}]'
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=record, stderr="", returncode=0)

    monkeypatch.setattr(device, "_run", run)
    closed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append(True)

    monkeypatch.setattr(device.socket, "create_connection", lambda *_args, **_kwargs: Connection())
    assert device.tunnel_running() is True
    assert closed == [True]
    assert calls == [["tunnel", "ls"], ["image", "list"]]


def test_tunnel_running_rejects_local_listener_with_dead_phone_route(monkeypatch):
    record = '[{"address":"fd00::1","rsdPort":64313,"userspaceTunPort":60106}]'

    def run(args, **_kwargs):
        if args == ["tunnel", "ls"]:
            return SimpleNamespace(stdout=record, stderr="", returncode=0)
        raise subprocess.TimeoutExpired("ios image list", 3)

    monkeypatch.setattr(device, "_run", run)
    monkeypatch.setattr(device.socket, "create_connection", lambda *_args, **_kwargs: contextlib.nullcontext())
    assert device.tunnel_running(timeout=3) is False


def test_ios_path_prefers_path_lookup(monkeypatch):
    monkeypatch.setattr(device.shutil, "which", lambda _: r"C:\somewhere\ios.exe")
    assert device.ios_path() == r"C:\somewhere\ios.exe"


def test_ios_path_falls_back_to_npm_global_dir(monkeypatch, tmp_path):
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm dir
    # even though terminals see it (bit the Startup shortcut live 2026-08-10).
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    exe = tmp_path / "npm" / "ios.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert device.ios_path() == str(exe)




def test_ios_path_none_when_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert device.ios_path() is None


# ---- WDA bundle cache: deep sleep empties `ios apps --list` (seen live
# 2026-08-10) while the app is still installed. The cache keeps up() working
# so the link can heal the moment the phone wakes.


def test_wda_bundle_cached_on_live_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.x.WebDriverAgentRunner.xctrunner", "name": "WDA"}],
    )
    assert device.detect_wda_bundle() == "com.x.WebDriverAgentRunner.xctrunner"
    cached = (tmp_path / "wda_bundle").read_text(encoding="utf-8").strip()
    assert cached == "com.x.WebDriverAgentRunner.xctrunner"


def test_wda_bundle_falls_back_to_cache_when_list_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() == "com.x.cached.xctrunner"


def test_wda_bundle_none_when_empty_list_and_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    monkeypatch.setattr(device, "list_apps", lambda: [])
    assert device.detect_wda_bundle() is None


# ---- Developer Disk Image: an iOS update silently unmounts it (bit live
# 2026-08-10, the 26.6 update) — runwda then dies in dtx channel timeouts.
# `image list` prints a "signature" line when mounted, msg "none" when not.


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_ddi_mounted_sees_signature_line(monkeypatch):
    out = (
        '{"level":"INFO","msg":"no udid specified using first device in list"}\n'
        '{"level":"INFO","msg":"image signature","signature":"28080689ce6e"}\n'
    )
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    assert device.ddi_mounted()


def test_ddi_mounted_false_on_none(monkeypatch):
    out = '{"level":"INFO","msg":"none"}\n'
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    assert not device.ddi_mounted()


def test_mount_ddi_names_locked_phone(monkeypatch):
    # The one mount failure a human can fix on the spot must be named, not
    # buried in a log tail: DeviceLocked -> "unlock it".
    out = '{"level":"ERROR","msg":"error mounting image","err":"map[Error:DeviceLocked]"}\n'
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(out))
    monkeypatch.setattr(device, "ddi_mounted", lambda: False)
    ok, msg = device.mount_ddi()
    assert not ok
    assert "unlock" in msg.lower()


def test_mount_ddi_verifies_by_reprobe(monkeypatch):
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _Proc(""))
    monkeypatch.setattr(device, "ddi_mounted", lambda: True)
    ok, msg = device.mount_ddi()
    assert ok
    assert "mounted" in msg


def test_wda_bundle_no_cache_fallback_when_app_really_absent(monkeypatch, tmp_path):
    # A non-empty list without WDA means genuinely uninstalled: the stale
    # cache must NOT resurrect it.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device.config, "WDA_BUNDLE_ID", "")
    (tmp_path / "wda_bundle").write_text("com.x.cached.xctrunner", encoding="utf-8")
    monkeypatch.setattr(
        device,
        "list_apps",
        lambda: [{"bundle_id": "com.apple.mobilesafari", "name": ""}],
    )
    assert device.detect_wda_bundle() is None


# ---- list_apps() persistent cache: same deep-sleep-empties-the-list failure
# mode as detect_wda_bundle's own cache, but now lives INSIDE list_apps()
# itself so every caller (helpers.open_app, admin._check_wda_installed,
# detect_wda_bundle) benefits without touching helpers.py.


def _ios_apps_proc(bundle_ids):
    lines = "\n".join(
        f'{{"CFBundleIdentifier":"{bid}","CFBundleName":"{bid}"}}' for bid in bundle_ids
    )
    return _Proc(lines)


def test_list_apps_caches_nonempty_result_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(
        device, "_run", lambda args, timeout=30.0: _ios_apps_proc(["com.a.App"])
    )
    apps = device.list_apps()
    assert apps == [{"bundle_id": "com.a.App", "name": "com.a.App"}]
    cached = json.loads((tmp_path / "apps_cache.json").read_text(encoding="utf-8"))
    assert cached == apps


def test_list_apps_falls_back_to_cache_when_live_list_empty(monkeypatch, tmp_path):
    # Deep sleep empties `ios apps --list` while apps stay installed
    # (docs/ERRORS.md) — an empty live result must never overwrite, or shadow,
    # a previously cached good list.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "apps_cache.json").write_text(
        json.dumps([{"bundle_id": "com.a.App", "name": "com.a.App"}]), encoding="utf-8"
    )
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _ios_apps_proc([]))
    assert device.list_apps() == [{"bundle_id": "com.a.App", "name": "com.a.App"}]


def test_list_apps_empty_with_no_cache_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(device, "_run", lambda args, timeout=30.0: _ios_apps_proc([]))
    assert device.list_apps() == []


def test_list_apps_fresh_nonempty_overwrites_stale_cache(monkeypatch, tmp_path):
    # A newly installed app must still be findable — staleness must not stick.
    monkeypatch.setattr(device.config, "STATE_DIR", tmp_path)
    (tmp_path / "apps_cache.json").write_text(
        json.dumps([{"bundle_id": "com.old.App", "name": "com.old.App"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        device, "_run", lambda args, timeout=30.0: _ios_apps_proc(["com.new.App"])
    )
    assert device.list_apps() == [{"bundle_id": "com.new.App", "name": "com.new.App"}]
    cached = json.loads((tmp_path / "apps_cache.json").read_text(encoding="utf-8"))
    assert cached == [{"bundle_id": "com.new.App", "name": "com.new.App"}]



def test_safe_kill_can_spare_the_process_tree(monkeypatch):
    # The tunnel and the forwards are CHILDREN of whatever launched them, so a
    # tree kill aimed at a stale viewer takes the phone link down with it.
    # Measured 2026-08-12: 11 green checks, then a second SideTap launch left
    # the tunnel and WDA dead.
    cmds = []
    monkeypatch.setattr(device.sys, "platform", "win32")
    monkeypatch.setattr(device, "_pid_image", lambda pid: "python.exe")
    monkeypatch.setattr(
        device.subprocess, "run", lambda cmd, **kw: cmds.append((cmd, kw))
    )
    assert device._safe_kill(4242, "python", tree=False) is True
    assert cmds[0][0] == ["taskkill", "/PID", "4242", "/F"]
    assert device._safe_kill(4242, "python") is True  # default still kills the tree
    assert "/T" in cmds[1][0]

