from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from .analyze import safe_tag
from .accounts import PLATFORMS
from .timezones import pc_time_zone, zone

DESTINATIONS = PLATFORMS
TIMED_DESTINATIONS = frozenset(DESTINATIONS)
EDITABLE = ("draft", "reserved")
DEFAULT_SLOTS = ("10:00", "19:00")
# Phone checks the operator can switch off. Account and exact-file checks are
# safety gates, not preferences, so they are deliberately absent here.
PHONE_CHECK_DEFAULTS = {
    # One Setting for the phone's state during a run: Low Power Mode off and Auto-Lock at Never
    # (the phone locked mid-export, 2026-10-02), Do Not Disturb on, rotation locked. Each is put
    # back afterwards only if the run changed it. video_drop/phone_focus.prepare_phone.
    "preparePhone": True,
    "youtubeQualityEveryUpload": False,
    "inspectPhoneOnOpen": True,
    # Once Instagram has its native receipt, move the Edits project its 4K export left behind
    # to Edits' Trash (recoverable there). Posts save nothing to Photos (measured 2026-10-01).
    # video_drop/edits_cleanup.py + scripts/phone_cleanup.py.
    "removeAfterPost": True,
    # Post now: Instagram's one upload turns on these "Also share on…" switches.
    "crosspostFacebook": True,
    "crosspostThreads": True,
    # Post now with the Threads crosspost off: post Threads on its own in the Threads app.
    "threadsSeparatePost": False,
    # Post now starts YouTube, then Instagram, then TikTok, then a separate Threads post.
    "postNowInOrder": True,
    # Open OneDrive videos through the Files app like every other provider, instead of the OneDrive app.
    "filesAppForOneDrive": False,
    # After a final tap, look at the app's own profile a few times in the next hour (read-only)
    # and record the receipt the phone shows (video_drop/receipt_sweep.py).
    "readReceipts": True,
}
DELIVERY_MODES = ("schedule", "post_now")
PLATFORM_HASHTAGS = {"youtube": "#shorts", "instagram": "#reels", "facebook": "#reels", "threads": "", "tiktok": "#fyp"}
# Apps with no native scheduler on the operator's account: in Schedule mode this app posts
# them itself at the slot, and only inside SLOT_GRACE after it. A later start is a missed slot.
APP_POSTED_DESTINATIONS = frozenset({"tiktok"})
SLOT_GRACE = timedelta(minutes=15)
POST_ORDER = ("youtube", "instagram", "tiktok", "facebook", "threads")
# Destinations Instagram's one upload can carry through its "Also share on…" switches, per
# delivery mode. Instagram's scheduler turns the Threads crosspost off, so a scheduled video's
# Threads post is its own native schedule in the Threads app. The crosspost settings pick which
# are on; those have no final tap of their own, because Instagram's final tap claims them.
CROSSPOSTED_BY_INSTAGRAM = {"post_now": ("facebook", "threads"), "schedule": ("facebook",)}
CROSSPOST_SETTINGS = {"facebook": "crosspostFacebook", "threads": "crosspostThreads"}
RECEIPT_CHOICES = ("posted", "scheduled")
# A user saying "I checked it on the phone", or a hand-started post, is a hand fix.
MANUAL_EVENTS = frozenset({"receipt_manual", "manual_intervention"})
# Receipts the app read back from the native app itself.
APP_RECEIPT_EVENTS = frozenset({"native_schedule_observed", "native_post_observed"})
# Receipts scripts/phone_receipts.py reads back from the native apps and hands to
# Store.record_receipt as an evidence JSON, keyed by (platform, choice). Each value is the
# evidence's "verification" (video_drop/receipts.py). Facebook's crosspost is confirmed the
# same way once its Meta Business Suite screens are recorded (receipts.ROUTES): a carried
# crosspost keeps its own receipt, in Post now and in Schedule.
NATIVE_RECEIPT_VERIFICATIONS = {
    ("instagram", "posted"): frozenset({"instagram_profile_count_newest_tile_first_frame"}),
    ("threads", "posted"): frozenset({"threads_profile_newest_caption"}),
    ("facebook", "posted"): frozenset({"facebook_page_reels_newest_caption_first_frame"}),
    ("facebook", "scheduled"): frozenset({"facebook_page_reels_scheduled_caption_slot"}),
}
STREAK_GOAL = 20
RECEIPT_WINDOW = timedelta(hours=1)


