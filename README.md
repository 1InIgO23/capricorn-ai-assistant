# Capri
> 🚧 **Status: ongoing project.** Capri is still under active development; features, character images and documentation may change.

A desktop construct companion for Windows 11.

## Introduction

Capri is an animated character that lives on your desktop in a frameless, transparent,
always-on-top window. She is a helpful assistant with a compact holographic chat panel: you can
ask her questions, and she can search the web, manage files on your computer, and write and run
simple scripts. With a stronger cloud model configured through an API key, more complex coding
tasks become possible.

She is not a static pet in a corner. She floats, breathes, glitches, turns to face your mouse,
"re-renders" from one place to another instead of sliding, shows what she is doing in a small
HUD under her, gets sleepy at night and falls into standby when you leave her alone.

Capri works out of the box **without any character images**: until you add your own, she is
drawn as an abstract glowing "construct" (a core, orbiting rings and drifting glyphs).

## Screenshots

Capri idling next to the chat panel, after writing a script:

![Capri idling next to the chat panel](docs/screenshots/idling.png)

Capri running a web search: the data-fragment ring, the activity HUD and the inline tool log:

![Capri running a web search](docs/screenshots/searching.png)

## Unofficial fan project disclaimer

Capri is a character from *Cyberpunk: Edgerunners*. This is a non-commercial, unofficial fan
project for personal use, and it is not affiliated with or endorsed by CD Projekt Red, Studio
Trigger or Netflix. All rights to the character belong to their respective owners.

The persona text in this project is original; no dialogue from the series is quoted or
reproduced.

**Character images:** the character images used by this project (in `assets/character/`) were
generated with **ChatGPT Images** (OpenAI). They are fan-made, not official artwork.

## Features

**Desktop presence**

- Procedural effects on top of the character layer: floating bob, breathing, glitch slices,
  RGB channel split, scanline flicker and a glow outline. Effects depend on her state.
  *Effects intensity* setting: off / subtle / full.
- Re-render movement: she dissolves into scanlines and pixels and reassembles at the
  destination. She can be dragged; dropped near a screen edge she docks and only peeks out
  until hovered.
- Awareness: she turns to face the mouse and tilts slightly. Repeated clicks make her react,
  first playfully, then mildly annoyed.
- Activity HUD under her, e.g. `SEARCHING "python pathlib"`, `READING report.pdf`,
  `MOVING 4 files`, `RUNNING cleanup.py · 3s`. It fades out when she is idle.
- Tool-specific visuals: a rotating ring of data fragments with page domains flickering in the
  HUD (web search); an animated data link to a floating file chip (files); a warning-colored
  glow with a progress pulse, a green flash on success, and a red glitch burst plus the most
  relevant error line on failure (scripts).
- Needs attention: when a confirmation is required she moves next to the dialog and pulses in
  the alert color; the dialog border pulses with her. One small nudge after 30 seconds, never
  repeated.
- Mood (bored / content / excited / tired), persistent across restarts. It shifts with
  interaction, time of day and session length and subtly changes her motion, glow color and
  the tone of rare remarks (at most one every 20 minutes by default; configurable).
- Rest: dimmer at night, asleep after a configurable idle time, then standby (only a dim
  pulsing core remains). Hovering or a new message wakes her with a quick reassemble.
- Quiet mode: tray toggle, also automatic while a fullscreen app or game is in front.
- Left-click toggles the chat. Right-click (or the tray icon) opens the menu.

**Chat panel**

- A holographic panel that unfolds from Capri's side and follows her; one click detaches it
  into a normal resizable window.
- Streaming answers; the first line of each answer resolves with a short "decrypt" effect.
- Code blocks with syntax highlighting and a copy button; a stop button while generating.
- Tool activity inline as compact collapsible entries (e.g. `▸ web search · 3 pages read`).
- Persistent conversations (create, rename, delete, reopen) in a side drawer.
- If she answers while the panel is closed, a preview appears in a speech bubble; click it.

**Tools**

- Web search (via `ddgs`) that reads the top result pages and answers with source links.
- Files: list, search, read (text and PDF), create, copy, move, rename, open with the default
  app. Deleting always goes to the **Recycle Bin**. Every modifying action asks first.
  System folders are refused.
- Scripts (Python, PowerShell, batch) saved in a dedicated workspace and run only after you
  have seen the full script and confirmed.

