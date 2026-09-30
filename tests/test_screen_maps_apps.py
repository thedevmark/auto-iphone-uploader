"""Every screen map recognizes its screen and resolves each control uniquely, on every iPhone size.

Synthetic screens copy the frames recorded on the reference phone (iPhone 16
Pro Max, iOS 26.7) for YouTube 21.38.3, Instagram, Threads, TikTok and Files.
They carry placeholder names only ("creator", "example-video"). The local
recordings under ``.state/fixtures`` are replayed by ``RecordedScreensTests``,
which skips when they are absent.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_size_independence import (DEVICES, FIXTURES, LABELS, MAPS, REFERENCE, SCREENS, YOUTUBE, Item, back,
                                     elements, rendered_snapshot, resolve, scale_snapshot)
from video_drop.screens.matcher import AmbiguousMatch, MatchError, find, identify
from video_drop.screens.model import load_maps
from video_drop.screens.runner import IrreversibleError, Runner
from video_drop.screens.snapshot import load_fixture

EDITS = "com.burbn.basel"
INSTAGRAM, THREADS, TIKTOK, FILES = "com.burbn.instagram", "com.burbn.barcelona", "com.zhiliaoapp.musically", \
    "com.apple.DocumentsApp"
DOT, ELLIPSIS = "·", "…"


def ig_nav(title, width, left=None):
    return [Item("NavigationBar", "", (0, 62, 440, 54), "stretch", name="navigation-bar"),
            Item("Button", "Back", (24, 66, 36, 36), name="BackButton"),
            Item("StaticText", title, (left if left is not None else 220 - width / 2, 74, width, 20), "center")]


def ig_switch_row(label, top, height):
    """IG exposes a row-sized Switch carrying the state in its label, plus the real toggle (value is always 0)."""
    return [Item("Switch", label, (0, top, 440, height), "stretch", value="0"),
            Item("Switch", "", (361, top + 15, 63, 29), "right", name="igds-switch", value="0")]


def crosspost_row(label, text, top, *, switch=True):
    row = [Item("Cell", label, (0, top, 440, 60), "stretch"),
           Item("StaticText", text, (73, top + 30, 276, 18), "stretch")]
    if switch:
        row.append(Item("Switch", "", (361, top + 16, 63, 28), "right", name="share-service-cell-switch", value="0"))
    return row


REEL_BOTTOM = [Item("Button", "Save draft", (16, 865, 196, 45), "half_left", "bottom", name="save-draft-button")]
REEL_LOWER = [
    *ig_nav("New reel", 68),
    Item("Cell", "Tag people, ", (0, 163, 440, 48), "stretch"),
    # Scrolled to the end, the last rows sit just above the pinned Share bar.
    Item("Cell", f"Also share on{ELLIPSIS}, 2 profiles", (0, 724, 440, 49), "stretch", "bottom"),
    Item("StaticText", f"Also share on{ELLIPSIS}", (52, 739, 111, 20), "left", "bottom"),
    Item("Cell", "More options", (0, 801, 440, 49), "stretch", "bottom"),
    Item("StaticText", "More options", (52, 816, 96, 20), "left", "bottom"),
    *REEL_BOTTOM,
]
SHARE = Item("Button", "Share", (228, 865, 196, 45), "half_right", "bottom", name="share-sheet-share-button")
SCHEDULE = Item("Button", "Schedule", (228, 865, 196, 45), "half_right", "bottom", name="share-sheet-share-button")
MORE_OPTIONS_TOP = [
    *ig_nav("More options", 102),
    Item("Cell", "", (0, 124, 440, 48), "stretch"),
    *ig_switch_row("Checked, Allow gifts on this reel, This selection will apply to this reel", 275, 94),
]
TAB_BAR = [Item("TabBar", "Tab Bar", (0, 873, 440, 83), "stretch", "bottom", name="DOC.browsingModeTabBar"),
           Item("Button", "Browse", (259, 877, 94, 54), "center", "bottom", value="1")]
YT_DETAILS_V2 = [
    Item("Button", "Back", (-2, 56, 44, 45), name="id.ui.browse.back.button"),
    Item("StaticText", "Add details", (46, 65, 109, 27)),
    Item("ScrollView", "", (0, 100, 440, 750), "stretch", "stretch", name="id.metadata_editor.scroll_view"),
    Item("Other", "Edit thumbnail, 0:52", (12, 112, 86, 131),
         name="id.elements.components.metadata_editor.open_shorts_thumbnail_editor"),
    Item("Other", "", (109, 136, 320, 40), "stretch", name="id.elements.components.metadata_editor.title"),
    Item("Other", "Creator, @creator", (0, 336, 440, 69), "stretch", name="id.elements.components.identity_chip_component"),
    Item("Button", "Visibility, Public", (0, 404, 440, 57), "stretch",
         name="id.elements.components.metadata_editor.privacy_picker_v2"),
    Item("Button", "", (0, 404, 440, 57), "stretch", name="id.elements.components.metadata_editor.privacy_picker_v2"),
    Item("Button", "Audience, No, it's not made for kids", (0, 460, 440, 57), "stretch",
         name="id.elements.components.metadata_editor.audience_picker"),
    Item("Button", "", (0, 460, 440, 57), "stretch", name="id.elements.components.metadata_editor.audience_picker"),
    Item("Button", "Show more", (12, 520, 416, 49), "stretch",
         name="id.elements.components.metadata_editor.expander.collapsed_button"),
    Item("Button", "Upload Short", (12, 862, 416, 48), "stretch", "bottom", name="id.metadata_editor.upload_button"),
    Item("StaticText", "Upload Short", (175, 877, 90, 18), "center", "bottom"),
]

# variant -> (bundle, (app, screen), items)
VARIANTS = {
    **{f"youtube/{name}": (YOUTUBE, ("youtube", name), items) for name, items in SCREENS.items()},
    "youtube/details_v2": (YOUTUBE, ("youtube", "details"), YT_DETAILS_V2),
    # The share extension lives in whichever app opened the share sheet; the stale sheet stays in the tree.
    "instagram/share_extension": (FILES, ("instagram", "share_extension"), [
        Item("Button", "Close", (380, 96, 36, 36), "right", name="header.closeButton"),
        Item("Other", "", (0, 72, 440, 884), "stretch", "stretch", name="shareSheet.activity.contentView"),
        Item("Cell", "Instagram", (131, 328, 83, 129), name="shareCell"),
        Item("NavigationBar", "", (0, 72, 440, 54), "stretch", name="navigation-bar"),
        Item("Button", "", (115, 73, 210, 42), "center"),
        Item("StaticText", "Sharing as creator", (123, 85, 202, 18), "center"),
        Item("StaticText", "Reel", (40, 591, 30, 18)),
        Item("StaticText", "Post", (149, 591, 32, 18)),
        Item("StaticText", "Story", (256, 591, 38, 18)),
        Item("StaticText", "Message", (354, 591, 62, 18)),
    ]),
    "instagram/new_reel": (INSTAGRAM, ("instagram", "new_reel"), [
        *ig_nav("New reel", 68),
        Item("Other", "Edit cover", (145, 140, 150, 264), "center"),
        Item("Button", "Preview", (155, 150, 87, 31), "center"),
        Item("Button", "Edit cover", (155, 363, 101, 31), "center"),
        Item("TextView", "Add a caption...", (16, 424, 408, 148), "stretch", name="caption-cell-text-view"),
        Item("Cell", "Tag people, ", (0, 580, 440, 48), "stretch"),
        *REEL_BOTTOM, SHARE,
    ]),
    "instagram/new_reel_lower": (INSTAGRAM, ("instagram", "new_reel"), [*REEL_LOWER, SHARE]),
    "instagram/new_reel_scheduled": (INSTAGRAM, ("instagram", "new_reel"), [*REEL_LOWER, SCHEDULE]),
    "instagram/more_options": (INSTAGRAM, ("instagram", "more_options"), [
        *MORE_OPTIONS_TOP,
        *ig_switch_row("Not checked, Schedule this reel, Some features may be unavailable with scheduled content.",
                       468, 77),
        *ig_switch_row("Checked, Upload at highest quality, Always upload the highest quality photos and videos",
                       545, 110),
    ]),
    "instagram/more_options_scheduled": (INSTAGRAM, ("instagram", "more_options"), [
        *MORE_OPTIONS_TOP,
        *ig_switch_row("Checked, Schedule this reel, Some features may be unavailable with scheduled content.",
                       468, 77),
        *ig_switch_row("Not checked, Upload at highest quality, Always upload the highest quality photos and videos",
                       545, 110),
    ]),
    "instagram/schedule_sheet": (INSTAGRAM, ("instagram", "schedule_sheet"), [
        Item("Other", "", (0, 620, 440, 736), "stretch", "bottom", name="ig-partial-modal-sheet-view-controller-content"),
        Item("Button", "Dismiss", (203, 635, 34, 3), "center", "bottom", name="Button"),
        Item("StaticText", "Schedule reel", (0, 658, 440, 25), "stretch", "bottom"),
        Item("StaticText", "Time zone is based on your device's settings", (16, 690, 408, 18), "stretch", "bottom"),
        Item("Button", "Done", (16, 850, 408, 45), "stretch", "bottom"),
    ]),
    "instagram/schedule_date": (INSTAGRAM, ("instagram", "schedule_date"), [
        Item("DatePicker", "", (94, 387, 320, 333), "center"),
        Item("Button", "Show year picker", (110, 403, 157, 38), "center", name="DatePicker.Show", value="September 2026"),
        Item("Button", "Previous Month", (325, 403, 44, 38), "center", name="DatePicker.PreviousMonth"),
        Item("Button", "Next Month", (368, 403, 44, 38), "center", name="DatePicker.NextMonth"),
        Item("Button", "Wednesday, September 30", (232, 656, 43, 46), "center"),
        Item("Button", "dismiss popup", (0, 0, 440, 956), "stretch", "stretch", name="PopoverDismissRegion"),
    ]),
    "instagram/also_share_on": (INSTAGRAM, ("instagram", "also_share_on"), [
        *ig_nav(f"Also share on{ELLIPSIS}", 120),
        *crosspost_row(f"creator, Threads {DOT} Public, On", f"Threads {DOT} Public", 124),
        *crosspost_row(f"Creator, Facebook {DOT} Public, On", f"Facebook {DOT} Public", 184),
        *ig_switch_row("Not checked, Your story", 306, 48),
    ]),
    # Scheduling a reel turns the Threads crosspost off and removes its switch.
    "instagram/also_share_on_scheduled": (INSTAGRAM, ("instagram", "also_share_on"), [
        *ig_nav(f"Also share on{ELLIPSIS}", 120),
        *crosspost_row(f"creator, Threads {DOT} Public, Off", f"Threads {DOT} Public", 124, switch=False),
        *crosspost_row(f"Creator, Facebook {DOT} Public, On", f"Facebook {DOT} Public", 184),
    ]),
    "instagram/stop_sharing_popup": (INSTAGRAM, ("instagram", "stop_sharing_popup"), [
        Item("StaticText", "Stop sharing on Threads?", (121, 391, 198, 20), "center"),
        Item("Button", "Don't share this reel", (70, 438, 300, 53), "center"),
        Item("Button", "Stop sharing all reels", (70, 490, 300, 53), "center"),
        Item("Button", "Cancel", (70, 542, 300, 53), "center"),
    ]),
    "instagram/account_switcher": (INSTAGRAM, ("instagram", "account_switcher"), [
        Item("Button", "Dimmed background", (0, 0, 440, 484), "stretch", name="bottom_sheet_background"),
        Item("Button", "Close", (200, 492, 40, 4), "center", "bottom", name="feed-controls-menu-drag-handle"),
        Item("Button", "INSTAGRAM profile, creator", (16, 509, 408, 64), "stretch", "bottom", value="1"),
        Item("Other", "", (16, 829, 408, 64), "stretch", "bottom", name="IdentitySwitcher_EntryPoint_AddInstagramAccount"),
        Item("Button", "Add Instagram account", (16, 829, 408, 64), "stretch", "bottom"),
    ]),
    "threads/composer": (THREADS, ("threads", "composer"), [
        Item("NavigationBar", "", (0, 82, 440, 54), "stretch", name="navigation-bar"),
        Item("Button", "Cancel", (24, 82, 85, 44)),
        Item("StaticText", "New thread", (173, 93, 94, 22), "center"),
        Item("Button", "Post settings", (392, 92, 24, 24), "right"),
        Item("StaticText", "creator  ", (60, 149, 155, 20)),
        Item("Link", "creator", (60, 145, 137, 23)),
        Item("Button", "Community or topic", (215, 144, 175, 27)),
        Item("TextView", "What's new?", (55, 176, 378, 34), "stretch", name="composer-text-view"),
        Item("Button", "Add photos and videos from your camera roll", (60, 211, 24, 45)),
        Item("Button", "Add a GIF", (148, 211, 24, 45)),
        Item("Button", "Post Options", (12, 586, 238, 40)),
        Item("Switch", "", (262, 586, 90, 40), value="0"),
        Item("Button", "Post this thread", (364, 586, 64, 40), "right", name="composer-post-button"),
    ]),
    "threads/photo_picker": (THREADS, ("threads", "photo_picker"), [
        Item("StaticText", "Select up to 20 items.", (153, 101, 134, 17), "center"),
        Item("Button", "Cancel", (24, 130, 36, 36)),
        Item("Button", "Photos", (125, 124, 95, 48), "center", value="1"),
        Item("Button", "Collections", (220, 124, 95, 48), "center"),
        Item("Button", "Done", (380, 130, 36, 36), "right"),
        Item("ScrollView", "", (0, 72, 440, 884), "stretch", "stretch", name="photosView_content_scroll_view"),
        Item("Button", "Sort and Filter", (33, 885, 38, 38), "left", "bottom"),
        Item("Button", "Search", (369, 885, 38, 38), "right", "bottom"),
    ]),
    "threads/posting": (THREADS, ("threads", "posting"), [
        Item("CollectionView", "", (0, 0, 440, 956), "stretch", "stretch", name="main-feed"),
        Item("Other", f"Posting{ELLIPSIS}, Keep Threads open to finish uploading., Progress", (56, 817, 282, 21),
             "left", "bottom", value="0%"),
        Item("Button", "Create post", (177, 877, 86, 54), "center", "bottom"),
    ]),
    # Edits (recorded 2026-09-30): header controls, the quality popover, export progress, share targets.
    "edits/project": (EDITS, ("edits", "project"), [
        Item("Button", "Close project", (8, 59, 44, 44), name="project_navigation_close_button"),
        Item("Button", "New project", (52, 72, 102, 18), name="project_navigation_project_name_button"),
        Item("Button", "4K", (304, 59, 45, 44), "right", name="project_navigation_video_quality_button"),
        Item("Button", "Next", (352, 64, 68, 34), "right", name="project_navigation_export_button"),
        Item("StaticText", "00:00, 00:31", (201, 565, 38, 30), "center", name="playback_time_label"),
    ]),
    "edits/quality": (EDITS, ("edits", "quality"), [
        *[Item("Button", label, (left, 144, 68, 32), "right", name=f"video_quality_segment_{i}_{label}")
          for i, (label, left) in enumerate((("HD", 204), ("2K", 272), ("4K", 340)))],
        *[Item("Button", label, (left, 216, 68, 31), "right", name=f"video_quality_segment_{i}_{label}")
          for i, (label, left) in enumerate((("24", 204), ("30", 272), ("60", 340)))],
        Item("Button", "SDR", (204, 287, 102, 32), "right", name="video_quality_segment_0_SDR"),
        Item("Button", "HDR", (306, 287, 102, 32), "right", name="video_quality_segment_1_HDR"),
    ]),
    "edits/exporting": (EDITS, ("edits", "exporting"), [
        Item("StaticText", "Export progress: 12.1%", (183, 140, 74, 29), "center", name="bsl_export_progress_label"),
    ]),
    "edits/share": (EDITS, ("edits", "share"), [
        Item("StaticText", "Choose where to share", (92, 140, 256, 29), "center", name="bsl_export_title_label"),
        *[Item("Button", label, (left, 808, 64, 65), "left", "bottom", name=name) for label, left, name in (
            ("Instagram", 20, "bsl_export_share_instagram_button"), ("Facebook", 96, "bsl_export_share_facebook_button"),
            ("Stories", 172, "bsl_export_share_story_button"), ("Download", 248, "bsl_export_download_button"),
            ("More", 324, "bsl_export_more_button"))],
    ]),
    "tiktok/post": (TIKTOK, ("tiktok", "post"), [
        Item("Button", "Back", (6, 62, 44, 44), name="(publishPageBackButton)"),
        Item("TextView", "Add description...", (16, 120, 280, 143)),
        Item("StaticText", "Preview", (322, 122, 49, 17), "right"),
        Item("Button", "Edit cover", (318, 246, 100, 26), "right"),
        Item("Button", "Add a hashtag to your post", (16, 294, 95, 32)),
        Item("Button", "Location.", (0, 354, 440, 92), "stretch"),
        Item("Button", "Drafts", (12, 870, 205, 48), "half_left", "bottom"),
        Item("Button", "Post", (223, 870, 205, 48), "half_right", "bottom"),
        Item("StaticText", "Post", (320, 885, 35, 18), "right", "bottom"),
    ]),
    "files/browse": (FILES, ("files", "browse"), [
        Item("StaticText", "Browse", (19, 119, 120, 42)),
        Item("SearchField", "Search", (20, 169, 400, 44), "stretch"),
        Item("Cell", "Locations", (20, 341, 400, 45), "stretch", name="DOC.sidebar.header.Locations"),
        Item("Cell", "On My iPhone", (20, 385, 400, 53), "stretch", name="DOC.sidebar.item.On My iPhone"),
        Item("Cell", "OneDrive", (20, 437, 400, 53), "stretch", name="DOC.sidebar.item.OneDrive"),
        *TAB_BAR,
    ]),
    "files/folder": (FILES, ("files", "folder"), [
        Item("Button", "Files", (20, 62, 44, 44), name="BackButton"),
        Item("Button", "Videos, Actions Menu", (175, 73, 64, 22), "center"),
        Item("Button", "More", (380, 66, 36, 36), "right", name="OverflowBarButtonItem"),
        Item("SearchField", "Search", (20, 117, 400, 44), "stretch"),
        Item("CollectionView", "", (0, 0, 440, 956), "stretch", "stretch", name="File View", value="List Mode"),
        Item("Cell", "example-video, 9/22/26 - 189 MB", (0, 176, 440, 64), "stretch", name="example-video, mp4"),
        *TAB_BAR,
    ]),
    "files/content_unavailable": (FILES, ("files", "content_unavailable"), [
        Item("Button", "OneDrive", (20, 62, 44, 44), name="BackButton"),
        Item("StaticText", "Content Unavailable", (116, 470, 208, 28), "center"),
        Item("Button", "Try Again", (172, 551, 96, 35), "center"),
        *TAB_BAR,
    ]),
    "files/turn_on_provider": (FILES, ("files", "turn_on_provider"), [
        Item("Alert", "Turn On “OneDrive”?", (60, 406, 320, 172), "center"),
        Item("Button", "Cancel", (76, 514, 140, 48), "center"),
        Item("Button", "Turn On", (224, 514, 140, 48), "center"),
    ]),
    "files/context_menu": (FILES, ("files", "context_menu"), [
        Item("Other", "Preview", (20, 286, 400, 59), "stretch"),
        Item("Other", "example-video, 3:22 PM - 271.2 MB", (19, 286, 402, 59), "stretch"),
        Item("Button", "Copy", (28, 364, 78, 78)),
        Item("Button", "Move", (106, 364, 78, 78)),
        Item("Button", "Share", (184, 364, 78, 78)),
        Item("Button", "Quick Look", (20, 452, 250, 43)),
        Item("Button", "Open With", (20, 494, 250, 43)),
        Item("Button", "Get Info", (20, 557, 250, 43)),
    ]),
    # Children of the remote container report popover-local frames, as recorded.
    "files/share_sheet": (FILES, ("files", "share_sheet"), [
        Item("Other", "dismiss popup", (0, 0, 440, 956), "stretch", "stretch", name="PopoverDismissRegion"),
        Item("Other", "", (9, 62, 422, 528), "stretch", name="ShareSheet.RemoteContainerView"),
        Item("Other", "example-video", (87, 40, 308, 19), name="LP.CaptionBar.TopCaption"),
        Item("Other", f"Video {DOT} 271.2 MB", (87, 58, 308, 21), name="LP.CaptionBar.BottomCaption"),
        Item("Other", "", (0, 0, 422, 528), name="shareSheet.activity.contentView"),
        Item("Cell", "OneDrive", (121, 253, 83, 129), name="shareCell"),
    ]),
}

# The last tap on each platform, and the cover control that must be set (to the first frame) before it.
FINAL_TAPS = {("youtube", "details", "upload"), ("instagram", "new_reel", "share"),
              ("instagram", "new_reel", "schedule"), ("threads", "composer", "post"), ("tiktok", "post", "post")}
COVER = {("youtube", "details"): "thumbnail", ("instagram", "new_reel"): "edit_cover", ("tiktok", "post"): "edit_cover"}
# Threads' composer exposes no cover control; see docs/size-independence-audit.md ("Not in the tree").
NO_COVER_CONTROL = {("threads", "composer")}

ALL_MAPS = load_maps(MAPS)
BY_KEY = {(m.app, m.screen): m for m in ALL_MAPS}


def snapshot_of(variant, device=REFERENCE):
    bundle, _, items = VARIANTS[variant]
    return rendered_snapshot(items, device, bundle)


class SyntheticScreenTests(unittest.TestCase):
    def test_every_map_has_a_synthetic_screen(self):
        modeled = {expected for _, expected, _ in VARIANTS.values()}
        self.assertEqual(set(BY_KEY) - modeled, set())

    def test_each_screen_is_identified_and_every_control_resolves_uniquely(self):
        resolved = {key: set() for key in BY_KEY}
        for variant, (_, key, _) in VARIANTS.items():
            snapshot = snapshot_of(variant)
            with self.subTest(variant=variant):
                screen = identify(snapshot, ALL_MAPS, LABELS)
                self.assertEqual((screen.app, screen.screen), key)
                for name, locator in screen.elements.items():
                    try:
                        find(snapshot, locator, LABELS)
                    except AmbiguousMatch as exc:
                        self.fail(f"{variant}.{name} is ambiguous: {exc}")
                    except MatchError:
                        continue
                    resolved[key].add(name)
        for key, screen in BY_KEY.items():
            with self.subTest(screen=key):
                self.assertEqual(set(screen.elements) - resolved[key], set(), "never resolved in any variant")

    def check_sizes(self, make):
        for variant in VARIANTS:
            reference = snapshot_of(variant)
            screen = identify(reference, ALL_MAPS, LABELS)
            for device in DEVICES:
                snapshot = make(variant, reference, device)
                with self.subTest(variant=variant, device=device.name):
                    self.assertIs(identify(snapshot, ALL_MAPS, LABELS), screen)
                    for name, locator in screen.elements.items():
                        before, after = resolve(reference, locator), resolve(snapshot, locator)
                        if isinstance(before, MatchError):
                            self.assertIsInstance(after, MatchError, name)
                            continue
                        self.assertNotIsInstance(after, MatchError, f"{name}: {after}")
                        self.assertEqual(after.element, snapshot.elements[reference.elements.index(before.element)], name)
                        self.assertTrue(0 <= after.x <= device.width and 0 <= after.y <= device.height, name)

    def test_scaled_frames_on_every_size(self):
        self.check_sizes(lambda variant, reference, device: scale_snapshot(reference, device))

    def test_safe_area_layout_on_every_size(self):
        self.check_sizes(lambda variant, reference, device: snapshot_of(variant, device))


class SafetyTests(unittest.TestCase):
    def test_final_post_share_and_schedule_taps_are_irreversible(self):
        for app, screen, action in FINAL_TAPS:
            with self.subTest(screen=f"{app}/{screen}", action=action):
                self.assertTrue(BY_KEY[(app, screen)].actions[action].irreversible)
        final_words = {"upload", "share", "schedule", "post"}
        for screen in ALL_MAPS:
            for action in screen.actions.values():
                if action.name in final_words:
                    self.assertIn((screen.app, screen.screen, action.name), FINAL_TAPS)

    def test_every_platform_has_its_cover_control_beside_the_final_tap(self):
        for app, screen, _ in FINAL_TAPS:
            if (app, screen) in NO_COVER_CONTROL:
                continue
            with self.subTest(screen=f"{app}/{screen}"):
                self.assertIn(COVER[(app, screen)], BY_KEY[(app, screen)].elements)

    def test_youtube_thumbnail_can_only_be_confirmed_at_the_first_frame(self):
        editor, first = BY_KEY[("youtube", "thumbnail_editor")], BY_KEY[("youtube", "thumbnail_first_frame")]
        self.assertNotIn("done", editor.elements)
        self.assertEqual(first.actions["done"].expect, "details")
        self.assertEqual(identify(snapshot_of("youtube/thumbnail_editor"), ALL_MAPS, LABELS), editor)
        self.assertEqual(identify(snapshot_of("youtube/thumbnail_first_frame"), ALL_MAPS, LABELS), first)

    def test_unauthorized_final_taps_never_reach_the_phone(self):
        variants = {"youtube/details": "upload", "instagram/new_reel": "share",
                    "instagram/new_reel_scheduled": "schedule", "threads/composer": "post", "tiktok/post": "post"}
        for variant, action in variants.items():
            phone = FakePhone(REFERENCE, variant)
            with self.subTest(variant=variant), self.assertRaises(IrreversibleError):
                Runner(phone, ALL_MAPS, LABELS, timeout=0, sleep=lambda _: None).act(action)
            self.assertEqual(phone.taps, [])


class FakePhone:
    """Serves one synthetic screen; a tap moves to the next screen by the label of the control it lands in."""

    FLOW = {
        ("youtube/details", "Edit thumbnail, 0:30"): "youtube/thumbnail_editor",
        ("youtube/thumbnail_editor", "Back"): "youtube/details",
        ("youtube/thumbnail_first_frame", "Done"): "youtube/details",
        ("youtube/details", "Visibility, Private"): "youtube/visibility",
        ("youtube/visibility", "Schedule"): "youtube/schedule",
        ("youtube/schedule", "Back"): "youtube/details",
        ("instagram/new_reel_lower", "More options"): "instagram/more_options",
        ("instagram/more_options",
         "Not checked, Schedule this reel, Some features may be unavailable with scheduled content."):
            "instagram/schedule_sheet",
        ("instagram/schedule_sheet", "Done"): "instagram/more_options_scheduled",
        ("instagram/more_options_scheduled", "Back"): "instagram/new_reel_scheduled",
        ("instagram/new_reel_scheduled", f"Also share on{ELLIPSIS}, 2 profiles"): "instagram/also_share_on",
        ("instagram/also_share_on", f"creator, Threads {DOT} Public, On"): "instagram/stop_sharing_popup",
        ("instagram/stop_sharing_popup", "Don't share this reel"): "instagram/also_share_on_scheduled",
        ("threads/composer", "Add photos and videos from your camera roll"): "threads/photo_picker",
        ("threads/photo_picker", "Done"): "threads/composer",
        ("threads/composer", "Post this thread"): "threads/posting",
        ("files/context_menu", "Share"): "files/share_sheet",
    }

    def __init__(self, device, variant):
        self.device, self.variant, self.taps = device, variant, []

    def items(self):
        return VARIANTS[self.variant][2]

    def screen_info(self):
        return {"width": self.device.width, "height": self.device.height}

    def current_app(self):
        return {"bundleId": VARIANTS[self.variant][0]}

    def ui_tree(self):
        children = [{"type": "XCUIElementType" + e.type, "label": e.label, "name": e.name, "value": e.value,
                     "isVisible": "1", "rect": {"x": e.left, "y": e.top, "width": e.width, "height": e.height}}
                    for e in elements(self.items(), self.device)]
        return {"type": "XCUIElementTypeApplication", "label": "", "isVisible": "1",
                "rect": {"x": 0, "y": 0, "width": self.device.width, "height": self.device.height},
                "children": children}

    def screenshot(self):
        return snapshot_of(self.variant, self.device).screenshot

    def tap(self, x, y):
        hits = [e for e in elements(self.items(), self.device) if e.label
                and e.left <= x <= e.left + e.width and e.top <= y <= e.top + e.height]
        label = min(hits, key=lambda e: e.width * e.height).label if hits else None
        self.taps.append((x, y, label))
        self.variant = self.FLOW.get((self.variant, label), self.variant)


class RunnerFlowTests(unittest.TestCase):
    FLOWS = {
        "youtube/details": [("open_thumbnail", "thumbnail_editor"), ("back", "details"),
                            ("open_visibility", "visibility"), ("choose_schedule", "schedule"), ("back", "details")],
        "youtube/thumbnail_first_frame": [("done", "details")],
        "instagram/new_reel_lower": [("open_more_options", "more_options"), ("turn_on_schedule", "schedule_sheet"),
                                     ("done", "more_options"), ("back", "new_reel"),
                                     ("open_also_share_on", "also_share_on"), ("turn_off_threads", "stop_sharing_popup"),
                                     ("dont_share_this_reel", "also_share_on")],
        "threads/composer": [("add_media", "photo_picker"), ("done", "composer")],
        "files/context_menu": [("open_share_sheet", "share_sheet")],
    }

    def test_mapped_actions_land_on_their_control_on_every_size(self):
        for start, steps in self.FLOWS.items():
            for device in DEVICES:
                phone = FakePhone(device, start)
                runner = Runner(phone, ALL_MAPS, LABELS, timeout=0, sleep=lambda _: None)
                with self.subTest(flow=start, device=device.name):
                    for action, expect in steps:
                        self.assertEqual(runner.act(action).screen, expect, action)
                    self.assertEqual(len(phone.taps), len(steps))

    def test_thumbnail_done_is_refused_away_from_the_first_frame(self):
        runner = Runner(FakePhone(REFERENCE, "youtube/thumbnail_editor"), ALL_MAPS, LABELS, timeout=0,
                        sleep=lambda _: None)
        with self.assertRaisesRegex(MatchError, "not defined"):
            runner.act("done")

    def test_authorized_final_tap_without_a_receipt_is_reported_unconfirmed(self):
        phone = FakePhone(REFERENCE, "instagram/new_reel_scheduled")
        runner = Runner(phone, ALL_MAPS, LABELS, authorized=frozenset({"schedule"}), timeout=0, sleep=lambda _: None)
        with self.assertRaisesRegex(MatchError, "unconfirmed"):
            runner.act("schedule")
        self.assertEqual([tap[2] for tap in phone.taps], ["Schedule"])

    def test_threads_post_is_confirmed_by_the_posting_banner(self):
        phone = FakePhone(REFERENCE, "threads/composer")
        runner = Runner(phone, ALL_MAPS, LABELS, authorized=frozenset({"post"}), timeout=0, sleep=lambda _: None)
        self.assertEqual(runner.act("post").screen, "posting")


# Local recordings (ignored by git) keyed by folder and timestamp only: screen, and the
# controls the recording legitimately does not show (scrolled away, or not in that state).
RECORDED = {
    # 2026-09-30: the Edits 4K route (OneDrive -> Edits -> export -> Instagram) and the TikTok
    # post screen with the keyboard up (Post is then a red pill outside the tree).
    "edits/20260930-111429": ("edits", "project", ()),
    "edits/20260930-111442": ("edits", "quality", ()),
    "edits/20260930-111523": ("edits", "quality", ()),
    "edits/20260930-111534": ("edits", "project", ()),
    "edits/20260930-111547": ("edits", "exporting", ()),
    "edits/20260930-111710": ("edits", "share", ()),
    "instagram/20260930-104648": ("instagram", "share_extension", ()),
    "tiktok/20260930-092433": ("tiktok", "post", ("drafts", "post")),
    "instagram/20260930-104722": ("instagram", "new_reel", ("also_share_on", "more_options", "schedule")),
    "instagram/20260930-111839": ("instagram", "new_reel", ("back", "edit_cover", "caption", "schedule")),
    "instagram/20260930-111849": ("instagram", "also_share_on", ()),
    "files/20260929-151017": ("files", "folder", ()),
    "files/20260929-151036": ("files", "browse", ()),
    "files/20260929-151054": ("files", "turn_on_provider", ()),
    "files/20260929-151514": ("files", "turn_on_provider", ()),
    "files/20260929-151524": ("files", "folder", ()),
    "files/20260929-151549": ("files", "content_unavailable", ()),
    "files/20260929-151943": ("files", "content_unavailable", ()),
    "files/20260929-151953": ("files", "content_unavailable", ()),
    "files/20260929-152101": ("files", "folder", ()),
    "files/20260929-152135": ("files", "folder", ()),
    "files/20260929-152206": ("files", "folder", ()),
    "files/20260929-153830": ("files", "context_menu", ()),
    "files/20260929-153851": ("files", "share_sheet", ()),
    "files/20260929-153918": ("files", "share_sheet", ()),
    "files/20260929-153958": ("files", "folder", ()),
    "instagram/20260929-223344": ("instagram", "share_extension", ()),
    "instagram/20260929-223450": ("instagram", "new_reel", ("also_share_on", "more_options", "schedule")),
    "instagram/20260929-223527": ("instagram", "new_reel", ("edit_cover", "caption", "schedule")),
    "instagram/20260929-223541": ("instagram", "also_share_on", ()),
    "instagram/20260929-223612": ("instagram", "stop_sharing_popup", ()),
    "instagram/20260929-232537": ("instagram", "share_extension", ()),
    "instagram/20260929-232631": ("instagram", "account_switcher", ()),
    "instagram/20260929-233021": ("instagram", "new_reel", ("also_share_on", "more_options", "schedule")),
    "instagram/20260929-233145": ("instagram", "new_reel", ("edit_cover", "caption", "schedule")),
    "instagram/20260929-233223": ("instagram", "more_options", ("schedule_is_on",)),
    "instagram/20260929-233237": ("instagram", "schedule_sheet", ()),
    "instagram/20260929-233258": ("instagram", "schedule_date", ()),
    # Recorded as "schedule-time", but the tree still shows More options: the time picker was never captured.
    "instagram/20260929-233340": ("instagram", "more_options", ("schedule_is_on",)),
    "instagram/20260929-233709": ("instagram", "new_reel", ("edit_cover", "caption", "share")),
    "instagram/20260929-233723": ("instagram", "also_share_on", ("threads_switch",)),
    "threads/20260929-222758": ("threads", "posting", ()),
    "threads/20260929-234258": ("threads", "composer", ()),
    "threads/20260929-234319": ("threads", "photo_picker", ()),
    "tiktok/20260929-225051": ("tiktok", "post", ()),
    "youtube/20260929-154035": ("files", "folder", ()),
    "youtube/20260929-200121": ("youtube", "details", ()),
    "youtube/20260929-200158": ("youtube", "details", ()),
    "youtube/20260929-200213": ("youtube", "visibility", ()),
    "youtube/20260929-200428": ("youtube", "details", ("upload",)),
    "youtube/20260929-200448": ("youtube", "details", ()),
    "youtube/20260929-200503": ("youtube", "details", ("show_more",)),
    "youtube/20260929-200516": ("youtube", "details", ("title", "thumbnail", "show_more")),
    "youtube/20260929-200538": ("youtube", "details", ("title", "thumbnail", "show_more")),
    "youtube/20260929-200559": ("youtube", "details", ("title", "thumbnail", "show_more")),
    "youtube/20260929-200617": ("youtube", "description_editor", ()),
    "youtube/20260929-200736": ("youtube", "paid_promotion", ()),
    "youtube/20260929-200840": ("youtube", "attributes", ()),
    "youtube/20260929-200853": ("youtube", "ai_use", ()),
    "youtube/20260929-200934": ("youtube", "attributes", ()),
    "youtube/20260929-201003": ("youtube", "details", ("show_more",)),
    "youtube/20260929-231342": ("youtube", "editor", ()),
    "youtube/20260929-231409": ("youtube", "details", ()),
    "youtube/20260929-231513": ("youtube", "details", ()),
    "youtube/20260929-231531": ("youtube", "thumbnail_first_frame", ()),
    "youtube/20260929-231849": ("youtube", "schedule", ()),
    "youtube/20260929-231907": ("youtube", "schedule_picker", ()),
    "youtube/20260929-231932": ("youtube", "time_wheels", ()),
    "youtube/20260929-232058": ("youtube", "schedule", ()),
    "youtube/20260929-232125": ("youtube", "details", ("show_more",)),
}


@unittest.skipUnless(any(FIXTURES.glob("*/*.json")), "no local recordings under .state/fixtures")
class RecordedScreensTests(unittest.TestCase):
    """Local only: replay the reference phone's recordings against the maps."""

    def test_recordings_match_their_screen_and_resolve_their_controls(self):
        seen = set()
        for path in sorted(FIXTURES.glob("*/*.json")):
            stamp = re.search(r"\d{8}-\d{6}$", path.stem)
            key = f"{path.parent.name}/{stamp.group(0) if stamp else path.stem}"
            snapshot = load_fixture(path.with_suffix(""))
            with self.subTest(recording=key):
                if key not in RECORDED:
                    # Screens with no map (home, gallery, Photos, Settings, a springboard-only read) stay unknown.
                    with self.assertRaisesRegex(MatchError, "Unknown screen"):
                        identify(snapshot, ALL_MAPS, LABELS)
                    continue
                seen.add(key)
                app, name, absent = RECORDED[key]
                screen = identify(snapshot, ALL_MAPS, LABELS)
                self.assertEqual((screen.app, screen.screen), (app, name))
                for element, locator in screen.elements.items():
                    try:
                        find(snapshot, locator, LABELS)
                        self.assertNotIn(element, absent, "expected absent but resolved")
                    except AmbiguousMatch as exc:
                        self.fail(f"{element}: {exc}")
                    except MatchError:
                        self.assertIn(element, absent)
        self.assertTrue(seen)


if __name__ == "__main__":
    unittest.main()
