"""Open one exact file in Apple's Files app and leave the iOS share sheet up.

Files > Browse > the provider's location > each folder > the exact file >
long-press > Share. Every step is identified by the Files screen maps
(``maps/files``) and proven before the next one: the folder title after each
tap, the file's name, type and size in its row, the context menu's preview,
and the share sheet's caption. A cloud-only file is downloaded first, within a
bound. A provider that is missing or switched off in Files stops with the fix
for the user; this module never turns a location on or changes a setting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Callable

from .phone_ui import size_shown
from .screens.labels import Labels
from .screens.matcher import MatchError, find, identify
from .screens.model import ScreenMap, load_maps
from .screens.runner import Runner
from .screens.snapshot import Element, Snapshot, capture
from .source_route import FilesPath

FILES_BUNDLE = "com.apple.DocumentsApp"
MAPS = Path(__file__).resolve().parent.parent / "maps"
SIDEBAR_ITEM = "DOC.sidebar.item."
NAV_BAR = "FullDocumentManagerViewControllerNavigationBar"
TAB_BAR = "DOC.browsingModeTabBar"


class FilesError(RuntimeError):
    pass


def files_maps(root: Path = MAPS) -> list[ScreenMap]:
    return [screen for screen in load_maps(root) if screen.app == "files"]


def turn_on_fix(provider: str) -> str:
    return (f"On the iPhone, open Files → Browse → tap More (…) at the top → Edit, turn on {provider} under "
            f"Locations and tap Done, then post again. If {provider} is not listed there, install the {provider} "
            "app from the App Store and sign in first. Nothing was posted")


def download_bound(size_bytes: int) -> float:
    """Seconds to wait for a cloud-only file: two minutes plus 2 MB/s, at most half an hour."""
    return min(1800.0, 120.0 + size_bytes / 2_000_000)


def split_cell_name(name: str) -> tuple[str, str]:
    """Files names a row "<shown name>, <kind>": the extension for a file, "Folder" for a folder."""
    shown, _, kind = name.rpartition(", ")
    return shown, kind


def folder_title(snapshot: Snapshot, labels: Labels) -> str | None:
    """The open folder's name: its title menu button, or the plain title beside the Back button."""
    suffix = labels.text("@label:actions_menu")
    menus = {e.label[:-len(suffix)] for e in snapshot.elements
             if e.type == "Button" and e.label.endswith(suffix) and len(e.label) > len(suffix)}
    if len(menus) == 1:
        return menus.pop()
    backs = [e for e in snapshot.elements if e.name == "BackButton"]
    if not backs:
        return None
    back = backs[0]
    titles = {e.label for e in snapshot.elements
              if e.type == "StaticText" and e.label and back.top <= e.y <= back.top + back.height}
    return titles.pop() if len(titles) == 1 else None


def list_area(snapshot: Snapshot) -> tuple[float, float]:
    """Rows are only tappable between the navigation bar and the tab bar."""
    top = max((e.top + e.height for e in snapshot.elements if e.name == NAV_BAR), default=0.0)
    bottom = min((e.top for e in snapshot.elements if e.name == TAB_BAR), default=snapshot.height)
    return top, bottom


def visible(element: Element, snapshot: Snapshot) -> bool:
    top, bottom = list_area(snapshot)
    return top < element.y < bottom and 0 <= element.x <= snapshot.width


def file_cells(snapshot: Snapshot, filename: str) -> list[Element]:
    stem, ext = PurePath(filename).stem, PurePath(filename).suffix.lstrip(".")
    found = []
    for element in snapshot.elements:
        shown, kind = split_cell_name(element.name)
        if element.type == "Cell" and shown == stem and kind.casefold() == ext.casefold():
            found.append(element)
    return list(dict.fromkeys(found))


def folder_cells(snapshot: Snapshot, folder: str, labels: Labels) -> list[Element]:
    kind = labels.text("@label:folder")
    return list(dict.fromkeys(e for e in snapshot.elements if e.type == "Cell" and e.name == f"{folder}, {kind}"))


