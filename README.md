# petproj-mvp

A small desktop pet for Windows: a pixel character that walks out from off-screen
when you stop touching the PC, sets up a tiny desk + laptop, types for a while,
and packs up when you come back.

Built with Python + PyQt6. Renders pixel sprites in a transparent always-on-top
window above all other apps.

```
   user idle 5s
       │
       ▼
   ENTERING ──► SETUP ──► WORKING ──► LEAVING ──► OFFSTAGE
       │                     │
       └────► FLEEING ◄──────┘   (user touches mouse / keyboard)
```

## Quick start

Requires **Python 3.10+** (tested on 3.14) and Windows. The character relies on
the Win32 `GetLastInputInfo` API to detect idleness — it won't work on macOS or
Linux without a port.

```powershell
# from the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Stand still (don't touch mouse/keyboard) for ~5 seconds. The character should
walk in from the edge of one of your screens.

## Controls

A system-tray icon (bottom-right of the taskbar, the little pixel head) provides:

- **Pause** — temporarily stops the scene. Toggle off to resume.
- **Multi-monitor** — when on, the scene can spawn on any screen randomly. When
  off, only on `primary_screen_index` from `config.json`.
- **Quit** — close the app cleanly.

Double-click the tray icon as a shortcut for Pause/Resume.

## Running on Windows startup (autostart)

Easiest is the **Startup folder** approach — no real Windows service needed
since this is a desktop app.

1. Press `Win + R`, type `shell:startup`, press Enter. A folder opens.
2. Right-click in that folder → **New** → **Shortcut**.
3. Target: full path to `pythonw.exe` from your venv, then a space, then full
   path to `main.py`. Example:
   ```
   C:\Users\<you>\path\to\petproj-mvp\.venv\Scripts\pythonw.exe C:\Users\<you>\path\to\petproj-mvp\main.py
   ```
   `pythonw.exe` (not `python.exe`) starts without a console window.
4. Name the shortcut `petproj-mvp` and finish.

It will now launch on every login. Quit it from the tray icon when you don't
want it running.

If you'd prefer a real **Windows Task Scheduler** entry (e.g. with delay-after-login
or run-as-admin), the same command above works as the action.

## Configuration

`config.json` is created next to `main.py` on first save (or you create it by
hand). Defaults are sane; missing/unknown keys are ignored.

```json
{
  "idle_threshold_s": 5.0,
  "multi_monitor": true,
  "primary_screen_index": 0
}
```

| Key | Meaning |
| --- | --- |
| `idle_threshold_s` | Seconds of idle before the scene starts. |
| `multi_monitor` | If `true`, scenes can spawn on any monitor. |
| `primary_screen_index` | Which screen to use when `multi_monitor` is `false`. `0` = first screen reported by Qt (typically the primary). |

The Multi-monitor tray toggle writes back to `config.json` automatically.

## Project structure

```
petproj-mvp/
├── main.py              entry point — builds Qt app, scene, tray
├── scene.py             scene state machine + multi-monitor lane logic
├── character.py         transparent always-on-top SpriteWidget
├── idle_detector.py     Win32 GetLastInputInfo via ctypes
├── config.py            persistent settings (loaded from config.json)
├── spritesheet.py       loads PNG + JSON (Aseprite format) into QPixmap frames
├── sprites.py           ASCII grid sprite definitions (used to bootstrap PNGs)
├── export_sprites.py    CLI: ASCII → PNG sprite sheets (selective per actor)
├── preview_sprites.py   render all sprites to ./preview/ at 10× zoom for review
├── assets/              the actual game art the runtime loads
│   ├── person/          7 frames: walk_a/pass/b/pass2/stand/sit/sit_type
│   │   ├── person.png   sprite sheet (frames laid out horizontally)
│   │   └── person.json  Aseprite-compatible frame metadata + animation tags
│   ├── table/
│   ├── laptop/
│   ├── chair/
│   └── icon/            tray icon
├── requirements.txt
└── README.md
```

## Editing the art

Two paths.

**Through ASCII grids in `sprites.py`** — fast for geometric props (table,
laptop, chair). Edit the grid, then regenerate the matching asset:

```powershell
python export_sprites.py chair        # only chair.png + chair.json
python export_sprites.py table laptop # multiple
python export_sprites.py --all        # everything (overwrites hand edits!)
```

The script **always backs up** existing files as `<name>.png.bak.<timestamp>`
before overwriting. So a wrong target is recoverable by renaming the latest backup.

**By painting PNGs directly** — open `assets/person/person.png` (or any
other) in Aseprite, LibreSprite, Piskel, Pixilart, or any pixel-art editor. The app
re-loads them on next launch. The JSON sidecar describes frame coordinates +
animation tags in Aseprite's standard sprite-sheet export format, so any tool
that exports that format will plug in without changes.

⚠️  Don't run `export_sprites.py --all` after you've hand-edited a PNG — it
will overwrite your work with the ASCII version. Use specific targets only.

## How it works (architecture)

- `IdleDetector` polls `GetLastInputInfo` every tick to know how long since the
  last input event. The scene runs a 60ms `QTimer`.
- `Scene` is a state machine: OFFSTAGE → ENTERING → SETUP → WORKING → LEAVING
  → OFFSTAGE, with FLEEING as an interrupt for any state when input resumes.
- Each character/prop is a separate `SpriteWidget` — a frameless,
  click-through, always-on-top, translucent window that just draws a `QPixmap`.
  Position is in absolute virtual-desktop coordinates so the same code handles
  any monitor layout.
- Multi-monitor: every physical screen is a `Lane` with computed neighbour
  flags (does it have a screen to its left/right?). The scene only uses
  *external* edges (those with no neighbour) for spawn/exit, so the character
  never appears in the dead zone "off the edge of one screen but on top of
  another".
- `SpriteSheet` loads PNG + Aseprite-format JSON, exposes frames by name and
  animation tags by name (with pingpong support).

## Roadmap / open ideas

- Add a cat as a second actor; coexists with the person, occasionally interrupts.
- Multi-character scenarios (the typing/cat-mischief story from `taskV0/idea.md`
  if that ever lands here).
- Migration between monitors via internal edges (currently only external edges
  are used).
- Hover/click interaction with the character (cursor as a "petting" affordance).
- Director with weighted scenarios + simple memory across scenes.

## License

MIT — do whatever you want with it. The pixel art was generated procedurally
and lives under the same terms.
