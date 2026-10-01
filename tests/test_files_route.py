"""The Files-app route: path mapping per provider, route selection, and every Files screen decision.

Screens are synthetic, modeled on the reference iPhone 16 Pro Max (iOS 26.7)
recordings of the Files app. They carry placeholder names only ("example-video",
"_Clips"); the private recordings stay under .state/fixtures.
"""

from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from test_size_independence import DEVICES, REFERENCE, Item, elements
from scripts import phone_youtube
from video_drop import source_route
from video_drop.core import PHONE_CHECK_DEFAULTS, Store
from video_drop.files_app import FILES_BUNDLE, FilesApp, FilesError, download_bound, folder_title
from video_drop.screens.labels import Labels
from video_drop.screens.snapshot import Snapshot
from video_drop.source_route import FILES_APP, ONEDRIVE_APP, FilesPath, SourceRouteError, choose_route, files_path

W = PureWindowsPath
MB = 271_200_000  # shown as "271.2 MB"
SIZE = "271.2 MB"
LABELS = Labels.load(Path(__file__).resolve().parent.parent / "maps", "en")


class PathMappingTests(unittest.TestCase):
    def test_each_provider_maps_its_sync_root_to_its_files_location(self):
        cases = {
            "OneDrive": (W(r"C:\Users\tester\OneDrive"), W(r"C:\Users\tester\OneDrive\_Clips\example-video.mp4"),
                         ("OneDrive",), ("Files", "_Clips")),
            "Google Drive": (W(r"G:\My Drive"), W(r"G:\My Drive\_Clips\example-video.mp4"),
                             ("Google Drive", "Drive"), ("My Drive", "_Clips")),
            "Dropbox": (W(r"C:\Users\tester\Dropbox"), W(r"C:\Users\tester\Dropbox\_Clips\example-video.mp4"),
                        ("Dropbox",), ("_Clips",)),
            "iCloud Drive": (W(r"C:\Users\tester\iCloudDrive"),
                             W(r"C:\Users\tester\iCloudDrive\Exports\Final\example-video.mp4"),
                             ("iCloud Drive",), ("Exports", "Final")),
        }
        for provider, (root, source, locations, folders) in cases.items():
            with self.subTest(provider=provider):
                self.assertEqual(files_path(provider, root, source),
                                 FilesPath(provider, locations, folders, "example-video.mp4"))

    def test_a_file_at_the_root_has_only_the_provider_prefix(self):
        self.assertEqual(files_path("Dropbox", W(r"D:\Dropbox"), W(r"D:\Dropbox\example-video.mp4")).folders, ())
        self.assertEqual(files_path("Google Drive", W(r"G:\My Drive"), W(r"G:\My Drive\example-video.mp4")).folders,
                         ("My Drive",))

    def test_root_matches_without_case_and_the_phone_gets_the_source_spelling(self):
        found = files_path("Dropbox", W(r"c:\users\tester\dropbox"), W(r"C:\Users\tester\Dropbox\My Clips\a.mp4"))
        self.assertEqual(found.folders, ("My Clips",))
        self.assertEqual(found.shown, "Dropbox › My Clips")

    def test_outside_the_root_or_an_unknown_provider_is_refused(self):
        with self.assertRaises(SourceRouteError):
            files_path("Dropbox", W(r"C:\Users\tester\Dropbox"), W(r"C:\Users\tester\Dropbox Backup\a.mp4"))
        with self.assertRaises(SourceRouteError):
            files_path("Dropbox", W(r"C:\Users\tester\Dropbox"), W(r"C:\Users\tester\Dropbox"))
        with self.assertRaises(SourceRouteError):
            files_path("Box", W(r"C:\Box"), W(r"C:\Box\a.mp4"))


ONEDRIVE, DROPBOX = Path("C:/Users/tester/OneDrive"), Path("C:/Users/tester/OneDrive/Nested Dropbox")
GDRIVE = Path("G:/My Drive")
CLOUDS = [("OneDrive", ONEDRIVE), ("Dropbox", DROPBOX), ("Google Drive", GDRIVE)]