def threads_post_refusal(release: dict) -> str | None:
    """Why this release gets no separate, immediate Threads post, or None when it may.

    ``release`` comes from Store.release, which adds instagramCrossposts and threadsSeparatePost.
    """
    if release["threadsSeparatePost"]:
        return None
    if release["delivery_mode"] == "post_now":
        if "threads" in release["instagramCrossposts"]:
            return ("Post now shares Threads through Instagram's “Also share on…” Threads switch in the same "
                    "upload; there is no separate Threads post. Nothing was posted.")
        return ("The Threads crosspost is off and “Post Threads separately” is off in Settings, so this app "
                "does not post Threads. Nothing was posted.")
    return ("A scheduled video's Threads post is scheduled natively in the Threads app (its + composer with "
            "the clip in Photos, then Schedule), because Instagram's scheduler turns its Threads crosspost off. "
            "Native Threads scheduling is not built yet, and Threads is never posted immediately. "
            "Nothing was posted.")


def crosspost_refusal(platform: str, release: dict) -> str | None:
    """A reason this destination cannot be claimed on its own, or None when it can."""
    if platform == "threads" and release["delivery_mode"] == "post_now":
        return threads_post_refusal(release)
    if platform in release["instagramCrossposts"]:
        return (f"{platform.title()} goes out with Instagram's upload through its “Also share on…” switch; "
                "it has no final tap of its own. Nothing was posted.")
    return None


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


def next_slot(now: datetime, occupied: set[str], slots: tuple[str, ...] = DEFAULT_SLOTS,
              zone_info: tzinfo | None = None) -> datetime:
    """The first free posting time after ``now``, as wall-clock time in ``zone_info`` (default: this PC's zone)."""
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    slots = validate_slots(slots)
    zone_info = zone_info or pc_time_zone()
    local = now.astimezone(zone_info)
    for day in range(366):
        date = local.date() + timedelta(days=day)
        for time in slots:
            hour, minute = map(int, time.split(":"))
            slot = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone_info)
            if slot.astimezone(timezone.utc).astimezone(zone_info).replace(tzinfo=None) != slot.replace(tzinfo=None):
                continue
            slot_utc = slot.astimezone(timezone.utc)
            if slot_utc > now.astimezone(timezone.utc) + timedelta(minutes=1) and slot_utc.isoformat() not in occupied:
                return slot
    raise ValueError("no_video_slot_available")


