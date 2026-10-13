# SnapPatch

A lightweight Snagit-style tool for Windows. Capture the screen, patch the screenshot, and add annotations you can still edit afterwards.

## Start

- **Run:** double-click `run.bat`. It needs Python 3.8 or later. The first time, it installs Pillow, numpy and imageio-ffmpeg (for MP4 recording).
- **Build an .exe:** double-click `build_exe.bat`. It creates `SnapPatch.exe` in `C:\Users\<you>\SnapPatch` and opens that folder. The .exe runs on PCs without Python.
- **Offline builds:** run `prepare_offline.bat` once on a PC with internet. It fills a `deps` folder (~70 MB) with the Python packages and the Inno Setup installer. From then on `run.bat`, `build_exe.bat` and `build_installer.bat` use `deps` and never contact the internet. Copy the whole folder, including `deps`, to build elsewhere.
- **For end users nothing is downloaded at any point:** `SnapPatch-Setup.exe` contains everything (the app, ffmpeg, the icon) and the app itself has no network code.
- **Build a one-click installer to share:** double-click `build_installer.bat`. It builds the exe and then `SnapPatch-Setup.exe` (needs the free Inno Setup, which the script installs via winget if missing). Colleagues double-click the setup, get Start-menu and desktop shortcuts, an optional "start with Windows" entry and a normal uninstaller — no Python, no admin rights needed.
- **Start with Windows:** File → *Start SnapPatch with Windows* starts it minimized at logon so the hotkeys are always ready.

- **Updating:** just run the newer `SnapPatch-Setup.exe` — it closes the running SnapPatch, replaces the files in place and keeps your settings and History. (Check the window title or Help → About for the version.)

Note: the exe is not code-signed, so Windows SmartScreen may show "Windows protected your PC" the first time — click *More info → Run anyway*. A code-signing certificate from IT would remove that warning.

## If auto-detect doesn't work

- **Check the status line.** The bottom-left corner of the capture screen shows the state of auto-detect. Green text means it's on. Red text explains why it's off, for example when numpy is missing.
- **Run Help → Diagnostics.** It tests the screen capture and auto-detect, then shows the results.
- **Open the log.** Problems are written to `%LOCALAPPDATA%\SnapPatch\snappatch.log`. Use Help → Open log file.

## Capture

Press **Ctrl+Shift+F9** anywhere, or click **◉ Capture**. (Ctrl+Shift+F9 isn't used by Windows, Office, Teams, Eclipse or IntelliJ, so it's free on practically every PC.) If it is taken, SnapPatch tries the next free one and shows you a message with the keys it ended up with.

| Action | What it does |
|---|---|
| Hover | Highlights the window, panel, group box, field or button under the mouse |
| Mouse wheel | Up makes the highlighted area bigger (field → panel → window → screen). Down makes it smaller. |
| Click | Captures the highlighted area |
| Drag | Captures any area you draw |
| Arrow keys | Move the cursor 1 px, or 10 px with Shift. The magnifier shows the exact pixel. |
| Enter / Esc | Enter captures the full screen. Esc or a right-click cancels. |

Auto-detect reads the boxes and lines in the screenshot itself, so it also works in Java programs like ANCA's software.

- **Delay:** use the Delay button to wait 3, 5 or 10 s before capturing. This lets you capture open menus and tooltips.

## Hotkeys

Defaults are **Ctrl+Shift+F9** (capture) and **Ctrl+Shift+F10** (record) — combinations no common program uses. A SnapPatch hotkey is global: while SnapPatch is running that combination no longer reaches other programs. To change them, open **File → Hotkeys**, click a box and press the keys you want (or pick from the list), then **Apply**. A red note tells you if another program already uses that combination. Note that **Ctrl+Alt+<key>** combinations are often owned by desktop shortcuts — a shortcut with that key in its *Properties → Shortcut key* box will win and open its program instead. Settings are saved in `%LOCALAPPDATA%\SnapPatch\settings.json`.

**Hotkeys only work while SnapPatch is running.** Closing the window (X) no longer quits — SnapPatch minimizes to the taskbar and keeps the hotkeys active, like Snagit's tray icon. Use **File → Exit SnapPatch completely** to quit. Tick *File → Start SnapPatch with Windows* so it's always there after logon.

**If a hotkey works only sometimes:**
- **A program running as administrator has the focus.** Windows blocks hotkeys from normal programs while an elevated window is active (security feature, applies to Snagit and ShareX too). Click a normal window first, or run SnapPatch as administrator as well.
- **Two copies of SnapPatch were running** (e.g. started by hand and by "start with Windows"). From 3.4 a second copy refuses to start and tells you.
- **A full-screen game or remote-desktop session** has the keyboard. Alt-tab out first.
- The start-up popup and the status bar show which keys are actually registered — if a default was taken, the key may be different from what you expect.

## Record a video

Press **Ctrl+Shift+F10** anywhere (or the alternative SnapPatch told you about at start-up), or click **● Record**. You can also press the capture hotkey and switch to **Video** on the capture screen: click the Image / Video switch at the top, or press **Tab**. Pick the area the same way as for a capture: hover and click, or drag.