class RouteSelectionTests(unittest.TestCase):
    def test_onedrive_keeps_the_onedrive_app_unless_the_setting_forces_files(self):
        source = ONEDRIVE / "_Clips" / "example-video.mp4"
        self.assertEqual(choose_route(source, CLOUDS, force_files=False),
                         source_route.Route(ONEDRIVE_APP, "OneDrive"))
        forced = choose_route(source, CLOUDS, force_files=True)
        self.assertEqual((forced.kind, forced.files.folders), (FILES_APP, ("Files", "_Clips")))

    def test_every_other_provider_goes_through_files(self):
        for source, provider in ((GDRIVE / "_Clips" / "a.mp4", "Google Drive"), (DROPBOX / "a.mp4", "Dropbox")):
            for force in (False, True):
                with self.subTest(provider=provider, force=force):
                    route = choose_route(source, CLOUDS, force_files=force)
                    self.assertEqual((route.kind, route.provider), (FILES_APP, provider))

    def test_a_source_outside_every_cloud_is_refused(self):
        with self.assertRaisesRegex(SourceRouteError, "not inside"):
            choose_route(Path("C:/Exports/a.mp4"), CLOUDS, force_files=False)

    def test_the_setting_is_a_phone_check_with_a_switch(self):
        self.assertIs(PHONE_CHECK_DEFAULTS[source_route.FILES_SETTING], False)


class ReleaseRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.clouds = [("OneDrive", base / "OneDrive"), ("Dropbox", base / "Dropbox")]
        self.db = base / "state" / "video-drop.sqlite"
        self.ids = {}
        for provider, root in self.clouds:
            (root / "_Clips").mkdir(parents=True)
            source = root / "_Clips" / f"example-{provider}.mp4"
            source.write_bytes(provider.encode() * 1000)
            with Store(self.db) as store:
                self.ids[provider] = store.import_file(source)["id"]
        self.sizes = {provider: len(provider.encode()) * 1000 for provider, _ in self.clouds}

    def tearDown(self):
        self.temp.cleanup()

    def data(self, provider):
        return {"releaseId": self.ids[provider], "filename": f"example-{provider}.mp4",
                "sizeBytes": self.sizes[provider]}

    def test_route_follows_the_release_source_and_the_setting(self):
        self.assertEqual(source_route.release_route(self.db, self.data("OneDrive"), self.clouds).kind, ONEDRIVE_APP)
        dropbox = source_route.release_route(self.db, self.data("Dropbox"), self.clouds)
        self.assertEqual((dropbox.kind, dropbox.files.locations, dropbox.files.folders),
                         (FILES_APP, ("Dropbox",), ("_Clips",)))
        with Store(self.db) as store:
            store.set_phone_checks({source_route.FILES_SETTING: True})
        forced = source_route.release_route(self.db, self.data("OneDrive"), self.clouds)
        self.assertEqual((forced.kind, forced.files.folders), (FILES_APP, ("Files", "_Clips")))

    def test_a_different_file_than_the_flow_verified_is_refused(self):
        for change in ({"filename": "other.mp4"}, {"sizeBytes": 1}):
            with self.subTest(change=change), self.assertRaisesRegex(SourceRouteError, "does not match"):
                source_route.release_route(self.db, {**self.data("Dropbox"), **change}, self.clouds)
        with self.assertRaisesRegex(SourceRouteError, "not in"):
            source_route.release_route(self.db, {**self.data("Dropbox"), "releaseId": 999}, self.clouds)

    def test_the_database_is_only_read(self):
        before = self.db.stat().st_mtime_ns
        source_route.release_route(self.db, self.data("Dropbox"), self.clouds)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_open_source_file_dispatches_by_route(self):
        with patch.object(phone_youtube, "open_onedrive_file") as onedrive, \
                patch.object(phone_youtube, "FilesApp") as files, patch.object(phone_youtube, "stage"):
            phone_youtube.open_source_file(self.data("OneDrive"), db=self.db, clouds=self.clouds)
            onedrive.assert_called_once()
            files.assert_not_called()
            phone_youtube.open_source_file(self.data("Dropbox"), db=self.db, clouds=self.clouds)
            onedrive.assert_called_once()
            target, size = files.return_value.open_file.call_args.args
            self.assertEqual((target.provider, target.folders, size), ("Dropbox", ("_Clips",), self.sizes["Dropbox"]))

    def test_open_source_file_reports_route_and_files_problems_as_upload_errors(self):
        with patch.object(phone_youtube, "stage"), \
                self.assertRaisesRegex(phone_youtube.PhoneUploadError, "does not match"):
            phone_youtube.open_source_file({**self.data("Dropbox"), "sizeBytes": 1}, db=self.db, clouds=self.clouds)
        with patch.object(phone_youtube, "FilesApp") as files, patch.object(phone_youtube, "stage"), \
                self.assertRaisesRegex(phone_youtube.PhoneUploadError, "switched off"):
            files.return_value.open_file.side_effect = FilesError("Dropbox is switched off in the Files app")
            phone_youtube.open_source_file(self.data("Dropbox"), db=self.db, clouds=self.clouds)


