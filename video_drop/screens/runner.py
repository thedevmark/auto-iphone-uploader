"""Run mapped actions one at a time and prove each one landed."""

from __future__ import annotations

import time

from .labels import Labels
from .matcher import MatchError, find, identify
from .model import ScreenMap
from .snapshot import capture


class IrreversibleError(RuntimeError):
    pass


class Runner:
    def __init__(self, phone, maps: list[ScreenMap], labels: Labels, *, authorized: frozenset = frozenset(),
                 timeout: float = 15.0, poll: float = 0.4, read=capture, sleep=time.sleep, clock=time.monotonic):
        self.phone, self.maps, self.labels = phone, maps, labels
        self.authorized, self.timeout, self.poll = frozenset(authorized), timeout, poll
        self.read, self.sleep, self.clock = read, sleep, clock
        self.snapshot = None

    def current(self) -> ScreenMap:
        self.snapshot = self.read(self.phone, screenshot=True)
        return identify(self.snapshot, self.maps, self.labels)

    def wait_for(self, screen: str, timeout: float | None = None) -> ScreenMap:
        deadline = self.clock() + (self.timeout if timeout is None else timeout)
        last = None
        while True:
            try:
                found = self.current()
                if found.screen == screen:
                    return found
                last = MatchError(f"Expected {screen!r}; phone shows {found.app}/{found.screen}")
            except Exception as exc:  # a dropped phone link is one more failed poll, not an answer
                last = exc if isinstance(exc, MatchError) else MatchError(f"Phone read failed: {exc}")
            if self.clock() >= deadline:
                raise last
            self.sleep(self.poll)

    def act(self, action: str) -> ScreenMap:
        screen = self.current()
        step = screen.actions.get(action)
        if step is None:
            raise MatchError(f"Action {action!r} is not defined on {screen.app}/{screen.screen}")
        if step.irreversible and action not in self.authorized:
            raise IrreversibleError(f"{action!r} is irreversible and was not authorized for this run")
        target = find(self.snapshot, screen.elements[step.tap], self.labels, screen.path.parent / "icons")
        self.phone.tap(target.x, target.y)
        try:
            return self.wait_for(step.expect)
        except Exception as exc:
            if step.irreversible:
                raise MatchError(f"{action!r} tap sent but unconfirmed; check for a native receipt before any retry: {exc}") from exc
            raise