def location_cells(snapshot: Snapshot, names: tuple[str, ...]) -> list[Element]:
    wanted = {SIDEBAR_ITEM + name for name in names}
    return list(dict.fromkeys(e for e in snapshot.elements if e.type == "Cell" and e.name in wanted))


def download_button(snapshot: Snapshot, cell: Element, labels: Labels) -> Element | None:
    """The cloud icon Files draws on a row whose content is not on the iPhone yet."""
    label = labels.text("@label:download")
    buttons = [e for e in snapshot.elements if e.type == "Button" and e.label == label
               and cell.left <= e.x <= cell.left + cell.width and cell.top <= e.y <= cell.top + cell.height]
    return buttons[0] if buttons else None


def needs_download(snapshot: Snapshot, cell: Element, labels: Labels) -> bool:
    return download_button(snapshot, cell, labels) is not None or \
        cell.label.endswith(", " + labels.text("@label:download"))


def verify_row(cell: Element, filename: str, size_bytes: int, where: str, provider: str) -> None:
    if not size_shown(size_bytes, [cell.label]):
        raise FilesError(f"{filename} in {where} does not show the size it has on this PC. Wait for {provider} "
                         "to finish syncing it, then post again. Nothing was posted")


def verify_share_sheet(snapshot: Snapshot, screen: ScreenMap, labels: Labels, filename: str, size_bytes: int) -> None:
    try:
        name = find(snapshot, screen.elements["file_name"], labels).element
        details = find(snapshot, screen.elements["file_details"], labels).element
    except MatchError as exc:
        raise FilesError(f"The iOS share sheet does not show which file it shares: {exc}") from exc
    if name is None or name.label not in (filename, PurePath(filename).stem):
        raise FilesError("The iOS share sheet has the wrong file name. Nothing was posted")
    if details is None or not size_shown(size_bytes, [details.label]):
        raise FilesError("The iOS share sheet has the wrong file size. Nothing was posted")