# ---- synthetic Files screens (placeholder names only) ----------------------

TAB = [Item("TabBar", "Tab Bar", (0, 873, 440, 83), "stretch", "bottom", name="DOC.browsingModeTabBar"),
       Item("Button", "Recents", (87, 877, 94, 54), "center", "bottom"),
       Item("Button", "Browse", (259, 877, 94, 54), "center", "bottom", value="1")]


def nav(back, title, *, menu=False, search=True):
    items = [Item("NavigationBar", "", (0, 62, 440, 114), "stretch",
                  name="FullDocumentManagerViewControllerNavigationBar")]
    if back:
        items.append(Item("Button", back, (20, 62, 44, 44), name="BackButton"))
    items.append(Item("Button", f"{title}, Actions Menu", (175, 73, 90, 22), "center") if menu
                 else Item("StaticText", title, (190, 73, 60, 22), "center"))
    items.append(Item("Button", "More", (380, 66, 36, 36), "right", name="OverflowBarButtonItem"))
    if search:
        items.append(Item("SearchField", "Search", (20, 117, 400, 44), "stretch"))
    return items


def folder(back, title, rows, *, menu=True):
    return [*nav(back, title, menu=menu),
            Item("CollectionView", "", (0, 0, 440, 956), "stretch", "stretch", name="File View", value="List Mode"),
            *rows, *TAB]


def folder_row(name, top):
    return [Item("Cell", f"{name}, 1/2/26 - 3 items", (0, top, 440, 64), "stretch", name=f"{name}, Folder")]


def file_row(stem, top, *, size=SIZE, ext="mp4", download=False):
    row = [Item("Cell", f"{stem}, 1/2/26 - {size}" + (", iCloud download" if download else ""),
                (0, top, 440, 64), "stretch", name=f"{stem}, {ext}")]
    if download:
        row.append(Item("Button", "iCloud download", (397, top + 18, 27, 28), "right"))
    return row


def browse(*locations):
    rows = [Item("StaticText", "Browse", (19, 119, 120, 42)),
            Item("SearchField", "Search", (20, 169, 400, 44), "stretch"),
            Item("Cell", "Locations", (20, 341, 400, 45), "stretch", name="DOC.sidebar.header.Locations"),
            Item("Cell", "On My iPhone", (20, 385, 400, 53), "stretch", name="DOC.sidebar.item.On My iPhone")]
    for index, location in enumerate(locations):
        rows.append(Item("Cell", location, (20, 437 + 52 * index, 400, 53), "stretch",
                         name=f"DOC.sidebar.item.{location}"))
    return [*rows, *TAB]


