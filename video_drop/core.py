from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .analyze import safe_tag
from .accounts import PLATFORMS

NY = ZoneInfo("America/New_York")
DESTINATIONS = PLATFORMS
TIMED_DESTINATIONS = frozenset(DESTINATIONS) - {"threads"}
EDITABLE = ("draft", "reserved")
DEFAULT_SLOTS = ("10:00", "19:00")
DELIVERY_MODES = ("schedule", "post_now")
PLATFORM_HASHTAGS = {"youtube": "#shorts", "instagram": "#reels", "facebook": "#reels", "threads": "", "tiktok": "#fyp"}


def caption_with_title(caption: str, title: str) -> str:
    """Replace caption prose while keeping its existing hashtag section verbatim."""
    hashtag = re.search(r"(?<!\w)#[\w]+", caption)
    if not hashtag:
        return title
    prefix = caption[:hashtag.start()]
    separator = re.search(r"\s*$", prefix)[0] or " "
    return title + separator + caption[hashtag.start():]


def shared_hashtags(value: str) -> list[str]:
    """Normalize reviewed shared hashtags; platform suffixes are generated separately."""
    if not isinstance(value, str):
        raise ValueError("Hashtags must be text")
    result: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", value.strip()):
        if not token:
            continue
        if not re.fullmatch(r"#[\w]+", token):
            raise ValueError("Enter hashtags with #, separated by spaces")
        name = token[1:]
        if not safe_tag(name):
            raise ValueError("A hashtag has a blocked sensitive topic")
        key = name.casefold()
        if key not in seen and key not in {"shorts", "reels", "fyp"}:
            result.append(token)
            seen.add(key)
    return result


def caption_for_platform(caption: str, platform: str) -> str:
    prose = re.sub(r"(?<!\w)#[\w]+", "", caption)
    prose = re.sub(r"[ \t]{2,}", " ", prose).strip()
    common = shared_hashtags(" ".join(re.findall(r"(?<!\w)#[\w]+", caption)))
    suffix = PLATFORM_HASHTAGS[platform]
    return " ".join(part for part in (prose, *common, suffix) if part)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_slots(slots: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(slots, (list, tuple)) or not 1 <= len(slots) <= 5:
        raise ValueError("Choose one to five posting times")
    if any(not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in slots):
        raise ValueError("Posting times must use HH:MM")
    if len(set(slots)) != len(slots):
        raise ValueError("Posting times must be unique")
    return tuple(sorted(slots))


def next_slot(now: datetime, occupied: set[str], slots: tuple[str, ...] = DEFAULT_SLOTS) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    slots = validate_slots(slots)
    local = now.astimezone(NY)
    for day in range(366):
        date = local.date() + timedelta(days=day)
        for time in slots:
            hour, minute = map(int, time.split(":"))
            slot = datetime(date.year, date.month, date.day, hour, minute, tzinfo=NY)
            if slot.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) != slot.replace(tzinfo=None):
                continue
            slot_utc = slot.astimezone(timezone.utc)
            if slot_utc > now.astimezone(timezone.utc) + timedelta(minutes=1) and slot_utc.isoformat() not in occupied:
                return slot
    raise ValueError("no_video_slot_available")


def digest(path: Path) -> str:
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hash_.update(block)
    return hash_.hexdigest()