@dataclass
class FilesApp:
    phone: object
    maps: list[ScreenMap] = field(default_factory=files_maps)
    labels: Labels = field(default_factory=lambda: Labels.load(MAPS, "en"))
    stage: Callable[[str], None] = lambda name: None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    poll: float = 0.5
    load_timeout: float = 30.0
    retries: int = 3
    max_scrolls: int = 40

    # One read of the phone; the Files maps say which screen it is (None while it loads).
    def read(self) -> tuple[ScreenMap | None, Snapshot]:
        snapshot = capture(self.phone, screenshot=False)
        try:
            return identify(snapshot, self.maps, self.labels), snapshot
        except MatchError:
            return None, snapshot

    def tap(self, element: Element) -> None:
        self.phone.tap(element.x, element.y)

    def tap_control(self, screen: ScreenMap, snapshot: Snapshot, name: str) -> None:
        target = find(snapshot, screen.elements[name], self.labels)
        self.phone.tap(target.x, target.y)

    def swipe_up(self, snapshot: Snapshot) -> None:
        # A slow, short drag moves less than one screen of rows, so no row is skipped between reads.
        x = snapshot.width / 2
        self.phone.swipe(x, snapshot.height * 0.68, x, snapshot.height * 0.38, 0.8)
        self.sleep(0.6)

    def refuse_turn_on(self, screen: ScreenMap, snapshot: Snapshot, provider: str) -> None:
        # Cancel only dismisses the prompt; turning a location on stays the user's decision.
        self.tap_control(screen, snapshot, "cancel")
        raise FilesError(f"{provider} is switched off in the Files app. {turn_on_fix(provider)}")

    def launch(self) -> None:
        self.stage("files_open")
        # A fresh launch drops any menu or share sheet an earlier run left open.
        self.phone.press_home()
        self.phone.close_app(FILES_BUNDLE)
        self.phone.open_app(FILES_BUNDLE, wait_seconds=12)
        if self.phone.current_app().get("bundleId") != FILES_BUNDLE:
            raise FilesError("The Files app did not come to the front. Nothing was posted")

    def to_browse(self) -> Snapshot:
        """Back out of whatever folder Files restored until the Browse list shows."""
        deadline, taps = self.clock() + self.load_timeout, 0
        while taps <= 16:
            screen, snapshot = self.read()
            name = screen.screen if screen else None
            if name == "browse":
                return snapshot
            if name == "turn_on_provider":
                # A prompt left from before this run: dismiss it (never Turn On) and keep backing out.
                self.tap_control(screen, snapshot, "cancel")
                taps += 1
                self.sleep(self.poll)
                continue
            if name in ("context_menu", "share_sheet"):
                raise FilesError("Files reopened with a menu still showing. Close Files on the iPhone, then post "
                                 "again. Nothing was posted")
            if name in ("folder", "content_unavailable"):
                backs = [e for e in snapshot.elements if e.name == "BackButton"]
                tab = [e for e in snapshot.elements
                       if e.type == "Button" and e.label == self.labels.text("@label:browse")]
                if backs or len(tab) == 1:
                    before = folder_title(snapshot, self.labels)
                    self.tap(backs[0] if backs else tab[0])
                    taps += 1
                    self.wait_until(lambda s, snap: s is None or s.screen != name
                                    or folder_title(snap, self.labels) != before, 5.0, quiet=True)
                    deadline = self.clock() + self.load_timeout
                    continue
            if self.clock() >= deadline:
                break
            self.sleep(self.poll)
        raise FilesError("Files did not get back to its Browse list. Nothing was posted")

    def wait_until(self, done: Callable[[ScreenMap | None, Snapshot], bool], timeout: float, *,
                   quiet: bool = False) -> tuple[ScreenMap | None, Snapshot] | None:
        deadline = self.clock() + timeout
        while True:
            screen, snapshot = self.read()
            if done(screen, snapshot):
                return screen, snapshot
            if self.clock() >= deadline:
                if quiet:
                    return None
                raise FilesError("Files did not reach the expected screen")
            self.sleep(self.poll)

    def scan(self, match: Callable[[Snapshot], list[Element]], missing: str) -> tuple[Element, Snapshot]:
        """Scroll down the current list until exactly one visible row matches, or the list ends."""
        previous = None
        for _ in range(self.max_scrolls + 1):
            _, snapshot = self.read()
            found = match(snapshot)
            shown = [e for e in found if visible(e, snapshot)]
            if len(found) > 1 or len(shown) > 1:
                raise FilesError(f"{missing}: more than one row matches, so none was chosen. Nothing was posted")
            if shown:
                return shown[0], snapshot
            rows = tuple(e.name for e in snapshot.elements if e.type == "Cell")
            if rows == previous:
                break
            previous = rows
            self.swipe_up(snapshot)
        raise FilesError(missing)

    def enter(self, title: str, provider: str, where: str) -> Snapshot:
        """Wait for the folder just tapped: its title proves it, Try Again gets a bounded retry."""
        deadline, tries = self.clock() + self.load_timeout, 0
        while True:
            screen, snapshot = self.read()
            name = screen.screen if screen else None
            if name == "folder" and folder_title(snapshot, self.labels) == title:
                return snapshot
            if name == "turn_on_provider":
                self.refuse_turn_on(screen, snapshot, provider)
            if name == "content_unavailable":
                tries += 1
                if tries > self.retries:
                    raise FilesError(f"Files could not show {where} (\"Content Unavailable\"). Open the {provider} "
                                     "app on the iPhone once, check it is signed in and online, then post again. "
                                     "Nothing was posted")
                self.tap_control(screen, snapshot, "try_again")
                deadline = self.clock() + self.load_timeout
            if self.clock() >= deadline:
                raise FilesError(f"Files did not open {where}. Nothing was posted")
            self.sleep(self.poll)

    def download(self, cell: Element, snapshot: Snapshot, target: FilesPath, size_bytes: int) -> Element:
        """Bring a cloud-only file onto the iPhone and wait for it, within a bound."""
        if not needs_download(snapshot, cell, self.labels):
            return cell
        self.stage("files_download")
        button = download_button(snapshot, cell, self.labels)
        if button is not None:
            self.tap(button)
        bound = download_bound(size_bytes)
        deadline = self.clock() + bound
        while True:
            self.sleep(max(self.poll, 2.0))
            _, snapshot = self.read()
            rows = [e for e in file_cells(snapshot, target.filename) if visible(e, snapshot)]
            if len(rows) != 1:
                raise FilesError(f"{target.filename} left the list while it downloaded. Nothing was posted")
            if not needs_download(snapshot, rows[0], self.labels):
                return rows[0]
            if self.clock() >= deadline:
                raise FilesError(f"{target.filename} is still downloading from {target.provider} to the iPhone after "
                                 f"{round(bound / 60)} min. Check the iPhone is online, open Files and let the "
                                 "download finish, then post again. Nothing was posted")

    def open_file(self, target: FilesPath, size_bytes: int) -> None:
        """Files > Browse > location > folders > exact file > Share; the iOS share sheet stays up."""
        provider, where = target.provider, target.shown
        self.launch()
        self.stage("files_browse")
        self.to_browse()
        cell, _ = self.scan(lambda snap: location_cells(snap, target.locations),
                            f"{provider} is not listed in the Files app. {turn_on_fix(provider)}")
        location = cell.name[len(SIDEBAR_ITEM):]
        self.tap(cell)
        self.enter(location, provider, provider)
        for depth, folder in enumerate(target.folders):
            place = " › ".join((provider, *target.folders[:depth + 1]))
            cell, _ = self.scan(lambda snap, folder=folder: folder_cells(snap, folder, self.labels),
                                f"Files has no folder {folder!r} in {' › '.join((provider, *target.folders[:depth]))}. "
                                f"Check {provider} has synced it to the iPhone, then post again. Nothing was posted")
            self.tap(cell)
            self.enter(folder, provider, place)
        self.stage("files_file")
        cell, snapshot = self.scan(lambda snap: file_cells(snap, target.filename),
                                   f"Files has no {target.filename} in {where}. Wait for {provider} to finish "
                                   "syncing it to the iPhone, then post again. Nothing was posted")
        verify_row(cell, target.filename, size_bytes, where, provider)
        cell = self.download(cell, snapshot, target, size_bytes)
        verify_row(cell, target.filename, size_bytes, where, provider)
        self.phone.long_press(cell.x, cell.y, 1.0)
        found = self.wait_until(lambda s, snap: s is not None and s.screen == "context_menu", 8.0, quiet=True)
        if found is None:
            raise FilesError(f"Holding {target.filename} did not open its menu in Files. Nothing was posted")
        _, snapshot = found
        stem = PurePath(target.filename).stem
        previews = [e for e in snapshot.elements if e.label.startswith(stem + ", ") and size_shown(size_bytes, [e.label])]
        if not previews:
            raise FilesError("The Files menu opened for a different file. Nothing was posted")
        self.stage("files_share")
        runner = Runner(self.phone, self.maps, self.labels, timeout=download_bound(size_bytes), poll=self.poll,
                        read=lambda phone, screenshot=False: capture(phone, screenshot=False),
                        sleep=self.sleep, clock=self.clock)
        try:
            screen = runner.act("open_share_sheet")
        except MatchError as exc:
            raise FilesError(f"Share in Files did not open the iOS share sheet: {exc}") from exc
        verify_share_sheet(runner.snapshot, screen, self.labels, target.filename, size_bytes)
