import tempfile
import unittest
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from video_drop.core import Store, next_slot
from video_drop.analyze import analyze, generate, safe_tag
from video_drop.accounts import load_targets

NY = ZoneInfo("America/New_York")


class VideoDropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite")
        self.video = self.root / "finished.mp4"
        self.video.write_bytes(b"video fixture")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_chosen_posting_time_must_be_a_free_future_configured_slot(self):
        release_id = self.store.import_file(self.video)["id"]
        self.store.save_text(release_id, "youtube", "@examplechannel", "Title", "Description", "tag")
        self.store.authorize(release_id, "youtube")
        now = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)
        tomorrow_ten = datetime(2026, 9, 30, 10, 0, tzinfo=NY)
        for bad, reason in ((datetime(2026, 9, 30, 11, 0, tzinfo=NY), "not one of the posting times"),
                            (datetime(2026, 9, 29, 10, 0, tzinfo=NY), "future"),
                            (datetime(2026, 9, 30, 10, 0), "timezone")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.store.reserve_slot(release_id, now, at=bad)
        planned = self.store.reserve_slot(release_id, now, at=tomorrow_ten)
        self.assertEqual(planned["scheduled_at"], "2026-09-30T14:00:00+00:00")
        (self.root / "other.mp4").write_bytes(b"other video")
        other = self.store.import_file(self.root / "other.mp4")["id"]
        self.store.save_text(other, "youtube", "@examplechannel", "Other", "Description", "tag")
        self.store.authorize(other, "youtube")
        with self.assertRaisesRegex(ValueError, "already has that posting time"):
            self.store.reserve_slot(other, now, at=tomorrow_ten)

    def test_phone_checks_default_on_and_persist(self):
        self.assertEqual(self.store.phone_checks(), {"doNotDisturb": True, "youtubeQualityEveryUpload": False,
                                                     "inspectPhoneOnOpen": True, "removeAfterPost": True,
                                                     "crosspostFacebook": True, "crosspostThreads": True,
                                                     "threadsSeparatePost": False, "postNowInOrder": True,
                                                     "filesAppForOneDrive": False})
        self.store.set_phone_checks({"doNotDisturb": False})
        with Store(self.root / "state.sqlite") as reopened:
            self.assertFalse(reopened.phone_checks()["doNotDisturb"])
            self.assertTrue(reopened.phone_checks()["inspectPhoneOnOpen"])
        for bad in ({}, {"accountCheck": False}, {"doNotDisturb": "no"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.set_phone_checks(bad)

    def test_slot_sequence_across_evening_and_dst(self):
        now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)  # 4 PM ET
        first = next_slot(now, set())
        second = next_slot(now, {first.astimezone(timezone.utc).isoformat()})
        self.assertEqual((first.hour, second.hour), (19, 10))
        self.assertEqual((second.date() - first.date()).days, 1)
        before_dst = next_slot(datetime(2026, 10, 31, 21, tzinfo=timezone.utc), set())
        self.assertEqual(before_dst.hour, 19)
        after_fall_back = next_slot(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), set(), ("01:45",))
        self.assertEqual(after_fall_back.date().day, 2)
        spring = next_slot(datetime(2027, 3, 14, 6, tzinfo=timezone.utc), set(), ("02:30",))
        self.assertEqual(spring.date().day, 15)

    def test_duplicate_and_multiple_drafts_keep_order(self):
        release = self.store.import_file(self.video)
        self.assertNotIn("x", {item["platform"] for item in release["destinations"]})
        self.assertTrue(all(not item["account"] for item in release["destinations"]))
        with self.assertRaisesRegex(ValueError, "exact video"):
            self.store.import_file(self.video)
        other = self.root / "other.mp4"
        other.write_bytes(b"other video")
        following = self.store.import_file(other)
        self.assertEqual([r["id"] for r in self.store.drafts()], [release["id"], following["id"]])
        self.store.save_text(release["id"], "youtube", "@examplechannel", "Original title", "Description #clip", "game")
        self.store.authorize(release["id"], "youtube")
        reserved = self.store.reserve_slot(release["id"], datetime(2026, 9, 27, 20, tzinfo=timezone.utc))
        repeated = self.store.reserve_slot(release["id"], datetime(2026, 9, 27, 21, tzinfo=timezone.utc))
        self.assertEqual(reserved["scheduled_at"], repeated["scheduled_at"])
        self.assertEqual(reserved["status"], "reserved")
        self.assertEqual(self.store.current()["id"], following["id"])

    def test_existing_single_draft_database_upgrades_for_batch(self):
        self.store.db.execute("CREATE UNIQUE INDEX one_draft_release ON release((1)) WHERE status='draft'")
        self.store.db.commit()
        first = self.store.import_file(self.video)
        self.store.close()
        self.store = Store(self.root / "state.sqlite")
        other = self.root / "batch.mp4"
        other.write_bytes(b"another export")
        second = self.store.import_file(other)
        self.assertEqual([r["id"] for r in self.store.drafts()], [first["id"], second["id"]])

    def test_custom_posting_times_plan_four_consecutive_videos(self):
        self.assertEqual(self.store.posting_slots(), ("10:00", "19:00"))
        self.assertEqual(self.store.set_posting_slots(["19:00", "10:00"]), ("10:00", "19:00"))
        with self.assertRaisesRegex(ValueError, "one to five"):
            self.store.set_posting_slots([])
        with self.assertRaisesRegex(ValueError, "unique"):
            self.store.set_posting_slots(["10:00", "10:00"])
        now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)  # 4 PM ET
        ids = [self.store.import_file(self.video)["id"]]
        for index in range(3):
            path = self.root / f"clip-{index}.mp4"
            path.write_bytes(f"clip {index}".encode())
            ids.append(self.store.import_file(path)["id"])
        planned = []
        for release_id in ids:
            self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
            self.store.authorize(release_id, "youtube")
            planned.append(self.store.reserve_slot(release_id, now)["scheduled_at"])
        local = [datetime.fromisoformat(value).astimezone(next_slot(now, set()).tzinfo) for value in planned]
        self.assertEqual([(d.day, d.hour) for d in local], [(27, 19), (28, 10), (28, 19), (29, 10)])

    def test_delivery_choice_defaults_to_schedule_and_persists(self):
        release_id = self.store.import_file(self.video)["id"]
        self.assertEqual(self.store.release(release_id)["delivery_mode"], "schedule")
        self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
        self.store.authorize(release_id, "youtube")
        self.assertEqual(self.store.set_delivery_mode(release_id, "post_now")["delivery_mode"], "post_now")
        with self.assertRaisesRegex(ValueError, "does not reserve"):
            self.store.reserve_slot(release_id)
        with Store(self.root / "state.sqlite") as reopened:
            self.assertEqual(reopened.release(release_id)["delivery_mode"], "post_now")
        self.assertEqual(self.store.set_delivery_mode(release_id, "schedule")["delivery_mode"], "schedule")
        self.store.reserve_slot(release_id)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            self.store.set_delivery_mode(release_id, "post_now")
        with self.assertRaisesRegex(ValueError, "Choose Schedule"):
            self.store.set_delivery_mode(release_id, "later")

    def test_post_now_attempt_does_not_reserve_a_time(self):
        release_id = self.store.import_file(self.video)["id"]
        self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
        revision = self.store.authorize(release_id, "youtube")["destinations"][0]["revision_hash"]
        with self.assertRaisesRegex(ValueError, "No platform action"):
            self.store.mark_unconfirmed(release_id, "youtube", expected_revision=revision)
        self.store.set_delivery_mode(release_id, "post_now")
        started = self.store.mark_unconfirmed(release_id, "youtube", expected_revision=revision)
        self.assertIsNone(started["scheduled_at"])
        self.assertEqual(started["status"], "needs_check")
        self.assertEqual(started["destinations"][0]["status"], "unconfirmed")
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.store.mark_unconfirmed(release_id, "youtube", expected_revision=revision)

    def test_existing_database_gets_schedule_default(self):
        legacy = self.root / "legacy.sqlite"
        db = sqlite3.connect(legacy)
        try:
            db.execute("""CREATE TABLE release (
                id INTEGER PRIMARY KEY, homebase_id INTEGER UNIQUE,
                source_path TEXT NOT NULL, source_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft', scheduled_at TEXT,
                analysis_json TEXT NOT NULL DEFAULT '{}', legacy_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.commit()
        finally:
            db.close()
        with Store(legacy) as upgraded:
            release = upgraded.import_file(self.video)
            self.assertEqual(release["delivery_mode"], "schedule")

    def test_parallel_slot_requests_take_distinct_times(self):
        first = self.store.import_file(self.video)["id"]
        other = self.root / "other.mp4"
        other.write_bytes(b"another video")
        second = self.store.import_file(other)["id"]
        for release_id in (first, second):
            self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
            self.store.authorize(release_id, "youtube")

        def plan(release_id):
            with Store(self.root / "state.sqlite") as store:
                return store.reserve_slot(release_id, datetime(2026, 9, 27, 20, tzinfo=timezone.utc))["scheduled_at"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(plan, (first, second)))
        self.assertEqual(len(set(values)), 2)

    def test_batch_reserves_four_reviewed_clips_in_order(self):
        release_ids = []
        for index in range(4):
            clip = self.root / f"batch-{index}.mp4"
            clip.write_bytes(f"video {index}".encode())
            release_id = self.store.import_file(clip)["id"]
            self.store.save_text(release_id, "youtube", "@channel", f"Title {index}", "Description", "tag")
            self.store.authorize(release_id, "youtube")
            release_ids.append(release_id)
        planned = self.store.reserve_batch(release_ids, datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        local_times = [datetime.fromisoformat(item["scheduled_at"]).astimezone(NY).strftime("%Y-%m-%d %H:%M")
                       for item in planned]
        self.assertEqual(local_times, ["2026-09-28 10:00", "2026-09-28 19:00",
                                       "2026-09-29 10:00", "2026-09-29 19:00"])
        self.assertEqual([item["status"] for item in planned], ["reserved"] * 4)

    def test_batch_rolls_back_when_any_video_is_not_authorized(self):
        first = self.store.import_file(self.video)["id"]
        other = self.root / "batch-unreviewed.mp4"
        other.write_bytes(b"other")
        second = self.store.import_file(other)["id"]
        self.store.save_text(first, "youtube", "@channel", "Title", "Description", "tag")
        self.store.authorize(first, "youtube")
        with self.assertRaisesRegex(ValueError, "Authorize"):
            self.store.reserve_batch([first, second], datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        self.assertEqual([self.store.release(item)["scheduled_at"] for item in (first, second)], [None, None])

    def test_batch_rolls_back_if_slot_allocation_fails_mid_transaction(self):
        first = self.store.import_file(self.video)["id"]
        other = self.root / "batch-second.mp4"
        other.write_bytes(b"other")
        second = self.store.import_file(other)["id"]
        for release_id in (first, second):
            self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
            self.store.authorize(release_id, "youtube")
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("slot allocator failed")
            return next_slot(*args)

        with patch("video_drop.core.next_slot", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "slot allocator failed"):
                self.store.reserve_batch([first, second], datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
        self.assertEqual([self.store.release(item)["scheduled_at"] for item in (first, second)], [None, None])

    def test_expired_unattempted_slot_moves_to_next_free_time(self):
        release_id = self.store.import_file(self.video)["id"]
        self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
        self.store.authorize(release_id, "youtube")
        first = self.store.reserve_slot(release_id, datetime(2026, 9, 27, 20, tzinfo=timezone.utc))
        moved = self.store.reserve_slot(release_id, datetime(2026, 9, 28, 0, tzinfo=timezone.utc))
        self.assertNotEqual(first["scheduled_at"], moved["scheduled_at"])
        self.assertEqual(datetime.fromisoformat(moved["scheduled_at"]).hour, 14)  # 10 AM ET
        self.assertEqual(moved["status"], "reserved")

    def test_expired_attempted_slot_requires_receipt_check(self):
        release_id = self.store.import_file(self.video)["id"]
        self.store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
        self.store.authorize(release_id, "youtube")
        first = self.store.reserve_slot(release_id, datetime(2026, 9, 27, 20, tzinfo=timezone.utc))
        self.store.mark_unconfirmed(release_id, "youtube")
        with self.assertRaisesRegex(ValueError, "check native receipts"):
            self.store.reserve_slot(release_id, datetime(2026, 9, 28, 0, tzinfo=timezone.utc))
        self.assertEqual(self.store.release(release_id)["scheduled_at"], first["scheduled_at"])

    def test_account_targets_live_only_in_local_state(self):
        state = self.root / "operator"
        state.mkdir()
        (state / "accounts.json").write_text('{"youtube":"@creator", "facebook":"12345"}', encoding="utf-8")
        with Store(state / "video-drop.sqlite", load_targets(state)) as store:
            release = store.import_file(self.video)
        accounts = {item["platform"]: item["account"] for item in release["destinations"]}
        self.assertEqual(accounts["youtube"], "@creator")
        self.assertEqual(accounts["facebook"], "12345")
        self.assertEqual(accounts["instagram"], "")

    def test_exact_text_revision_invalidated_on_edit(self):
        release = self.store.import_file(self.video)
        id_ = release["id"]
        self.store.save_text(id_, "youtube", "@examplechannel", "Title", "Description", "tag")
        authorized = self.store.authorize(id_, "youtube")
        self.assertTrue(authorized["destinations"][0]["revision_hash"])
        edited = self.store.save_text(id_, "youtube", "@examplechannel", "Changed", "Description", "tag")
        self.assertEqual(edited["destinations"][0]["revision_hash"], "")
        with self.assertRaisesRegex(ValueError, "Authorize"):
            self.store.reserve_slot(id_)

    def test_unconfirmed_needs_receipt(self):
        release = self.store.import_file(self.video)
        id_ = release["id"]
        self.store.save_text(id_, "youtube", "@examplechannel", "Title", "Description", "tag")
        self.store.authorize(id_, "youtube")
        with self.assertRaisesRegex(ValueError, "No platform action"):
            self.store.mark_unconfirmed(id_, "youtube")
        self.store.reserve_slot(id_)
        uncertain = self.store.mark_unconfirmed(id_, "youtube")
        self.assertEqual(uncertain["status"], "needs_check")
        self.assertEqual(uncertain["destinations"][0]["status"], "unconfirmed")
        with self.assertRaisesRegex(ValueError, "verification is not connected"):
            self.store.record_receipt(id_, "youtube", "https://youtube.com/shorts/example")
        self.assertEqual(self.store.release(id_)["destinations"][0]["status"], "unconfirmed")
        with self.assertRaisesRegex(ValueError, "after platform work starts"):
            self.store.save_text(id_, "youtube", "@examplechannel", "Changed", "Description", "tag")
        with self.assertRaisesRegex(ValueError, "after platform work starts"):
            self.store.authorize(id_, "youtube")

    def test_observed_instagram_schedule_requires_matching_native_details(self):
        release_id = self.store.import_file(self.video)["id"]
        caption = 'Episode two is back up after the review #podcast #radio #reels'
        self.store.save_text(release_id, "instagram", "@examplechannel", "Day 2", caption, "")
        self.store.authorize(release_id, "instagram")
        self.store.reserve_slot(release_id, datetime(2026, 9, 29, 8, tzinfo=timezone.utc))
        self.store.mark_unconfirmed(release_id, "instagram")
        frame = Image.new("RGB", (320, 568), "#152020")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 124, 320, 284), fill="#f2c849")
        draw.rectangle((0, 284, 320, 444), fill="#2878bd")
        draw.ellipse((100, 220, 220, 340), fill="#e92573")
        screenshot = Image.new("RGB", (1320, 2868), "white")
        screenshot.paste(frame.crop((0, 124, 320, 444)).resize((219, 219)), (36, 390))
        evidence = self.video.parent / "scheduled.png"
        screenshot.save(evidence)
        rows = [
            {"text": "Scheduled content", "x": 220, "y": 90},
            {"text": 'Episode two is back up after the review', "x": 215, "y": 139},
            {"text": "#podcast #radio #reels", "x": 200, "y": 158},
            {"text": "Scheduled Sep 29 at 10:00 AM", "x": 215, "y": 181},
        ]
        kwargs = {"account": "@examplechannel", "native_rows": rows,
                  "device_time_zone": "America/New_York", "screen_info": {"width": 440, "height": 956},
                  "evidence_image": evidence}
        with self.assertRaisesRegex(ValueError, "account or time"):
            self.store.record_observed_schedule(release_id, "instagram", account="@wrongaccount",
                                                **{key: value for key, value in kwargs.items() if key != "account"})
        with patch("video_drop.instagram_schedule.first_frame", return_value=frame):
            with self.assertRaisesRegex(ValueError, "caption is missing"):
                self.store.record_observed_schedule(release_id, "instagram",
                                                    **{**kwargs, "native_rows": rows[:2] + rows[3:]})
            with self.assertRaisesRegex(ValueError, "no unique scheduled entry"):
                self.store.record_observed_schedule(release_id, "instagram",
                                                    **{**kwargs, "native_rows": rows[:3] +
                                                       [{"text": "Scheduled Sep 29 at 7:00 PM", "x": 215, "y": 181}]})
            blank = Image.new("RGB", (1320, 2868), "white")
            blank.save(evidence)
            with self.assertRaisesRegex(ValueError, "does not match"):
                self.store.record_observed_schedule(release_id, "instagram", **kwargs)
            screenshot.save(evidence)
            result = self.store.record_observed_schedule(release_id, "instagram", **kwargs)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(next(d for d in result["destinations"] if d["platform"] == "instagram")["status"], "scheduled")
        with self.assertRaisesRegex(ValueError, "No uncertain native schedule"):
            self.store.record_observed_schedule(release_id, "instagram", **kwargs)

    def test_uncertain_action_rechecks_source_identity(self):
        id_ = self.store.import_file(self.video)["id"]
        self.store.save_text(id_, "youtube", "@examplechannel", "Title", "Description", "tag")
        self.store.authorize(id_, "youtube")
        self.store.reserve_slot(id_)
        self.video.write_bytes(b"different video")
        with self.assertRaisesRegex(ValueError, "Source video changed"):
            self.store.mark_unconfirmed(id_, "youtube")
        self.assertEqual(self.store.release(id_)["destinations"][0]["status"], "pending")

    def test_facebook_and_threads_share_instagram_caption_but_not_account(self):
        id_ = self.store.import_file(self.video)["id"]
        self.store.save_text(id_, "facebook", "new-safe-page", "wrong", "wrong", "")
        self.store.save_text(id_, "threads", "@examplechannel", "wrong", "wrong", "")
        release = self.store.save_text(id_, "instagram", "@examplechannel", "", "Caption #clip", "")
        by_platform = {d["platform"]: d for d in release["destinations"]}
        self.assertEqual(by_platform["facebook"]["account"], "new-safe-page")
        self.assertEqual(by_platform["threads"]["account"], "@examplechannel")
        self.assertEqual(by_platform["facebook"]["description"], "Caption #clip #reels")
        self.assertEqual(by_platform["threads"]["description"], "Caption #clip")

    def test_shared_title_updates_one_clip_without_changing_platform_hashtags(self):
        first = self.store.import_file(self.video)["id"]
        other = self.root / "other.mp4"
        other.write_bytes(b"another video")
        second = self.store.import_file(other)["id"]
        untouched = self.store.release(second)["destinations"]
        self.store.save_analysis(first, {"status": "complete", "suggestions": {
            "youtube_title": "Old suggestion", "youtube_description": "Description #Game",
            "youtube_tags": ["Game"], "instagram_caption": "Old line #reels",
            "tiktok_caption": "Different line #fyp"}})
        self.store.save_text(first, "youtube", "@examplechannel", "Old title", "My description #Game", "Game")
        self.store.save_text(first, "instagram", "@examplechannel", "", "Custom line   #Podcast #reels", "")
        self.store.save_text(first, "tiktok", "@examplechannel", "", "TikTok line #fyp", "")
        self.store.authorize(first, "youtube")
        self.store.authorize(first, "instagram")
        updated = self.store.apply_shared_title(first, "New hook")
        destinations = {d["platform"]: d for d in updated["destinations"]}
        self.assertEqual(destinations["youtube"]["title"], "New hook")
        self.assertEqual(destinations["youtube"]["description"], "My description #Game")
        self.assertEqual(destinations["youtube"]["tags"], "Game")
        self.assertEqual(destinations["instagram"]["description"], "New hook   #Podcast #reels")
        self.assertEqual(destinations["facebook"]["description"], "New hook #Podcast #reels")
        self.assertEqual(destinations["threads"]["description"], "New hook #Podcast")
        self.assertEqual(destinations["tiktok"]["description"], "New hook #fyp")
        self.assertEqual(destinations["facebook"]["account"], "")
        self.assertFalse(destinations["youtube"]["revision_hash"])
        self.assertFalse(destinations["instagram"]["revision_hash"])
        self.assertEqual(self.store.release(second)["destinations"], untouched)
        self.store.authorize(first, "youtube")
        confirmed = self.store.release(first)["destinations"][0]["revision_hash"]
        self.store.apply_shared_title(first, "New hook")
        self.assertEqual(self.store.release(first)["destinations"][0]["revision_hash"], confirmed)

    def test_shared_copy_separates_common_and_platform_hashtags(self):
        release_id = self.store.import_file(self.video)["id"]
        self.store.save_analysis(release_id, {"status": "complete", "suggestions": {
            "youtube_description": "Gameplay details #Old #shorts", "youtube_tags": ["Valheim"]}})
        self.store.save_text(release_id, "youtube", "@examplechannel", "Old", "My edited detail #Old", "Valheim")
        self.store.authorize(release_id, "youtube")
        updated = self.store.apply_shared_copy(release_id, "Valheim kick", "#Valheim #Kick #valheim #reels")
        destinations = {d["platform"]: d for d in updated["destinations"]}
        self.assertTrue(all(d["title"] == "Valheim kick" for d in destinations.values()))
        self.assertEqual(destinations["youtube"]["title"], "Valheim kick")
        self.assertEqual(destinations["youtube"]["description"], "#Valheim #Kick #shorts\nMy edited detail")
        self.assertEqual(destinations["youtube"]["tags"], "Valheim")
        self.assertEqual(destinations["instagram"]["description"], "Valheim kick #Valheim #Kick #reels")
        self.assertEqual(destinations["facebook"]["description"], "Valheim kick #Valheim #Kick #reels")
        self.assertEqual(destinations["threads"]["description"], "Valheim kick #Valheim #Kick")
        self.assertEqual(destinations["tiktok"]["description"], "Valheim kick #Valheim #Kick #fyp")
        self.assertFalse(destinations["youtube"]["revision_hash"])
        self.store.authorize(release_id, "youtube")
        confirmed = self.store.release(release_id)["destinations"][0]["revision_hash"]
        self.store.apply_shared_copy(release_id, "Valheim kick", "#Valheim #Kick")
        self.assertEqual(self.store.release(release_id)["destinations"][0]["revision_hash"], confirmed)
        with self.assertRaisesRegex(ValueError, "Enter hashtags"):
            self.store.apply_shared_copy(release_id, "Valheim kick", "Valheim Kick")
        with self.assertRaisesRegex(ValueError, "blocked sensitive topic"):
            self.store.apply_shared_copy(release_id, "Valheim kick", "#elections")

    def test_shared_title_requires_analysis_and_cannot_touch_started_release(self):
        release_id = self.store.import_file(self.video)["id"]
        with self.assertRaisesRegex(ValueError, "Wait for local analysis"):
            self.store.apply_shared_title(release_id, "New hook")
        self.store.save_analysis(release_id, {"status": "complete", "suggestions": {"instagram_caption": "Line #clip"}})
        self.store.apply_shared_title(release_id, "New hook")
        with self.assertRaisesRegex(ValueError, "Keep hashtags"):
            self.store.apply_shared_title(release_id, "New hook #clip")
        self.store.save_text(release_id, "youtube", "@examplechannel", "New hook", "Description", "tag")
        self.store.authorize(release_id, "youtube")
        self.store.reserve_slot(release_id)
        self.store.mark_unconfirmed(release_id, "youtube")
        with self.assertRaisesRegex(ValueError, "after platform work starts"):
            self.store.apply_shared_title(release_id, "Another hook")

    def test_local_models_produce_suggestions_without_authorizing_public_text(self):
        release = self.store.import_file(self.video)
        with patch("video_drop.analyze.probe", return_value={"duration_seconds": 12, "width": 1080,
                                                         "height": 1920, "fps": "60/1", "has_audio": False}), \
             patch("video_drop.analyze.frame", return_value=b"jpeg"), \
             patch("video_drop.analyze.generate", side_effect=[
                 {"summary": "A player kicks in Valheim", "game": "Valheim",
                  "on_screen_title": "Day 5 of asking for a radio host"},
                 {"youtube_title": "A Valheim kick", "youtube_description": "A kick #Valheim",
                  "youtube_tags": ["Valheim", "swing voters"],
                  "instagram_caption": "A kick #Valheim #elections",
                  "tiktok_caption": "A kick #Valheim", "game": "Valheim"},
             ]) as model:
            result = analyze(self.video)
        self.assertEqual([call.args[0] for call in model.call_args_list], ["qwen2.5vl:7b", "qwen3:14b"])
        self.assertIn('"on_screen_title": "Day 5 of asking for a radio host"', model.call_args_list[1].args[1])
        self.assertEqual(result["suggestions"]["youtube_tags"], ["Valheim"])
        self.assertEqual(result["suggestions"]["instagram_caption"], "A kick #Valheim")
        self.store.save_analysis(release["id"], result)
        self.assertFalse(any(d["revision_hash"] for d in self.store.release(release["id"])["destinations"]))

    def test_vision_pass_unloads_before_stronger_text_model(self):
        response = io.BytesIO(json.dumps({"response": "{}"}).encode())
        with patch("video_drop.analyze.urlopen", return_value=response) as request:
            generate("vision", "Describe", [b"frame"])
        vision_payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(vision_payload["keep_alive"], 0)
        self.assertEqual(vision_payload["images"], ["ZnJhbWU="])
        self.assertEqual(vision_payload["options"]["num_ctx"], 8192)
        self.assertFalse(vision_payload["think"])

        response = io.BytesIO(json.dumps({"response": "{}"}).encode())
        with patch("video_drop.analyze.urlopen", return_value=response) as request:
            generate("text", "Write suggestions")
        text_payload = json.loads(request.call_args.args[0].data)
        self.assertNotIn("keep_alive", text_payload)

    def test_sensitive_tag_filter(self):
        self.assertFalse(safe_tag("political commentary"))
        self.assertFalse(safe_tag("#swingvoters"))
        self.assertTrue(safe_tag("Valheim"))
        id_ = self.store.import_file(self.video)["id"]
        with self.assertRaisesRegex(ValueError, "blocked sensitive"):
            self.store.save_text(id_, "youtube", "@examplechannel", "Title", "Description #elections", "Valheim")


if __name__ == "__main__":
    unittest.main()
