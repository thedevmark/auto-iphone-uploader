"""Store.record_receipt: a posted (or scheduled crosspost) receipt the phone read back itself.

The evidence JSON is the one scripts/phone_receipts.py writes (record_post); core checks it
against the release before a destination leaves "unconfirmed", and logs an app receipt that
counts toward the unattended streak.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import phone_receipts
from video_drop.core import APP_RECEIPT_EVENTS, Store

ACCOUNTS = {"youtube": "@creator", "instagram": "@creator", "threads": "@creator", "tiktok": "@creator"}
SLOT = datetime(2030, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
INSTAGRAM = {"account": "@creator", "postsBefore": 4, "postsAfter": 5, "tileIndex": 0, "difference": 2.5,
             "verification": "instagram_profile_count_newest_tile_first_frame"}
THREADS = {"account": "@creator", "caption": "Approved caption", "postIndex": 0,
           "verification": "threads_profile_newest_caption"}


class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name)
        self.store = Store(self.state / "video-drop.sqlite", ACCOUNTS)
        self.evidence = self.state / "evidence"
        self.evidence.mkdir()

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def release(self, mode: str = "post_now", approved=("instagram", "facebook", "threads")) -> int:
        source = self.state / f"clip-{mode}.mp4"
        source.write_bytes(mode.encode())
        release_id = self.store.import_file(source)["id"]
        self.store.save_text(release_id, "instagram", "@creator", "", "Approved caption #reels", "")
        for platform in ("facebook", "threads"):
            self.store.save_text(release_id, platform, "@creator", "", "", "")
        for platform in approved:
            self.store.authorize(release_id, platform)
        self.store.set_delivery_mode(release_id, mode)
        if mode == "schedule":
            self.store.reserve_batch([release_id], now=SLOT - timedelta(days=1), at=SLOT)
        self.store.mark_unconfirmed(release_id, "instagram")  # Instagram's tap claims its crossposts
        return release_id

    def write(self, release_id: int, platform: str, verified: dict, *, choice: str = "posted",
              screenshot: bool = True, screenshot_name: str | None = None, folder: Path | None = None,
              **changes) -> Path:
        self.written = getattr(self, "written", 0) + 1  # one file per call: cases are built up front
        stem = (folder or self.evidence) / f"release{release_id}-{platform}-receipt-{self.written}"
        if screenshot:
            stem.with_suffix(".png").write_bytes(b"png")
        path = stem.with_suffix(".json")
        path.write_text(json.dumps({"releaseId": release_id, "platform": platform, "choice": choice,
                                    "screenshot": screenshot_name or (stem.with_suffix(".png").name if screenshot else None),
                                    **verified, **changes}), encoding="utf-8")
        return path

    def status(self, release_id: int) -> dict:
        release = self.store.release(release_id)
        return {"release": release["status"], **{d["platform"]: d["status"] for d in release["destinations"]}}

    def events(self, release_id: int) -> list[tuple[str, str, dict]]:
        return [(row[0], row[1], json.loads(row[2])) for row in self.store.db.execute(
            "SELECT platform, kind, payload FROM event WHERE release_id=? AND kind LIKE 'native%' ORDER BY id",
            (release_id,))]

    def test_instagram_and_its_threads_crosspost_are_posted_from_the_evidence(self):
        release_id = self.release()
        self.store.record_receipt(release_id, "instagram", self.write(release_id, "instagram", INSTAGRAM).as_uri())
        self.assertEqual(self.status(release_id)["instagram"], "posted")
        self.assertEqual(self.status(release_id)["release"], "partial")
        self.store.record_receipt(release_id, "threads", self.write(release_id, "threads", THREADS).as_uri())
        [(_, kind, first), (_, _, second)] = self.events(release_id)
        self.assertEqual(kind, "native_post_observed")
        self.assertEqual((first["via"], second["via"]), ("own_post", "instagram_crosspost"))
        self.assertEqual(second["verification"], "threads_profile_newest_caption")
        self.assertEqual(len(second["evidence_sha256"]), 64)
        self.assertEqual(self.status(release_id)["facebook"], "unconfirmed")  # Facebook keeps its own receipt

    def test_a_scheduled_facebook_crosspost_is_confirmed_by_the_same_mechanism(self):
        release_id = self.release("schedule", approved=("instagram", "facebook"))
        self.assertEqual(self.status(release_id)["facebook"], "unconfirmed")
        facebook = {"account": "@creator", "verification": "facebook_page_reels_scheduled_caption_slot"}
        self.store.record_receipt(release_id, "facebook",
                                  self.write(release_id, "facebook", facebook, choice="scheduled").as_uri())
        self.assertEqual(self.status(release_id)["facebook"], "scheduled")
        [(platform, kind, payload)] = self.events(release_id)
        self.assertEqual((platform, kind, payload["via"]), ("facebook", "native_schedule_observed", "instagram_crosspost"))

    def test_app_receipts_on_every_destination_count_toward_the_streak(self):
        self.assertIn("native_post_observed", APP_RECEIPT_EVENTS)
        release_id = self.release("schedule", approved=("instagram", "facebook"))
        with self.store.db:  # Instagram's own schedule read-back (record_observed_schedule) leaves this behind
            self.store.db.execute("UPDATE destination SET status='scheduled' WHERE release_id=? AND platform='instagram'",
                                  (release_id,))
            self.store._event(release_id, "instagram", "native_schedule_observed", {"verification": "test"})
        facebook = {"account": "@creator", "verification": "facebook_page_reels_newest_caption_first_frame"}
        self.store.record_receipt(release_id, "facebook", self.write(release_id, "facebook", facebook).as_uri())
        self.assertEqual(self.status(release_id)["release"], "scheduled")
        self.assertEqual(self.store.unattended_streak(SLOT.astimezone(timezone.utc) + timedelta(hours=2))["count"], 1)

    def test_evidence_that_does_not_match_is_refused_and_changes_nothing(self):
        release_id = self.release()
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        cases = {
            "not connected for youtube": ("youtube", self.write(release_id, "instagram", INSTAGRAM).as_uri()),
            "file: URI": ("instagram", "https://instagram.com/p/unverified"),
            "under this app's state folder": ("instagram", self.write(release_id, "instagram", INSTAGRAM,
                                                                       folder=Path(other.name)).as_uri()),
            "missing": ("instagram", (self.evidence / "absent.json").as_uri()),
            "another release or app": ("instagram", self.write(release_id, "instagram", INSTAGRAM,
                                                               releaseId=release_id + 1).as_uri()),
            "no accepted instagram posted verification": (
                "instagram", self.write(release_id, "instagram", INSTAGRAM, verification="looks_right").as_uri()),
            "account does not match": ("instagram", self.write(release_id, "instagram", INSTAGRAM,
                                                               account="@someone").as_uri()),
            "cannot have a scheduled receipt": (
                "facebook", self.write(release_id, "facebook", {"account": "@creator",
                                       "verification": "facebook_page_reels_scheduled_caption_slot"},
                                       choice="scheduled").as_uri()),
            "screenshot that is not beside it": ("threads", self.write(release_id, "threads", THREADS,
                                                                       screenshot_name="../x.png").as_uri()),
        }
        for message, (platform, uri) in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.store.record_receipt(release_id, platform, uri)
        self.assertEqual(set(self.status(release_id).values()) - {"needs_check"}, {"unconfirmed", "pending"})
        self.assertEqual(self.events(release_id), [])

    def test_only_an_unconfirmed_destination_takes_a_receipt(self):
        release_id = self.release()
        uri = self.write(release_id, "instagram", INSTAGRAM).as_uri()
        self.store.record_receipt(release_id, "instagram", uri)
        with self.assertRaisesRegex(ValueError, "recorded final tap"):
            self.store.record_receipt(release_id, "instagram", uri)
        self.assertEqual(len(self.events(release_id)), 1)

    def test_the_receipt_runner_evidence_is_recorded(self):
        release_id = self.release()
        stem = phone_receipts.evidence_stem(self.state, release_id, "threads")
        stem.with_suffix(".png").write_bytes(b"png")
        result = phone_receipts.record_post(self.store, release_id, "threads", THREADS, stem, record=True)
        self.assertEqual(result["kind"], "recorded", result)
        self.assertEqual(self.status(release_id)["threads"], "posted")

    def test_a_failed_read_captures_the_screen_before_leaving_the_app(self):
        # A failed sweep read leaves a trace of what the phone showed.
        from unittest import mock
        from video_drop import failure_capture
        order = []
        release = {"id": 7, "delivery_mode": "post_now"}
        with mock.patch.object(phone_receipts.rc, "receipt_route", return_value={"status": "verify"}),                 mock.patch.object(phone_receipts, "threads_post",
                                  side_effect=phone_receipts.rc.ReceiptError("header found 0")),                 mock.patch.object(failure_capture, "capture", side_effect=lambda *a: order.append(("capture", a[1]))),                 mock.patch.object(phone_receipts, "leave", side_effect=lambda: order.append(("leave", None))):
            result = phone_receipts.check(self.store, release, "threads", self.state, posts_before=None, record=True)
        self.assertEqual(result["kind"], "unverified")
        self.assertEqual(order, [("capture", "receipt threads"), ("leave", None)])


if __name__ == "__main__":
    unittest.main()
