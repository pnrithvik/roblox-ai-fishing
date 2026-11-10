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

import cv2
import numpy as np

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _win_dpi_aware():
    """Use physical pixels on Windows, so window positions, the cursor and screenshots all
    agree on scaled displays (125% / 150% laptops, mixed-DPI multi-monitor setups)."""
    import ctypes
    for fn in (lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),  # per-monitor v2
               lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),                          # Win 8.1+
               lambda: ctypes.windll.user32.SetProcessDPIAware()):                              # Vista+
        try:
            if fn():
                return
        except Exception:
            pass


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
class MouseInput:
    """Left mouse button at the current cursor position.
    Windows: pydirectinput (SendInput, which Roblox reliably accepts). macOS/Linux: pynput."""

    def __init__(self):
        self._down = self._up = None
        if IS_WIN:
            try:
                import pydirectinput
                pydirectinput.PAUSE = 0
                pydirectinput.FAILSAFE = False
                self._down, self._up = pydirectinput.mouseDown, pydirectinput.mouseUp
            except ImportError:
                pass
        if self._down is None:
            from pynput.mouse import Button, Controller
            m = Controller()
            self._down, self._up = (lambda: m.press(Button.left)), (lambda: m.release(Button.left))

    def down(self): self._down()
    def up(self): self._up()

    def click(self):
        self._down(); time.sleep(random.uniform(0.012, 0.03)); self._up()


class GamepadInput:
    def __init__(self):
        if not IS_WIN:
            sys.exit("--gamepad is only supported on Windows (it needs the ViGEmBus driver). Use mouse mode instead.")
        try:
            import vgamepad as vg  # requires the ViGEmBus driver
        except ImportError:
            sys.exit("--gamepad needs:  pip install vgamepad   (and the ViGEmBus driver)")
        self.pad = vg.VX360Gamepad()

    def _trig(self, v):
        self.pad.right_trigger(value=v); self.pad.update()

    def down(self): self._trig(255)
    def up(self): self._trig(0)

    def click(self):
        self._trig(255); time.sleep(random.uniform(0.015, 0.035)); self._trig(0)


def cursor_pos():
    """Mouse position in the same coordinate space as the screen capture."""
    if IS_WIN:
        import ctypes
        import ctypes.wintypes as wt
        p = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    if IS_MAC:
        try:
            import Quartz
            loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            return int(loc.x), int(loc.y)
        except Exception:
            pass
    from pynput.mouse import Controller
    x, y = Controller().position
    return int(x), int(y)


class CursorGuard:
    """Only lets presses through while the mouse cursor is over the captured game area,
    so clicks never land in another window (editor, browser, ...)."""

    def __init__(self, inp, grabber):
        self.inp, self.g, self.warned = inp, grabber, False

    def _ok(self):
        x, y = cursor_pos()
        a = self.g.area
        inside = bool(a) and a["left"] <= x < a["left"] + a["width"] and a["top"] <= y < a["top"] + a["height"]
        if not inside and not self.warned:
            print("  ! cursor is not over the game window - not clicking until it is")
        self.warned = not inside
        return inside

    def down(self):
        if self._ok(): self.inp.down()

    def up(self): self.inp.up()  # always allowed, so a hold is never left stuck

    def click(self):
        if self._ok(): self.inp.click()


class LogInput:
    """Dry-run input: records actions instead of sending them."""
    def __init__(self): self.events = []; self.t = 0.0; self.clicks = 0
    def down(self): self.events.append((self.t, "PRESS (hold)"))
    def up(self): self.events.append((self.t, "RELEASE"))
    def click(self): self.clicks += 1


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
def _find_roblox_windows():
    """Client area of the Roblox game window, or None. Windows: physical pixels."""
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
                        found.append({"left": p.x, "top": p.y, "width": r.right, "height": r.bottom})
        return True

    u.EnumWindows(each, 0)
    return max(found, key=lambda a: a["width"] * a["height"]) if found else None