**LLM backends** (selectable in Settings): Ollama (local, default), any OpenAI-compatible API,
and the Anthropic API. Native tool calling is used where the backend supports it, with an
automatic text-protocol fallback for local models without tool support.

## Requirements

- Windows 11 (Windows 10 should also work)
- Python 3.11 or newer
- For the default local backend: [Ollama](https://ollama.com) and about 5 GB of disk space
  for the recommended model (8 GB of RAM or more recommended)
- Optional: an API key for an OpenAI-compatible provider or for Anthropic
- Python packages listed in `requirements.txt`

## Installation

Open **PowerShell** and run the steps below.

1. Install Python 3.11+ from <https://www.python.org/downloads/> (tick *Add python.exe to
   PATH*), then check it:

   ```powershell
   python --version
   ```

2. Go to the project folder (adjust the path to where you put it):

   ```powershell
   cd C:\Path\To\Capri
   ```

3. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell refuses to run the activation script, run this once and try again:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

4. Install the dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

5. Install Ollama from <https://ollama.com/download> and download the recommended model:

   ```powershell
   ollama pull qwen2.5:7b
   ```

   Any Ollama model that supports tool calling works; change the name in *Settings → Brain*.
   Ollama normally runs in the background after installation (look for its tray icon).

6. Start Capri:

   ```powershell
   python main.py
   ```

   To start her later without a console window, double-click `run_capri.bat`.

Optional: run the tests with `python -m pytest`.

## How to use the program

**Launching.** Run `python main.py` (inside the virtual environment) or double-click
`run_capri.bat`. Capri appears near the bottom-right corner of your main screen and an icon is
added to the system tray. Only one instance runs at a time.

**Opening the chat.** Left-click Capri (or left-click the tray icon). The panel unfolds from
her side. Type and press **Enter** to send (**Shift+Enter** for a new line). While she is
answering, the SEND button becomes **STOP**. The `⧉` button detaches the chat into a normal
resizable window; press it again (or close that window) to attach it back. `☰` opens the
conversation drawer: *New*, click to reopen, double-click or *Rename* to rename, *Delete*.

**Moving her.** Drag her anywhere. Drop her within a few pixels of a screen edge and she docks
there, half hidden, peeking out until you hover over her.

**Right-click and tray menus** (identical):

| Item | What it does |
| --- | --- |
| Open chat | Opens the chat panel |
| Quiet mode | No remarks, no effects, dimmed character |
| Settings… | Opens the settings window |
| Reload assets | Rescans `assets/character` for images |
| Open assets folder | Opens `assets/character` in Explorer |
| Quit | Closes Capri |

**Configuring backends and API keys.** Open *Settings → Brain*, choose a backend and fill in:

| Backend | Base URL (default) | Model (default) | API key |
| --- | --- | --- | --- |
| Ollama (local) | `http://localhost:11434` | `qwen2.5:7b` | not needed |
| OpenAI-compatible API | `https://api.openai.com/v1` | `gpt-4o-mini` | required |
| Anthropic API (Claude) | `https://api.anthropic.com` | `claude-sonnet-5-5` | required |

Model names change over time: enter a model your provider currently offers. The
OpenAI-compatible backend also works with other services and local servers that speak the
same protocol (set their base URL). **Local models are fine for chatting, searching, file
chores and simple scripts; for complex coding tasks a cloud model is recommended.** The chat
panel shows which kind is active.

Other settings: *Body* (character size, effects intensity, always on top, sleep and standby
times, remarks and their minimum interval, automatic quiet mode) and *Tools* (workspace
folder, script timeout).

**What Capri can do, with example requests**

- "What's new in Python 3.13? Give me sources."
- "Find all PDFs in my Downloads folder that mention *invoice*."
- "Read notes.txt on my Desktop and summarize it."
- "Move the screenshots from my Desktop into a new folder called Screenshots."
- "Delete the three oldest .log files in D:\Temp." (they go to the Recycle Bin)
- "Write a script that renames every .jpeg in my Pictures\Trip folder to .jpg, then run it."
- "Open my Documents folder."

**How confirmations work.** Anything that writes, creates, copies, moves, renames, deletes,
opens a program or runs a script first shows a dialog titled *CAPRI REQUESTS PERMISSION* that
states exactly what will happen (every source and destination path, or the complete script).
Capri moves next to the dialog and pulses. Nothing happens unless you click **Allow**;
**Deny**, Esc and closing the dialog all refuse. Reading, listing, searching and web access do
not ask. A script is checked again right before it runs and is refused if it changed after you
approved it. Scripts are not sandboxed: read them before allowing.

