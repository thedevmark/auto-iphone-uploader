import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock

from video_drop import phone_focus
from unittest import mock

from video_drop.phone_focus import FocusError, focus_state, optional_focus, upload_focus
from video_drop.screens.snapshot import Element


def node(kind, name, x, y, value="", label=""):
    return {"type": "XCUIElementType" + kind, "name": name, "label": label or name, "value": value,
            "rect": {"x": x - 30, "y": y - 30, "width": 60, "height": 60}, "isVisible": "1"}


class FakePhone:
    """Control Center as recorded on iOS 26.7 (fixtures/control-center/*)."""

    def __init__(self, focus="", rotation="0"):
        self.focus = focus
        self.rotation = rotation
        self.in_control = False
        self.in_menu = False
        self.taps = []

    def screen_info(self):
        return {"width": 440, "height": 956}

    def press_home(self):
        self.in_control = self.in_menu = False

    def swipe(self, x1, y1, x2, y2, seconds):
        self.in_control = y1 < 5 and y2 > y1
        self.swipes = getattr(self, "swipes", 0) + 1

    def ui_tree(self):
        self.reads = getattr(self, "reads", 0) + 1
        if getattr(self, "stuck_menu", False):
            return {"type": "XCUIElementTypeApplication",
                    "children": [node("Other", "focus-modes-ui", 220, 400)]}
        if self.in_menu:
            children = [node("Button", "mode-Do Not Disturb", 220, 263, label="Do Not Disturb, Silence all notifications"),
                        node("Button", "mode-Work", 220, 435, label="Work")]
        elif self.in_control:
            children = [node("Button", "focus-module", 129, 450, self.focus, "Focus"),
                        dict(node("Switch", "orientation-lock", 84, 359, self.rotation, "Lock Rotation"),
                             type="XCUIElementTypeSwitch")]
        else:
            children = [node("Button", "Video composer", 200, 200)]
        return {"type": "XCUIElementTypeApplication", "children": children}

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.in_control and not self.in_menu and (x, y) == (220.0, 930.0):
            self.in_control = False
            return
        if getattr(self, "stuck_menu", False):
            # Recorded 2026-10-01: Home leaves this menu up; only empty space closes it.
            self.stuck_menu = (x, y) != (220, 790)
            return
        if self.in_menu and (x, y) == (220, 263):
            self.focus = "" if self.focus == "Do Not Disturb" else "Do Not Disturb"
        elif self.in_control and (x, y) == (84, 359):
            self.rotation = "0" if self.rotation == "1" else "1"
        elif self.in_control and (x, y) == (129, 450):
            self.in_menu = True


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)
class PhoneFocusTests(unittest.TestCase):
    def setUp(self):
        import video_drop.phone_focus as focus
        focus._quiet_until = 0.0

    def test_restores_off_even_when_upload_fails(self):
        phone = FakePhone()
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            with upload_focus(phone):
                self.assertEqual(phone.focus, "Do Not Disturb")
                raise RuntimeError("upload failed")
        self.assertEqual(phone.focus, "")
        self.assertFalse(phone.in_control)

    def test_switched_off_check_leaves_focus_untouched(self):
        phone = FakePhone()
        with optional_focus(phone, False):
            self.assertEqual(phone.focus, "")
        self.assertEqual(phone.taps, [])

    def test_switched_on_check_holds_dnd(self):
        phone = FakePhone()
        with optional_focus(phone, True):
            self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(phone.focus, "")

    def test_preserves_dnd_that_was_already_on(self):
        phone = FakePhone("Do Not Disturb")
        with upload_focus(phone):
            self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(set(phone.taps) - {(220.0, 930.0)}, set())  # only closing Control Center

    def test_another_focus_is_kept_and_the_run_proceeds(self):
        # 2026-09-30: the owner was live with the "Streaming" Focus on; it already silences the phone.
        phone = FakePhone("Streaming")
        with upload_focus(phone):
            self.assertEqual(phone.focus, "Streaming")
        self.assertEqual(phone.focus, "Streaming")
        self.assertEqual(set(phone.taps) - {(220.0, 930.0)}, set())  # only closing Control Center

    def test_an_owner_focus_is_read_once_per_session_window(self):
        import video_drop.phone_focus as focus
        phone = FakePhone("Streaming")
        with upload_focus(phone):
            pass
        reads = phone.swipes
        with upload_focus(phone):  # second platform of the same Post now: no Control Center read
            pass
        self.assertEqual(phone.swipes, reads)
        focus._quiet_until = 0.0
        with upload_focus(phone):
            pass
        self.assertGreater(phone.swipes, reads)

    def test_a_leftover_focus_menu_is_closed_by_empty_space_not_home(self):
        phone = FakePhone("Streaming")
        phone.stuck_menu = True
        with upload_focus(phone):
            self.assertEqual(phone.focus, "Streaming")
        self.assertIn((220, 790), phone.taps)
        self.assertFalse(phone.stuck_menu)

    def test_control_center_is_proven_by_one_tree_read_when_the_module_is_up(self):
        # Every open used to read the tree twice (leftover-menu check, then the module wait);
        # a Post now toggles DND on and off per platform, so each read is ~1 s of WDA time saved.
        from video_drop.phone_focus import open_control_center
        phone = FakePhone("Do Not Disturb")
        elements = open_control_center(phone)
        self.assertEqual(phone.reads, 1)
        self.assertEqual(focus_state(elements), "dnd")
        stuck = FakePhone("Streaming")
        stuck.stuck_menu = True
        open_control_center(stuck)
        self.assertEqual(stuck.reads, 2)  # the menu read, then the one read after closing it

    def test_state_reads_the_recorded_module_value(self):
        module = lambda value: (Element("Button", "Focus", "focus-module", value, 46, 412, 166, 76),)
        self.assertEqual(focus_state(module("")), "off")
        self.assertEqual(focus_state(module("Do Not Disturb")), "dnd")
        with self.assertRaisesRegex(FocusError, "found 0"):
            focus_state(())


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)
class RotationLockTests(unittest.TestCase):
    def setUp(self):
        import video_drop.phone_focus as focus
        focus._quiet_until = 0.0

    def test_locks_for_the_run_and_restores_only_what_it_changed(self):
        from video_drop.phone_focus import run_guards
        phone = FakePhone()
        with mock.patch("video_drop.phone_awake.stay_awake", lambda p: nullcontext()):
            with run_guards(phone, {"preparePhone": True}):
                self.assertEqual((phone.focus, phone.rotation), ("Do Not Disturb", "1"))
            self.assertEqual((phone.focus, phone.rotation), ("", "0"))
            already = FakePhone(rotation="1")
            with run_guards(already, {"preparePhone": True}):
                pass
        self.assertEqual(already.rotation, "1")  # the owner's own lock stays on

    def test_one_setting_turns_the_whole_preparation_off(self):
        from video_drop.phone_focus import run_guards
        phone = FakePhone()
        with mock.patch("video_drop.phone_awake.stay_awake", side_effect=AssertionError("touched Settings")):
            with run_guards(phone, {"preparePhone": False}):
                self.assertEqual((phone.focus, phone.rotation), ("", "0"))

    def test_preparation_runs_in_order_and_unwinds_in_reverse(self):
        from contextlib import contextmanager
        from video_drop import phone_focus
        order = []

        def guard(name):
            @contextmanager
            def cm(phone):
                order.append(name)
                yield
                order.append("undo " + name)
            return cm
        with mock.patch("video_drop.phone_awake.stay_awake", guard("awake")),                 mock.patch.object(phone_focus, "upload_focus", guard("dnd")),                 mock.patch.object(phone_focus, "rotation_lock", guard("rotation")):
            with phone_focus.prepare_phone(object(), True):
                order.append("run")
        self.assertEqual(order, ["awake", "dnd", "rotation", "run", "undo rotation", "undo dnd", "undo awake"])


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)
class CloseControlCenterTests(unittest.TestCase):
    def test_an_open_control_center_is_closed_by_empty_space_and_proven(self):
        from video_drop.phone_focus import close_control_center
        phone = FakePhone()
        phone.in_control = True
        phone.press_home = lambda: None  # measured 2026-10-02: Home leaves Control Center up
        close_control_center(phone)
        self.assertFalse(phone.in_control)
        self.assertEqual(phone.taps, [(220.0, 930.0)])

    def test_nothing_is_tapped_when_control_center_is_not_up(self):
        from video_drop.phone_focus import close_control_center
        phone = FakePhone()
        close_control_center(phone)
        self.assertEqual(phone.taps, [])


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)
class SwipeRetryTests(unittest.TestCase):
    def test_a_swipe_that_does_not_open_control_center_is_tried_once_more(self):
        from video_drop.phone_focus import open_control_center
        phone = FakePhone()
        real = phone.swipe
        missed = []

        def swipe(*args):
            if not missed:  # captured 2026-10-02 12:15: the first swipe left the Home Screen up
                missed.append(1)
                phone.swipes = getattr(phone, "swipes", 0) + 1
                return
            real(*args)

        phone.swipe = swipe
        with mock.patch("video_drop.phone_focus.time.monotonic", side_effect=[0, 0, 10, 10, 20, 20, 30, 30]):
            elements = open_control_center(phone)
        self.assertTrue(any(e.name == "focus-module" for e in elements))
        self.assertEqual(phone.swipes, 2)