def unavailable(back, title):
    return [*nav(back, title), Item("StaticText", "Content Unavailable", (116, 470, 208, 28), "center"),
            Item("Button", "Try Again", (172, 551, 96, 35), "center"), *TAB]


def loading(back, title):
    return [*nav(back, title), Item("ActivityIndicator", "In progress", (210, 454, 20, 20), "center"),
            Item("StaticText", "LOADING", (187, 484, 66, 18), "center"), *TAB]


TURN_ON = [Item("Alert", "Turn On “Dropbox”?", (60, 406, 320, 172), "center"),
           Item("Button", "Cancel", (76, 514, 140, 48), "center"),
           Item("Button", "Turn On", (224, 514, 140, 48), "center")]


def context_menu(stem, size=SIZE):
    return [Item("Other", "Preview", (20, 286, 400, 59), "stretch"),
            Item("Other", f"{stem}, 1/2/26 - {size}", (19, 286, 402, 59), "stretch"),
            Item("Button", "Copy", (28, 364, 78, 78)), Item("Button", "Share", (184, 364, 78, 78)),
            Item("Button", "Quick Look", (20, 452, 250, 43)), Item("Button", "Get Info", (20, 557, 250, 43))]


def share_sheet(caption, details=f"Video · {SIZE}"):
    return [Item("Other", "dismiss popup", (0, 0, 440, 956), "stretch", "stretch", name="PopoverDismissRegion"),
            Item("Other", "", (9, 62, 422, 528), "stretch", name="ShareSheet.RemoteContainerView"),
            Item("Other", caption, (87, 40, 308, 19), name="LP.CaptionBar.TopCaption"),
            Item("Other", details, (87, 58, 308, 21), name="LP.CaptionBar.BottomCaption"),
            Item("Other", "", (0, 0, 422, 528), name="shareSheet.activity.contentView"),
            Item("Cell", "Instagram", (121, 253, 83, 129), name="shareCell")]


def dropbox_screens(files_rows, **extra):
    """Files restored inside On My iPhone; Dropbox > _Clips holds the file."""
    return {
        "restored": folder("On My iPhone", "Downloads", file_row("other-file", 176)),
        "on_my_iphone": folder("Browse", "On My iPhone", folder_row("Downloads", 176), menu=False),
        "browse": browse("Dropbox", "iCloud Drive"),
        "dropbox": folder("Browse", "Dropbox", [*folder_row("Archive", 176), *folder_row("_Clips", 240)], menu=False),
        "clips": folder("Dropbox", "_Clips", files_rows),
        "menu": context_menu("example-video"),
        "sheet": share_sheet("example-video"),
        **extra,
    }


HAPPY = {
    ("restored", "BACK"): "on_my_iphone", ("on_my_iphone", "BACK"): "browse",
    ("browse", "Dropbox"): "dropbox", ("dropbox", "_Clips, 1/2/26 - 3 items"): "clips",
    ("clips", f"LONG example-video, 1/2/26 - {SIZE}"): "menu", ("menu", "Share"): "sheet",
}


