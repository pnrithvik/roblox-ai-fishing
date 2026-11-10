"""
AI Fishing: screen-reading fishing automation for Roblox (advanced Void Pearl farming).

Works on Windows and macOS, on any screen size / resolution / DPI scaling
(1080p laptops, 1440p/4K monitors, MacBook Retina displays, multi-monitor setups).

The fishing cycle:
  CAST    hold the button -> CHARGE meter fills (~1.0s) -> release when the fill
          reaches the gold band ("PERFECT!")
  WAIT    float sits in the pond until the bite
  MASH    "Mash R2! / Click Fast!" bar appears at the bottom -> click fast (~3s)
  REWARD  item popup (~3s) -> popup fades -> CAST again

Usage:
  python ai_fishing.py                 live mode, clicks at the current mouse position
  python ai_fishing.py --dry           live screen reading + decision log, NO clicks (safe first test)
  python ai_fishing.py --snapshot      save snapshot.png with the detection areas drawn on it
  python ai_fishing.py --simulate VIDEO   run the detector on a screen recording
  python ai_fishing.py --gamepad       Windows only: press R2 on a virtual Xbox pad (needs ViGEmBus + vgamepad)

Hotkeys (live): F8 pause/resume, F9 quit (on a Mac: fn+F8 / fn+F9). Ctrl+C in the terminal also quits.

DISCLAIMER: educational project, not affiliated with Roblox or any game developer. Automation may
break the rules of Roblox or a game and could get an account warned or banned. Use at your own risk.
"""
import argparse
import platform
import random
import sys
import time
import traceback

import cv2
import numpy as np

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _win_dpi_aware():
    """Use physical pixels on Windows, so window positions, the cursor and screenshots all
    agree on scaled displays (125% / 150% laptops, mixed-DPI multi-monitor setups)."""
    import ctypes
    errors = []
    for name, fn in (
            ("per-monitor v2", lambda: bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))),
            ("per-monitor", lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0),  # S_OK, Win 8.1+
            ("system", lambda: bool(ctypes.windll.user32.SetProcessDPIAware()))):           # Vista+
        try:
            if fn():
                return
            errors.append(f"{name}: refused")
        except Exception as e:  # older Windows without this API: try the next one
            errors.append(f"{name}: {e}")
    print(f"  ! could not enable DPI awareness ({'; '.join(errors)}); "
          "on scaled displays the click area may be slightly off")


if IS_WIN:
    _win_dpi_aware()

# ---------------------------------------------------------------- detection
# Every frame is scaled to a fixed height first, and every size/position below is
# measured in units of the image HEIGHT (Roblox scales the 3D view and most GUIs by
# height). This makes detection independent of resolution, Retina/HiDPI scaling,
# window size and aspect ratio (16:9, 16:10 MacBooks, ultrawide, 4:3).
WORK_H = 540

# Mash bar: red STOP button at the right end of a bar with bright-green progress segments.
# Measured on real gameplay: width 0.10 H, height 0.04 H, aspect 2.4-2.9, centre
# 0.86-0.91 H down, bar ~22 button-heights long. Limits below leave wide margins.
STOP_SEARCH = (0.0, 1.0, 0.50, 1.0)   # (x0, x1, y0, y1) fractions: whole bottom half
STOP_W = (0.035, 0.30)                # button width, in image heights
STOP_ASPECT = (1.6, 4.6)              # width / height
BAR_LEN = 24                          # how far left of STOP to look for the bar, in button heights

# CHARGE meter: vertical capsule beside the character (who is always at screen centre).
# Measured: 0.11 H left .. 0.28 H right of centre.
CHARGE_X = (-0.17, 0.45)              # offset from screen centre, in image heights
CHARGE_Y = (0.05, 0.90)               # fractions of image height

# HSV colour ranges (OpenCV: H 0-180, S/V 0-255). Widened from the measured values so
# small colour shifts (Mac Display-P3 capture, HDR, different GPUs) still match.
RED_LO1, RED_HI1 = (0, 120, 110), (10, 255, 255)
RED_LO2, RED_HI2 = (166, 120, 110), (180, 255, 255)
WHITE_LO, WHITE_HI = (0, 0, 175), (180, 80, 255)
LIME_LO, LIME_HI = (34, 110, 180), (60, 255, 255)
# Mint is kept tight on purpose: the lagoon scenery sits just outside it (hue 46-47). Mac P3
# capture still reads ~96% of the meter height, and the cast logic only uses relative changes.
MINT_LO, MINT_HI = (48, 90, 200), (88, 210, 255)