- A control bar appears above the area with the elapsed time, the size and frame rate, **Pause**, **Stop & save** and **✕** (discard). A red frame shows what is being recorded. Drag the bar if it's in the way.
- The same hotkey again, Esc on the bar, or the Stop button ends the recording.
- Videos are saved as MP4 (H.264) in `Pictures\SnapPatch` and appear in the History strip. The video editor opens as soon as you stop.
- File → Recording frame rate sets 8 to 30 frames per second. 12 is a good default for screen demos.
- File → Show mouse cursor in recordings adds a yellow halo where the mouse is.

If imageio-ffmpeg is not installed, recordings are saved as GIF instead.

## Edit a video (trim and crop)

Click a video in the History strip, or right-click it and choose **Edit**.

- **Scrub** with the slider or click the green timeline. **Space** plays and pauses. **← / →** step one frame.
- **Trim:** move to the first frame you want and press **[ Set start** (or the `[` key). Move to the last frame and press **Set end ]** (or `]`). The green part of the timeline is what will be kept.
- **Crop:** drag a rectangle on the video. **Clear crop** removes it.
- **Save as MP4…** or **Save as GIF…** writes a new file (the original is kept) and adds it to History. A dialog lets you choose the **size** (100% down to 25%, or an exact width), the **quality** (MP4 compression, or number of GIF colours) and the **frame rate**, with a rough file-size estimate.

## History

The strip along the bottom shows your captures and recordings, newest first. They are stored in `Pictures\SnapPatch`.

- **Click** an image to open it in the editor, or a video to trim and crop it.
- **Double-click** to open with the default Windows app.
- **Right-click** an image to copy it to the clipboard, paste it into the current image as a layer, copy its path or delete it.
- **Open folder** opens the folder in Explorer. View → History strip hides or shows the strip.

## Editing

The tool palette is on the left. Hover over a tool to see its tip. The blue bar above the image shows only the options that apply to what you're doing.

- **One click selects a UI element.** With Select, Erase, Crop, Box, Highlight or Blur, just click a field, button, panel or table cell and the tool acts on exactly that element. Drag if you want a custom area.
- **Pixel tools** change the screenshot itself.
  - **Select:** click an element or drag an area. The blue bar then offers *Copy as layer* (a floating copy of the selection you can drag anywhere on the same image – Ctrl+D), *Copy to clipboard* (Ctrl+C, paste into Teams / Word / Outlook or back into SnapPatch with Ctrl+V), *Crop image to this*, Erase, Cut out rows and Cut out columns.
  - **Move:** drag a selected area somewhere else. The hole it leaves is filled automatically.
  - **Crop:** click an element or drag the area you want to keep.
  - **Erase:** click an element or drag over anything to remove it. The gap is filled to match the surroundings — stripes, gradients and table rows are continued, so there's nothing to pick. (Turn off *Auto fill colour* only if you want a fixed colour.)
- **Annotations** stay editable: box, rounded box, filled box, ellipse, arrow, double arrow, line, freehand pen, highlight, spotlight (darkens everything else), blur, text, callout and step numbers.
  - **Click** one to select it.
  - **Drag** it to move it. Drag a handle to resize it.
  - **Change the colour, width or size** in the blue bar to restyle it.
  - **Text boxes are adjustable:** click the text and eight handles appear. The side handles change the width and the text wraps to fit. The top and bottom handles change the text size. The corners change both. The cursor changes to a resize arrow when you're over a handle.
  - **Double-click** text or a callout to change its wording.
- **Right-click** anywhere for the actions that apply at that spot.
- **Pasted or duplicated pieces:** drag to move, drag a corner handle to resize (proportional; hold Shift for free resize), or type a Scale % in the blue bar. *Original size* undoes the resize. Enter applies the piece and Esc cancels it.
- **Zoom:** Ctrl+mouse wheel zooms in and out around the cursor.

## Saving

- **Save as… (Ctrl+Shift+S)** opens a dialog first:
  - **High quality** – PNG, original size (lossless).
  - **Screen** – PNG fitted to 1920 px wide.
  - **Standard** – JPG 85 %, small files for e-mail or chat.
  - **Custom** – pick the format (PNG / JPG / GIF / BMP), size (% or exact width) and quality yourself.

  A rough file size is shown before you save. Tick *Use these settings for Ctrl+S* and plain **Save** reuses them.
- **File → Save as project** keeps the annotations editable (`.snappatch`).
- **`.snappatch` project:** keeps the annotations editable, so you can reopen the file and change them later.

## Shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+C | Copy the selection, or the whole image if nothing is selected |
| Ctrl+V | Paste as a layer |
| Ctrl+Shift+V | Paste as a new image |
| Ctrl+D | Duplicate |
| Delete | Delete the selected annotation, or erase the selected area |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save as |
| Ctrl+0 | Fit the image to the window |
| Ctrl+1 | Zoom to 100% |

Tool shortcuts: S select · M move · C crop · E erase · B box · R rounded box · F filled box · O ellipse · A arrow · D double arrow · L line · P pen · H highlight · G spotlight · U blur · T text · K callout · N step · I pick colour