class FakeFiles:
    """Serves synthetic Files screens; taps and holds move between them by the control they land on."""

    def __init__(self, screens, flow, start="restored", *, device=REFERENCE, after_reads=None):
        self.screens, self.flow, self.state, self.device = screens, dict(flow), start, device
        self.after_reads = dict(after_reads or {})  # state -> (reads, next state): a screen that changes by itself
        self.reads, self.log, self.now = 0, [], 0.0

    # clock for FilesApp
    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def items(self):
        return self.screens[self.state]

    def screen_info(self):
        return {"width": self.device.width, "height": self.device.height}

    def current_app(self):
        return {"bundleId": FILES_BUNDLE}

    def ui_tree(self):
        if self.state in self.after_reads:
            count, following = self.after_reads[self.state]
            self.reads += 1
            if self.reads > count:
                self.state, self.reads = following, 0
        children = [{"type": "XCUIElementType" + e.type, "label": e.label, "name": e.name, "value": e.value,
                     "isVisible": "1", "rect": {"x": e.left, "y": e.top, "width": e.width, "height": e.height}}
                    for e in elements(self.items(), self.device)]
        return {"type": "XCUIElementTypeApplication", "label": "Files", "isVisible": "1",
                "rect": {"x": 0, "y": 0, "width": self.device.width, "height": self.device.height},
                "children": children}

    def _hit(self, x, y):
        hits = [e for e in elements(self.items(), self.device)
                if e.label and e.type != "CollectionView" and e.left <= x <= e.left + e.width
                and e.top <= y <= e.top + e.height]
        if not hits:
            return None
        hit = min(hits, key=lambda e: e.width * e.height)
        return "BACK" if hit.name == "BackButton" else hit.label

    def tap(self, x, y):
        label = self._hit(x, y)
        self.log.append(("tap", label))
        self.state = self.flow.get((self.state, label), self.state)

    def long_press(self, x, y, seconds=1.0):
        label = self._hit(x, y)
        self.log.append(("hold", label))
        self.state = self.flow.get((self.state, f"LONG {label}"), self.state)

    def swipe(self, *args):
        self.log.append(("swipe", None))
        self.state = self.flow.get((self.state, "SWIPE"), self.state)

    def press_home(self):
        self.log.append(("home", None))

    def close_app(self, name):
        self.log.append(("close", name))
        return True

    def open_app(self, name, wait_seconds=0.0):
        self.log.append(("open", name))

    def taps(self):
        return [label for kind, label in self.log if kind in ("tap", "hold")]


def target(provider="Dropbox", locations=("Dropbox",), folders=("_Clips",), filename="example-video.mp4"):
    return FilesPath(provider, locations, folders, filename)


def app(phone, **options):
    return FilesApp(phone, labels=LABELS, sleep=phone.sleep, clock=phone.clock, **options)


