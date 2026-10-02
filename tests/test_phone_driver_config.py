"""The app-specific parts of the vendored driver's config: one state owner
(.state/phone under VIDEO_DROP_STATE), the go-ios override, and the migration
fallback that reads only the passcode and bundle id from SideTap's .env."""

import importlib
from pathlib import Path

import pytest

from video_drop.phone import config, device


@pytest.fixture
def reloaded(monkeypatch, tmp_path):
    """Reload config under a controlled environment; put the real one back after.

    The app's own .env is swapped for an empty one: the owner's real file holds the passcode."""

    def load(**env):
        monkeypatch.setenv("VIDEO_DROP_ENV_FILE", str(tmp_path / "no-app.env"))
        for key in ("VIDEO_DROP_STATE", "GO_IOS_PATH", "SIDETAP_ROOT", "LOCALAPPDATA", "PHONE_PASSCODE",
                    "WDA_BUNDLE_ID", "PHONE_UDID", "SIDETAP_UDID", "VIDEO_DROP_NO_LEGACY_ENV"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield load
    monkeypatch.undo()
    importlib.reload(config)


def test_state_dir_is_the_apps_own_phone_folder(reloaded, tmp_path):
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path / "none"))
    assert cfg.STATE_DIR == tmp_path.resolve() / "phone"
    assert cfg.WDA_PORT == 8100 and cfg.MJPEG_PORT == 9100
    assert cfg.WDA_URL == "http://127.0.0.1:8100"


def test_default_state_dir_sits_beside_the_link_status(reloaded, tmp_path):
    cfg = reloaded(LOCALAPPDATA=str(tmp_path / "none"))
    assert cfg.STATE_DIR == cfg.REPO_ROOT / ".state" / "phone"


def test_legacy_env_lends_only_the_passcode_and_bundle_id(reloaded, tmp_path):
    root = tmp_path / "Packages" / "OpenAI.Codex_x" / "LocalCache" / "Local" / "SideTap"
    root.mkdir(parents=True)
    (root / ".env").write_text("PHONE_PASSCODE=246810\nWDA_BUNDLE_ID=\nWDA_PORT=8102\nVIEWER_PORT=1\n",
                               encoding="utf-8")
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    assert cfg.PHONE_PASSCODE == "246810"
    assert cfg.WDA_PORT == 8100, "only the two migration keys may come from SideTap's .env"
    assert cfg.legacy_keys() == ["PHONE_PASSCODE"]


def test_legacy_env_can_be_switched_off_and_never_beats_the_process_env(reloaded, tmp_path):
    root = tmp_path / "SideTap"
    root.mkdir()
    (root / ".env").write_text("PHONE_PASSCODE=246810\n", encoding="utf-8")
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path), PHONE_PASSCODE="111111")
    assert cfg.PHONE_PASSCODE == "111111"
    assert cfg.legacy_keys() == []
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path), VIDEO_DROP_NO_LEGACY_ENV="1")
    assert cfg.PHONE_PASSCODE is None


def test_go_ios_override_wins_and_a_bad_path_is_loud(monkeypatch, tmp_path):
    exe = tmp_path / "ios.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(config, "GO_IOS_PATH", str(exe))
    monkeypatch.setattr(device.shutil, "which", lambda _: pytest.fail("PATH must not be consulted"))
    assert device.ios_path() == str(exe)
    monkeypatch.setattr(config, "GO_IOS_PATH", str(tmp_path / "missing.exe"))
    with pytest.raises(device.DeviceError, match="GO_IOS_PATH"):
        device.ios_path()


def test_missing_go_ios_names_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GO_IOS_PATH", None)
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert device.ios_path() is None
    with pytest.raises(device.DeviceError, match="npm install -g go-ios"):
        device._run(["list"])


def test_udid_pin_accepts_both_names(reloaded, tmp_path):
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path / "none"), PHONE_UDID="00008120-A")
    assert cfg.SIDETAP_UDID == "00008120-A"
    assert device.pin_udid(["apps", "--list"]) == ["apps", "--list", "--udid=00008120-A"]
    assert device.pin_udid(["list"]) == ["list"]
    cfg = reloaded(VIDEO_DROP_STATE=str(tmp_path), LOCALAPPDATA=str(tmp_path / "none"), SIDETAP_UDID="00008120-B")
    assert cfg.SIDETAP_UDID == "00008120-B"


def test_package_imports_spawn_nothing():
    """Importing the driver must never touch go-ios or the phone (the setup
    check imports it in a child process to prove it loads). A child interpreter
    keeps the reload out of this process, where it would swap class identities
    under the other test modules."""
    import subprocess
    import sys

    code = ("import subprocess\n"
            "def boom(*a, **k): raise SystemExit('import spawned a process')\n"
            "subprocess.run = boom; subprocess.Popen = boom\n"
            "import video_drop.phone.config, video_drop.phone.device, video_drop.phone.wda_client\n"
            "import video_drop.phone.capture, video_drop.phone.helpers, video_drop.phone.signing\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          cwd=str(Path(config.__file__).parents[2]))
    assert done.returncode == 0, done.stderr


def test_video_guard_app_list_and_its_empty_off_switch(reloaded, tmp_path):
    cfg = reloaded()
    # Every app a flow drives that can play video, not just TikTok (2026-10-01 soak: YouTube).
    assert cfg.AX_VIDEO_APPS == frozenset({"com.zhiliaoapp.musically", "com.google.ios.youtube",
                                           "com.burbn.instagram", "com.burbn.basel", "com.burbn.barcelona",
                                           "com.facebook.Facebook"})
    cfg = reloaded(AX_VIDEO_APPS="com.zhiliaoapp.musically, com.burbn.instagram")
    assert cfg.AX_VIDEO_APPS == frozenset({"com.zhiliaoapp.musically", "com.burbn.instagram"})
    cfg = reloaded(AX_VIDEO_APPS="")  # the ax-shallow arm: guard off
    assert cfg.AX_VIDEO_APPS == frozenset()