def _find_roblox_mac(monitors):
    """Roblox window on macOS, in points (the same space mss uses), without the title bar."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    best = None
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        owner = str(w.get(Quartz.kCGWindowOwnerName, "") or "").lower()
        if not owner.startswith("roblox") or "studio" in owner or w.get(Quartz.kCGWindowLayer, 0) != 0:
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        a = {"left": int(b.get("X", 0)), "top": int(b.get("Y", 0)),
             "width": int(b.get("Width", 0)), "height": int(b.get("Height", 0))}
        if a["width"] > 200 and a["height"] > 200 and (best is None or a["width"] * a["height"] > best["width"] * best["height"]):
            best = a
    if best:
        fullscreen = any(m["left"] == best["left"] and m["top"] == best["top"] and m["width"] == best["width"]
                         and m["height"] == best["height"] for m in monitors[1:])
        if not fullscreen and best["height"] > 300:  # windowed: skip the ~28 pt title bar
            best["top"] += 28
            best["height"] -= 28
    return best


class GameGrabber:
    """Captures the Roblox game area on whichever monitor it is on (follows it if it moves).
    Falls back to the monitor under the mouse cursor if the window can't be found."""

    def __init__(self, log=print):
        import mss
        self.log = log
        self.sct = (getattr(mss, "MSS", None) or mss.mss)()
        self.area, self.t_area, self.desc = None, 0.0, None
        self.find_failed = False

    def _find_window(self):
        try:
            if IS_WIN:
                return _find_roblox_windows()
            if IS_MAC:
                return _find_roblox_mac(self.sct.monitors)
        except Exception as e:  # e.g. pyobjc missing - fall back to monitor capture
            if not self.find_failed:
                self.log(f"  (window lookup unavailable: {e})")
                self.find_failed = True
        return None

    def _find_area(self):
        a = self._find_window()
        if a:
            v = self.sct.monitors[0]  # bounding box of all monitors: clip off-screen parts
            l, t = max(a["left"], v["left"]), max(a["top"], v["top"])
            r = min(a["left"] + a["width"], v["left"] + v["width"])
            b = min(a["top"] + a["height"], v["top"] + v["height"])
            if r - l > 200 and b - t > 200:
                return {"left": l, "top": t, "width": r - l, "height": b - t}, "Roblox window"
        x, y = cursor_pos()
        for m in self.sct.monitors[1:]:
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
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
        img = np.asarray(self.sct.grab(self.area))
        if changed:
            scale = img.shape[1] / self.area["width"]
            extra = f", {img.shape[1]}x{img.shape[0]} px ({scale:g}x HiDPI/Retina)" if abs(scale - 1) > 0.01 else ""
            self.log(f"capturing {self.desc}: {self.area['width']}x{self.area['height']} "
                     f"at ({self.area['left']},{self.area['top']}){extra}")
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


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
    except Exception:
        pass
    try:
        from ApplicationServices import AXIsProcessTrusted
        if not AXIsProcessTrusted():
            print("! Accessibility permission is missing (needed to click and for F8/F9). Enable it for your\n"
                  "  terminal app in System Settings > Privacy & Security > Accessibility, then restart the terminal.")
            ok = False
    except Exception:
        pass
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
    img = GameGrabber()()
    d = Detector().analyze(img)
    h, w = img.shape[:2]
    lw = max(2, h // 300)
    for (x0, x1, y0, y1), c in [(stop_box(w, h), (0, 0, 255)), (charge_box(w, h), (255, 255, 0))]:
        cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), c, lw)
    cv2.imwrite("snapshot.png", img)
    print("saved snapshot.png", d)


def live(use_gamepad, countdown, dry=False, max_seconds=0):
    print(f"AI Fishing on {platform.system()} {platform.release()}, Python {platform.python_version()}")
    print("Educational project, use at your own risk: automation may break Roblox or game rules (see README).")
    if IS_MAC and not dry:
        check_mac_permissions()
    flags = {"paused": False, "quit": False}
    try:
        from pynput import keyboard

        def on_key(k):
            if k == keyboard.Key.f8:
                flags["paused"] = not flags["paused"]
                print("PAUSED" if flags["paused"] else "RESUMED")
            elif k == keyboard.Key.f9:
                flags["quit"] = True
                return False
        keyboard.Listener(on_press=on_key).start()
        hot = "F8 = pause/resume, F9 = quit" + (" (fn+F8 / fn+F9 on a Mac)" if IS_MAC else "") + ", or Ctrl+C here"
    except Exception as e:
        hot = f"hotkeys unavailable ({e}); press Ctrl+C in this window to quit"

    inp = LogInput() if dry else GamepadInput() if use_gamepad else MouseInput()
    if dry:
        print("DRY RUN: reading the screen and logging decisions, no clicks sent.")
    grab, det = GameGrabber(), Detector()
    grab()  # log what will be captured
    if grab.desc != "Roblox window":
        print("! Roblox window not found. Open Roblox (not minimised); until then the monitor under the cursor is used.")
    if not dry:
        inp = CursorGuard(inp, grab)
    print(f"Starting in {countdown:g}s - click into the game window and leave the cursor over it.")
    print(hot)
    time.sleep(countdown)
    bot, t0 = Bot(inp), time.monotonic()
    frames, t_fps = 0, time.monotonic()
    try:
        while not flags["quit"]:
            t_loop = time.monotonic()
            if max_seconds and t_loop - t0 > max_seconds:
                break
            if flags["paused"]:
                inp.up(); bot.state = "START"; time.sleep(0.1); continue
            bot.tick(det.analyze(grab()), time.monotonic() - t0)
            frames += 1
            if time.monotonic() - t_fps > 10:
                fps = frames / (time.monotonic() - t_fps)
                slow = "  <- slow; close other apps or make the Roblox window smaller" if fps < 6 else ""
                print(f"  (loop {fps:.0f} fps, state {bot.state}){slow}")
                frames, t_fps = 0, time.monotonic()
            # ~30 checks per second is plenty; don't burn a whole CPU core on fast PCs
            time.sleep(max(0.0, 1 / 30 - (time.monotonic() - t_loop)))
    except KeyboardInterrupt:
        pass
    finally:
        inp.up()  # never leave the mouse button held down
    print(f"stopped: casts {bot.casts}, catches {bot.catches}, perfect releases {bot.perfect_releases}")


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
        live(a.gamepad, a.countdown, a.dry, a.seconds)