class FilesFlowTests(unittest.TestCase):
    def test_happy_path_opens_the_exact_file_and_leaves_the_share_sheet_up_on_every_size(self):
        for device in DEVICES:
            with self.subTest(device=device.name):
                phone = FakeFiles(dropbox_screens([*file_row("example-video 2", 176), *file_row("example-video", 240)]),
                                  HAPPY, device=device)
                app(phone).open_file(target(), MB)
                self.assertEqual(phone.state, "sheet")
                self.assertEqual(phone.taps(), ["BACK", "BACK", "Dropbox", "_Clips, 1/2/26 - 3 items",
                                                f"example-video, 1/2/26 - {SIZE}", "Share"])
                self.assertEqual(phone.log[:3], [("home", None), ("close", FILES_BUNDLE), ("open", FILES_BUNDLE)])

    def test_google_drive_is_found_under_either_location_name(self):
        screens = dropbox_screens(file_row("example-video", 176), browse=browse("Drive"),
                                  drive=folder("Browse", "Drive", folder_row("My Drive", 176), menu=False),
                                  my_drive=folder("Drive", "My Drive", folder_row("_Clips", 176)),
                                  clips=folder("My Drive", "_Clips", file_row("example-video", 176)))
        flow = {**HAPPY, ("browse", "Drive"): "drive", ("drive", "My Drive, 1/2/26 - 3 items"): "my_drive",
                ("my_drive", "_Clips, 1/2/26 - 3 items"): "clips"}
        phone = FakeFiles(screens, flow)
        app(phone).open_file(target("Google Drive", ("Google Drive", "Drive"), ("My Drive", "_Clips")), MB)
        self.assertEqual(phone.state, "sheet")

    def test_a_location_listed_under_two_names_is_not_guessed(self):
        phone = FakeFiles(dropbox_screens([], browse=browse("Drive", "Google Drive")), HAPPY)
        with self.assertRaisesRegex(FilesError, "more than one"):
            app(phone).open_file(target("Google Drive", ("Google Drive", "Drive"), ("My Drive",)), MB)

    def test_provider_missing_from_browse_stops_with_the_turn_on_fix(self):
        phone = FakeFiles(dropbox_screens([], browse=browse("iCloud Drive")), HAPPY)
        with self.assertRaises(FilesError) as caught:
            app(phone).open_file(target(), MB)
        message = str(caught.exception)
        for words in ("Dropbox is not listed", "Browse", "Edit", "turn on Dropbox", "install the Dropbox app"):
            self.assertIn(words, message)
        self.assertNotIn("Dropbox", phone.taps())

    def test_turn_on_prompt_is_cancelled_never_accepted(self):
        phone = FakeFiles(dropbox_screens([], turn_on=TURN_ON), {**HAPPY, ("browse", "Dropbox"): "turn_on",
                                                                  ("turn_on", "Cancel"): "browse"})
        with self.assertRaisesRegex(FilesError, "switched off in the Files app.*turn on Dropbox"):
            app(phone).open_file(target(), MB)
        self.assertEqual(phone.taps()[-1], "Cancel")
        self.assertNotIn("Turn On", phone.taps())
        self.assertEqual(phone.state, "browse")

    def test_a_leftover_turn_on_prompt_at_launch_is_dismissed_and_the_route_continues(self):
        phone = FakeFiles(dropbox_screens(file_row("example-video", 176), leftover=TURN_ON),
                          {**HAPPY, ("leftover", "Cancel"): "restored"}, start="leftover")
        app(phone).open_file(target(), MB)
        self.assertEqual((phone.state, phone.taps()[0]), ("sheet", "Cancel"))
        self.assertNotIn("Turn On", phone.taps())

    def test_loading_then_content_unavailable_is_retried_within_a_bound(self):
        screens = dropbox_screens(file_row("example-video", 176), loading=loading("Browse", "Dropbox"),
                                  broken=unavailable("Browse", "Dropbox"))
        flow = {**HAPPY, ("browse", "Dropbox"): "loading", ("broken", "Try Again"): "dropbox"}
        phone = FakeFiles(screens, flow, after_reads={"loading": (3, "broken")})
        app(phone).open_file(target(), MB)
        self.assertEqual(phone.state, "sheet")
        self.assertEqual(phone.taps().count("Try Again"), 1)

        stuck = FakeFiles(screens, {**HAPPY, ("browse", "Dropbox"): "broken"})
        with self.assertRaisesRegex(FilesError, "Content Unavailable.*Open the Dropbox app"):
            app(stuck, retries=2).open_file(target(), MB)
        self.assertEqual(stuck.taps().count("Try Again"), 2)

    def test_a_folder_that_never_loads_fails_within_the_bound(self):
        screens = dropbox_screens([], loading=loading("Browse", "Dropbox"))
        phone = FakeFiles(screens, {**HAPPY, ("browse", "Dropbox"): "loading"})
        with self.assertRaisesRegex(FilesError, "did not open Dropbox"):
            app(phone, load_timeout=10).open_file(target(), MB)
        self.assertLess(phone.now, 20)

    def test_the_file_is_found_after_scrolling_and_the_list_end_stops_the_search(self):
        first = [*file_row("clip-a", 176), *file_row("clip-b", 240)]
        second = [*file_row("clip-b", 176), *file_row("example-video", 240)]
        screens = dropbox_screens(first, clips_2=folder("Dropbox", "_Clips", second))
        flow = {**HAPPY, ("clips", "SWIPE"): "clips_2", ("clips_2", f"LONG example-video, 1/2/26 - {SIZE}"): "menu"}
        phone = FakeFiles(screens, flow)
        app(phone).open_file(target(), MB)
        self.assertEqual(phone.state, "sheet")

        missing = FakeFiles(dropbox_screens(first), HAPPY)
        with self.assertRaisesRegex(FilesError, "no example-video.mp4 in Dropbox › _Clips"):
            app(missing).open_file(target(), MB)
        self.assertEqual([kind for kind, _ in missing.log].count("swipe"), 1)

    def test_only_the_exact_name_and_type_match(self):
        rows = [*file_row("example-video", 176, ext="mov"), *file_row("example-video copy", 240),
                *file_row("Example-Video", 304)]
        phone = FakeFiles(dropbox_screens(rows), HAPPY)
        with self.assertRaisesRegex(FilesError, "no example-video.mp4"):
            app(phone).open_file(target(), MB)
        self.assertNotIn("hold", [kind for kind, _ in phone.log])

    def test_a_different_size_stops_before_the_menu(self):
        phone = FakeFiles(dropbox_screens(file_row("example-video", 176, size="12 MB")), HAPPY)
        with self.assertRaisesRegex(FilesError, "does not show the size.*finish syncing"):
            app(phone).open_file(target(), MB)
        self.assertNotIn("hold", [kind for kind, _ in phone.log])

    def test_a_cloud_only_file_is_downloaded_and_waited_for(self):
        screens = dropbox_screens(file_row("example-video", 176, download=True),
                                  fetching=folder("Dropbox", "_Clips", file_row("example-video", 176, download=True)),
                                  fetched=folder("Dropbox", "_Clips", file_row("example-video", 176)))
        flow = {**HAPPY, ("clips", "iCloud download"): "fetching",
                ("fetched", f"LONG example-video, 1/2/26 - {SIZE}"): "menu"}
        phone = FakeFiles(screens, flow, after_reads={"fetching": (4, "fetched")})
        app(phone).open_file(target(), MB)
        self.assertEqual(phone.state, "sheet")
        self.assertIn("iCloud download", phone.taps())

    def test_a_download_that_never_finishes_fails_clearly_within_its_bound(self):
        screens = dropbox_screens(file_row("example-video", 176, download=True))
        phone = FakeFiles(screens, HAPPY)
        with self.assertRaisesRegex(FilesError, "still downloading from Dropbox.*Nothing was posted"):
            app(phone).open_file(target(), MB)
        self.assertGreaterEqual(phone.now, download_bound(MB))
        self.assertLess(phone.now, download_bound(MB) + 30)
        self.assertNotIn("hold", [kind for kind, _ in phone.log])

    def test_a_menu_for_another_file_or_a_wrong_share_sheet_is_refused(self):
        wrong_menu = FakeFiles(dropbox_screens(file_row("example-video", 176), menu=context_menu("other-file")), HAPPY)
        with self.assertRaisesRegex(FilesError, "different file"):
            app(wrong_menu).open_file(target(), MB)
        self.assertNotIn("Share", wrong_menu.taps())
        for sheet, words in ((share_sheet("other-file"), "wrong file name"),
                             (share_sheet("example-video", "Video · 12 MB"), "wrong file size")):
            phone = FakeFiles(dropbox_screens(file_row("example-video", 176), sheet=sheet), HAPPY)
            with self.subTest(words=words), self.assertRaisesRegex(FilesError, words):
                app(phone).open_file(target(), MB)

    def test_the_share_sheet_may_show_the_full_file_name(self):
        phone = FakeFiles(dropbox_screens(file_row("example-video", 176), sheet=share_sheet("example-video.mp4")),
                          HAPPY)
        app(phone).open_file(target(), MB)
        self.assertEqual(phone.state, "sheet")