class Upright(unittest.TestCase):
    """Soak 2026-10-02 16:38: a sideways phone stopped two flows before their first step."""

    def phone(self, sizes):
        sizes = iter(sizes)
        phone = MagicMock()
        phone.screen_info.side_effect = lambda: dict(zip(("width", "height"), next(sizes)))
        return phone

    def test_a_sideways_phone_is_turned_upright(self):
        phone = self.phone([(956, 440), (440, 956)])
        phone_focus.upright(phone, settle=0)
        phone.set_portrait.assert_called_once()

    def test_an_upright_phone_is_left_alone(self):
        phone = self.phone([(440, 956)])
        phone_focus.upright(phone, settle=0)
        phone.set_portrait.assert_not_called()

    def test_a_phone_that_stays_sideways_stops_with_a_plain_message(self):
        with self.assertRaisesRegex(phone_focus.FocusError, "stand it in portrait"):
            phone_focus.upright(self.phone([(956, 440), (956, 440)]), settle=0)


class PreparedSession(unittest.TestCase):
    """2026-10-02: preparing around each app cost ~80 s + ~50 s per app; a Post now prepares once."""

    def test_the_phone_is_prepared_by_the_first_run_and_restored_once_at_the_end(self):
        from contextlib import contextmanager
        from video_drop import phone_focus
        order = []

        @contextmanager
        def prepare(phone, enabled):
            order.append("prepare")
            yield
            order.append("restore")
        with mock.patch.object(phone_focus, "prepare_phone", prepare), \
                mock.patch.object(phone_focus, "upright", lambda phone: None):
            with phone_focus.prepared_session():
                for app in ("youtube", "instagram", "tiktok"):
                    with phone_focus.run_guards(object(), {"preparePhone": True}):
                        order.append(app)
            with phone_focus.run_guards(object(), {"preparePhone": True}):  # outside: per run again
                order.append("alone")
        self.assertEqual(order, ["prepare", "youtube", "instagram", "tiktok", "restore",
                                 "prepare", "alone", "restore"])

    def test_a_failed_restore_is_reported_not_raised(self):
        from contextlib import contextmanager
        from video_drop import phone_focus

        @contextmanager
        def prepare(phone, enabled):
            yield
            raise RuntimeError("Control Center did not open")
        with mock.patch.object(phone_focus, "prepare_phone", prepare), \
                mock.patch.object(phone_focus, "upright", lambda phone: None):
            with phone_focus.prepared_session():
                with phone_focus.run_guards(object(), {"preparePhone": True}):
                    pass
        self.assertIn("could not be put back", phone_focus.restore_problem)