**Where things are stored**

| What | Location |
| --- | --- |
| Settings and API keys | `%APPDATA%\Capri\settings.json` (plain text; keep it private) |
| Conversations | `%APPDATA%\Capri\conversations\` (one JSON file each) |
| Mood and other state | `%APPDATA%\Capri\state.json` |
| Log files | `%APPDATA%\Capri\logs\capri.log` (API keys are never logged) |
| Script workspace | `%USERPROFILE%\CapriWorkspace` (configurable) |
| Images and theme | `assets\` inside the project folder |

Capri's tools cannot read `%APPDATA%\Capri` (so a model can never see your keys), cannot
modify her own program folder, and refuse `C:\Windows`, `Program Files`, `ProgramData` and
other system locations.

## Adding your own images

The images currently in `assets/character/` were generated with ChatGPT Images. You can
replace them or add missing states at any time following the rules below.

No images are needed. When you add some, Capri detects them at startup (or via *Reload
assets*) with no code changes. The startup log lists which images were found and which states
use placeholders.

| Rule | Value |
| --- | --- |
| Location | `assets/character/` |
| Format | PNG with a transparent background |
| Size | 512x512 recommended; **all images should have the same size** |
| Orientation | Draw her facing **right**; she is mirrored automatically to face the mouse |
| Single image per state | `<state>.png`, e.g. `idle.png`, `happy.png` |
| Animated state (frames) | `<state>_<number>.png`, starting at 1, e.g. `idle_1.png`, `idle_2.png`, `idle_3.png` |
| Priority | If frames exist they take priority over the single image and play in numeric order |
| App icon | `assets/icon.ico` (a generated icon is used if missing) |

States and fallback chain (first available image wins, otherwise the runtime placeholder):

| State | Used when | Fallback chain |
| --- | --- | --- |
| `idle` | Default | placeholder |
| `blink` | Short blink every few seconds | `idle` → placeholder |
| `happy` | After finishing an answer, playful clicks | `idle` → placeholder |
| `talking` | While an answer streams | `idle` → placeholder |
| `thinking` | Waiting for the model | `idle` → placeholder |
| `searching` | Web search, reading pages | `thinking` → `idle` → placeholder |
| `files` | File operations | `thinking` → `idle` → placeholder |
| `coding` | Writing and running scripts | `thinking` → `idle` → placeholder |
| `alert` | Waiting for your confirmation | `idle` → placeholder |
| `error` | Something failed | `alert` → `idle` → placeholder |
| `sleeping` | Asleep | `idle` → placeholder |
| `standby` | Long idle | none: without `standby` images her body fades and only the glowing core remains |
| `peek` (optional) | Docked at a screen edge | `idle` → placeholder |

Because the procedural effects are applied on top, a single `idle.png` is already enough for
a lively Capri; more states and frames just make her richer.

Examples:

```text
assets/character/idle.png                         one image, used for almost everything
assets/character/idle_1.png ... idle_6.png        animated idle (overrides idle.png)
assets/character/thinking.png                     also used for searching, files and coding
assets/character/blink_1.png, blink_2.png         a two-frame blink
```

**Frame rates (optional).** Create `assets/character/animation.json` (an example is provided
as `animation.example.json`; rename it). Values are frames per second, 1 to 60:

```json
{
  "default_fps": 8,
  "states": {
    "idle": { "fps": 6 },
    "blink": { "fps": 14 },
    "talking": { "fps": 10 }
  }
}
```

States not listed use `default_fps` (8 if the file is missing).

### Theme file

All colors, fonts and effect parameters live in `assets/theme.json`. Edit it and restart
Capri. Missing keys fall back to built-in defaults, and a broken file is ignored.

| Section | Content |
| --- | --- |
| `colors` | UI colors (hex) and `glass_alpha` (panel opacity, 0.2 to 1) |
| `state_colors` | Glow / placeholder color for each character state |
| `mood_tints` | Tint blended into the idle glow for each mood |
| `fonts` | Font family lists (`ui`, `display`, `mono`; first installed wins) and point sizes |
| `effects` | Effect parameters: `intensity` multipliers for off/subtle/full, bob, breathing, glitch timing and size, `rgb_split`, scanlines, glow, `rerender_ms`, tilt, `awareness_radius`, `pulse_period`, night and quiet dimming, panel scanlines and radius, decrypt speed |
| `syntax` | Code highlighting colors |

Palette extracted from the reference images (the images themselves are not part of the
project):

| Hex | Name | Role |
| --- | --- | --- |
| `#08080D` | Ink black | Deepest background, code blocks, menus |
| `#0F1020` | Midnight navy | Panel glass surface |
| `#1E2036` | Slate indigo | Raised surfaces, user messages, inactive borders |
| `#FF1450` | Hot crimson | Primary accent, idle glow, primary buttons, keywords |
| `#FF8DB4` | Sparkle pink | Soft accent, happy / excited glow, headings |
| `#12F56C` | Acid green | Secondary accent, links, talking / files glow, success |
| `#A23BF2` | Electric violet | Neon borders and outlines, thinking glow |
| `#2A6BFF` | Signal blue | Tertiary accent, searching glow, user tag, RGB split |
| `#F5F2F8` | Static white | Main text |
| `#8E98A8` | Haze grey | Secondary text |
| `#FFC61A` | Hazard yellow | Warning: script running, hints |
| `#FF8A1F` | Amber alert | Needs attention: confirmation pulse |
| `#FF3030` | Fault red | Errors |
| `#6A3FA8` / `#4B2E7A` | Dim violets | Sleeping and standby |

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "I can't reach Ollama…" | Start the Ollama app (or run `ollama serve`); check the base URL in Settings |
| "Ollama doesn't have the model…" | Run `ollama pull <model>` or fix the model name in Settings |
| She answers but never uses tools | The local model handles tools poorly; try `qwen2.5:7b` or a cloud model |
| "The API key was rejected" | Paste the key again in *Settings → Brain*; check the backend selection |
| Model not found on a cloud API | Enter a model name your provider currently offers |
| Web search fails | Check your internet connection; the search engine may be rate-limiting, try again later |
| Capri is off-screen | Quit from the tray, delete the `window` block in `%APPDATA%\Capri\settings.json`, restart |
| No tray icon | Check the hidden icons arrow in the taskbar; the right-click menu on Capri is identical |
| High CPU use on an old PC | Set *Effects intensity* to subtle or off, or reduce the character size |
| My images do not show | Check the names against the table, then *Reload assets*; see the log for ignored files |
| Text looks odd | The fonts in `assets/theme.json` fall back automatically; edit the `fonts` lists |
| PowerShell scripts blocked | Capri runs them with `-ExecutionPolicy Bypass`; company policies can still block them |
| Anything else | Read `%APPDATA%\Capri\logs\capri.log` |