class FolderTitleTests(unittest.TestCase):
    def snapshot(self, items):
        return Snapshot(440, 956, elements(items, REFERENCE))

    def test_title_comes_from_the_menu_button_or_the_plain_title(self):
        self.assertEqual(folder_title(self.snapshot(folder("Dropbox", "_Clips", [])), LABELS), "_Clips")
        self.assertEqual(folder_title(self.snapshot(folder("Browse", "Dropbox", [], menu=False)), LABELS), "Dropbox")
        self.assertIsNone(folder_title(self.snapshot(browse("Dropbox")), LABELS))


FIXTURES = Path(__file__).resolve().parent.parent / ".state" / "fixtures" / "files"


def recording(stem):
    from video_drop.screens.snapshot import load_fixture
    return load_fixture(FIXTURES / stem)


@unittest.skipUnless(any(FIXTURES.glob("*.json")), "no local Files recordings under .state/fixtures/files")
class RecordedFilesTests(unittest.TestCase):
    """Local only: the Files decisions read the reference phone's recordings; names come from the recordings."""

    def test_browse_finds_the_onedrive_location_once(self):
        from video_drop.files_app import location_cells
        self.assertEqual(len(location_cells(recording("browse-20260929-151036"), ("OneDrive",))), 1)

    def test_folder_titles_and_rows_resolve(self):
        from video_drop.files_app import file_cells, folder_cells, needs_download, split_cell_name, visible
        root = recording("onedrive-root-20260929-151524")
        self.assertEqual(folder_title(root, LABELS), "OneDrive")
        self.assertEqual(len(folder_cells(root, "Files", LABELS)), 1)
        videos = recording("onedrive-videos-20260929-152135")
        self.assertEqual(folder_title(videos, LABELS), "_Videos")
        files = [e for e in videos.elements if e.type == "Cell" and split_cell_name(e.name)[1] not in ("", "Folder")]
        self.assertTrue(files)
        for cell in files:
            shown, kind = split_cell_name(cell.name)
            self.assertEqual(file_cells(videos, f"{shown}.{kind}"), [cell])
            self.assertTrue(visible(cell, videos))
        folders = [e for e in videos.elements if e.type == "Cell" and e.name.endswith(", Folder")]
        self.assertTrue(folders and all(needs_download(videos, cell, LABELS) for cell in folders))
        self.assertFalse(any(needs_download(videos, cell, LABELS) for cell in files))

    def test_share_sheet_caption_is_verified(self):
        import re
        from video_drop.files_app import files_maps, verify_share_sheet
        from video_drop.screens.matcher import identify
        sheet = recording("share-sheet-20260929-153851")
        screen = identify(sheet, files_maps(), LABELS)
        caption = next(e.label for e in sheet.elements if e.name == "LP.CaptionBar.TopCaption")
        details = next(e.label for e in sheet.elements if e.name == "LP.CaptionBar.BottomCaption")
        size = round(float(re.search(r"([\d.]+) MB", details).group(1)) * 1_000_000)
        verify_share_sheet(sheet, screen, LABELS, caption + ".mp4", size)
        with self.assertRaises(FilesError):
            verify_share_sheet(sheet, screen, LABELS, caption + "x.mp4", size)
        with self.assertRaises(FilesError):
            verify_share_sheet(sheet, screen, LABELS, caption + ".mp4", size * 2)


if __name__ == "__main__":
    unittest.main()


class PathSpellingTests(unittest.TestCase):
    def test_another_spelling_of_the_same_folder_is_still_inside(self):
        # GitHub's Windows runners hand out C:\Users\RUNNER~1\... temp paths while stored sources are
        # the long form; a junction or a relative path is the same case on a user's PC.
        from video_drop.setup_check import inside
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "OneDrive"
            (root / "_Clips").mkdir(parents=True)
            clip = root / "_Clips" / "clip.mp4"
            clip.write_bytes(b"x")
            spelled = Path(os.path.relpath(clip)) if Path.cwd().drive == clip.drive else clip
            self.assertTrue(inside(spelled, root.resolve()))
            self.assertTrue(inside(clip.resolve(), root))
            self.assertFalse(inside(Path(folder) / "elsewhere.mp4", root))
