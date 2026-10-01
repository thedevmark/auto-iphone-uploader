import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from video_drop import edits_cleanup as ec
from video_drop import receipts as rc
from video_drop.screens.snapshot import Element

NOTED = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
OURS = "0AD0CB42-551C-4100-8812-B6B97E156E7C"


def tile(project, label):
    return Element("Cell", label, ec.TILE_PREFIX + project, "", 20, 160, 124, 160)


class MatchingTests(unittest.TestCase):
    def test_tiles_and_ages_as_recorded(self):
        shown = ec.tiles([tile(OURS, "Untitled project, 58m · 167.9 MB"),
                          Element("Button", "Create new project", "new-project-button", "", 0, 0, 1, 1)])
        self.assertEqual(shown, {OURS: "Untitled project, 58m · 167.9 MB"})
        for label, age in (("Untitled project, 58m · 167.9 MB", timedelta(minutes=58)),
                           ("Untitled project, 3h · 1.03 GB", timedelta(hours=3)),
                           ("Untitled project, 12w · 1.55 GB", timedelta(weeks=12)),
                           ("Untitled project, now · 1 MB", timedelta(0)),
                           ("Untitled project", None)):
            self.assertEqual(ec.tile_age(label), age, label)

    def test_exactly_the_new_untitled_project_from_this_upload(self):
        shown = {"OLD": "Untitled project, 3h · 1.03 GB", OURS: "Untitled project, 20m · 167.9 MB"}
        self.assertEqual(ec.new_project(["OLD"], shown, NOTED, NOTED + timedelta(minutes=25)), OURS)

    def test_never_guesses(self):
        later = NOTED + timedelta(minutes=25)
        cases = {
            "renamed by the owner": {OURS: "Homebase day 4, 20m · 1 GB"},
            "older than the note": {OURS: "Untitled project, 3h · 1 GB"},
            "two new projects": {OURS: "Untitled project, 20m · 1 GB", "B": "Untitled project, 5m · 1 GB"},
            "none new": {"OLD": "Untitled project, 3h · 1 GB"},
            "unreadable age": {OURS: "Untitled project, yesterday · 1 GB"},
        }
        for name, shown in cases.items():
            with self.subTest(name), self.assertRaises(ec.CleanupSkipped):
                ec.new_project(["OLD"], shown, NOTED, later)

    def test_ready_once_instagram_has_its_receipt(self):
        def release(status):
            return {"destinations": [{"platform": "youtube", "status": "pending"},
                                     {"platform": "instagram", "status": status}]}
        self.assertTrue(ec.ready(release("posted")))
        self.assertTrue(ec.ready(release("scheduled")))
        for status in ("pending", "unconfirmed", "failed"):
            self.assertFalse(ec.ready(release(status)), status)
        self.assertFalse(ec.ready({"destinations": [{"platform": "youtube", "status": "posted"}]}))


class FakeEdits:
    """Edits' Projects list, menu and confirm prompt as recorded 2026-10-01."""

    def __init__(self, projects):
        self.projects = dict(projects)
        self.screen = "home"
        self.held = None
        self.taps = []

    def ui_tree(self):
        def node(kind, name, label, x, y):
            return {"type": "XCUIElementType" + kind, "name": name, "label": label, "value": "",
                    "rect": {"x": x - 20, "y": y - 20, "width": 40, "height": 40}, "isVisible": "1"}
        children = [node("Button", "projects-tab", "Projects", 58, 900)]
        children += [node("Cell", ec.TILE_PREFIX + p, label, 82 + 138 * i, 236)
                     for i, (p, label) in enumerate(self.projects.items())]
        if self.screen == "menu":
            children.append(node("Button", "menu-item-Move to Trash", "Move to Trash", 114, 812))
        if self.screen == "confirm":
            children += [node("StaticText", "", "Move project to Trash?", 220, 393),
                         node("Button", "Move to Trash", "Move to Trash", 220, 524),
                         node("Button", "Cancel", "Cancel", 220, 576)]
        return {"type": "XCUIElementTypeApplication", "children": children}

    def long_press(self, x, y, seconds):
        for i, p in enumerate(self.projects):
            if (x, y) == (82 + 138 * i, 236):
                self.held, self.screen = p, "menu"

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.screen == "menu" and (x, y) == (114, 812):
            self.screen = "confirm"
        elif self.screen == "confirm" and (x, y) == (220, 524):
            del self.projects[self.held]
            self.screen = "home"
        elif self.screen == "confirm" and (x, y) == (220, 576):
            self.screen = "home"


@patch("scripts.phone_cleanup.time.sleep", lambda seconds: None)
class TrashTests(unittest.TestCase):
    def test_moves_exactly_that_project_after_the_prompt(self):
        from scripts import phone_cleanup
        edits = FakeEdits({"OLD": "Untitled project, 3h · 1 GB", OURS: "Untitled project, 20m · 167.9 MB"})
        with patch.object(phone_cleanup, "phone", edits):
            phone_cleanup.trash(OURS)
        self.assertEqual(list(edits.projects), ["OLD"])

    def test_a_missing_prompt_moves_nothing(self):
        from scripts import phone_cleanup
        edits = FakeEdits({OURS: "Untitled project, 20m · 167.9 MB"})
        edits.tap = lambda x, y: setattr(edits, "screen", "home")  # the menu tap opens nothing
        with patch.object(phone_cleanup, "phone", edits), self.assertRaises(ec.CleanupSkipped):
            phone_cleanup.trash(OURS)
        self.assertIn(OURS, edits.projects)