def chosen_slot(at: datetime, now: datetime, occupied: set[str], slots: tuple[str, ...],
                zone_info: tzinfo | None = None) -> datetime:
    """Validate one operator-chosen posting time against the configured daily times."""
    local = at.astimezone(zone_info or pc_time_zone())
    if local.strftime("%H:%M") not in validate_slots(slots) or local.second or local.microsecond:
        raise ValueError(f"{local:%I:%M %p} is not one of the posting times")
    slot_utc = local.astimezone(timezone.utc)
    if slot_utc <= now.astimezone(timezone.utc) + timedelta(minutes=1):
        raise ValueError("Choose a posting time in the future")
    if slot_utc.isoformat() in occupied:
        raise ValueError("Another video already has that posting time")
    return local


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
                raise ValueError("This exact video is already in Auto iPhone Uploader")
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
        checks = self.phone_checks()
        result["instagramCrossposts"] = self._instagram_crossposts(result, checks)
        result["threadsSeparatePost"] = (result["delivery_mode"] == "post_now" and checks["threadsSeparatePost"]
                                         and "threads" not in result["instagramCrossposts"])
        return result

    def _instagram_crossposts(self, release: dict, checks: dict) -> list[str]:
        """The "Also share on…" switches Instagram's upload turns on for this release.

        Once Instagram's final tap is recorded this is what that tap carried, so a later
        settings change never rewrites history. Before it, the crosspost settings decide.
        """
        instagram = next((d for d in release["destinations"] if d["platform"] == "instagram"), None)
        if instagram and instagram["status"] != "pending":
            row = self.db.execute("""SELECT json_extract(payload,'$.crossposts') FROM event WHERE release_id=?
                AND platform='instagram' AND kind='unconfirmed' ORDER BY id DESC LIMIT 1""", (release["id"],)).fetchone()
            if row and row[0] is not None:
                return json.loads(row[0])
        return [platform for platform in CROSSPOSTED_BY_INSTAGRAM.get(release["delivery_mode"], ())
                if checks[CROSSPOST_SETTINGS[platform]]]

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

    def time_zone_setting(self) -> str:
        """The chosen IANA zone, or "" to follow this PC's own time zone."""
        row = self.db.execute("SELECT value FROM setting WHERE key='time_zone'").fetchone()
        return json.loads(row[0]) if row else ""

    def set_time_zone(self, name: str) -> str:
        """Override the posting-time zone; "" goes back to this PC's zone. Held slots keep their moment."""
        if not isinstance(name, str):
            raise ValueError("Choose a time zone")
        with self.db:
            if name.strip():
                self.db.execute("INSERT INTO setting(key,value) VALUES('time_zone',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (json.dumps(zone(name).key),))
            else:
                self.db.execute("DELETE FROM setting WHERE key='time_zone'")
        return self.time_zone_setting()

    def time_zone(self) -> tzinfo:
        """The zone posting times are read in: the setting if it is still valid, else this PC's zone."""
        name = self.time_zone_setting()
        try:
            return zone(name) if name else pc_time_zone()
        except ValueError:
            return pc_time_zone()

    def phone_checks(self) -> dict:
        row = self.db.execute("SELECT value FROM setting WHERE key='phone_checks'").fetchone()
        saved = json.loads(row[0]) if row else {}
        return {key: saved.get(key, default) if isinstance(saved.get(key), bool) else default
                for key, default in PHONE_CHECK_DEFAULTS.items()}

    def set_phone_checks(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Expected phone check settings")
        unknown = set(changes) - set(PHONE_CHECK_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown phone check: {sorted(unknown)[0]}")
        if not all(isinstance(value, bool) for value in changes.values()):
            raise ValueError("Phone checks must be on or off")
        checks = {**self.phone_checks(), **changes}
        with self.db:
            self.db.execute("INSERT INTO setting(key,value) VALUES('phone_checks',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(checks),))
        return checks

    def setup_completed_at(self) -> str:
        row = self.db.execute("SELECT value FROM setting WHERE key='setup_completed_at'").fetchone()
        return json.loads(row[0]) if row else ""

    def complete_setup(self) -> str:
        """Record the first time every required setup item passed; later passes keep that time."""
        with self.db:
            self.db.execute("INSERT INTO setting(key,value) VALUES('setup_completed_at',?) ON CONFLICT(key) DO NOTHING",
                            (json.dumps(utc_now().isoformat()),))
        return self.setup_completed_at()

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
                # YouTube leads with its hashtags, then one plain line about the clip.
                description = ("\n".join(part for part in (hashtag_text, prose) if part) if platform == "youtube"
                               else " ".join(part for part in (title, hashtag_text) if part))
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

    def reserve_slot(self, release_id: int, now: datetime | None = None, at: datetime | None = None) -> dict:
        return self.reserve_batch([release_id], now, at=at)[0]

    def reserve_batch(self, release_ids: list[int], now: datetime | None = None, *,
                      at: datetime | None = None) -> list[dict]:
        """Atomically hold successive local times for reviewed clips in queue order.

        `at` picks one specific configured posting time for a single clip instead of the next free one.
        It also moves a clip's future held time, but only while no destination has been attempted.
        """
        now = now or utc_now()
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        if not release_ids or len(set(release_ids)) != len(release_ids):
            raise ValueError("Choose distinct videos to plan")
        if at is not None and (len(release_ids) != 1 or at.tzinfo is None):
            raise ValueError("A chosen posting time needs one video and a timezone")

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
                raise ValueError("Authorize the text for at least one destination first")

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
            zone_info = self.time_zone()
            for release_id in release_ids:
                fresh = self.release(release_id)
                validate(fresh)
                if slot_is_future(fresh["scheduled_at"]):
                    if at is None or datetime.fromisoformat(fresh["scheduled_at"]) == at:
                        continue
                    if any(d["status"] != "pending" for d in fresh["destinations"]):
                        raise ValueError("The posting time cannot move after an app has the video; check its receipts")
                if fresh["scheduled_at"]:
                    occupied.discard(fresh["scheduled_at"])
                slot = (next_slot(now, occupied, slots, zone_info) if at is None
                        else chosen_slot(at, now, occupied, slots, zone_info))
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
        """Record that a final tap is about to happen, so it is never replayed.

        Instagram's final tap also claims the destinations its "Also share on…" switches
        carry (the release's instagramCrossposts); each keeps its own receipt. Those
        destinations cannot be claimed on their own. In Post now every carried destination
        must be approved and untouched, since its switch is on.
        """
        release = self.release(release_id)
        def can_start(item: dict) -> bool:
            return (item["status"] in {"reserved", "scheduled", "uploading", "partial", "needs_check"}
                    or (item["status"] == "draft" and item["delivery_mode"] == "post_now"))
        if refusal := crosspost_refusal(platform, release):
            raise ValueError(refusal)
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
            if refusal := crosspost_refusal(platform, fresh):
                raise ValueError(refusal)
            if platform in TIMED_DESTINATIONS and fresh["delivery_mode"] == "schedule" and not fresh["scheduled_at"]:
                raise ValueError("Reserve a posting time before scheduling this destination")
            self.db.execute("UPDATE destination SET status='unconfirmed',updated_at=? WHERE id=?", (utc_now().isoformat(), dest["id"]))
            self.db.execute("UPDATE release SET status='needs_check',updated_at=? WHERE id=?", (utc_now().isoformat(), release_id))
            carried = fresh["instagramCrossposts"] if platform == "instagram" else []
            self._event(release_id, platform, "unconfirmed", {"crossposts": carried} if platform == "instagram" else {})
            if platform == "instagram":
                for follower in fresh["destinations"]:
                    if follower["platform"] not in carried:
                        continue
                    ready = follower["status"] == "pending" and follower["revision_hash"]
                    if fresh["delivery_mode"] == "post_now" and not ready:
                        # Post now always crossposts, so its switches are on: each must be claimable.
                        raise ValueError(f"Post now crossposts to {follower['platform'].title()}; approve its text "
                                         "and check it has no earlier attempt first. Nothing was posted.")
                    if ready:
                        self.db.execute("UPDATE destination SET status='unconfirmed',updated_at=? WHERE id=?",
                                        (utc_now().isoformat(), follower["id"]))
                        self._event(release_id, follower["platform"], "unconfirmed", {"via": "instagram_crosspost"})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def record_receipt(self, release_id: int, platform: str, url: str) -> dict:
        """Record a receipt the phone read back from the native app itself.

        ``url`` is the ``file:`` URI of scripts/phone_receipts.py's evidence JSON, kept under
        the state directory. Its release, platform, account, choice and verification type must
        match an unconfirmed destination (a crossposted Facebook or Threads included). The
        destination becomes posted or scheduled like a manual receipt, but the event is an app
        receipt (native_post_observed, or native_schedule_observed for a scheduled one), so it
        counts toward the unattended streak.
        """
        from urllib.parse import urlsplit
        from urllib.request import url2pathname
        if not any(key[0] == platform for key in NATIVE_RECEIPT_VERIFICATIONS):
            raise ValueError(f"Native receipt verification is not connected for {platform} yet")
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.scheme != "file" or parts.netloc not in {"", "localhost"}:
            raise ValueError("Receipt evidence must be the file: URI of the receipt runner's evidence JSON")
        try:
            evidence_path = Path(url2pathname(parts.path)).resolve(strict=True)
        except OSError as exc:
            raise ValueError("Receipt evidence file is missing") from exc
        if (not evidence_path.is_relative_to(self.path.parent.resolve()) or evidence_path.suffix != ".json"
                or not evidence_path.is_file() or evidence_path.stat().st_size == 0):
            raise ValueError("Receipt evidence must be a JSON file under this app's state folder")
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Receipt evidence is not readable JSON") from exc
        if not isinstance(evidence, dict):
            raise ValueError("Receipt evidence must be a JSON object")
        if evidence.get("releaseId") != release_id or evidence.get("platform") != platform:
            raise ValueError("Receipt evidence is for another release or app")
        choice = evidence.get("choice")
        if choice not in RECEIPT_CHOICES:
            raise ValueError("Receipt evidence must say posted or scheduled")
        verification = evidence.get("verification")
        if verification not in NATIVE_RECEIPT_VERIFICATIONS.get((platform, choice), ()):
            raise ValueError(f"Receipt evidence has no accepted {platform} {choice} verification")
        screenshot = evidence.get("screenshot")
        if screenshot is not None:
            shot = evidence_path.parent / str(screenshot)
            if Path(str(screenshot)).name != screenshot or not shot.is_file() or shot.stat().st_size == 0:
                raise ValueError("Receipt evidence names a screenshot that is not beside it")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            destination = next((d for d in release["destinations"] if d["platform"] == platform), None)
            if destination is None:
                raise ValueError("Unknown destination")
            if destination["status"] != "unconfirmed":
                raise ValueError("Only a destination with a recorded final tap can take a native receipt")
            if choice == "scheduled" and release["delivery_mode"] != "schedule":
                raise ValueError("A Post now video cannot have a scheduled receipt")
            def handle(value: object) -> str:
                return str(value or "").strip().lstrip("@").casefold()
            if not handle(evidence.get("account")) or handle(evidence.get("account")) != handle(destination["account"]):
                raise ValueError("Receipt evidence account does not match this destination's account")
            now = utc_now().isoformat()
            self.db.execute("UPDATE destination SET status=?,updated_at=? WHERE id=?", (choice, now, destination["id"]))
            included = [d for d in self._included(release) if d["id"] != destination["id"]]
            included.append({**destination, "status": choice})
            remaining = [d for d in included if d["status"] not in RECEIPT_CHOICES]
            status = ("partial" if remaining else
                      "posted" if all(d["status"] == "posted" for d in included) else "scheduled")
            self.db.execute("UPDATE release SET status=?,updated_at=? WHERE id=?", (status, now, release_id))
            self._event(release_id, platform, "native_post_observed" if choice == "posted" else "native_schedule_observed", {
                "choice": choice, "account": evidence["account"], "verification": verification,
                "via": "instagram_crosspost" if platform in release["instagramCrossposts"] else "own_post",
                "evidence": evidence_path.name, "evidence_sha256": digest(evidence_path), "confirmed_at": now,
            })
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

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
        from .instagram_schedule import first_frame, verified_scheduled_reel
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
                match = verified_scheduled_reel(native_rows, screenshot, first_frame(source),
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

    @staticmethod
    def _included(release: dict) -> list[dict]:
        """Destinations this release actually targets: approved text, or work already started."""
        return [d for d in release["destinations"] if d["revision_hash"] or d["status"] != "pending"]

    def record_manual_receipt(self, release_id: int, platform: str, choice: str) -> dict:
        """Record the user's own phone check as the receipt. It is never a native verification."""
        if choice not in RECEIPT_CHOICES:
            raise ValueError("Choose Posted or Scheduled")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            release = self.release(release_id)
            if release["status"] == "discarded":
                raise ValueError("This video was discarded")
            destination = next((d for d in release["destinations"] if d["platform"] == platform), None)
            if not destination:
                raise ValueError("Unknown destination")
            if destination["status"] not in {"pending", "unconfirmed"}:
                raise ValueError("This destination already has a receipt")
            now = utc_now().isoformat()
            self.db.execute("UPDATE destination SET status=?,updated_at=? WHERE id=?", (choice, now, destination["id"]))
            included = [d for d in self._included(release) if d["id"] != destination["id"]]
            included.append({**destination, "status": choice})
            remaining = [d for d in included if d["status"] not in RECEIPT_CHOICES]
            status = ("partial" if remaining else
                      "posted" if all(d["status"] == "posted" for d in included) else "scheduled")
            self.db.execute("UPDATE release SET status=?,updated_at=? WHERE id=?", (status, now, release_id))
            self._event(release_id, platform, "receipt_manual", {
                "choice": choice, "previous": destination["status"], "confirmed_at": now,
                "verification": "user_checked_phone",
            })
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.release(release_id)

    def record_manual_intervention(self, release_id: int, platform: str, action: str) -> None:
        with self.db:
            self._event(release_id, platform, "manual_intervention", {"action": action})

    def slot_post_releases(self) -> list[dict]:
        """Schedule-mode releases that hold a slot and may still need the app to post."""
        rows = self.db.execute("""SELECT id FROM release WHERE delivery_mode='schedule' AND scheduled_at IS NOT NULL
            AND status IN ('reserved','scheduled','uploading','partial','needs_check') ORDER BY scheduled_at, id""")
        return [self.release(row[0]) for row in rows.fetchall()]

    def slot_post_marks(self) -> dict[tuple[int, str, str], set[str]]:
        """Persisted scheduler marks, keyed by (release, platform, slot) so a replanned slot starts fresh."""
        marks: dict[tuple[int, str, str], set[str]] = {}
        for row in self.db.execute("""SELECT release_id, platform, kind, json_extract(payload,'$.slot') FROM event
                WHERE kind IN ('slot_armed','slot_post_queued','slot_post_failed')"""):
            marks.setdefault((row[0], row[1], row[3]), set()).add(row[2])
        return marks

    def mark_slot_post(self, release_id: int, platform: str, slot: str, kind: str, **details) -> bool:
        """Write one scheduler mark once. Returns False if that mark already exists."""
        if kind not in {"slot_armed", "slot_post_queued", "slot_post_failed"}:
            raise ValueError("Unknown slot mark")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            exists = self.db.execute("""SELECT 1 FROM event WHERE release_id=? AND platform=? AND kind=?
                AND json_extract(payload,'$.slot')=?""", (release_id, platform, kind, slot)).fetchone()
            if not exists:
                self._event(release_id, platform, kind, {"slot": slot, **details})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return not exists

    def unattended_streak(self, now: datetime | None = None) -> dict:
        """Consecutive most-recent Schedule releases that reached every app with no hand fix.

        Releases still inside their receipt window are skipped. A manual receipt or intervention,
        a missed slot, a receipt the app did not read itself, or a receipt still missing an hour
        after the slot ends the streak. Post now releases are started by hand and are not counted.
        """
        now = now or utc_now()
        kinds: dict[int, set[tuple[str | None, str]]] = {}
        for row in self.db.execute("SELECT release_id, platform, kind FROM event"):
            kinds.setdefault(row[0], set()).add((row[1], row[2]))
        count = 0
        rows = self.db.execute("""SELECT id FROM release WHERE delivery_mode='schedule' AND status NOT IN ('draft','discarded')
            ORDER BY COALESCE(scheduled_at, created_at) DESC, id DESC""").fetchall()
        for row in rows:
            release = self.release(row[0])
            events = kinds.get(release["id"], set())
            included = self._included(release)
            if not included:
                continue
            if any(kind in MANUAL_EVENTS for _, kind in events):
                break
            if all(d["status"] in RECEIPT_CHOICES for d in included):
                if all(any((d["platform"], kind) in events for kind in APP_RECEIPT_EVENTS) for d in included):
                    count += 1
                    continue
                break
            slot = datetime.fromisoformat(release["scheduled_at"]) if release["scheduled_at"] else None
            if slot is None or now < slot + SLOT_GRACE:
                continue
            if now >= slot + RECEIPT_WINDOW or any(d["status"] == "pending" for d in included):
                break
        return {"count": count, "goal": STREAK_GOAL}

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