## Project structure

```text
Capri/
├── main.py                     entry point
├── run_capri.bat               start without a console window
├── requirements.txt
├── README.md
├── docs/
│   └── screenshots/            preview images used in this README
├── assets/
│   ├── theme.json              colors, fonts, effect parameters
│   ├── icon.ico                optional app icon (generated if missing)
│   └── character/              your images + optional animation.json
├── capri/
│   ├── app.py                  controller wiring everything together
│   ├── config/                 paths, settings, theme, logging
│   ├── character/              assets.py (detection), sprites, placeholder construct,
│   │                           window.py (effects, HUD, docking), mood, speech bubble
│   ├── llm/                    base.py (backend interface), agent loop, persona,
│   │   └── providers/          ollama.py, openai_compat.py, anthropic.py
│   ├── tools/                  safety.py, files.py, web.py, scripts.py, registry
│   ├── ui/                     chat panel, messages, dialogs, markdown, style
│   ├── storage/                conversations
│   └── system/                 Windows helpers (fullscreen detection)
└── tests/                      asset detection, safety checks, small helpers
```

Adding an LLM provider means adding one file with one class to `capri/llm/providers/`:
subclass `LLMBackend`, decorate it with `@register_backend`, and implement `stream_chat`. It
is discovered automatically and appears in the settings window.

## AI

This software, including its source code and documentation, was generated entirely by
Claude Fable 5.2 and Claude Opus 5.5, artificial intelligence models developed by Anthropic,
through the methodology known as "vibe coding". The character images were generated with
ChatGPT Images by OpenAI. The software is provided "as is", without warranty of any kind,
and users are advised to review the code before executing it.