def _red(hsv):
    return cv2.inRange(hsv, RED_LO1, RED_HI1) | cv2.inRange(hsv, RED_LO2, RED_HI2)


def charge_box(w, h):
    """CHARGE search box (x0, x1, y0, y1) in pixels for an image of size w x h."""
    cx = w / 2
    return (max(int(cx + CHARGE_X[0] * h), 0), min(int(cx + CHARGE_X[1] * h), w),
            int(CHARGE_Y[0] * h), int(CHARGE_Y[1] * h))


def stop_box(w, h):
    return (int(STOP_SEARCH[0] * w), int(STOP_SEARCH[1] * w), int(STOP_SEARCH[2] * h), int(STOP_SEARCH[3] * h))


class Detector:
    def analyze(self, bgr):
        h0, w0 = bgr.shape[:2]
        s = WORK_H / h0
        interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
        bgr = cv2.resize(bgr, (max(int(round(w0 * s)), 1), WORK_H), interpolation=interp)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mash_bar, lime = self._mash_bar(hsv)
        charge = self._charge(hsv)
        # Reward popup washes the whole screen out: mean brightness jumps ~+30.
        bright = float(hsv[..., 2].mean())
        return {"mash_bar": mash_bar, "lime": lime, "bright": bright, **charge}

    @staticmethod
    def _mash_bar(hsv):
        """Find the STOP button (red ~2.5:1 box with white text) and check that the bar
        to its left contains the bright-green progress segments."""
        H, W = hsv.shape[:2]
        x0, x1, y0, y1 = stop_box(W, H)
        roi = hsv[y0:y1, x0:x1]
        red = cv2.morphologyEx(_red(roi), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(red)
        best = 0.0
        for x, y, w, h, _area in stats[1:]:
            if not (STOP_W[0] * H <= w <= STOP_W[1] * H and STOP_ASPECT[0] <= w / max(h, 1) <= STOP_ASPECT[1]):
                continue
            box = roi[y:y + h, x:x + w]
            white = np.count_nonzero(cv2.inRange(box, WHITE_LO, WHITE_HI)) / box[..., 0].size
            if white < 0.06:
                continue
            bx = x0 + x
            strip = hsv[y0 + y:y0 + y + h, max(bx - BAR_LEN * h, 0):bx]
            if strip.shape[1] < 2 * h:
                continue
            lime = np.count_nonzero(cv2.inRange(strip, LIME_LO, LIME_HI)) / strip[..., 0].size
            best = max(best, lime)
        return best > 0.06, best

    @staticmethod
    def _charge(hsv):
        """Charge meter visibility and fill height (fraction of image height).
        The fill rises ~1s and stops when it reaches the gold band -> release for PERFECT."""
        H, W = hsv.shape[:2]
        x0, x1, y0, y1 = charge_box(W, H)
        mint = cv2.inRange(hsv[y0:y1, x0:x1], MINT_LO, MINT_HI)
        h = int(np.count_nonzero(mint, axis=0).max()) if mint.size else 0
        visible = h >= H * 0.02  # the "x1 Luck" label alone is ~0.013
        return {"charge_visible": visible, "fill": h / H if visible else 0.0}


# ---------------------------------------------------------------- input
class FailSafeTriggered(Exception):
    """The mouse was moved into a screen corner: emergency stop."""


FAILSAFE_MARGIN = 2  # px from a monitor corner that counts as "in the corner"


class MouseInput:
    """Left mouse button at the current cursor position.
    Windows: pydirectinput (SendInput, which Roblox reliably accepts). macOS: pynput.
    Never use this directly in live mode: always wrap it in CursorGuard."""

    def __init__(self):
        self._down = self._up = None
        self._pdi = None
        self.held = False
        if IS_WIN:
            try:
                import pydirectinput
                pydirectinput.PAUSE = 0
                pydirectinput.FAILSAFE = True  # mouse into a screen corner = emergency stop
                self._pdi = pydirectinput
                self._down, self._up = pydirectinput.mouseDown, pydirectinput.mouseUp
            except ImportError:
                print("  ! pydirectinput is not installed - using pynput for clicks instead")
        if self._down is None:
            from pynput.mouse import Button, Controller
            m = Controller()
            self._down, self._up = (lambda: m.press(Button.left)), (lambda: m.release(Button.left))

    def down(self):
        self.held = True  # set first, so a failure half-way through still gets released
        try:
            self._down()
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                raise FailSafeTriggered("mouse moved into a screen corner") from e
            raise

    def up(self):
        """Release the button if we are holding it."""
        if self.held:
            self.release_all()

    def release_all(self):
        """Always send a release, even with the cursor in a fail-safe corner
        (pydirectinput would otherwise refuse and leave the button stuck down)."""
        pdi = self._pdi
        old = pdi.FAILSAFE if pdi else None
        try:
            if pdi:
                pdi.FAILSAFE = False
            self._up()
        finally:
            if pdi:
                pdi.FAILSAFE = old
            self.held = False

    def click(self):
        self.down()
        try:
            time.sleep(random.uniform(0.012, 0.03))
        finally:
            self.up()


class GamepadInput:
    def __init__(self):
        if not IS_WIN:
            sys.exit("--gamepad is only supported on Windows (it needs the ViGEmBus driver). Use mouse mode instead.")
        try:
            import vgamepad as vg  # requires the ViGEmBus driver
        except ImportError:
            sys.exit("--gamepad needs:  pip install vgamepad   (and the ViGEmBus driver)")
        self.pad = vg.VX360Gamepad()
        self.held = False

    def _trig(self, v):
        self.pad.right_trigger(value=v); self.pad.update()

    def down(self):
        self.held = True
        self._trig(255)

    def up(self):
        if self.held:
            self.release_all()

    def release_all(self):
        try:
            self._trig(0)
        finally:
            self.held = False

    def click(self):
        self.down()
        try:
            time.sleep(random.uniform(0.015, 0.035))
        finally:
            self.up()


_cursor_warned = False


def cursor_pos():
    """Mouse position in the same coordinate space as the screen capture."""
    global _cursor_warned
    if IS_WIN:
        import ctypes
        import ctypes.wintypes as wt
        p = wt.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
            raise OSError("GetCursorPos failed")
        return p.x, p.y
    if IS_MAC:
        try:
            import Quartz
            loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            return int(loc.x), int(loc.y)
        except Exception as e:
            if not _cursor_warned:
                print(f"  ! Quartz cursor lookup failed ({e}); using pynput instead")
                _cursor_warned = True
    from pynput.mouse import Controller
    x, y = Controller().position
    return int(x), int(y)


def in_failsafe_corner(x, y, monitors):
    """True if (x, y) is in a corner of any monitor."""
    m = FAILSAFE_MARGIN
    for mon in monitors:
        l, t = mon["left"], mon["top"]
        r, b = l + mon["width"] - 1, t + mon["height"] - 1
        if (abs(x - l) <= m or abs(x - r) <= m) and (abs(y - t) <= m or abs(y - b) <= m):
            return True
    return False


class CursorGuard:
    """The single safety gate for every automated press. Before EACH press/click it checks,
    freshly, that:
      1. the Roblox window is positively identified (exists, visible, not minimised),
      2. Roblox is the foreground (active) window,
      3. the mouse cursor is inside the Roblox window,
      4. the cursor is not in a screen corner (fail-safe: raises FailSafeTriggered).
    If any check fails, nothing is sent, and a held button is released."""

    def __init__(self, inp, grabber, log=print):
        self.inp, self.g, self.log = inp, grabber, log
        self.last_reason = None

    @property
    def held(self):
        return getattr(self.inp, "held", False)

    def check(self):
        """(ok, reason). Raises FailSafeTriggered if the cursor is in a screen corner."""
        try:
            x, y = cursor_pos()
        except Exception as e:
            return False, f"cannot read the mouse position ({e})"
        if in_failsafe_corner(x, y, self.g.monitors()):
            raise FailSafeTriggered("mouse moved into a screen corner")
        win = self.g.find_window()
        if not win:
            return False, "Roblox window not found (open it and don't minimise it)"
        try:
            front = is_foreground(win)
        except Exception as e:
            return False, f"cannot check the active window ({e})"
        if not front:
            return False, "Roblox is not the active window (click into the game)"
        a = win["area"]
        if not (a["left"] <= x < a["left"] + a["width"] and a["top"] <= y < a["top"] + a["height"]):
            return False, "the cursor is not over the Roblox window"
        return True, ""

    def _ok(self):
        ok, why = self.check()
        if not ok and why != self.last_reason:
            self.log(f"  ! not clicking: {why}")
        elif ok and self.last_reason:
            self.log("  clicking enabled again")
        self.last_reason = None if ok else why
        return ok

    def down(self):
        if self._ok():
            self.inp.down()

    def up(self):
        self.inp.up()  # always allowed, so a hold is never left stuck

    def release_all(self):
        self.inp.release_all()

    def click(self):
        if self._ok():
            self.inp.click()

    def watchdog(self):
        """Called every frame: fail-safe corner check, and if a button is held while it is
        no longer safe, let go."""
        try:
            x, y = cursor_pos()
        except Exception:
            x = y = None
        if x is not None and in_failsafe_corner(x, y, self.g.monitors()):
            raise FailSafeTriggered("mouse moved into a screen corner")
        if self.held and not self._ok():
            self.inp.up()


class LogInput:
    """Dry-run input: records/logs what would happen, never sends any real input."""

    def __init__(self, log=None):
        self.events = []; self.t = 0.0; self.clicks = 0; self.held = False; self.log = log

    def down(self):
        self.events.append((self.t, "PRESS (hold)")); self.held = True
        if self.log:
            self.log("  [dry] would PRESS and hold the mouse button")

    def up(self):
        self.events.append((self.t, "RELEASE"))
        if self.held and self.log:
            self.log("  [dry] would RELEASE the mouse button")
        self.held = False

    def release_all(self):
        self.up()

    def click(self):
        self.clicks += 1
        if self.log and self.clicks % 10 == 1:
            self.log(f"  [dry] would click ({self.clicks} clicks so far)")


# ---------------------------------------------------------------- state machine
class Bot:
    def __init__(self, inp, log=print, cast_wait=1.0):
        self.inp, self.log = inp, log
        self.cast_wait = cast_wait  # how long to hold waiting for the CHARGE meter to appear
        self.release_delay = 0.18
        self.top_at = None
        self.charge_start = 0.0
        self.state, self.t_state = "START", 0.0
        self.next_click = 0.0
        self.bar_seen_at = self.bar_lost_at = None
        self.charge_seen = False
        self.fill_full = None   # learned full-charge height
        self.base_bright = None
        self.bar_streak = self.blind_casts = 0
        self.peak_bright, self.popup_gone_at = 0.0, None
        self.casts = self.catches = self.perfect_releases = 0
        self.mash_clicks = 0

    def _go(self, state, t):
        self.log(f"[{t:7.2f}s] {self.state:>9} -> {state}")
        self.state, self.t_state = state, t
        if state == "COOLDOWN":
            self.peak_bright, self.popup_gone_at = 0.0, None

    def tick(self, d, t):
        dt = t - self.t_state
        s = self.state
        # Require the bar on 3 consecutive frames before reacting, so a stray red/green
        # icon can never make us click (= reel in) while the float is waiting for a bite.
        self.bar_streak = self.bar_streak + 1 if d["mash_bar"] else 0
        bar_confirmed = self.bar_streak >= 3
        if self.base_bright is None:
            self.base_bright = d["bright"]

        if s == "START":
            if bar_confirmed:
                self._go("MASH", t)
            elif dt > 0.3:
                self._go("CAST", t); self._press(t)

        elif s == "CAST":  # button held, watching the charge meter
            f = d["fill"]
            ready = False
            if d["charge_visible"]:
                if not self.charge_seen:
                    self.charge_seen, self.fill_first, self.fill_hist = True, f, []
                    self.charge_start = dt
                self.fill_hist.append((t, f))
                # "Fill stopped rising" = stable over the last 0.12s, and over at least 3 frames
                # so a slow PC (few frames per second) can't mistake one sample for a plateau.
                recent = [(tt, v) for (tt, v) in self.fill_hist if t - tt <= 0.12]
                if len(recent) < 3:
                    recent = self.fill_hist[-3:]
                vals = [v for _, v in recent]
                grown = f >= 2.2 * self.fill_first
                plateau = (grown and len(recent) >= 3 and t - self.fill_hist[0][0] > 0.4
                           and max(vals) - min(vals) < 0.0025)
                learned = self.fill_full and f >= 0.97 * self.fill_full
                if plateau:
                    self.fill_full = max(self.fill_full or 0, f)
                # The mint height tops out just before the fill enters the gold band;
                # the player's PERFECT releases came ~0.2s later, so wait a beat.
                # (On slow frame rates the plateau is seen late; count from when it began.)
                if (plateau or learned) and self.top_at is None:
                    self.top_at = min(t, recent[0][0] + 0.12) if plateau else t
                ready = self.top_at is not None and t - self.top_at >= self.release_delay
            gone_after_seen = self.charge_seen and not d["charge_visible"]
            too_long = (dt - self.charge_start > 1.6) if self.charge_seen else (dt > self.cast_wait)
            if ready or gone_after_seen or too_long:
                self.inp.up()
                why = ("gold reached" if ready else "meter gone" if gone_after_seen
                       else "timeout")
                if ready:
                    self.perfect_releases += 1
                held = dt - self.charge_start if self.charge_seen else dt
                self.log(f"[{t:7.2f}s] release ({why}, charge meter held {held:.2f}s, fill={f:.3f})")
                if self.charge_seen:
                    self.casts += 1
                    self.blind_casts = 0
                    self._go("WAIT_BITE", t)
                else:  # cast didn't register (popup / menu open) - retry
                    self.blind_casts += 1
                    if self.blind_casts >= 3:
                        self.log(f"[{t:7.2f}s] WARNING: {self.blind_casts} casts in a row with no CHARGE "
                                 "meter visible. Is the Roblox window visible (not minimised/covered), "
                                 "and is the rod equipped? Slowing retries to every 8s.")
                    self._go("RETRY", t)

        elif s == "RETRY":
            if bar_confirmed:
                self._go("MASH", t)
            elif dt > (8.0 if self.blind_casts >= 3 else 1.5):
                self._go("CAST", t); self._press(t)

        elif s == "WAIT_BITE":
            if bar_confirmed:
                self._go("MASH", t); self.bar_lost_at = None
            elif dt > 60:
                self.log(f"[{t:7.2f}s] no bite in 60s, recasting")
                self._go("CAST", t); self._press(t)

        elif s == "MASH":
            if d["mash_bar"]:
                # Track normal scene brightness so the reward popup can be recognised.
                self.base_bright = 0.9 * self.base_bright + 0.1 * d["bright"] \
                    if self.base_bright else d["bright"]
                self.bar_lost_at = None
                # Usually one click per frame; on a slow PC catch up (max 3) so the
                # click rate stays ~11-22 clicks/s regardless of frame rate.
                n = 0
                while t >= self.next_click and n < 3:
                    self.inp.click(); self.mash_clicks += 1; n += 1
                    self.next_click = max(self.next_click, t - 0.2) + random.uniform(0.045, 0.09)
            else:
                self.bar_lost_at = self.bar_lost_at or t
                if t - self.bar_lost_at > 0.35:
                    self.catches += 1
                    self.log(f"[{t:7.2f}s] catch #{self.catches} done")
                    self._go("COOLDOWN", t)

        elif s == "COOLDOWN":  # reward popup (~2.6s) brightens the screen; game ignores input
            self.peak_bright = max(self.peak_bright, d["bright"])
            popup_seen = self.peak_bright > self.base_bright + 12
            if popup_seen and self.popup_gone_at is None and d["bright"] < self.base_bright + 6:
                self.popup_gone_at = t
                self.log(f"[{t:7.2f}s] reward popup closed")
            if bar_confirmed:
                self._go("MASH", t)
            # Live runs showed the game still ignores a cast ~1s after the popup fades.
            elif (self.popup_gone_at and t - self.popup_gone_at > 1.2) or dt > 6.0:
                self._go("CAST", t); self._press(t)

    def _press(self, t):
        self.charge_seen = False
        self.top_at = None
        self.inp.down()


# ---------------------------------------------------------------- screen capture
class CaptureError(Exception):
    """The game could not be captured (window missing, capture failed, invalid frame)."""


def _find_roblox_windows():
    """The visible, non-minimised Roblox game window: {"area": client area, "hwnd": handle},
    or None. Windows: physical pixels."""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if 0 < n < 64:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.strip().lower() == "roblox":  # exact title; skips browser tabs etc.
                    r, p = wt.RECT(), wt.POINT(0, 0)
                    u.GetClientRect(hwnd, ctypes.byref(r))
                    u.ClientToScreen(hwnd, ctypes.byref(p))
                    if r.right > 200 and r.bottom > 200:
                        found.append({"area": {"left": p.x, "top": p.y, "width": r.right, "height": r.bottom},
                                      "hwnd": hwnd})
        return True

    u.EnumWindows(each, 0)
    return max(found, key=lambda w: w["area"]["width"] * w["area"]["height"]) if found else None


def _find_roblox_mac(monitors):
    """The Roblox window on macOS: {"area": in points (the same space mss uses) without the
    title bar, "front": True if Roblox is the frontmost app}, or None."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    best, front_owner = None, None
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:  # front to back
        if w.get(Quartz.kCGWindowLayer, 0) != 0:
            continue
        owner = str(w.get(Quartz.kCGWindowOwnerName, "") or "").lower()
        b = w.get(Quartz.kCGWindowBounds) or {}
        a = {"left": int(b.get("X", 0)), "top": int(b.get("Y", 0)),
             "width": int(b.get("Width", 0)), "height": int(b.get("Height", 0))}
        if front_owner is None and a["width"] > 50 and a["height"] > 50:
            front_owner = owner  # first normal window = the frontmost application
        if not owner.startswith("roblox") or "studio" in owner:
            continue
        if a["width"] > 200 and a["height"] > 200 and (best is None or a["width"] * a["height"] > best["width"] * best["height"]):
            best = a
    if not best:
        return None
    fullscreen = any(m["left"] == best["left"] and m["top"] == best["top"] and m["width"] == best["width"]
                     and m["height"] == best["height"] for m in monitors[1:])
    if not fullscreen and best["height"] > 300:  # windowed: skip the ~28 pt title bar
        best["top"] += 28
        best["height"] -= 28
    front = bool(front_owner) and front_owner.startswith("roblox") and "studio" not in front_owner
    return {"area": best, "front": front}


def find_roblox_window(monitors):
    """The positively identified Roblox window, or None. On other systems there is no reliable
    window lookup, so this returns None and automated input stays disabled (fail closed)."""
    if IS_WIN:
        return _find_roblox_windows()
    if IS_MAC:
        return _find_roblox_mac(monitors)
    return None


def is_foreground(win):
    """True if the Roblox window is the active (foreground) window."""
    if IS_WIN:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wt.HWND
        u.GetAncestor.restype = wt.HWND
        u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        fg = u.GetForegroundWindow()
        if not fg:
            return False
        return fg == win["hwnd"] or u.GetAncestor(fg, 2) == win["hwnd"]  # 2 = GA_ROOT
    if IS_MAC:
        return bool(win.get("front"))
    return False


class GameGrabber:
    """Captures the Roblox game area on whichever monitor it is on (follows it if it moves).
    allow_fallback=True (only for --dry and --snapshot, which never click) captures the monitor
    under the cursor when Roblox isn't found; otherwise a missing window is a CaptureError."""

    def __init__(self, log=print, allow_fallback=False):
        import mss
        self.log = log
        self.allow_fallback = allow_fallback
        self.sct = (getattr(mss, "MSS", None) or mss.mss)()
        self.area, self.t_area, self.desc = None, 0.0, None
        self.lookup_error = None

    def monitors(self):
        return self.sct.monitors[1:]

    def find_window(self):
        """Fresh lookup of the Roblox window (or None). Lookup errors are reported, not hidden."""
        try:
            win = find_roblox_window(self.sct.monitors)
        except Exception as e:  # e.g. pyobjc missing: report it; input stays disabled
            msg = f"{type(e).__name__}: {e}"
            if msg != self.lookup_error:
                self.log(f"  ! Roblox window lookup failed ({msg}) - automated clicking is disabled")
                self.lookup_error = msg
            return None
        self.lookup_error = None
        return win

    def _find_area(self):
        win = self.find_window()
        if win:
            a = win["area"]
            v = self.sct.monitors[0]  # bounding box of all monitors: clip off-screen parts
            l, t = max(a["left"], v["left"]), max(a["top"], v["top"])
            r = min(a["left"] + a["width"], v["left"] + v["width"])
            b = min(a["top"] + a["height"], v["top"] + v["height"])
            if r - l > 200 and b - t > 200:
                return {"left": l, "top": t, "width": r - l, "height": b - t}, "Roblox window"
        if not self.allow_fallback:
            return None, "Roblox window not found (open Roblox and don't minimise it)"
        try:
            x, y = cursor_pos()
        except Exception as e:
            self.log(f"  ! cannot read the mouse position ({e}); using the primary monitor")
            x, y = None, None
        for m in self.monitors():
            if x is not None and m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return dict(m), "monitor under the cursor (Roblox window not found)"
        return dict(self.sct.monitors[1]), "primary monitor (Roblox window not found)"

    def __call__(self):
        now = time.monotonic()
        if self.area is None or now - self.t_area > 1.0:  # follow the window if it moves/resizes
            area, desc = self._find_area()
            changed = (area, desc) != (self.area, self.desc)
            self.area, self.desc, self.t_area = area, desc, now
        else:
            changed = False
        if self.area is None:
            self.t_area = 0.0  # look again next time
            raise CaptureError(self.desc)
        try:
            img = np.asarray(self.sct.grab(self.area))
        except Exception as e:
            self.area = None  # re-locate the window on the next attempt
            raise CaptureError(f"screen capture failed ({type(e).__name__}: {e})") from e
        if img.ndim != 3 or img.shape[2] not in (3, 4) or img.shape[0] < 50 or img.shape[1] < 50:
            self.area = None
            raise CaptureError(f"invalid frame from screen capture (shape {img.shape})")
        if changed:
            scale = img.shape[1] / self.area["width"]
            extra = f", {img.shape[1]}x{img.shape[0]} px ({scale:g}x HiDPI/Retina)" if abs(scale - 1) > 0.01 else ""
            self.log(f"capturing {self.desc}: {self.area['width']}x{self.area['height']} "
                     f"at ({self.area['left']},{self.area['top']}){extra}")
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR if img.shape[2] == 4 else cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------- permissions (macOS)
def check_mac_permissions():
    """macOS needs Screen Recording (to see Roblox) and Accessibility (to click / hotkeys)
    permission for the app you run this from (Terminal, iTerm, VS Code, ...)."""
    ok = True
    try:
        import Quartz
        if hasattr(Quartz, "CGPreflightScreenCaptureAccess") and not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
            print("! Screen Recording permission is missing. Enable it for your terminal app in\n"
                  "  System Settings > Privacy & Security > Screen Recording, then restart the terminal.")
            ok = False
    except Exception as e:
        print(f"! Could not check the Screen Recording permission ({e}).")
    try:
        from ApplicationServices import AXIsProcessTrusted
        if not AXIsProcessTrusted():
            print("! Accessibility permission is missing (needed to click and for F8/F9). Enable it for your\n"
                  "  terminal app in System Settings > Privacy & Security > Accessibility, then restart the terminal.")
            ok = False
    except Exception as e:
        print(f"! Could not check the Accessibility permission ({e}).")
    return ok


# ---------------------------------------------------------------- runners
def simulate(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    det, inp = Detector(), LogInput()
    # The recording can't react to our presses, so hold until the player's own cast shows the meter.
    bot = Bot(inp, cast_wait=10.0)
    i, t0 = 0, time.perf_counter()
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if f is None or f.size == 0:
            print(f"  ! skipping invalid frame {i}")
            i += 1
            continue
        t = i / fps
        inp.t = t
        bot.tick(det.analyze(f), t)
        i += 1
    el = time.perf_counter() - t0
    print(f"\n--- simulation summary ---\nframes {i} ({i / fps:.1f}s video) analysed in {el:.1f}s "
          f"-> {i / max(el, 1e-9):.0f} fps detector throughput (CPU)")
    print(f"casts {bot.casts}, perfect releases {bot.perfect_releases}, catches {bot.catches}, "
          f"mash clicks that would be sent {bot.mash_clicks}")


def snapshot():
    try:
        img = GameGrabber(allow_fallback=True)()
    except CaptureError as e:
        sys.exit(f"ERROR: {e}")
    d = Detector().analyze(img)
    h, w = img.shape[:2]
    lw = max(2, h // 300)
    for (x0, x1, y0, y1), c in [(stop_box(w, h), (0, 0, 255)), (charge_box(w, h), (255, 255, 0))]:
        cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), c, lw)
    if not cv2.imwrite("snapshot.png", img):
        sys.exit("ERROR: could not write snapshot.png (is the folder writable?)")
    print("saved snapshot.png", d)


def make_hotkey_handler(flags, keyboard, log=print):
    """F8 toggles pause, F9 quits."""
    def on_key(k):
        if k == keyboard.Key.f8:
            flags["paused"] = not flags["paused"]
            log("PAUSED" if flags["paused"] else "RESUMED")
        elif k == keyboard.Key.f9:
            flags["quit"] = True
            log("F9 pressed - stopping")
            return False
    return on_key


MAX_CAPTURE_FAILURES = 60  # consecutive failures (~2 minutes with back-off) before giving up


def run_loop(bot, grab, det, inp, flags, max_seconds=0, log=print, sleep=time.sleep, clock=time.monotonic):
    """Main loop. Returns an exit code (0 = normal stop). The mouse button is always released."""
    t0 = clock()
    frames, t_fps, failures = 0, clock(), 0
    code = 0
    try:
        while not flags["quit"]:
            t_loop = clock()
            if max_seconds and t_loop - t0 > max_seconds:
                break
            if flags["paused"]:
                inp.up(); bot.state = "START"; sleep(0.1); continue
            try:
                frame = grab()
            except CaptureError as e:
                failures += 1
                inp.up()  # never keep holding while blind
                bot.state = "START"
                if failures == 1 or failures % 10 == 0:
                    log(f"  ! {e} - retrying (attempt {failures}/{MAX_CAPTURE_FAILURES})")
                if failures >= MAX_CAPTURE_FAILURES:
                    log("ERROR: the game could not be captured for too long - stopping.")
                    code = 1
                    break
                sleep(min(0.25 * failures, 2.0))  # back off instead of spinning
                continue
            if failures:
                log("  capture working again")
                failures = 0
            if hasattr(inp, "watchdog"):
                inp.watchdog()
            bot.tick(det.analyze(frame), clock() - t0)
            frames += 1
            if clock() - t_fps > 10:
                fps = frames / (clock() - t_fps)
                slow = "  <- slow; close other apps or make the Roblox window smaller" if fps < 6 else ""
                log(f"  (loop {fps:.0f} fps, state {bot.state}){slow}")
                frames, t_fps = 0, clock()
            # ~30 checks per second is plenty; don't burn a whole CPU core on fast PCs
            sleep(max(0.0, 1 / 30 - (clock() - t_loop)))
    except KeyboardInterrupt:
        log("Ctrl+C - stopping")
    except FailSafeTriggered:
        log("FAIL-SAFE: the mouse was moved into a screen corner - emergency stop")
    except Exception as e:
        log(f"ERROR: unexpected {type(e).__name__}: {e}")
        traceback.print_exc()
        code = 1
    finally:
        try:
            inp.release_all()  # never leave the mouse button held down
        except Exception as e:
            log(f"ERROR: could not release the mouse button ({e}) - click once manually to release it")
            code = 1
    return code


def live(use_gamepad, countdown, dry=False, max_seconds=0):
    print(f"AI Fishing on {platform.system()} {platform.release()}, Python {platform.python_version()}")
    print("Educational project, use at your own risk: automation may break Roblox or game rules (see README).")
    if IS_MAC and not dry:
        check_mac_permissions()
    if not dry and not (IS_WIN or IS_MAC):
        sys.exit("Live mode needs Windows or macOS (Roblox window detection). Use --dry to test the detector.")
    flags = {"paused": False, "quit": False}
    try:
        from pynput import keyboard
        keyboard.Listener(on_press=make_hotkey_handler(flags, keyboard)).start()
        hot = "F8 = pause/resume, F9 = quit" + (" (fn+F8 / fn+F9 on a Mac)" if IS_MAC else "") + ", or Ctrl+C here"
    except Exception as e:
        hot = f"hotkeys unavailable ({e}); press Ctrl+C in this window to quit"

    if dry:
        inp = LogInput(log=print)
        print("DRY RUN: reading the screen and logging decisions, no clicks sent.")
    else:
        inp = GamepadInput() if use_gamepad else MouseInput()
    grab, det = GameGrabber(allow_fallback=dry), Detector()
    try:
        grab()  # log what will be captured
    except CaptureError as e:
        print(f"! {e}. Waiting for it...")
    if not dry:
        inp = CursorGuard(inp, grab)
        print("Clicks are only sent while Roblox is the active window and the cursor is over it.")
        print("Emergency stop: move the mouse into any screen corner.")
    print(f"Starting in {countdown:g}s - click into the game window and leave the cursor over it.")
    print(hot)
    try:
        time.sleep(countdown)
    except KeyboardInterrupt:
        print("cancelled")
        return 0
    bot = Bot(inp)
    code = run_loop(bot, grab, det, inp, flags, max_seconds)
    print(f"stopped: casts {bot.casts}, catches {bot.catches}, perfect releases {bot.perfect_releases}")
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AI Fishing for Roblox (Windows / macOS)")
    ap.add_argument("--simulate", metavar="VIDEO", help="run the detector on a screen recording")
    ap.add_argument("--snapshot", action="store_true", help="save snapshot.png with the detection areas")
    ap.add_argument("--gamepad", action="store_true", help="Windows only: virtual Xbox pad R2 instead of mouse")
    ap.add_argument("--dry", action="store_true", help="live screen reading, but send no input")
    ap.add_argument("--countdown", type=float, default=5)
    ap.add_argument("--seconds", type=float, default=0, help="stop automatically after N seconds")
    a = ap.parse_args()
    if a.simulate:
        simulate(a.simulate)
    elif a.snapshot:
        snapshot()
    else:
        sys.exit(live(a.gamepad, a.countdown, a.dry, a.seconds))
