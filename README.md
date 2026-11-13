# Switch2DiscordPresence

A local Windows tray app that reports Nintendo Switch 2 activity to Discord Rich
Presence using the Elgato Game Capture Neo through OBS. No Nintendo account, no
NSO/Reef/nxapi, and no third-party presence service.

> The repository intentionally ships no Nintendo artwork. `assets/` is
> gitignored; run the asset generator or supply your own art. See [Assets](#assets).

What it does:

- Detects console power on/off from the capture signal (black-frame analysis).
- Detects the HOME menu with a dock template (plus OCR fallback).
- Reads the selected game title with the built-in Windows OCR engine and fuzzy
  matches it against a local game database.
- Shows `Playing <game>` with a since timer and generic Switch 2 art.
- Freezes the timer on the HOME menu and resumes it when you return to the game.
- While browsing other titles on HOME, reports `Browsing: <title>`; reselecting
  or relaunching the exited game resumes its frozen timer.
- Handheld mode: pick a previously detected game or show `Playing Handheld`.
- Clears presence after the console sleeps/powers off.

Everything stays on the machine. The app does not log secrets or screenshots.

---

## Requirements

- Windows 10/11, Python 3.12 for running from source (3.14/3.15 are not used).
- OBS Studio with **obs-websocket 5.x** enabled. The bundled OBS WebSocket server
  is disabled by default; enable it in `Tools → WebSocket Server Settings`.
- Elgato Game Capture Neo configured in OBS as a source named `Elgato Neo`
  (configurable) in a scene named `Consoles` (scene name does not matter).
- A Discord application with a Rich Presence asset keyed `switch2`.
- SDR output at 1080p (or 720p) for reliable OCR. HDR/120 Hz can hurt accuracy.

> This app uses `GetSourceScreenshot`, which renders the source even when it is
> not in the live program scene, so the scene layout does not matter.

---

## Quick start from source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts\generate_assets.py
python -m switch2_presence --diagnose
python -m switch2_presence
```

On first run the app creates `%APPDATA%\Switch2DiscordPresence\config.json`.

### 1. Configure OBS

1. In OBS: `Tools → WebSocket Server Settings`.
2. Tick **Enable WebSocket server**.
3. Port `4455` is the default. Set a password (recommended).
4. Store the password encrypted with Windows DPAPI:

```powershell
python -m switch2_presence --set-obs-password
```

The password is encrypted with `CryptProtectData` under your Windows profile and
is never written in plaintext or logged.

If your OBS source is not named `Elgato Neo`, edit `obs.source_name` in
`config.json`. `--diagnose` lists available DirectShow capture sources.

### 2. Configure Discord

1. Create an application at <https://discord.com/developers/applications>.
2. Generate the local art (or supply your own; see [Assets](#assets)):

```powershell
python scripts\generate_assets.py
```

3. Open **Rich Presence → Art Assets** and upload `assets/switch2.png` with the
   asset key `switch2` (the key is what the app sends, not the filename). The
   image must be at least 512×512.
4. Copy the **Application ID** and store it:

```powershell
python -m switch2_presence --set-discord-client-id
```

Or edit `config.json` directly:

```json
"discord": {
  "client_id": "123456789012345678",
  "large_image": "switch2",
  "small_image": ""
}
```

The app stays idle (and the tray shows `Discord off`) until a real client ID is
present. Discord must be running to connect over IPC.

### 3. Calibrate

Right-click the tray icon → **Run calibration…** with the console on the HOME
menu. The wizard captures from OBS and asks you to drag boxes around:

1. the selected game's title text,
2. the selected game icon (optional),
3. the bottom dock (`eShop`/`Album`/`Settings`/...).

Drag with the left mouse button; the selection uses a 2 px light + 2 px dark
outline so it stays visible on dark frames. **SPACE/ENTER** accepts, **R** resets,
and **ESC** cancels (or skips the optional icon step). The tray stays usable
while calibration is open, and **Quit** closes the wizard.

The title step only needs the **vertical band** around the title text: the app
searches that band across the full screen width, because the selected title
moves horizontally with the tile as you browse. You do not need to recalibrate
for each game position.

It saves a dock template and then samples frames with the console off/asleep and
on at HOME to derive the black/on thresholds. If the OFF samples are bright (the
capture card holds the last frame), the wizard enables the frozen-frame fallback
automatically; otherwise it stays off. Regions are stored relative to a 1920 px
width, so changing OBS output resolution can scale them. Re-run the wizard after
changing resolution, HDR, or the HOME layout.

Minimum defaults are already present, but the dock template is required for the
most reliable HOME detection; without it the app OCRs the dock labels.

---

## Assets

`assets/` is gitignored. Generate placeholders (or your own art) before building
or configuring Discord:

```powershell
python scripts\generate_assets.py
```

Required/generated files:

| File | Size / format | Purpose |
|---|---|---|
| `assets/switch2.png` | **512×512 px or larger**, square PNG | Discord Rich Presence art; upload it with asset key `switch2` |
| `assets/switch2.ico` | multi-size ICO (16, 32, 48, 64, 128, 256) | Windows executable icon used by PyInstaller |
| `assets/tray_off.png` | **64×64 px**, RGBA PNG, transparent background | Tray icon while the console is off (gray dot) |
| `assets/tray_home.png` | 64×64 px, RGBA PNG | Tray icon on HOME (blue dot) |
| `assets/tray_game.png` | 64×64 px, RGBA PNG | Tray icon in game (green dot) |
| `assets/tray_handheld.png` | 64×64 px, RGBA PNG | Tray icon in handheld mode (purple dot) |
| `assets/tray_error.png` | 64×64 px, RGBA PNG | Tray icon on OBS/Discord error (red dot) |

Optional source/display files:

| File | Size / format | Purpose |
|---|---|---|
| `assets/Switch2Icon.png` or `.webp` | square, **512×512 px or larger** | Your own source art. If present, the generator uses it for `switch2.png`, `switch2.ico`, and the tray icons. |
| `assets/Switch2Banner.jpg` | ~1600×400 px wide JPEG | Optional README/local banner; not used by the app. |

Notes:

- The generator also accepts `assets/switch2_base.png` as a source image.
- Tray icons are cosmetic; the app falls back to built-in icons if they are
  missing at runtime. The packaged build script generates them automatically.
- Use artwork you have the rights to use. The project does not distribute
  Nintendo assets.
- Discord requires Rich Presence images to be at least 512×512; keep the upload
  under 1 MB to avoid asset-upload limits.
- If you want a second small icon, upload another asset and set
  `discord.small_image` in `config.json`; leave it empty to hide it.

---

## Tray menu

- Status line + OBS/Discord connection indicators.
- **Detection active / Paused** — pause polling without quitting.
- **Handheld mode**
  - Enable handheld / Automatic (dock)
  - Choose game… (most recently detected first)
  - Resume last game
  - Generic "Playing Handheld"
  - Clear presence
- **Run calibration…**
- **Open logs**
- **Quit**

Handheld mode ignores the OBS signal (the console is physically docked away from
the capture card). Selecting a game or the generic option starts a fresh timer.
Returning to Automatic recomputes state from the current OBS signal. Handheld is
not auto-enabled on restart.

---

## Presence behavior

| Situation | Details | State | Timer |
|---|---|---|---|
| In game | `Playing <game>` | `Nintendo Switch 2` | runs |
| HOME (any title selected) | `On Switch 2 HOME menu` | `Browsing: <title>` | frozen until a game launches |
| HOME idle | `On Switch 2 HOME menu` | `Nintendo Switch 2` | none |
| System app launched | `On Nintendo Switch 2` | `Nintendo Switch 2` | none |
| Handheld + game | `Playing <game>` | `Nintendo Switch 2 • Handheld` | runs |
| Handheld generic | `Playing Handheld` | `Nintendo Switch 2` | runs |
| Console off | — | — | cleared after grace (60 s) |

Only state changes are sent (Discord rate limits to about 15 s). The live timer
comes from Discord's `start` timestamp, so the app never polls Discord.

Per-game art is intentionally not used. Upload/replace `assets/switch2.png`
(512×512 or larger) with your own generic art and keep the Discord asset key
`switch2`, or change `discord.large_image` to a different asset key.

---

## Configuration

Path: `%APPDATA%\Switch2DiscordPresence\config.json` (set
`SWITCH2_PRESENCE_HOME` to override the data directory).

Key settings:

| Key | Default | Meaning |
|---|---|---|
| `obs.source_name` | `Elgato Neo` | OBS source to screenshot |
| `obs.password_dpapi` | `""` | DPAPI blob from `--set-obs-password` |
| `discord.client_id` | placeholder | Discord application ID |
| `analysis.poll_ms` | `1500` | power poll interval |
| `analysis.vision_poll_ms` | `2000` | HOME/OCR interval while signal is on |
| `analysis.vision_width` | `1280` | analysis screenshot width |
| `analysis.off_hysteresis_s` | `20` | continuous black before OFF |
| `analysis.on_hysteresis_s` | `1.5` | continuous signal before ON |
| `analysis.freeze_diff_max` | `1.5` | frozen-frame difference threshold |
| `analysis.freeze_hysteresis_s` | `120` | static time before falling back to OFF |
| `analysis.freeze_detection_enabled` | `false` | opt-in fallback for capture cards that hold the last frame when the console sleeps |
| `analysis.home_match_threshold` | `0.80` | dock template NCC threshold |
| `timer.freeze_on_home` | `true` | freeze the since timer on HOME |
| `timer.clear_presence_after_off_s` | `60` | OFF grace before `rpc.clear()` |
| `ocr.language` | `en-US` | Windows OCR language tag |
| `ocr.full_width_title` | `true` | search the title band across the full screen width (selected title moves with the tile) |
| `ocr.refresh_s` | `10` | safety re-OCR interval when the title band is unchanged |

`games.json` entries are created automatically for unknown, plausible titles
after the same cleaned title is seen twice (partial OCR fragments are ignored):

```json
{
  "canonical": "Mario Kart World",
  "aliases": ["MK World"],
  "phash": null,
  "last_seen": "2026-11-12T18:00:00Z",
  "total_seconds": 12345
}
```

System apps (eShop, Album, System Settings, Sleep Mode, News, GameChat,
Controllers, …) are blocklisted and never reported as games.

---

## Packaging + autostart

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
# remove:
powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1
```

The build is a PyInstaller `--onedir --noconsole` bundle at
`dist\Switch2Presence\Switch2Presence.exe`. The autostart script registers a
Task Scheduler logon task with a 15 s delay. The app retries OBS/Discord if they
are not ready yet.

---

## Troubleshooting

**`OBS unavailable (refused)` / `--diagnose` exits 2**
Enable `Tools → WebSocket Server Settings` in OBS, then check the port/password.
Store the password with `python -m switch2_presence --set-obs-password`.

**OBS connected but `GetSourceScreenshot` fails**
The source name must match exactly. `--diagnose` lists DirectShow inputs found
in OBS. Rename the source or update `obs.source_name`.

**HOME is detected during gameplay / not detected**
Re-run calibration. Keep the dock and title boxes tight. If the capture card
tone-maps the console (HDR), switch the console/OBS to SDR. Ensure the HOME dock
layout matches the saved template.

**OCR misses titles**
Windows OCR needs the `en-US` (or configured) language pack. Check
`Settings → Time & Language → Language & region → English (United States)` and
install the optional OCR/reading pack. The app falls back to prefix and fuzzy
matching, and can fall back to Tesseract if you install `pytesseract` and point
it at a bundled `tesseract.exe`.

**Titles look truncated (`Mario Kart Wor…`)**
That is expected; fuzzy matching and prefix scoring resolve against the game DB.
Add aliases to `games.json` for stubborn cases.

**Console off/asleep is not detected**
Some capture devices hold the last frame instead of going black. The frozen-frame
fallback is off by default because static HOME/game screens can otherwise be
mistaken for sleep. Re-run calibration with the console off; if the OFF samples
are bright, the wizard enables `analysis.freeze_detection_enabled` automatically.
You can also enable it manually and tune `analysis.freeze_diff_max` /
`freeze_hysteresis_s`. The app never clears presence immediately on ambiguous
signal.

**Discord presence does not appear**
Make sure Discord is running, the client ID is real, and the `switch2` asset key
exists on the application. Presence updates are throttled to ~15 s.

**How is play time kept across the HOME menu?**
The app omits `start` while on HOME and freezes the active seconds. Relaunching
the same game resumes from the frozen value; launching a different game starts a
fresh timer.

---

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
python -m compileall -q src tests
python -m switch2_presence --headless   # run detection without the tray
```

Layout:

```
assets/               gitignored; generated or user-supplied art (see Assets)
  switch2.png         512x512+ Discord art (asset key switch2)
  switch2.ico         multi-size Windows/exe icon
  tray_*.png          64x64 tray icons
scripts/
  generate_assets.py  regenerates assets/ (uses Switch2Icon.* if present)
  build.ps1           PyInstaller build (runs the generator)
src/switch2_presence/
  app.py          worker loop, orchestration, persistence
  obs.py          obs-websocket v5 client (reconnect, auth)
  vision.py       power metrics, hysteresis, HOME template, OCR preprocess
  win_ocr.py      Windows.Media.Ocr via winsdk (+ Tesseract fallback)
  ocr.py          title reader, cleaning, matching, dock labels
  games.py        games.json, fuzzy matching, blocklist
  state.py        pure state machine + timers
  presence.py     Discord payloads + throttled pypresence manager
  handheld.py     handheld actions
  calibration.py  interactive wizard
  tray.py         pystray menu/status/notifications
  config.py       config defaults, atomic save, paths
  winutil.py      DPAPI, single-instance mutex
```

Tests are pure/synthetic: state transitions, timer math, fuzzy matching,
blocklist, config/DPAPI, power hysteresis, HOME debounce, and payload building.
Real frames can be added under `tests/fixtures/` later for live vision tests.

---

## Security and privacy

- OBS password: Windows DPAPI per user; never logged.
- No Discord tokens exist; no Nintendo credentials are used.
- Screenshots stay in memory and are never written to disk except the small
  calibrated dock template.
- Rotating logs in `%APPDATA%\Switch2DiscordPresence\logs\app.log` (5 × 2 MB)
  record state transitions, OCR text, and connection events.
- A named mutex prevents duplicate tray instances.

## Non-goals

- Per-game art, Nintendo account/API integration, online presence scraping.
- OCR of arbitrary gameplay (games rarely show their titles during play).
- Mobile/console app integration.