class RunTests(unittest.TestCase):
    def setUp(self):
        from video_drop.core import Store
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = Path(self.folder.name)
        self.db = self.state / "video-drop.sqlite"
        (self.state / "clip.mp4").write_bytes(b"video")
        with Store(self.db) as store:
            self.release_id = store.import_file(self.state / "clip.mp4")["id"]
            store.db.execute("UPDATE destination SET status='posted' WHERE release_id=? AND platform='instagram'",
                             (self.release_id,))
            store.db.commit()

    def run_cleanup(self, edits, **kwargs):
        from scripts import phone_cleanup
        with patch.object(phone_cleanup.share, "connect_sidetap", lambda: None), \
                patch.object(phone_cleanup.share, "phone", edits), \
                patch.object(phone_cleanup.share, "busy", lambda *a, **k: __import__("contextlib").nullcontext()), \
                patch.object(phone_cleanup.share, "layout", lambda **k: __import__(
                    "video_drop.phone_ui", fromlist=["PhoneLayout"]).PhoneLayout(440, 956)), \
                patch("scripts.phone_cleanup.time.sleep", lambda s: None):
            edits.unlock = lambda: None
            edits.open_app = lambda bundle: None
            edits.press_home = lambda: None
            edits.swipe = lambda *a: None
            return phone_cleanup.run(self.release_id, self.db, now=NOTED + timedelta(minutes=25), **kwargs)

    def test_trashes_once_and_settles_the_release(self):
        rc.save_baseline(self.state, self.release_id, "edits", {"projects": ["OLD"], "notedAt": NOTED.isoformat()})
        edits = FakeEdits({"OLD": "Untitled project, 3h · 1 GB", OURS: "Untitled project, 20m · 167.9 MB"})
        self.assertEqual(self.run_cleanup(edits)["kind"], "trashed")
        self.assertEqual(list(edits.projects), ["OLD"])
        note = rc.load_baseline(self.state, self.release_id, "edits")
        self.assertEqual((note["cleanup"]["project"], note["projects"]), (OURS, ["OLD"]))
        self.assertEqual(self.run_cleanup(FakeEdits({}))["kind"], "done")  # never twice

    def test_dry_run_moves_nothing_and_settles_nothing(self):
        rc.save_baseline(self.state, self.release_id, "edits", {"projects": ["OLD"], "notedAt": NOTED.isoformat()})
        edits = FakeEdits({OURS: "Untitled project, 20m · 167.9 MB"})
        self.assertEqual(self.run_cleanup(edits, dry_run=True)["kind"], "would_trash")
        self.assertIn(OURS, edits.projects)
        self.assertNotIn("cleanup", rc.load_baseline(self.state, self.release_id, "edits"))

    def test_no_note_from_before_the_upload_leaves_edits_alone(self):
        edits = FakeEdits({OURS: "Untitled project, 20m · 167.9 MB"})
        self.assertEqual(self.run_cleanup(edits)["kind"], "skipped")
        self.assertIn(OURS, edits.projects)

    def test_an_ambiguous_list_is_settled_as_skipped(self):
        rc.save_baseline(self.state, self.release_id, "edits", {"projects": [], "notedAt": NOTED.isoformat()})
        edits = FakeEdits({OURS: "Untitled project, 20m · 1 GB", "B": "Untitled project, 5m · 1 GB"})
        self.assertEqual(self.run_cleanup(edits)["kind"], "skipped")
        self.assertEqual(len(edits.projects), 2)
        self.assertEqual(rc.load_baseline(self.state, self.release_id, "edits")["cleanup"]["kind"], "skipped")


class ServerCleanupTests(unittest.TestCase):
    def test_due_only_when_noted_unsettled_and_instagram_confirmed(self):
        from video_drop import server
        from video_drop.core import Store
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "clip.mp4").write_bytes(b"video")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(state / "clip.mp4")["id"]
            with patch.object(server, "STATE", state):
                rc.save_baseline(state, release_id, "edits", {"projects": [], "notedAt": NOTED.isoformat()})
                self.assertEqual(server.cleanup_due(), [])  # Instagram still pending
                with Store(state / "video-drop.sqlite") as store:
                    store.db.execute("UPDATE destination SET status='posted' WHERE platform='instagram'")
                    store.db.commit()
                self.assertEqual(server.cleanup_due(), [release_id])
                (state / server.CLEANUP_ATTEMPTS_FILE).write_text(json.dumps({str(release_id): 3}))
                self.assertEqual(server.cleanup_due(), [])  # gave up after three failed tries
                (state / server.CLEANUP_ATTEMPTS_FILE).unlink()
                with Store(state / "video-drop.sqlite") as store:
                    store.set_phone_checks({"removeAfterPost": False})
                self.assertEqual(server.cleanup_due(), [])


if __name__ == "__main__":
    unittest.main()
