# Roblox AI Fishing: advanced Void Pearl farming

A small Python AI that plays the fishing loop for you: **cast → wait for the bite → mash → collect → repeat**.

It works only by **looking at the screen** (OpenCV colour and shape detection) and **clicking the mouse**, the same way a person would.
It does not read game memory, inject code or modify the Roblox client.

> ⚠️ **Educational project. Please read the [Disclaimer](#disclaimer) before using it.**
> Using auto-clickers, macros or automation may go against the rules of Roblox or a particular game. Use it at your own risk.

**Works on:** Windows 10/11 and macOS 11+ (Intel and Apple Silicon), on any screen:
1080p laptops, 1440p / 4K monitors, MacBook Retina displays (13", 14", 15", 16"), 16:9, 16:10 and ultrawide,
with any Windows display scaling (100%, 125%, 150%…), and with Roblox on any monitor, full-screen or windowed.

## Quick start

1. Install **Python 3.9 or newer** from [python.org](https://www.python.org/downloads/).
   On Windows, tick **"Add python.exe to PATH"** in the installer.
2. Download this repo: the green **Code** button → **Download ZIP**, then unzip it.
   You can also use `git clone`.
3. Open Roblox, go to your fishing spot and **equip the rod**.
4. Start AI Fishing:
   - **Windows:** double-click `run_windows.bat`.
   - **Mac:** right-click `run_mac.command` → **Open**. You can also run `bash run_mac.command` in Terminal.

   The first run installs everything automatically, which takes about a minute.
5. During the 5-second countdown, **click into the Roblox window and leave the mouse over the game**.

**F8** pauses or resumes, and **F9** quits. On a Mac, use **fn+F8** and **fn+F9**.
**Ctrl+C** in the terminal window also stops it.

### macOS: one-time permissions

macOS asks for two permissions for the app you run AI Fishing from (Terminal, iTerm or VS Code):

| Permission | Where | Why |
|---|---|---|
| **Screen Recording** | System Settings → Privacy & Security → Screen Recording | to see the Roblox window |
| **Accessibility** | System Settings → Privacy & Security → Accessibility | to click and use the F8/F9 hotkeys |

Turn both on, then **quit and reopen the terminal**. AI Fishing prints a reminder if either one is missing.

## Manual setup (instead of the run scripts)

```bash
git clone <this repo>
cd <repo>
python -m venv .venv            # macOS: python3 -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe ai_fishing.py

# macOS
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python ai_fishing.py
```

| Command | What it does |
|---|---|
| `ai_fishing.py --dry` | Reads the screen and logs decisions, **sends no clicks** (safe first test) |
| `ai_fishing.py --seconds 600` | Stops automatically after 10 minutes |
| `ai_fishing.py --snapshot` | Saves `snapshot.png` with the detection areas drawn on it |
| `ai_fishing.py --simulate video.mp4` | Replays a screen recording through the detector |
| `ai_fishing.py --countdown 10` | A longer countdown before it starts |
| `ai_fishing.py --gamepad` | Windows only: presses R2 on a virtual Xbox pad instead of clicking (needs the ViGEmBus driver and `pip install vgamepad`) |

The run scripts pass options through too, for example `run_windows.bat --dry`.

## How it works

AI Fishing captures the Roblox window up to 30 times a second and runs a small state machine:

| State | What it sees on screen | What it does |
|---|---|---|
| **CAST** | Vertical **CHARGE** meter filling up | Holds the button, releases when the fill tops out at the gold band (perfect cast) |
| **WAIT_BITE** | Float sitting in the pond | Nothing. Waits up to 60 s for the bite |
| **MASH** | "Mash R2! / Click Fast!" bar with a red **STOP** button | Clicks at random intervals (~11–22 clicks/s) until the bar is gone |
| **COOLDOWN** | Reward popup (whole screen brightens) | Waits until the popup fades, then casts again |

How each thing is detected:

- **Mash bar:** finds the red STOP button by shape (a ~2.5:1 red box with white text), then checks for bright-green progress segments to its left. The bar must be seen on 3 frames in a row before AI Fishing clicks, so stray icons can't make it reel in early.
- **Charge meter:** measures the height of the mint-green fill. When the fill stops rising, it waits ~0.18 s and releases.
- **Reward popup:** the average screen brightness jumps by about 30.

### Why it works on any screen

- **Resolution and Retina:** every frame is scaled to the same working height, and all sizes and positions are measured relative to the window height, never in pixels. A 1366×768 laptop, a 4K monitor and a 2× Retina MacBook all look the same to the detector.
- **Aspect ratio** (16:9, 16:10 MacBooks, ultrawide, 4:3): the CHARGE meter is searched relative to the screen centre, where your character always is, and the STOP button is searched across the whole bottom half.
- **Colour differences:** the colour ranges have margin for Mac Display-P3 capture, slightly darker or less saturated captures, and different GPUs.
- **Display scaling and multiple monitors:**
  - On Windows, AI Fishing uses physical pixels, so scaling at 125% or 150% and mixed-DPI monitors line up exactly.
  - It finds the Roblox window on whichever monitor it is on and follows it if you move or resize it.
  - On a Mac, it skips the window title bar in windowed mode.
- **Slow PCs:** the timing works from the clock, not the frame count. The perfect-cast release and the click rate stay correct even at a low frame rate. It prints a hint if the loop gets slower than about 6 fps.

Tested by replaying two real gameplay recordings (about 2,300 frames per setup), resized and recoloured to imitate 720p, an 800×450 window, 4K / Retina, 16:10 MacBooks, 4:3, Mac Display-P3 colours, darker or less saturated capture, and slow PCs at 10 and 7.5 fps.
In every setup it caught every fish, never falsely detected the mash bar, and released the cast within about 0.1 s of the original 2560×1440 setup.

### Safety

- Never clicks unless the mouse cursor is over the Roblox window.
- Releases the mouse button whenever it pauses, quits or crashes, so the mouse is never left held down.
- If it casts 3 times without seeing the charge meter, it prints a warning and slows down instead of spamming.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Roblox window not found` | Open Roblox and don't minimise it. Until it finds the window, it watches the monitor under your mouse. |
| `cursor is not over the game window` | Move the mouse over the game. For safety it only clicks inside Roblox. |
| `WARNING: 3 casts in a row with no CHARGE meter` | Equip the rod, close any open menus and make sure nothing covers the game window. |
| Mac: it sees only the desktop / never clicks | Grant **Screen Recording** and **Accessibility** (see above), then restart the terminal. |
| Windows: clicks do nothing | Run the terminal as the same user as Roblox. If Roblox runs as administrator, run the terminal as administrator too. |
| Windows with **HDR** on: nothing is detected | Turn HDR off in Windows display settings. HDR changes the captured colours. |
| Still not detecting | Run `--snapshot` and open `snapshot.png`. The red box should cover the bottom (STOP button) and the cyan box the CHARGE meter beside your character. |

## Tuning

The timings were measured from real gameplay and live at the top of each state in `Bot.tick`:

- `release_delay`: how long after the charge tops out to release
- the 60 s bite timeout
- the 1.2 s pause after the reward popup
- the mash click interval `random.uniform(0.045, 0.09)`

The detection areas and colour ranges are constants at the top of `ai_fishing.py`: `STOP_SEARCH`, `CHARGE_X`, `CHARGE_Y`, and the `*_LO` / `*_HI` HSV ranges.

## Disclaimer

This project is for **educational purposes only**, as a way to learn Python and computer vision.

- It is **not affiliated with or endorsed by** Roblox Corporation or the developers of Steal an Egg. All game names, art and trademarks belong to their owners.
- Using auto-clickers, macros or automation **may go against the [Roblox Terms of Use](https://en.help.roblox.com/hc/en-us/articles/115004647846), the Roblox Community Standards or the rules of a specific game**, and could lead to your account being **warned or banned**.
- Check the rules of the game you play before using it.
- If you use this code, you do so **at your own risk**. The author is **not responsible** for any action taken against your account, any loss of in-game items or progress, or any other damage.
- The software is provided "as is", without warranty of any kind (see the [LICENSE](LICENSE)).

## License

[MIT](LICENSE): free for anyone to download, use, modify and share.
