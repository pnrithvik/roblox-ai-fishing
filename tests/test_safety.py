"""Safety tests for AI Fishing. Run:  python -m unittest discover -s tests -v

All mouse input and screen capture is mocked: these tests never click, never move the
mouse and never take a real screenshot."""
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ai_fishing as af  # noqa: E402

MONITORS = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
ROBLOX = {"area": {"left": 100, "top": 100, "width": 1280, "height": 720}, "hwnd": 1234, "front": True}
INSIDE, OUTSIDE, CORNER = (500, 400), (1700, 50), (0, 0)


class FakeInput:
    """Stands in for MouseInput: records what would have been sent to the OS."""

    def __init__(self):
        self.sent = []
        self.held = False

    def down(self): self.sent.append("down"); self.held = True
    def up(self):
        if self.held:
            self.release_all()
    def release_all(self): self.sent.append("up"); self.held = False
    def click(self): self.sent.append("click")


class FakeGrabber:
    def __init__(self, win=ROBLOX):
        self.win = win

    def monitors(self): return MONITORS
    def find_window(self): return self.win


def guard_with(win=ROBLOX, cursor=INSIDE, front=True):
    inp, logs = FakeInput(), []
    g = af.CursorGuard(inp, FakeGrabber(win), log=logs.append)
    patches = [mock.patch.object(af, "cursor_pos", return_value=cursor),
               mock.patch.object(af, "is_foreground", return_value=front)]
    for p in patches:
        p.start()
    return g, inp, logs, patches


class GuardTests(unittest.TestCase):
    def run_guard(self, **kw):
        g, inp, logs, patches = guard_with(**kw)
        try:
            g.down(); g.click()
        finally:
            for p in patches:
                p.stop()
        return inp, logs

    def test_all_checks_pass_allows_input(self):
        inp, _ = self.run_guard()
        self.assertEqual(inp.sent, ["down", "click"])

    def test_window_detection_failure_blocks_input(self):
        inp, logs = self.run_guard(win=None)
        self.assertEqual(inp.sent, [])
        self.assertIn("Roblox window not found", logs[0])

    def test_inactive_window_blocks_input(self):
        inp, logs = self.run_guard(front=False)
        self.assertEqual(inp.sent, [])
        self.assertIn("not the active window", logs[0])

    def test_cursor_outside_window_blocks_input(self):
        inp, logs = self.run_guard(cursor=OUTSIDE)
        self.assertEqual(inp.sent, [])
        self.assertIn("cursor is not over", logs[0])

    def test_window_lookup_exception_blocks_input(self):
        g = af.GameGrabber.__new__(af.GameGrabber)  # no real mss
        g.log, g.lookup_error = (lambda *_: None), None
        g.sct = types.SimpleNamespace(monitors=[MONITORS[0]] + MONITORS)
        with mock.patch.object(af, "find_roblox_window", side_effect=ImportError("No module named 'Quartz'")):
            self.assertIsNone(g.find_window())
        inp = FakeInput()
        guard = af.CursorGuard(inp, g, log=lambda *_: None)
        with mock.patch.object(af, "cursor_pos", return_value=INSIDE), \
                mock.patch.object(af, "find_roblox_window", side_effect=ImportError("x")):
            guard.click()
        self.assertEqual(inp.sent, [])

    def test_unsupported_os_has_no_window(self):
        with mock.patch.object(af, "IS_WIN", False), mock.patch.object(af, "IS_MAC", False):
            self.assertIsNone(af.find_roblox_window(MONITORS))
            self.assertFalse(af.is_foreground(ROBLOX))

    def test_failsafe_corner_raises(self):
        g, inp, _, patches = guard_with(cursor=CORNER)
        try:
            with self.assertRaises(af.FailSafeTriggered):
                g.click()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(inp.sent, [])

    def test_watchdog_releases_hold_when_focus_lost(self):
        g, inp, _, patches = guard_with()
        try:
            g.down()
            self.assertTrue(inp.held)
            with mock.patch.object(af, "is_foreground", return_value=False):
                g.watchdog()
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(inp.held)
        self.assertEqual(inp.sent, ["down", "up"])


class FakePDI:
    """Mimics pydirectinput, including its fail-safe that also blocks mouseUp in a corner."""

    class FailSafeException(Exception):
        pass

    def __init__(self, corner=False):
        self.FAILSAFE, self.PAUSE, self.corner, self.sent = False, 0.1, corner, []

    def _check(self):
        if self.FAILSAFE and self.corner:
            raise FakePDI.FailSafeException("corner")

    def mouseDown(self): self._check(); self.sent.append("down")
    def mouseUp(self): self._check(); self.sent.append("up")


