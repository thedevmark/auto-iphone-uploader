from __future__ import annotations

import argparse
from functools import lru_cache
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from .core import Store, digest, next_slot, utc_now
from .analyze import OLLAMA, TEXT_MODEL, VISION_MODEL, analyze
from .accounts import load_targets
from .watch import WatchFolder, complete_video, eligible
from .runtime_identity import source_fingerprint

ROOT = Path(__file__).resolve().parent.parent
STATIC_FILES = {
    "/logo.svg": ("logo.svg", "image/svg+xml"),
    "/logo.ico": ("logo.ico", "image/x-icon"),
    "/fonts/inter-variable.woff2": ("fonts/inter-variable.woff2", "font/woff2"),
}
RUNTIME_FINGERPRINT = source_fingerprint(ROOT)
STATE = Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state")).resolve()
TEST_MODE = os.environ.get("VIDEO_DROP_TEST_MODE") == "1"
MAX_JSON = 1024 * 1024
ANALYSIS_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-drop-analysis")
ANALYSIS_LOCK = threading.Lock()
ANALYSIS_PENDING: set[int] = set()
PHONE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-drop-phone")
PHONE_LOCK = threading.Lock()
PHONE_RUNNING = False
PHONE_ACTION_LOCK = threading.Lock()
PHONE_ACTION_RUNNING = False
SIDETAP_STATUS_LOCK = threading.Lock()
SIDETAP_STATUS_CACHE: tuple[float, dict] | None = None
MODEL_RECOVERY_INTERVAL = 30
MODEL_RETRY_DELAY_SECONDS = 120
MODEL_RETRY_LIMIT = 3
WATCHER = WatchFolder(STATE, lambda release_id: queue_analysis(release_id))

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