class Store:
    def __init__(self, path: Path, account_targets: dict[str, str] | None = None):
        self.path = path
        self.account_targets = account_targets or {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS release (
                id INTEGER PRIMARY KEY, homebase_id INTEGER UNIQUE,
                source_path TEXT NOT NULL, source_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft', scheduled_at TEXT,
                delivery_mode TEXT NOT NULL DEFAULT 'schedule',
                analysis_json TEXT NOT NULL DEFAULT '{}', legacy_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS destination (
                id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL REFERENCES release(id),
                platform TEXT NOT NULL, account TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'public',
                revision_hash TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending', external_url TEXT NOT NULL DEFAULT '',
                legacy_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
                UNIQUE(release_id, platform)
            );
            CREATE TABLE IF NOT EXISTS event (
                id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL REFERENCES release(id),
                platform TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS watch_file (
                path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                state TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_scheduled_slot
              ON release(scheduled_at) WHERE scheduled_at IS NOT NULL AND status != 'discarded';
        """)
        if any(row[1] == "one_draft_release" for row in self.db.execute("PRAGMA index_list('release')")):
            self.db.execute("DROP INDEX one_draft_release")
        if "visibility" not in {row[1] for row in self.db.execute("PRAGMA table_info(destination)")}:
            self.db.execute("ALTER TABLE destination ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")
        if "delivery_mode" not in {row[1] for row in self.db.execute("PRAGMA table_info(release)")}:
            self.db.execute("ALTER TABLE release ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'schedule'")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _event(self, release_id: int, platform: str | None, kind: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO event(release_id,platform,kind,payload,created_at) VALUES(?,?,?,?,?)",
            (release_id, platform, kind, json.dumps(payload, sort_keys=True), utc_now().isoformat()),
        )

    def import_file(self, source: Path) -> dict:
        source = source.expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v"}:
            raise ValueError("Select one finished video file")
        before = source.stat()
        size = before.st_size
        if size == 0:
            raise ValueError("Video file is empty")
        sha = digest(source)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Video changed during import; wait for the export to finish")
        now = utc_now().isoformat()
        with self.db:
            if self.db.execute("SELECT 1 FROM release WHERE sha256=?", (sha,)).fetchone():
                raise ValueError("This exact video is already in Automated iPhone Social Media Uploads")
            cursor = self.db.execute(
                "INSERT INTO release(source_path,source_name,sha256,file_size,analysis_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (str(source), source.name, sha, size, json.dumps({"status": "pending"}), now, now),
            )
            release_id = cursor.lastrowid
            for platform in DESTINATIONS:
                self.db.execute("INSERT INTO destination(release_id,platform,account,updated_at) VALUES(?,?,?,?)",
                                (release_id, platform, self.account_targets.get(platform, ""), now))
            self._event(release_id, None, "imported", {"sha256": sha, "source": str(source)})
        return self.release(release_id)

    def release(self, release_id: int) -> dict:
        row = self.db.execute("SELECT * FROM release WHERE id=?", (release_id,)).fetchone()
        if not row:
            raise ValueError("Video not found")
        result = dict(row)
        result["destinations"] = [dict(item) for item in self.db.execute("SELECT * FROM destination WHERE release_id=? ORDER BY id", (release_id,))]
        return result

    def current(self) -> dict | None:
        row = self.db.execute("SELECT id FROM release WHERE status='draft' ORDER BY id LIMIT 1").fetchone()
        return self.release(row[0]) if row else None

    def drafts(self) -> list[dict]:
        return [self.release(row[0]) for row in self.db.execute("SELECT id FROM release WHERE status='draft' ORDER BY id")]

    def posting_slots(self) -> tuple[str, ...]:
        row = self.db.execute("SELECT value FROM setting WHERE key='posting_slots'").fetchone()
        return validate_slots(json.loads(row[0])) if row else DEFAULT_SLOTS

    def set_posting_slots(self, slots: list[str]) -> tuple[str, ...]:
        valid = validate_slots(slots)
        with self.db:
            self.db.execute("INSERT INTO setting(key,value) VALUES('posting_slots',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(valid),))
        return valid

    def set_delivery_mode(self, release_id: int, mode: str) -> dict:
        if mode not in DELIVERY_MODES:
            raise ValueError("Choose Schedule or Post now")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            if release["status"] != "draft" or release["scheduled_at"] or any(
                    destination["status"] != "pending" for destination in release["destinations"]):
                raise ValueError("Delivery choice cannot change after phone work starts")
            if release["delivery_mode"] == mode:
                self.db.commit()
                return release
            self.db.execute("UPDATE release SET delivery_mode=?,updated_at=? WHERE id=?",
                            (mode, utc_now().isoformat(), release_id))
            self._event(release_id, None, "delivery_mode_changed", {"mode": mode})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def save_analysis(self, release_id: int, result: dict) -> dict:
        release = self.release(release_id)
        if release["status"] != "draft":
            raise ValueError("Analysis can only update a draft")
        with self.db:
            self.db.execute("UPDATE release SET analysis_json=?,updated_at=? WHERE id=?",
                            (json.dumps(result, ensure_ascii=False), utc_now().isoformat(), release_id))
            self._event(release_id, None, "analysis_updated", {"status": result.get("status", "unknown")})
        return self.release(release_id)

    @staticmethod
    def _revision_hash(platform: str, account: str, title: str, description: str, tags: str, visibility: str) -> str:
        payload = json.dumps([platform, account, title, description, tags, visibility], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def save_text(self, release_id: int, platform: str, account: str, title: str, description: str, tags: str,
                  visibility: str = "public") -> dict:
        if platform not in DESTINATIONS:
            raise ValueError("Unknown destination")
        if any(not isinstance(item, str) for item in (account, title, description, tags)):
            raise ValueError("Text fields must be strings")
        if visibility not in {"public", "private", "unlisted"}:
            raise ValueError("Invalid visibility")
        tag_values = ([item.strip().lstrip("#") for item in tags.split(",") if item.strip()]
                      + re.findall(r"(?<!\w)#([\w]+)", description))
        if any(not safe_tag(value) for value in tag_values):
            raise ValueError("A tag or hashtag has a blocked sensitive topic")
        release = self.release(release_id)
        if release["status"] not in EDITABLE:
            raise ValueError("Text cannot change after platform work starts")
        destination = next(d for d in release["destinations"] if d["platform"] == platform)
        if destination["status"] != "pending":
            raise ValueError("Destination text cannot change after platform work starts")
        if platform in {"facebook", "threads"}:
            instagram = next(d for d in release["destinations"] if d["platform"] == "instagram")
            title, description, tags = instagram["title"], caption_for_platform(instagram["description"], platform), instagram["tags"]
        if platform == "instagram" and any(
            d["status"] != "pending" for d in release["destinations"] if d["platform"] in {"facebook", "threads"}
        ):
            raise ValueError("Instagram text cannot change after crossposting starts")
        with self.db:
            self.db.execute("""UPDATE destination SET account=?,title=?,description=?,tags=?,visibility=?,revision_hash='',updated_at=?
                WHERE release_id=? AND platform=?""", (account.strip(), title, description, tags, visibility, utc_now().isoformat(), release_id, platform))
            if platform == "instagram":
                for follower in ("facebook", "threads"):
                    follower_description = caption_for_platform(description, follower)
                    self.db.execute("""UPDATE destination SET title=?,description=?,tags=?,revision_hash='',updated_at=?
                        WHERE release_id=? AND platform=?""", (title, follower_description, tags, utc_now().isoformat(), release_id, follower))
            self._event(release_id, platform, "text_saved", {"title": title, "description": description, "tags": tags})
        return self.release(release_id)

    def apply_shared_title(self, release_id: int, title: str) -> dict:
        """Apply one reviewed headline across a clip without changing hashtags/tags."""
        title = title.strip()
        if not title:
            raise ValueError("Enter a title")
        if len(title) > 100:
            raise ValueError("YouTube titles must be 100 characters or fewer")
        if re.search(r"(?<!\w)#[\w]+", title):
            raise ValueError("Keep hashtags in each platform's caption or description")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            if release["status"] not in EDITABLE or any(d["status"] != "pending" for d in release["destinations"]):
                raise ValueError("Titles cannot change after platform work starts")
            analysis = json.loads(release["analysis_json"] or "{}")
            if analysis.get("status") not in {"complete", "partial"}:
                raise ValueError("Wait for local analysis before applying a shared title")
            suggestions = analysis.get("suggestions") or {}
            by_platform = {d["platform"]: d for d in release["destinations"]}
            youtube = by_platform["youtube"]
            instagram = by_platform["instagram"]
            tiktok = by_platform["tiktok"]
            youtube_description = youtube["description"] or suggestions.get("youtube_description") or ""
            youtube_tags = youtube["tags"] or ", ".join(suggestions.get("youtube_tags") or [])
            instagram_caption = caption_with_title(instagram["description"] or suggestions.get("instagram_caption") or "", title)
            tiktok_caption = caption_with_title(tiktok["description"] or suggestions.get("tiktok_caption") or "", title)
            for description, tags in ((youtube_description, youtube_tags), (instagram_caption, ""), (tiktok_caption, "")):
                tag_values = ([item.strip().lstrip("#") for item in tags.split(",") if item.strip()]
                              + re.findall(r"(?<!\w)#([\w]+)", description))
                if any(not safe_tag(value) for value in tag_values):
                    raise ValueError("A tag or hashtag has a blocked sensitive topic")
            changes = {
                "youtube": (title, youtube_description, youtube_tags),
                "instagram": (instagram["title"], instagram_caption, instagram["tags"]),
                "facebook": (by_platform["facebook"]["title"], caption_for_platform(instagram_caption, "facebook"), by_platform["facebook"]["tags"]),
                "threads": (by_platform["threads"]["title"], caption_for_platform(instagram_caption, "threads"), by_platform["threads"]["tags"]),
                "tiktok": (tiktok["title"], tiktok_caption, tiktok["tags"]),
            }
            changed = []
            for platform, (new_title, description, tags) in changes.items():
                current = by_platform[platform]
                if (current["title"], current["description"], current["tags"]) == (new_title, description, tags):
                    continue
                self.db.execute("""UPDATE destination SET title=?,description=?,tags=?,revision_hash='',updated_at=?
                    WHERE id=?""", (new_title, description, tags, utc_now().isoformat(), current["id"]))
                changed.append(platform)
            if changed:
                self._event(release_id, None, "shared_title_applied", {"platforms": changed})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def apply_shared_copy(self, release_id: int, title: str, hashtags: str) -> dict:
        """Apply one title and shared hashtag set to every pending destination."""
        title = title.strip()
        if not title:
            raise ValueError("Enter a title")
        if len(title) > 100:
            raise ValueError("YouTube titles must be 100 characters or fewer")
        if re.search(r"(?<!\w)#[\w]+", title):
            raise ValueError("Put hashtags in the hashtag box")
        common = shared_hashtags(hashtags)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            if release["status"] not in EDITABLE or any(d["status"] != "pending" for d in release["destinations"]):
                raise ValueError("Copy cannot change after platform work starts")
            analysis = json.loads(release["analysis_json"] or "{}")
            suggestions = analysis.get("suggestions") or {}
            by_platform = {d["platform"]: d for d in release["destinations"]}
            youtube = by_platform["youtube"]
            original_description = youtube["description"] or suggestions.get("youtube_description") or ""
            prose = re.sub(r"(?<!\w)#[\w]+", "", original_description)
            prose = re.sub(r"[ \t]{2,}", " ", prose).strip()
            youtube_tags = youtube["tags"] or ", ".join(suggestions.get("youtube_tags") or [])
            if any(not safe_tag(item.strip().lstrip("#")) for item in youtube_tags.split(",") if item.strip()):
                raise ValueError("A YouTube tag has a blocked sensitive topic")
            changed = []
            for platform, current in by_platform.items():
                platform_hashtags = common + ([PLATFORM_HASHTAGS[platform]] if PLATFORM_HASHTAGS[platform] else [])
                hashtag_text = " ".join(platform_hashtags)
                description = " ".join(part for part in ((prose if platform == "youtube" else title), hashtag_text) if part)
                new_title = title
                new_tags = youtube_tags if platform == "youtube" else current["tags"]
                if (current["title"], current["description"], current["tags"]) == (new_title, description, new_tags):
                    continue
                self.db.execute("""UPDATE destination SET title=?,description=?,tags=?,revision_hash='',updated_at=?
                    WHERE id=?""", (new_title, description, new_tags, utc_now().isoformat(), current["id"]))
                changed.append(platform)
            if changed:
                self._event(release_id, None, "shared_copy_applied", {"platforms": changed})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def authorize(self, release_id: int, platform: str) -> dict:
        release = self.release(release_id)
        if release["status"] not in EDITABLE:
            raise ValueError("Text cannot be authorized after platform work starts")
        destination = next((d for d in release["destinations"] if d["platform"] == platform), None)
        if not destination:
            raise ValueError("Unknown destination")
        if destination["status"] != "pending":
            raise ValueError("Destination cannot be authorized after platform work starts")
        if not destination["account"] or not (destination["title"] or destination["description"]):
            raise ValueError("Set the account and exact public text first")
        revision = self._revision_hash(platform, destination["account"], destination["title"], destination["description"], destination["tags"], destination["visibility"])
        with self.db:
            self.db.execute("UPDATE destination SET revision_hash=?,updated_at=? WHERE id=?", (revision, utc_now().isoformat(), destination["id"]))
            self._event(release_id, platform, "text_authorized", {"revision_hash": revision})
        return self.release(release_id)

    def reserve_slot(self, release_id: int, now: datetime | None = None) -> dict:
        return self.reserve_batch([release_id], now)[0]

    def reserve_batch(self, release_ids: list[int], now: datetime | None = None) -> list[dict]:
        """Atomically hold successive local times for reviewed clips in queue order."""
        now = now or utc_now()
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        if not release_ids or len(set(release_ids)) != len(release_ids):
            raise ValueError("Choose distinct videos to plan")

        def slot_is_future(value: str | None) -> bool:
            if not value:
                return False
            slot = datetime.fromisoformat(value)
            if slot.tzinfo is None:
                raise ValueError("Stored posting time has no timezone")
            return slot.astimezone(timezone.utc) > now.astimezone(timezone.utc) + timedelta(minutes=1)

        def timed_attempt_started(item: dict) -> bool:
            return any(d["platform"] in TIMED_DESTINATIONS and d["status"] != "pending"
                       for d in item["destinations"])

        def validate(item: dict) -> None:
            if item["delivery_mode"] != "schedule":
                raise ValueError("Post now does not reserve a posting time")
            if item["scheduled_at"] and not slot_is_future(item["scheduled_at"]) and timed_attempt_started(item):
                raise ValueError("Posting time passed after a platform attempt; check native receipts")
            threads_started = any(d["platform"] == "threads" and d["status"] == "unconfirmed"
                                  for d in item["destinations"])
            if item["status"] not in {"draft", "reserved"} and not (
                    item["status"] == "needs_check" and threads_started):
                raise ValueError("Release cannot reserve a slot")
            if not any(d["revision_hash"] and d["platform"] in TIMED_DESTINATIONS
                       for d in item["destinations"]):
                raise ValueError("Authorize at least one scheduled destination; Threads posts now")

        for release_id in release_ids:
            release = self.release(release_id)
            validate(release)
            source = Path(release["source_path"])
            if not source.is_file() or digest(source) != release["sha256"]:
                raise ValueError("Source video changed or is missing")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            occupied = {row[0] for row in self.db.execute(
                "SELECT scheduled_at FROM release WHERE scheduled_at IS NOT NULL AND status != 'discarded'")}
            slots = self.posting_slots()
            for release_id in release_ids:
                fresh = self.release(release_id)
                validate(fresh)
                if slot_is_future(fresh["scheduled_at"]):
                    continue
                if fresh["scheduled_at"]:
                    occupied.discard(fresh["scheduled_at"])
                slot = next_slot(now, occupied, slots)
                slot_utc = slot.astimezone(timezone.utc).isoformat()
                occupied.add(slot_utc)
                status = "needs_check" if fresh["status"] == "needs_check" else "reserved"
                self.db.execute("UPDATE release SET status=?,scheduled_at=?,updated_at=? WHERE id=?",
                                (status, slot_utc, utc_now().isoformat(), release_id))
                self._event(release_id, None, "slot_replanned" if fresh["scheduled_at"] else "slot_reserved",
                            {"slot": slot.isoformat(), "previous": fresh["scheduled_at"]})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return [self.release(release_id) for release_id in release_ids]

    def mark_unconfirmed(self, release_id: int, platform: str, *, expected_revision: str | None = None) -> dict:
        release = self.release(release_id)
        def can_start(item: dict) -> bool:
            return (item["status"] in {"reserved", "scheduled", "uploading", "partial", "needs_check"}
                    or (item["status"] == "draft" and
                        (platform == "threads" or item["delivery_mode"] == "post_now")))
        if not can_start(release):
            raise ValueError("No platform action is in progress")
        if platform in TIMED_DESTINATIONS and release["delivery_mode"] == "schedule" and not release["scheduled_at"]:
            raise ValueError("Reserve a posting time before scheduling this destination")
        dest = next((d for d in release["destinations"] if d["platform"] == platform), None)
        if not dest or not dest["revision_hash"]:
            raise ValueError("No authorized text for destination")
        if dest["status"] != "pending":
            raise ValueError("Destination already attempted; check the account before retrying")
        if expected_revision is not None and dest["revision_hash"] != expected_revision:
            raise ValueError("Destination text changed after phone preparation")
        source = Path(release["source_path"])
        if not source.is_file() or digest(source) != release["sha256"]:
            raise ValueError("Source video changed or is missing")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            fresh = self.release(release_id)
            dest = next((d for d in fresh["destinations"] if d["platform"] == platform), None)
            if not dest or dest["status"] != "pending" or not dest["revision_hash"]:
                raise ValueError("Destination already attempted or no longer authorized")
            if expected_revision is not None and dest["revision_hash"] != expected_revision:
                raise ValueError("Destination text changed after phone preparation")
            if not can_start(fresh):
                raise ValueError("No platform action is in progress")
            if platform in TIMED_DESTINATIONS and fresh["delivery_mode"] == "schedule" and not fresh["scheduled_at"]:
                raise ValueError("Reserve a posting time before scheduling this destination")
            self.db.execute("UPDATE destination SET status='unconfirmed',updated_at=? WHERE id=?", (utc_now().isoformat(), dest["id"]))
            self.db.execute("UPDATE release SET status='needs_check',updated_at=? WHERE id=?", (utc_now().isoformat(), release_id))
            self._event(release_id, platform, "unconfirmed", {})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def record_receipt(self, release_id: int, platform: str, url: str) -> dict:
        raise ValueError("Provider receipt verification is not connected yet")

    def record_observed_schedule(self, release_id: int, platform: str, *, account: str,
                                 native_rows: list[dict], device_time_zone: str,
                                 screen_info: dict, evidence_image: Path) -> dict:
        """Record an Instagram schedule only after its native row and cover match.

        The phone runner supplies the active account and verified iPhone time
        zone. Keep its screenshot under the ignored local state directory.
        """
        if platform != "instagram":
            raise ValueError("Native schedule verification is not connected for this destination")
        evidence_image = evidence_image.resolve(strict=True)
        if (not evidence_image.is_relative_to(self.path.parent.resolve())
                or not evidence_image.is_file() or evidence_image.stat().st_size == 0):
            raise ValueError("Scheduled-content evidence is empty")
        from PIL import Image
        from .instagram_schedule import opening_frames, verified_scheduled_reel
        from .phone_ui import PhoneLayout
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            destination = next(d for d in release["destinations"] if d["platform"] == platform)
            if destination["status"] != "unconfirmed" or release["delivery_mode"] != "schedule":
                raise ValueError("No uncertain native schedule to verify")
            if destination["account"].casefold() != account.casefold() or not release["scheduled_at"]:
                raise ValueError("Native scheduled-content account or time does not match this release")
            revision = self._revision_hash(platform, destination["account"], destination["title"],
                                           destination["description"], destination["tags"], destination["visibility"])
            if destination["revision_hash"] != revision:
                raise ValueError("Approved text revision changed")
            source = Path(release["source_path"])
            if not source.is_file() or digest(source) != release["sha256"]:
                raise ValueError("Source video changed or is missing")
            layout = PhoneLayout.from_info(screen_info)
            with Image.open(evidence_image) as screenshot:
                if (abs(screenshot.width / screenshot.height - layout.width / layout.height) > 0.01):
                    raise ValueError("Scheduled-content screenshot does not match the iPhone screen")
                match = verified_scheduled_reel(native_rows, screenshot, opening_frames(source),
                                                destination["description"], release["scheduled_at"],
                                                device_time_zone, layout)
            now = utc_now().isoformat()
            self.db.execute("UPDATE destination SET status='scheduled',updated_at=? WHERE id=?", (now, destination["id"]))
            remaining = [d for d in release["destinations"] if d["id"] != destination["id"]
                         and d["revision_hash"] and d["status"] != "scheduled"]
            self.db.execute("UPDATE release SET status=?,updated_at=? WHERE id=?",
                            ("partial" if remaining else "scheduled", now, release_id))
            self._event(release_id, platform, "native_schedule_observed", {
                "account": account, "caption": destination["description"],
                "scheduled_at": release["scheduled_at"], "cover_difference": match["difference"],
                "evidence_sha256": digest(evidence_image),
                "verification": "native_caption_time_first_frame",
            })
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def discard(self, release_id: int) -> dict:
        release = self.release(release_id)
        if release["status"] not in {"draft", "reserved"}:
            raise ValueError("Cannot discard an in-progress or published release")
        if any(d["status"] != "pending" for d in release["destinations"]):
            raise ValueError("A destination has an active status")
        with self.db:
            self.db.execute("UPDATE release SET status='discarded',updated_at=? WHERE id=?", (utc_now().isoformat(), release_id))
            self._event(release_id, None, "discarded", {})
        return self.release(release_id)