class MouseInputTests(unittest.TestCase):
    def make(self, pdi):
        with mock.patch.object(af, "IS_WIN", True), mock.patch.dict(sys.modules, {"pydirectinput": pdi}):
            return af.MouseInput()

    def test_failsafe_is_enabled(self):
        pdi = FakePDI()
        self.make(pdi)
        self.assertTrue(pdi.FAILSAFE)

    def test_emergency_release_works_even_in_failsafe_corner(self):
        pdi = FakePDI()
        m = self.make(pdi)
        m.down()
        pdi.corner = True  # user slams the mouse into a corner while the button is held
        m.release_all()
        self.assertEqual(pdi.sent, ["down", "up"])
        self.assertFalse(m.held)
        self.assertTrue(pdi.FAILSAFE)  # restored afterwards

    def test_failsafe_on_press_becomes_failsafe_triggered(self):
        pdi = FakePDI(corner=True)
        m = self.make(pdi)
        with self.assertRaises(af.FailSafeTriggered):
            m.down()

    def test_click_releases_even_if_interrupted(self):
        pdi = FakePDI()
        m = self.make(pdi)
        with mock.patch.object(af.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                m.click()
        self.assertEqual(pdi.sent, ["down", "up"])
        self.assertFalse(m.held)


def blank_frame():
    return np.zeros((540, 960, 3), np.uint8)


class LoopTests(unittest.TestCase):
    def loop(self, grab, inp=None, flags=None, max_seconds=0, det=None):
        inp = inp or FakeInput()
        flags = flags or {"paused": False, "quit": False}
        bot = af.Bot(inp, log=lambda *_: None)
        t = [0.0]

        def clock():
            t[0] += 0.01
            return t[0]
        logs = []
        code = af.run_loop(bot, grab, det or af.Detector(), inp, flags, max_seconds,
                           log=logs.append, sleep=lambda s: None, clock=clock)
        return code, inp, logs, bot

    def test_ctrl_c_stops_and_releases(self):
        inp = FakeInput(); inp.down()
        code, inp, logs, _ = self.loop(mock.Mock(side_effect=KeyboardInterrupt), inp=inp)
        self.assertEqual(code, 0)
        self.assertFalse(inp.held)
        self.assertIn("Ctrl+C - stopping", logs)

    def test_unexpected_exception_releases_and_reports(self):
        inp = FakeInput(); inp.down()
        det = mock.Mock(); det.analyze.side_effect = RuntimeError("boom")
        with mock.patch.object(af.traceback, "print_exc"):
            code, inp, logs, _ = self.loop(mock.Mock(return_value=blank_frame()), inp=inp, det=det)
        self.assertEqual(code, 1)
        self.assertFalse(inp.held)
        self.assertTrue(any("RuntimeError: boom" in m for m in logs))

    def test_failsafe_stops_loop_and_releases(self):
        inp = FakeInput(); inp.down()
        inp.watchdog = mock.Mock(side_effect=af.FailSafeTriggered("corner"))
        code, inp, logs, _ = self.loop(mock.Mock(return_value=blank_frame()), inp=inp)
        self.assertEqual(code, 0)
        self.assertFalse(inp.held)
        self.assertTrue(any("FAIL-SAFE" in m for m in logs))

    def test_screen_capture_failure_retries_with_limit(self):
        grab = mock.Mock(side_effect=af.CaptureError("screen capture failed"))
        inp = FakeInput(); inp.down()
        code, inp, logs, _ = self.loop(grab, inp=inp)
        self.assertEqual(code, 1)
        self.assertEqual(grab.call_count, af.MAX_CAPTURE_FAILURES)  # bounded, no endless loop
        self.assertFalse(inp.held)
        self.assertTrue(any("too long" in m for m in logs))

    def test_capture_recovers(self):
        frames = [af.CaptureError("glitch")] * 3 + [blank_frame()] * 5 + [KeyboardInterrupt()]
        code, _, logs, _ = self.loop(mock.Mock(side_effect=frames))
        self.assertEqual(code, 0)
        self.assertIn("  capture working again", logs)

    def test_f8_pause_and_f9_stop(self):
        flags = {"paused": False, "quit": False}
        key = types.SimpleNamespace(f8="F8", f9="F9")
        on_key = af.make_hotkey_handler(flags, types.SimpleNamespace(Key=key), log=lambda *_: None)
        on_key("F8"); self.assertTrue(flags["paused"])
        on_key("F8"); self.assertFalse(flags["paused"])
        self.assertIs(on_key("F9"), False)  # stops the key listener
        self.assertTrue(flags["quit"])

        # paused: no frames analysed, nothing pressed, held button released
        inp = FakeInput(); inp.down()
        grab = mock.Mock(return_value=blank_frame())
        code, inp, _, _ = self.loop(grab, inp=inp, flags={"paused": True, "quit": False}, max_seconds=0.5)
        self.assertEqual(grab.call_count, 0)
        # released on pause, then the unconditional emergency release when the loop ends
        self.assertEqual(inp.sent, ["down", "up", "up"])

        # quit flag: the loop exits immediately
        grab = mock.Mock(return_value=blank_frame())
        code, _, _, _ = self.loop(grab, flags={"paused": False, "quit": True})
        self.assertEqual((code, grab.call_count), (0, 0))


class CaptureTests(unittest.TestCase):
    def grabber(self, grab_result=None, grab_error=None, win=ROBLOX, allow_fallback=False):
        sct = mock.Mock()
        sct.monitors = [MONITORS[0]] + MONITORS
        if grab_error:
            sct.grab.side_effect = grab_error
        else:
            sct.grab.return_value = grab_result
        fake_mss = types.SimpleNamespace(mss=lambda: sct)
        with mock.patch.dict(sys.modules, {"mss": fake_mss}):
            g = af.GameGrabber(log=lambda *_: None, allow_fallback=allow_fallback)
        return g, mock.patch.object(af, "find_roblox_window", return_value=win)

    def test_missing_window_is_capture_error_in_live_mode(self):
        g, p = self.grabber(grab_result=np.zeros((720, 1280, 4), np.uint8), win=None)
        with p, self.assertRaises(af.CaptureError):
            g()

    def test_missing_window_falls_back_only_for_dry_run(self):
        g, p = self.grabber(grab_result=np.zeros((1080, 1920, 4), np.uint8), win=None, allow_fallback=True)
        with p, mock.patch.object(af, "cursor_pos", return_value=INSIDE):
            self.assertEqual(g().shape, (1080, 1920, 3))
        self.assertIn("not found", g.desc)

    def test_capture_exception_becomes_capture_error(self):
        g, p = self.grabber(grab_error=OSError("BitBlt failed"))
        with p, self.assertRaises(af.CaptureError):
            g()

    def test_invalid_frame_is_rejected(self):
        for bad in (np.zeros((0, 0, 4), np.uint8), np.zeros((720, 1280), np.uint8)):
            g, p = self.grabber(grab_result=bad)
            with p, self.assertRaises(af.CaptureError):
                g()


class DryRunTests(unittest.TestCase):
    def test_dry_run_sends_zero_real_input(self):
        """Run the real live() in --dry mode with every real input backend replaced by a
        tripwire: any attempt to press, click or even construct a mouse controller fails."""
        tripwire = mock.Mock(side_effect=AssertionError("real input sent in dry-run"))
        pdi = types.SimpleNamespace(mouseDown=tripwire, mouseUp=tripwire, FAILSAFE=True, PAUSE=0)
        pynput_mouse = types.SimpleNamespace(Controller=tripwire, Button=types.SimpleNamespace(left=1))
        listener = mock.Mock()
        pynput_keyboard = types.SimpleNamespace(Listener=mock.Mock(return_value=listener),
                                                Key=types.SimpleNamespace(f8="F8", f9="F9"))
        pynput = types.SimpleNamespace(mouse=pynput_mouse, keyboard=pynput_keyboard)

        class FakeGrab:
            def __init__(self, log=print, allow_fallback=False):
                assert allow_fallback, "dry run should be allowed to watch the screen"
                self.desc = "fake"

            def __call__(self):
                return blank_frame()

        printed = []
        with mock.patch.dict(sys.modules, {"pydirectinput": pdi, "pynput": pynput,
                                           "pynput.mouse": pynput_mouse, "pynput.keyboard": pynput_keyboard}), \
                mock.patch.object(af, "GameGrabber", FakeGrab), \
                mock.patch.object(af, "MouseInput", side_effect=AssertionError("MouseInput built in dry-run")), \
                mock.patch.object(af, "GamepadInput", side_effect=AssertionError("GamepadInput built in dry-run")), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            code = af.live(False, 0, dry=True, max_seconds=1.0)
        self.assertEqual(code, 0)
        tripwire.assert_not_called()
        # it still watched the screen and logged what it WOULD have done
        self.assertTrue(any("[dry] would PRESS" in p for p in printed), printed[-10:])


if __name__ == "__main__":
    unittest.main()
