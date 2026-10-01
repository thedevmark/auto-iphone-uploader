"""The standalone Windows installer's contract: pinned Python, two interpreters, per-user, keeps .state."""

import re
from pathlib import Path

import pytest

from video_drop.phone import signing

ROOT = Path(__file__).resolve().parent.parent
BUILD = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")
ISS = (ROOT / "installer" / "AutoiPhoneUploader.iss").read_text(encoding="utf-8")
INSTALL = (ROOT / "scripts" / "install_windows.ps1").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def test_embeddable_python_is_pinned_by_version_and_sha256():
    assert re.search(r"\$PythonVersion = '3\.14\.\d+'", BUILD)
    assert re.search(r"\$PythonSha256  = '[0-9A-F]{64}'", BUILD)
    assert "$PythonUrl     = \"https://www.python.org/ftp/python/$PythonVersion/" in BUILD
    # A file that does not match the pin is deleted, never used.
    assert "Remove-Item -LiteralPath $embedZip -Force" in BUILD
    assert "Nothing was built" in BUILD


def test_app_python_sees_the_app_and_resign_python_does_not():
    app = re.search(r"New-Interpreter 'python' @\((.+?)\)", BUILD).group(1)
    resign = re.search(r"New-Interpreter 'python-resign' @\((.+?)\)", BUILD).group(1)
    assert "'..'" in app and "'import site'" in app
    assert "'..'" not in resign and "'import site'" in resign
    assert "Install-Packages $appPython 'requirements.txt'" in BUILD
    assert "Install-Packages $resignPython 'requirements-resign.txt'" in BUILD
    # pymobiledevice3 (GPL-3.0) is never imported by the app's interpreter's own code.
    assert "pymobiledevice3" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_build_proves_the_payload_before_compiling():
    assert "video_drop.server" in BUILD
    assert "pymobiledevice3.lockdown" in BUILD
    assert BUILD.index("video_drop.server") < BUILD.index("& $iscc")


def test_installer_is_per_user_and_leaves_state():
    assert "PrivilegesRequired=lowest" in ISS
    assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in ISS
    assert "pythonw.exe" in ISS and "launch_video_drop.py" in ISS
    assert "-PackagesBundled" in ISS and "-NoPrompt" in ISS and "-NoLaunch" in ISS
    assert "postinstall" in ISS and "Launch {#AppName}" in ISS
    delete = ISS.split("[UninstallDelete]")[1].split("[Code]")[0]
    assert ".state" not in re.sub(r"(?m)^;.*$", "", delete)
    assert "Your data was kept" in ISS


def test_install_script_accepts_a_bundled_python():
    for switch in ("[switch]$PackagesBundled", "[switch]$NoDesktopShortcut", "[string]$Python"):
        assert switch in INSTALL
    assert "python-resign" in INSTALL
    assert INSTALL.count("Read-Host") == 1


def test_release_builds_attests_and_lists_the_installer():
    job = RELEASE.split("  windows-installer:")[1].split("\n  badge:")[0]
    assert "needs: [tests, source]" in job
    assert "build_installer.ps1" in job
    assert "attest-build-provenance@" in job
    assert "SHA256SUMS.txt" in job and "AutoiPhoneUploader-Setup-" in job
    for line in re.findall(r"uses: (\S+)", job):
        assert re.search(r"@[0-9a-f]{40}$", line), f"{line} is not pinned to a commit"


def test_resign_runs_in_the_bundled_interpreter_when_present(monkeypatch, tmp_path):
    exe = tmp_path / "python-resign" / "python.exe"
    package = exe.parent / "Lib" / "site-packages" / "pymobiledevice3"
    package.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(signing, "_bundled_resign_python", lambda: exe)
    assert signing._pymobiledevice3_installed()
    assert signing._resign_python() == str(exe)
    ran = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(signing.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or Done())
    assert signing._device_profiles("UDID") == []
    assert ran[0][0] == str(exe)


def test_resign_falls_back_to_the_current_interpreter(monkeypatch):
    monkeypatch.setattr(signing, "_bundled_resign_python", lambda: None)
    assert signing._resign_python() == signing.sys.executable


@pytest.mark.parametrize("name", ["pick_video.py", "pick_folder.py"])
def test_pickers_find_the_bundled_tcl(name):
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert "TCL_LIBRARY" in text and "TK_LIBRARY" in text
    assert text.index("TCL_LIBRARY") < text.index("from tkinter")