@lru_cache(maxsize=32)
def verified_source(path: str, size: int, mtime_ns: int, expected_sha: str) -> bool:
    source = Path(path)
    if digest(source) != expected_sha:
        return False
    after = source.stat()
    return (after.st_size, after.st_mtime_ns) == (size, mtime_ns)


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    if header is None:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()):
        raise ValueError("Invalid video range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix == 0:
            raise ValueError("Invalid video range")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Invalid video range")
    return start, end


def import_finished_video(store: Store, path: Path) -> dict:
    if not eligible(path) or not complete_video(path):
        raise ValueError("Video is still exporting or cannot be fully decoded; wait for the finished file")
    return store.import_file(path)


def phone_inspection() -> dict:
    path = STATE / "phone-inspection.json"
    if not path.is_file():
        return {"status": "idle"}
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("status") == "inspecting" and not PHONE_RUNNING:
        return {"status": "failed", "error": "Phone inspection was interrupted. Inspect again."}
    return result


def sidetap_status() -> dict:
    """Summarize SideTap's own doctor without running a second phone probe."""
    global SIDETAP_STATUS_CACHE
    with SIDETAP_STATUS_LOCK:
        now = time.monotonic()
        if SIDETAP_STATUS_CACHE and now - SIDETAP_STATUS_CACHE[0] < 20:
            return SIDETAP_STATUS_CACHE[1]
        try:
            with urlopen("http://127.0.0.1:8770/api/doctor", timeout=10) as response:
                checks = json.load(response)
            if not isinstance(checks, list) or not checks or any(
                    not isinstance(check, dict) or not isinstance(check.get("name"), str)
                    or not isinstance(check.get("ok"), bool) for check in checks):
                raise ValueError("SideTap returned invalid checks")
            passed = sum(check["ok"] for check in checks)
            first_failed_check = next((check for check in checks if not check["ok"]), {})
            result = {"status": "ready" if passed == len(checks) else "attention",
                      "passed": passed, "total": len(checks),
                      "firstFailure": first_failed_check.get("name", ""),
                      "firstFailureDetail": first_failed_check.get("detail", ""),
                      "firstFailureFix": first_failed_check.get("fix", ""),
                      "checks": [{"name": check["name"], "ok": check["ok"]} for check in checks]}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = {"status": "unavailable", "passed": 0, "total": 0,
                      "firstFailure": str(exc)[:120],
                      "firstFailureFix": "Start SideTap's viewer, then refresh this page.", "checks": []}
        SIDETAP_STATUS_CACHE = (time.monotonic(), result)
        return result


def queue_phone_inspection() -> dict:
    global PHONE_RUNNING
    with PHONE_LOCK:
        if PHONE_RUNNING:
            return phone_inspection()
        PHONE_RUNNING = True
        state = {"status": "inspecting"}
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-inspection.json").write_text(json.dumps(state), encoding="utf-8")

    PHONE_POOL.submit(run_phone_inspection)
    return state


def run_phone_inspection() -> dict:
    """Run the read-only phone inventory now; callers hold the single phone worker."""
    global PHONE_RUNNING
    with PHONE_LOCK:
        PHONE_RUNNING = True
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-inspection.json").write_text(json.dumps({"status": "inspecting"}), encoding="utf-8")
    try:
        env = os.environ.copy()
        env["VIDEO_DROP_STATE"] = str(STATE)
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "phone_onboard.py")],
                                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        output = json.loads((completed.stdout if completed.returncode == 0 else completed.stderr).strip().splitlines()[-1])
        if completed.returncode:
            result = {"status": "failed", "error": str(output.get("error", "Phone inspection failed"))[:240]}
        else:
            result = {"status": "ready", "screenPoints": output["screenPoints"],
                      "installed": output["installed"], "youtube": output["youtube"],
                      "youtubeProbeError": output.get("youtubeProbeError", "")}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:240]}
    finally:
        with PHONE_LOCK:
            PHONE_RUNNING = False
            (STATE / "phone-inspection.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def youtube_quality_gate(checks: dict) -> str:
    """Check YouTube's upload quality on the first upload and, if chosen, every upload."""
    quality = phone_inspection().get("youtube", {}).get("uploadQuality", {})
    if "status" in quality and not checks["youtubeQualityEveryUpload"]:
        return quality["status"]
    result = run_phone_inspection()
    if result["status"] != "ready":
        raise ValueError(f"YouTube quality check could not run; nothing was uploaded. {result.get('error', '')}".strip())
    status = result["youtube"].get("uploadQuality", {}).get("status", "unverified")
    if status == "limited":
        raise ValueError("YouTube is set to upload at reduced quality; nothing was uploaded. "
                         "In YouTube Settings set Upload quality to Full quality, then try again.")
    return status


def phone_action_status() -> dict:
    path = STATE / "phone-action.json"
    if not path.is_file():
        return {"status": "idle"}
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("status") == "running" and not PHONE_ACTION_RUNNING:
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            release = store.release(result["releaseId"])
        platform = result["platform"]
        destination = next(d for d in release["destinations"] if d["platform"] == platform)
        return {**result, "status": "needs_check" if destination["status"] == "unconfirmed" else "interrupted",
                "message": f"Check {platform.title()} for a matching post before trying again" if destination["status"] == "unconfirmed"
                           else "Phone preparation stopped before the final tap; reconnect SideTap and try again"}
    return result


def queue_phone_post(release_id: int, platform: str) -> dict:
    """Run one native phone action in this process so a server exit cannot leave a child posting."""
    global PHONE_ACTION_RUNNING
    if TEST_MODE:
        raise ValueError("Posting is disabled in this test session")
    if platform not in {"threads", "youtube"}:
        raise ValueError("This native phone posting path is not connected")
    with PHONE_ACTION_LOCK:
        if PHONE_ACTION_RUNNING:
            raise ValueError("A phone action is already running")
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            if platform == "threads":
                from scripts.phone_threads import release_input
                release_input(store, release_id)
            else:
                from video_drop.phone_manifest import youtube_input
                if youtube_input(store, release_id)["deliveryMode"] != "post_now":
                    raise ValueError("YouTube scheduling is not connected; choose Post now")
        result = {"status": "running", "platform": platform, "releaseId": release_id,
                  "message": f"Preparing the confirmed video in {platform.title()}"}
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-action.json").write_text(json.dumps(result), encoding="utf-8")
        PHONE_ACTION_RUNNING = True

    def work() -> None:
        global PHONE_ACTION_RUNNING
        result = {"status": "failed", "platform": platform, "releaseId": release_id,
                  "message": "Phone action stopped before a result was recorded"}
        try:
            if platform == "threads":
                from scripts.phone_threads import run
                outcome = run(release_id, STATE / "video-drop.sqlite", commit=True)
            else:
                from scripts.phone_youtube import run
                with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
                    checks = store.phone_checks()
                youtube_quality_gate(checks)
                outcome = run(str(release_id), STATE / "video-drop.sqlite", commit=True)
            result = {"status": "needs_check", "platform": platform, "releaseId": release_id,
                      "message": outcome["message"]}
        except Exception as exc:
            with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
                release = store.release(release_id)
            destination = next(d for d in release["destinations"] if d["platform"] == platform)
            uncertain = destination["status"] == "unconfirmed"
            result = {"status": "needs_check" if uncertain else "failed", "platform": platform,
                      "releaseId": release_id,
                      "message": f"Check {platform.title()} for a matching post before any retry" if uncertain else str(exc)[:240]}
        finally:
            (STATE / "phone-action.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            with PHONE_ACTION_LOCK:
                PHONE_ACTION_RUNNING = False

    PHONE_POOL.submit(work)
    return result


def queue_threads_post(release_id: int) -> dict:
    return queue_phone_post(release_id, "threads")


def initial_video_folder() -> Path:
    configured = os.environ.get("VIDEO_DROP_SOURCE_DIR")
    candidates = ([Path(configured)] if configured else [])
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        candidates.append(Path(onedrive) / "_Videos")
    candidates.extend((Path.home() / "OneDrive" / "_Videos", Path.home() / "Videos", Path.home()))
    return next(path for path in candidates if path.is_dir())


def local_video_files() -> dict:
    """List local candidates; final import still decodes and fingerprints the file."""
    folder = initial_video_folder().resolve()
    files = []
    for path in folder.rglob("*"):
        if not path.is_file() or not eligible(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"path": str(path), "name": path.name, "size": stat.st_size,
                      "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()})
    files.sort(key=lambda item: item["modified"], reverse=True)
    return {"folder": str(folder), "files": files}


def local_video_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Choose a video from the list")
    folder = initial_video_folder().resolve()
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_relative_to(folder) or not path.is_file() or not eligible(path):
        raise ValueError("Choose a finished video from the video folder")
    return path


def choose_local_video() -> Path | None:
    command = [sys.executable, str(ROOT / "scripts" / "pick_video.py"),
               "--initial-dir", str(initial_video_folder())]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=300, creationflags=flags)
    if completed.returncode:
        raise ValueError("The video picker could not open")
    try:
        selected = json.loads(completed.stdout)["path"]
    except (ValueError, KeyError) as exc:
        raise ValueError("The video picker returned no usable file") from exc
    return Path(selected) if selected else None


def choose_watch_folder() -> Path | None:
    saved = WATCHER.configuration()["path"]
    previous = Path(saved) if saved else None
    initial = previous if previous and previous.is_dir() else initial_video_folder()
    command = [sys.executable, str(ROOT / "scripts" / "pick_folder.py"),
               "--initial-dir", str(initial)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=300, creationflags=flags)
    if completed.returncode:
        raise ValueError("The folder picker could not open")
    selected = json.loads(completed.stdout)["path"]
    return Path(selected) if selected else None


def queue_analysis(release_id: int) -> None:
    with ANALYSIS_LOCK:
        if release_id in ANALYSIS_PENDING:
            return
        ANALYSIS_PENDING.add(release_id)

    def work() -> None:
        attempts = 0
        try:
            with Store(STATE / "video-drop.sqlite") as store:
                release = store.release(release_id)
                if release["status"] != "draft":
                    return
                previous = json.loads(release["analysis_json"])
                attempts = int(previous.get("attempts", 0)) + 1
                store.save_analysis(release_id, {"status": "processing", "attempts": attempts})
                path = Path(release["source_path"])
                if digest(path) != release["sha256"]:
                    raise ValueError("Source video changed after import")
            result = analyze(path)
            result["attempts"] = attempts
            with Store(STATE / "video-drop.sqlite") as store:
                store.save_analysis(release_id, result)
        except Exception as exc:
            with Store(STATE / "video-drop.sqlite") as store:
                try:
                    store.save_analysis(release_id, {"status": "failed", "attempts": attempts,
                                                     "warnings": [str(exc)[:400]]})
                except ValueError:
                    pass
        finally:
            with ANALYSIS_LOCK:
                ANALYSIS_PENDING.discard(release_id)

    ANALYSIS_POOL.submit(work)


def local_models_ready() -> bool:
    try:
        with urlopen(f"{OLLAMA}/api/tags", timeout=2) as response:
            models = json.load(response).get("models", [])
        names = {item.get("name") for item in models if isinstance(item, dict)}
        return VISION_MODEL in names and TEXT_MODEL in names
    except (OSError, ValueError, KeyError):
        return False


def retry_waiting_draft(*, force: bool = False) -> bool:
    with Store(STATE / "video-drop.sqlite") as store:
        waiting = []
        for release in store.drafts():
            result = json.loads(release["analysis_json"])
            warnings = result.get("warnings", [])
            attempts = int(result.get("attempts", 0))
            age = (utc_now() - datetime.fromisoformat(release["updated_at"])).total_seconds()
            if attempts >= MODEL_RETRY_LIMIT or (not force and age < MODEL_RETRY_DELAY_SECONDS * max(1, attempts)):
                continue
            interrupted = result.get("status") == "processing" and age >= 600
            model_failed = result.get("status") in {"partial", "failed"} and any(
                isinstance(warning, str) and warning.startswith("local ") and "model:" in warning
                for warning in warnings
            )
            if interrupted or model_failed:
                waiting.append(release["id"])
    for release_id in waiting:
        queue_analysis(release_id)
    return bool(waiting)


def model_recovery_loop(stop: threading.Event) -> None:
    previously_ready = False
    while not stop.is_set():
        ready = local_models_ready()
        if ready:
            retry_waiting_draft(force=not previously_ready)
        previously_ready = ready
        stop.wait(MODEL_RECOVERY_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def _trusted_request(self) -> bool:
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if host not in allowed_hosts or (origin is not None and origin.lower() != f"http://{host}"):
            self.send_error(403, "Local app requests must come from this origin")
            return False
        return True

    def _json(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > MAX_JSON:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _store(self) -> Store:
        return Store(STATE / "video-drop.sqlite", load_targets(STATE))

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/":
            body = (ROOT / "web" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            body = (ROOT / "web" / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/runtime":
            self._json(200, {"root": str(ROOT.resolve()), "stateDir": str(STATE),
                             "sourceFingerprint": RUNTIME_FINGERPRINT})
        elif match := re.fullmatch(r"/api/releases/(\d+)/video", path):
            try:
                with self._store() as store:
                    release = store.release(int(match[1]))
                source = Path(release["source_path"])
                info = source.stat()
                if (not source.is_file() or info.st_size != release["file_size"]
                        or not verified_source(str(source), info.st_size, info.st_mtime_ns, release["sha256"])):
                    raise ValueError("Source video changed or is missing")
                try:
                    requested = byte_range(self.headers.get("Range"), info.st_size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{info.st_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = requested or (0, info.st_size - 1)
                with source.open("rb") as video:
                    if video.seek(0, 2) != info.st_size or source.stat().st_mtime_ns != info.st_mtime_ns:
                        raise ValueError("Source video changed or is missing")
                    video.seek(start)
                    self.send_response(206 if requested else 200)
                    self.send_header("Content-Type", VIDEO_TYPES.get(source.suffix.lower(), "application/octet-stream"))
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    if requested:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{info.st_size}")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    remaining = end - start + 1
                    try:
                        while remaining:
                            chunk = video.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # Seeking or switching clips closes the previous browser request.
            except (ValueError, OSError):
                self.send_error(404, "Video unavailable")
        elif path == "/api/current":
            with self._store() as store:
                self._json(200, {"release": store.current(), "stateDir": str(STATE), "testMode": TEST_MODE})
        elif match := re.fullmatch(r"/api/releases/(\d+)", path):
            try:
                with self._store() as store:
                    self._json(200, {"release": store.release(int(match[1])), "testMode": TEST_MODE})
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/queue":
            with self._store() as store:
                rows = store.db.execute("SELECT id FROM release WHERE status IN ('draft','reserved','scheduled','uploading','partial','needs_check') ORDER BY CASE WHEN status='draft' THEN 0 ELSE 1 END, scheduled_at, id").fetchall()
                self._json(200, {"releases": [store.release(row[0]) for row in rows]})
        elif path == "/api/settings":
            with self._store() as store:
                slots = store.posting_slots()
                occupied = {row[0] for row in store.db.execute("SELECT scheduled_at FROM release WHERE scheduled_at IS NOT NULL AND status != 'discarded'")}
                upcoming = []
                for _ in range(5):
                    slot = next_slot(utc_now(), occupied, slots)
                    upcoming.append(slot.isoformat())
                    occupied.add(slot.astimezone(timezone.utc).isoformat())
                self._json(200, {"watch": WATCHER.status(), "postingSlots": slots,
                                 "timeZone": "America/New_York", "nextSlots": upcoming,
                                 "phoneChecks": store.phone_checks(),
                                 "youtubeQuality": phone_inspection().get("youtube", {}).get("uploadQuality", {})})
        elif path == "/api/phone":
            self._json(200, phone_inspection())
        elif path == "/api/phone-action":
            self._json(200, phone_action_status())
        elif path == "/api/source-files":
            self._json(200, local_video_files())
        elif path == "/api/sidetap":
            self._json(200, sidetap_status())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/import-path":
            try:
                value = self._body().get("path")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Enter the full path to a finished video")
                selected = Path(value).expanduser().resolve(strict=True)
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/source-files/import":
            try:
                selected = local_video_path(self._body().get("path"))
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/pick-file":
            try:
                selected = choose_local_video()
                if selected is None:
                    self._json(200, {"cancelled": True})
                    return
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/watch/pick-folder":
            try:
                selected = choose_watch_folder()
                self._json(200, {"cancelled": selected is None,
                                 "watch": WATCHER.configure(folder=selected) if selected else WATCHER.configuration()})
            except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path in {"/api/watch/toggle", "/api/settings/slots", "/api/settings/phone-checks"}:
            try:
                data = self._body()
                if path == "/api/watch/toggle":
                    if not isinstance(data.get("enabled"), bool):
                        raise ValueError("Expected an on/off value")
                    self._json(200, {"watch": WATCHER.configure(enabled=data["enabled"])})
                elif path == "/api/settings/phone-checks":
                    with self._store() as store:
                        self._json(200, {"phoneChecks": store.set_phone_checks(data.get("phoneChecks"))})
                else:
                    with self._store() as store:
                        self._json(200, {"postingSlots": store.set_posting_slots(data.get("slots"))})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/phone/inspect":
            self._json(200, queue_phone_inspection())
            return
        if path == "/api/queue/plan":
            try:
                if TEST_MODE:
                    raise ValueError("Slot planning is disabled in this test session")
                release_ids = self._body().get("releaseIds")
                if (not isinstance(release_ids, list) or not release_ids
                        or any(type(release_id) is not int or release_id <= 0 for release_id in release_ids)):
                    raise ValueError("Choose finished videos to plan")
                with self._store() as store:
                    planned = store.reserve_batch(sorted(release_ids))
                self._json(200, {"releases": planned, "nativeScheduled": False})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if match := re.fullmatch(r"/api/releases/(\d+)/(threads|youtube)-post", path):
            try:
                self._body()
                self._json(202, queue_phone_post(int(match[1]), match[2]))
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        match = re.fullmatch(r"/api/releases/(\d+)/(text|shared-title|shared-copy|authorize|delivery-mode|schedule|unconfirmed|receipt|discard|analyze)", path)
        try:
            data = self._body()
            with self._store() as store:
                if match:
                    release_id, action = int(match[1]), match[2]
                    platform = str(data.get("platform", ""))
                    if action == "text":
                        result = store.save_text(release_id, platform, str(data.get("account", "")), str(data.get("title", "")), str(data.get("description", "")), str(data.get("tags", "")), str(data.get("visibility", "public")))
                    elif action == "shared-title":
                        result = store.apply_shared_title(release_id, str(data.get("title", "")))
                    elif action == "shared-copy":
                        result = store.apply_shared_copy(release_id, str(data.get("title", "")), str(data.get("hashtags", "")))
                    elif action == "authorize":
                        result = store.authorize(release_id, platform)
                    elif action == "delivery-mode":
                        result = store.set_delivery_mode(release_id, data.get("mode"))
                    elif action == "schedule":
                        if platform == "threads":
                            raise ValueError("Threads only posts now; it does not use a schedule slot")
                        if TEST_MODE:
                            raise ValueError("Scheduling is disabled in this test session")
                        raise ValueError("Native platform scheduling is not connected yet")
                    elif action == "unconfirmed":
                        result = store.mark_unconfirmed(release_id, platform)
                    elif action == "receipt":
                        raise ValueError("Provider receipt verification is not connected yet")
                    elif action == "analyze":
                        if store.release(release_id)["status"] != "draft":
                            raise ValueError("Only a draft can be analyzed")
                        result = store.release(release_id)
                    else:
                        result = store.discard(release_id)
                else:
                    self.send_error(404)
                    return
            self._json(200, {"release": result})
            if match and match[2] == "analyze":
                queue_analysis(result["id"])
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Auto iPhone Uploader server")
    parser.add_argument("--port", type=int, default=4748)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    inspection = phone_inspection()
    with Store(STATE / "video-drop.sqlite") as store:
        inspect_on_open = store.phone_checks()["inspectPhoneOnOpen"]
    if inspect_on_open and (inspection["status"] in {"idle", "failed"} or (inspection["status"] == "ready" and
            "uploadQuality" not in inspection.get("youtube", {}))):
        queue_phone_inspection()
    recovery_stop = threading.Event()
    watch_stop = threading.Event()
    with Store(STATE / "video-drop.sqlite") as store:
        for draft in store.drafts():
            status = json.loads(draft["analysis_json"]).get("status", "pending")
            if status in {"pending", "processing"}:
                queue_analysis(draft["id"])
    recovery_thread = threading.Thread(target=model_recovery_loop, args=(recovery_stop,),
                                       name="video-drop-model-recovery", daemon=True)
    recovery_thread.start()
    watch_thread = threading.Thread(target=WATCHER.run, args=(watch_stop,),
                                    name="video-drop-watch-folder", daemon=True)
    watch_thread.start()
    print(f"Auto iPhone Uploader: http://127.0.0.1:{args.port}  state: {STATE}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        recovery_stop.set()
        watch_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
