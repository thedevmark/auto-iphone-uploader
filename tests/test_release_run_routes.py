"""The two posting modes end to end on a fake phone, driven through the server's routes.

Every flow's run() is replaced by a recorder that behaves like the real flow at its final
tap: it marks its destination unconfirmed in the store (Instagram's claim carries the
crossposts) and returns the flow's result. Nothing here touches an iPhone, go-ios, a
tunnel, WDA or the link supervisor.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from scripts.phone_youtube import PhoneUploadError
from video_drop import server
from video_drop.core import Store

REAL_WAIT_FOR_LINK = server.wait_for_link  # conftest swaps in "always ready"; the link tests put it back
ACCOUNTS = {"youtube": "@creator", "instagram": "@creator", "threads": "@creator", "tiktok": "@creator"}
SLOT = datetime(2030, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
ALL = ("youtube", "instagram", "facebook", "threads", "tiktok")


def make_release(state: Path, *, mode: str = "schedule", approved=ALL, checks: dict | None = None,
                 slot: datetime | None = SLOT) -> int:
    (state / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    with Store(state / "video-drop.sqlite", ACCOUNTS) as store:
        if checks:
            store.set_phone_checks(checks)
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "youtube", "@creator", "Title", "Description #shorts", "tag")
        store.save_text(release_id, "instagram", "@creator", "", "Approved caption #reels", "")
        store.save_text(release_id, "tiktok", "@creator", "", "Approved caption #fyp", "")
        for platform in ("facebook", "threads"):
            store.save_text(release_id, platform, "@creator", "", "", "")
        for platform in approved:
            store.authorize(release_id, platform)
        store.set_delivery_mode(release_id, mode)
        if mode == "schedule" and slot is not None:
            store.reserve_batch([release_id], at=slot)
    return release_id


def statuses(state: Path, release_id: int) -> dict:
    with Store(state / "video-drop.sqlite") as store:
        return {d["platform"]: d["status"] for d in store.release(release_id)["destinations"]}


class FakePhone:
    """Records every flow run and plays the flow's final tap on the store."""

    FLOWS = {"youtube_post": ("scripts.phone_youtube.run", "youtube"),
             "youtube_schedule": ("scripts.phone_youtube_schedule.run", "youtube"),
             "instagram": ("scripts.phone_instagram.run", "instagram"),
             "tiktok": ("scripts.phone_tiktok.run", "tiktok"),
             "threads": ("scripts.phone_threads.run", "threads")}

    def __init__(self, fail: dict | None = None):
        self.calls: list[tuple[str, int, dict]] = []
        self.fail = fail or {}  # flow -> (tap_first, exception)
        self.patches = [patch(target, side_effect=self.flow(name, platform))
                        for name, (target, platform) in self.FLOWS.items()]

    def flow(self, name: str, platform: str):
        def run(release, db, **kwargs):
            self.calls.append((name, int(release), kwargs))
            assert kwargs.get("commit") is True, "the server always runs flows with commit=True"
            tap, error = self.fail.get(name, (True, None))
            if tap:
                with Store(db) as store:
                    store.mark_unconfirmed(int(release), platform)
            if error:
                raise error
            return {"kind": "unconfirmed", "message": f"{name} final tap sent; check before any retry"}
        return run

    def order(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *_):
        for item in self.patches:
            item.stop()


class RunRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        for item in (patch.object(server, "STATE", self.state), patch.object(server, "TEST_MODE", False),
                     patch.object(server, "phone_free_bytes", return_value=10 ** 12),
                     patch.object(server, "youtube_quality_gate", return_value="full"),
                     # Instagram's real input checks run; only its ffprobe reads are stubbed.
                     patch("scripts.phone_instagram.source_frame_rate", return_value=60.0),
                     patch("scripts.phone_instagram.edits_color_mode", return_value="SDR")):
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.folder.cleanup()

    def call(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.http.server_port}{path}"
        request = Request(url, data=json.dumps(body or {}).encode(), method="POST") if body is not None else Request(url)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def start(self, release_id: int, mode: str) -> tuple[int, dict]:
        return self.call(f"/api/releases/{release_id}/{'post-now' if mode == 'post_now' else 'schedule'}", {})

    def finished(self) -> dict:
        for _ in range(500):
            _, action = self.call("/api/phone-action")
            if action["status"] != "running":
                return action
            time.sleep(0.02)
        self.fail("the phone run did not finish")

    def progress(self, release_id: int) -> dict:
        _, data = self.call(f"/api/releases/{release_id}")
        return {row["platform"]: row for row in data["release"]["progress"]}

    # ---- Schedule -----------------------------------------------------------------

    def test_schedule_runs_youtube_then_instagram_natively_and_posts_nothing_now(self):
        release_id = make_release(self.state)
        with FakePhone() as phone:
            code, started = self.start(release_id, "schedule")
            self.assertEqual(code, 202, started)
            self.assertEqual(started["run"]["status"], "running")
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_schedule", "instagram"])
        self.assertEqual(action["status"], "done")
        self.assertIn("TikTok posts from this app at the slot", action["message"])
        self.assertIn("native Threads scheduling isn't built yet", action["message"])
        self.assertEqual(statuses(self.state, release_id),
                         {"youtube": "unconfirmed", "instagram": "unconfirmed", "facebook": "unconfirmed",
                          "threads": "pending", "tiktok": "pending"})
        rows = self.progress(release_id)
        self.assertEqual(list(rows), ["youtube", "instagram", "facebook", "tiktok", "threads"])
        self.assertEqual({p: r["state"] for p, r in rows.items()},
                         {"youtube": "unconfirmed", "instagram": "unconfirmed", "facebook": "unconfirmed",
                          "tiktok": "at_slot", "threads": "needs_you"})
        self.assertIn("not built yet", rows["threads"]["note"])
        self.assertIn("stays pending", rows["threads"]["note"])

    def test_tiktok_is_posted_by_the_app_at_the_slot_not_by_schedule(self):
        release_id = make_release(self.state)
        slot_utc = SLOT.astimezone(ZoneInfo("UTC"))
        with FakePhone() as phone:
            self.assertEqual(server.slot_post_tick(slot_utc - timedelta(hours=1)), [])  # arms the slot
            self.start(release_id, "schedule")
            self.finished()
            self.assertNotIn("tiktok", phone.order())
            [started] = server.slot_post_tick(slot_utc + timedelta(minutes=1))
            action = self.finished()
        self.assertEqual((started.release_id, started.platform), (release_id, "tiktok"))
        self.assertEqual(phone.order(), ["youtube_schedule", "instagram", "tiktok"])
        self.assertEqual(phone.calls[-1][2]["not_after"], started.deadline)
        self.assertEqual(action["status"], "needs_check")
        self.assertEqual(statuses(self.state, release_id)["tiktok"], "unconfirmed")
        self.assertEqual(statuses(self.state, release_id)["threads"], "pending")

    def test_schedule_stops_at_the_first_error_with_a_plain_message(self):
        release_id = make_release(self.state)
        failing = {"youtube_schedule": (False, PhoneUploadError("YouTube channel changed before Upload Short"))}
        with FakePhone(failing) as phone:
            self.start(release_id, "schedule")
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_schedule"])
        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["message"],
                         "YouTube channel changed before Upload Short. Nothing was scheduled.")
        steps = {step["platform"]: step for step in action["steps"]}
        self.assertEqual(steps["youtube"]["state"], "needs_you")
        self.assertEqual(steps["instagram"]["state"], "stopped")
        self.assertIn("YouTube stopped first", steps["instagram"]["message"])
        self.assertEqual(set(statuses(self.state, release_id).values()), {"pending"})
        rows = self.progress(release_id)
        self.assertEqual((rows["youtube"]["state"], rows["instagram"]["state"], rows["facebook"]["state"]),
                         ("needs_you", "stopped", "with_instagram"))

    def test_an_uncertain_final_tap_stops_the_run_and_is_never_replayed(self):
        release_id = make_release(self.state)
        dropped = {"youtube_schedule": (True, RuntimeError("WDA timed out after Upload Short"))}
        with FakePhone(dropped) as phone:
            self.start(release_id, "schedule")
            action = self.finished()
            self.assertEqual(phone.order(), ["youtube_schedule"])
            self.assertEqual(action["status"], "needs_check")
            self.assertIn("check YouTube before any retry", action["message"])
            self.assertEqual(statuses(self.state, release_id)["youtube"], "unconfirmed")
            # Pressing Schedule again continues with Instagram; YouTube's uncertain tap is not replayed.
            phone.fail = {}
            code, again = self.start(release_id, "schedule")
            self.assertEqual(code, 202, again)
            self.finished()
        self.assertEqual(phone.order(), ["youtube_schedule", "instagram"])
        self.assertEqual(self.progress(release_id)["youtube"]["state"], "unconfirmed")

    def test_an_uncertain_instagram_tap_names_the_crosspost_it_carried(self):
        release_id = make_release(self.state)
        dropped = {"instagram": (True, RuntimeError("link dropped after Schedule"))}
        with FakePhone(dropped):
            self.start(release_id, "schedule")
            action = self.finished()
        self.assertEqual(action["status"], "needs_check")
        self.assertIn("check Instagram and Facebook before any retry", action["message"])
        self.assertEqual(statuses(self.state, release_id)["facebook"], "unconfirmed")

    def test_schedule_with_the_facebook_crosspost_off_stops_before_the_phone(self):
        release_id = make_release(self.state, checks={"crosspostFacebook": False})
        with FakePhone() as phone:
            code, refused = self.start(release_id, "schedule")
        self.assertEqual(code, 400)
        self.assertIn("needs the Facebook crosspost on", refused["error"])
        self.assertEqual(phone.calls, [])
        self.assertEqual(set(statuses(self.state, release_id).values()), {"pending"})

    def test_the_threads_crosspost_setting_never_puts_threads_on_a_scheduled_reel(self):
        release_id = make_release(self.state, checks={"crosspostThreads": True, "threadsSeparatePost": True})
        with FakePhone() as phone:
            self.start(release_id, "schedule")
            self.finished()
        self.assertNotIn("threads", phone.order())
        self.assertEqual(statuses(self.state, release_id)["threads"], "pending")
        self.assertEqual(self.progress(release_id)["threads"]["state"], "needs_you")

    def test_schedule_refuses_without_a_slot_or_in_post_now_mode(self):
        unplanned = make_release(self.state, slot=None)
        with FakePhone() as phone:
            code, refused = self.start(unplanned, "schedule")
            self.assertEqual(code, 400)
            self.assertIn("Reserve a posting time", refused["error"])
        with tempfile.TemporaryDirectory() as other, patch.object(server, "STATE", Path(other)):
            now_release = make_release(Path(other), mode="post_now")
            with FakePhone() as phone_now:
                code, refused = self.start(now_release, "schedule")
            self.assertEqual(code, 400)
            self.assertIn("switch it to Schedule", refused["error"])
            self.assertEqual(phone_now.calls, [])
        self.assertEqual(phone.calls, [])

    def test_schedule_with_only_tiktok_leaves_it_to_the_slot(self):
        release_id = make_release(self.state, approved=("tiktok",))
        with FakePhone() as phone:
            code, started = self.start(release_id, "schedule")
        self.assertEqual(code, 202)
        self.assertEqual(started["run"]["status"], "done")
        self.assertIn("TikTok posts from this app at the slot", started["run"]["message"])
        self.assertEqual(phone.calls, [])

    # ---- Post now -----------------------------------------------------------------

    def test_post_now_goes_youtube_instagram_tiktok_with_crossposts_on_instagram(self):
        release_id = make_release(self.state, mode="post_now")
        with FakePhone() as phone:
            code, started = self.start(release_id, "post_now")
            self.assertEqual(code, 202, started)
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_post", "instagram", "tiktok"])
        self.assertEqual(action["status"], "done")
        self.assertEqual(set(statuses(self.state, release_id).values()), {"unconfirmed"})
        rows = self.progress(release_id)
        self.assertEqual(list(rows), ["youtube", "instagram", "facebook", "threads", "tiktok"])
        self.assertEqual(rows["threads"].get("via"), "instagram")
        self.assertIn("Carried by Instagram's final tap", rows["facebook"]["note"])

    def test_post_now_posts_threads_separately_only_when_settings_ask(self):
        release_id = make_release(self.state, mode="post_now",
                                  checks={"crosspostThreads": False, "threadsSeparatePost": True})
        with FakePhone() as phone:
            self.start(release_id, "post_now")
            self.finished()
        self.assertEqual(phone.order(), ["youtube_post", "instagram", "tiktok", "threads"])
        with Store(self.state / "video-drop.sqlite") as store:
            carried = store.db.execute("""SELECT payload FROM event WHERE platform='instagram'
                AND kind='unconfirmed'""").fetchone()[0]
        self.assertEqual(json.loads(carried)["crossposts"], ["facebook"])
        self.assertEqual(set(statuses(self.state, release_id).values()), {"unconfirmed"})

    def test_post_now_leaves_threads_alone_when_both_threads_settings_are_off(self):
        release_id = make_release(self.state, mode="post_now",
                                  checks={"crosspostThreads": False, "threadsSeparatePost": False})
        with FakePhone() as phone:
            self.start(release_id, "post_now")
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_post", "instagram", "tiktok"])
        self.assertEqual(statuses(self.state, release_id)["threads"], "pending")
        self.assertEqual(self.progress(release_id)["threads"]["state"], "needs_you")
        self.assertIn("Threads needs you", action["message"])

    def test_post_now_with_facebook_crosspost_off_leaves_facebook_for_you(self):
        release_id = make_release(self.state, mode="post_now", checks={"crosspostFacebook": False})
        with FakePhone():
            self.start(release_id, "post_now")
            self.finished()
        self.assertEqual(statuses(self.state, release_id)["facebook"], "pending")
        rows = self.progress(release_id)
        self.assertEqual(rows["facebook"]["state"], "needs_you")
        self.assertIn("Facebook crosspost is off", rows["facebook"]["note"])
        self.assertEqual(statuses(self.state, release_id)["threads"], "unconfirmed")

    def test_post_now_stops_after_an_instagram_error_and_never_reaches_tiktok(self):
        release_id = make_release(self.state, mode="post_now")
        failing = {"instagram": (False, PhoneUploadError("Instagram has @other; expected @creator. Nothing was posted"))}
        with FakePhone(failing) as phone:
            self.start(release_id, "post_now")
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_post", "instagram"])
        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["message"], "Instagram has @other; expected @creator. Nothing was posted")
        steps = {step["platform"]: step for step in action["steps"]}
        self.assertEqual((steps["facebook"]["state"], steps["threads"]["state"], steps["tiktok"]["state"]),
                         ("stopped", "stopped", "stopped"))
        self.assertEqual(statuses(self.state, release_id)["tiktok"], "pending")

    def test_post_now_route_refuses_a_scheduled_video(self):
        release_id = make_release(self.state)
        with FakePhone() as phone:
            code, refused = self.start(release_id, "post_now")
        self.assertEqual(code, 400)
        self.assertIn("switch it to Post now", refused["error"])
        self.assertEqual(phone.calls, [])

    def test_a_second_run_is_refused_while_one_is_running(self):
        release_id = make_release(self.state, mode="post_now")
        gate = threading.Event()
        with FakePhone() as phone:
            slow = phone.flow("youtube_post", "youtube")
            with patch("scripts.phone_youtube.run", side_effect=lambda *a, **k: (gate.wait(5), slow(*a, **k))[1]):
                self.start(release_id, "post_now")
                code, refused = self.start(release_id, "post_now")
                gate.set()
                self.finished()
        self.assertEqual(code, 400)
        self.assertIn("already running", refused["error"])
        self.assertEqual(phone.order(), ["youtube_post", "instagram", "tiktok"])

    # ---- the phone link -----------------------------------------------------------

    def test_the_run_waits_for_the_link_and_shows_its_status(self):
        release_id = make_release(self.state)
        seen = []

        def wait_ready(timeout, *, state, sleep):
            for _ in range(2):
                sleep(0)
                seen.append(server.phone_action_status())
            return {"state": "ready", "message": "Phone link ready", "alive": True}

        recovering = {"state": "recovering", "message": "Restarting the phone tunnel", "alive": True}
        with patch.object(server, "wait_for_link", REAL_WAIT_FOR_LINK), \
                patch.object(server.phone_link, "wait_ready", side_effect=wait_ready), \
                patch.object(server.link_supervisor, "read_status", return_value=recovering), \
                FakePhone() as phone:
            self.start(release_id, "schedule")
            action = self.finished()
        self.assertEqual(phone.order(), ["youtube_schedule", "instagram"])
        self.assertEqual(action["status"], "done")
        waiting = [item for item in seen if item["platform"] == "youtube"]
        self.assertTrue(waiting)
        self.assertEqual(waiting[0]["link"], {"state": "recovering", "message": "Restarting the phone tunnel"})
        self.assertEqual(waiting[0]["message"], "Waiting for the phone link before YouTube: Restarting the phone tunnel")
        self.assertEqual(action["link"]["state"], "ready")

    def test_a_link_that_never_comes_back_stops_before_the_phone(self):
        release_id = make_release(self.state, mode="post_now")
        replug = {"state": "needs-replug", "message": "Unplug and replug the phone", "alive": True}
        with patch.object(server, "wait_for_link", return_value=replug), FakePhone() as phone:
            self.start(release_id, "post_now")
            action = self.finished()
        self.assertEqual(phone.calls, [])
        self.assertEqual(action["status"], "failed")
        self.assertIn("The phone link is not ready (Unplug and replug the phone)", action["message"])
        self.assertEqual(set(statuses(self.state, release_id).values()), {"pending"})

    def test_a_slot_post_also_waits_for_the_link(self):
        release_id = make_release(self.state)
        slot_utc = SLOT.astimezone(ZoneInfo("UTC"))
        replug = {"state": "unplugged", "message": "The phone is not on USB", "alive": True}
        with FakePhone() as phone:
            server.slot_post_tick(slot_utc - timedelta(hours=1))
            self.start(release_id, "schedule")
            self.finished()
            with patch.object(server, "wait_for_link", return_value=replug):
                server.slot_post_tick(slot_utc + timedelta(minutes=1))
                action = self.finished()
        self.assertNotIn("tiktok", phone.order())
        self.assertEqual(action["status"], "failed")
        self.assertIn("The phone is not on USB", action["message"])
        self.assertEqual(statuses(self.state, release_id)["tiktok"], "pending")

    def test_a_run_the_server_lost_reports_what_the_store_knows(self):
        release_id = make_release(self.state)
        with Store(self.state / "video-drop.sqlite") as store:
            store.mark_unconfirmed(release_id, "youtube")
        server.write_phone_action({"status": "running", "kind": "run", "mode": "schedule", "releaseId": release_id,
                                   "platform": "instagram", "message": "", "steps": [
                                       {"platform": "youtube", "state": "running", "message": ""},
                                       {"platform": "instagram", "state": "queued", "message": ""}]})
        _, action = self.call("/api/phone-action")
        self.assertEqual(action["status"], "needs_check")
        steps = {step["platform"]: step["state"] for step in action["steps"]}
        self.assertEqual(steps, {"youtube": "unconfirmed", "instagram": "stopped"})


class PageTests(unittest.TestCase):
    def test_page_has_one_primary_button_per_mode_a_progress_list_and_link_status(self):
        page = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="schedule"', "'Post now'", "`Schedule for ${", "/post-now", "/schedule`",
                       'id="runProgress"', 'id="linkStatus"', "/api/link", "needs_you", "Needs you",
                       "Unconfirmed", "Scheduled", "Posted", "Queued", "Running"):
            self.assertIn(needle, page)


if __name__ == "__main__":
    unittest.main()
