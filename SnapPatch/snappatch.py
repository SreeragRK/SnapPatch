"""
SnapPatch - a lightweight Snagit-style screenshot capture & patch editor for Windows.

Capture : Ctrl+Shift+X anywhere, or the Capture button.
          Hover = smart auto-detect of windows, panels, fields and buttons (works in Java apps too),
          mouse wheel = grow / shrink the detected area, click = capture it, drag = any region.
Edit    : pixel tools (select, move/patch, crop, erase, cut-out, paste layers) and editable
          annotation objects (box, ellipse, arrow, line, highlight, blur, text, callout, step).
Output  : PNG/JPG/BMP, .snappatch project (objects stay editable), clipboard.

Requires: Python 3.8+, Pillow 8.2+, numpy   (pip install pillow numpy)
"""
import sys
import os
import io
import json
import math
import time
import copy
import base64
import queue
import ctypes
import threading
from pathlib import Path
from datetime import datetime
from collections import Counter

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageGrab

import logging
import traceback
import subprocess
import struct

try:
    import numpy as np
    NUMPY_ERROR = None
except Exception as _ex:  # smart detection is optional
    np = None
    NUMPY_ERROR = f"numpy not available ({_ex.__class__.__name__}) – run: python -m pip install numpy"

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None


def ffmpeg_exe():
    """Path to an ffmpeg binary, or None (then recordings are saved as GIF)."""
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    for cand in ("ffmpeg.exe", "ffmpeg"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d and os.path.isfile(os.path.join(d, cand)):
                return os.path.join(d, cand)
    return None


APP_NAME = "SnapPatch"
APP_VERSION = "4.1"


def resource_path(name):
    """Path to a bundled file (works both from source and inside the PyInstaller exe)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def startup_enabled():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except Exception:
        return False


def set_startup(enabled):
    if not IS_WIN:
        return
    import winreg
    exe = sys.executable if getattr(sys, "frozen", False) else None
    cmd = f'"{exe}" --minimized' if exe else \
        f'"{os.path.join(os.path.dirname(sys.executable), "pythonw.exe")}" "{os.path.abspath(sys.argv[0])}" --minimized'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
IS_WIN = sys.platform.startswith("win")
# Defaults chosen not to clash with Eclipse / IntelliJ (Ctrl+Shift+X / Ctrl+Shift+R are taken there).
# Ctrl+Shift+F9 / F10 are not used by Windows, Office, Teams, Eclipse, IntelliJ, Snagit or ShareX,
# so they are free on practically every PC and never clash with "Save As" / "Open Resource".
DEFAULT_HOTKEYS = {"capture": "Ctrl+Shift+F9", "record": "Ctrl+Shift+F10"}
# If a key is already taken the next candidate in the chain is tried automatically.
HOTKEY_CHAINS = {"capture": ["Ctrl+Shift+F9", "Ctrl+Alt+F9", "Ctrl+Shift+Alt+S", "Shift+PrintScreen", "Ctrl+Shift+F12"],
                 "record": ["Ctrl+Shift+F10", "Ctrl+Alt+F10", "Ctrl+Shift+Alt+R", "Ctrl+Shift+PrintScreen",
                            "Ctrl+Shift+F11"]}
APP_VERSION_HOTKEYS = 4
HOTKEY_TEXT = DEFAULT_HOTKEYS["capture"]           # updated at runtime from settings
RECORD_HOTKEY_TEXT = DEFAULT_HOTKEYS["record"]
MAX_UNDO = 60
PROJECT_EXT = ".snappatch"
LIBRARY_DIR = Path.home() / "Pictures" / "SnapPatch"
HIGHLIGHT_COLOR = "#ffe600"

# --- ToolRoom help-image guidelines (Confluence: AMT ToolRoom KB -> Help Image Design Guide) ---------
HELP_PRESETS = {                       # name: (width, height, format)
    "ToolRoom help image 268 × 358": (268, 358, "png"),
    "BlankX help image 265 × 265": (265, 265, "png"),
    "TGP operation image 270 × 149 (jpg)": (270, 149, "jpg"),
}
HELP_BG = (0, 75, 44)                  # green background
HELP_COLOURS = [("Arrow / dimension", "#f87100"),    # RGB 248,113,0
                ("Reference line", "#a523d2"),       # RGB 165,35,210
                ("Axis line", "#000000")]
HELP_TEXT_OK = set("0123456789+-±×÷=.,:;%°()/ ")      # numbers and operators are allowed, words are not
PALETTE = ["#ff0000", "#ff8c00", "#ffe600", "#00b050", "#0a64ff", "#7030a0", "#000000", "#ffffff"]
UI_FONT = ("Segoe UI", 10)
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SnapPatch"
LOG_FILE = LOG_DIR / "snappatch.log"
SETTINGS_FILE = LOG_DIR / "settings.json"


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(data):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        logging.getLogger(APP_NAME).exception("could not save settings")
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
except Exception:
    logging.basicConfig(level=logging.INFO)
log = logging.getLogger(APP_NAME)
ACCENT = "#0a64ff"

if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# =========================================================================== #
#  Windows helpers
# =========================================================================== #
def copy_image_to_clipboard(img, hwnd=None, file_path=None):
    """Put an image on the Windows clipboard as CF_DIB (every app), PNG (Teams, browsers, Office) and –
    if file_path is given – as a file (CF_HDROP) so Ctrl+V in Explorer / Outlook attachments works too.
    hwnd: owner window – without one, EmptyClipboard makes SetClipboardData fail on recent Windows."""
    if not IS_WIN:
        raise RuntimeError("Copying images to the clipboard is only supported on Windows.")
    from ctypes import wintypes

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]
    pbuf = io.BytesIO()
    img.convert("RGBA" if img.mode == "RGBA" else "RGB").save(pbuf, "PNG")
    png = pbuf.getvalue()
    hdrop = None
    if file_path:
        # DROPFILES header (20 bytes) + UTF-16 path list ending with a double NUL
        path_bytes = (str(file_path) + "\0\0").encode("utf-16-le")
        hdrop = struct.pack("<IiiII", 20, 0, 0, 0, 1) + path_bytes

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    u32.RegisterClipboardFormatW.restype = wintypes.UINT

    def put(fmt, blob):
        h = k32.GlobalAlloc(0x0002, len(blob))
        if not h:
            raise MemoryError("Not enough memory to copy the image.")
        ptr = k32.GlobalLock(h)
        if not ptr:
            k32.GlobalFree(h)
            raise RuntimeError("Could not lock clipboard memory.")
        ctypes.memmove(ptr, blob, len(blob))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(fmt, h):
            err = ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()
            k32.GlobalFree(h)
            raise RuntimeError(f"Copying to the clipboard failed (error {err}), try again.")

    for _ in range(20):
        if u32.OpenClipboard(hwnd):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Clipboard is busy (another program holds it), try again.")
    try:
        u32.EmptyClipboard()
        put(8, data)                                            # CF_DIB
        try:
            put(u32.RegisterClipboardFormatW("PNG"), png)       # PNG – keeps transparency, preferred by many apps
        except Exception:
            pass
        if hdrop:
            try:
                put(15, hdrop)                                  # CF_HDROP – paste as a file in Explorer
            except Exception:
                log.exception("CF_HDROP failed")
    finally:
        u32.CloseClipboard()


VK_NAMES = {"PRINTSCREEN": 0x2C, "PRTSC": 0x2C, "PRINT": 0x2C, "SCROLLLOCK": 0x91, "PAUSE": 0x13,
            "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
            "SPACE": 0x20, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B, "BACKSPACE": 0x08,
            "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
            "NUMPAD0": 0x60, "NUMPAD1": 0x61, "NUMPAD2": 0x62, "NUMPAD3": 0x63, "NUMPAD4": 0x64,
            "NUMPAD5": 0x65, "NUMPAD6": 0x66, "NUMPAD7": 0x67, "NUMPAD8": 0x68, "NUMPAD9": 0x69,
            "MINUS": 0xBD, "PLUS": 0xBB, "EQUALS": 0xBB, "COMMA": 0xBC, "PERIOD": 0xBE, "SLASH": 0xBF,
            "BACKSLASH": 0xDC, "SEMICOLON": 0xBA, "QUOTE": 0xDE, "LBRACKET": 0xDB, "RBRACKET": 0xDD, "GRAVE": 0xC0}
for _i in range(1, 25):
    VK_NAMES[f"F{_i}"] = 0x6F + _i
MOD_NAMES = {"CTRL": 0x2, "CONTROL": 0x2, "SHIFT": 0x4, "ALT": 0x1, "WIN": 0x8, "WINDOWS": 0x8, "META": 0x8}


def parse_hotkey(text):
    """'Ctrl+Alt+PrintScreen' -> (modifiers, vk).  Raises ValueError if it can't be understood."""
    parts = [p.strip() for p in text.replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods, key = 0, parts[-1].upper()
    for m in parts[:-1]:
        if m.upper() not in MOD_NAMES:
            raise ValueError(f"unknown modifier '{m}'")
        mods |= MOD_NAMES[m.upper()]
    if key in VK_NAMES:
        vk = VK_NAMES[key]
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key)
    else:
        raise ValueError(f"unknown key '{parts[-1]}'")
    if mods == 0 and vk not in (0x2C, 0x91, 0x13) and not (0x70 <= vk <= 0x87):
        raise ValueError("use at least one modifier (Ctrl / Alt / Shift / Win) with that key")
    return mods, vk


def normalize_hotkey(text):
    mods, vk = parse_hotkey(text)
    names = [n for n, bit in (("Ctrl", 2), ("Alt", 1), ("Shift", 4), ("Win", 8)) if mods & bit]
    rev = {v: k for k, v in VK_NAMES.items()}
    rev[0x2C] = "PRINTSCREEN"
    key = rev.get(vk, chr(vk))
    key = {"PRINTSCREEN": "PrintScreen", "SCROLLLOCK": "ScrollLock", "PAUSE": "Pause"}.get(key, key.capitalize()
                                                                                             if len(key) > 1 else key)
    if key.upper().startswith("F") and key[1:].isdigit():
        key = key.upper()
    return "+".join(names + [key])


class HotkeyService:
    """Owns the Windows hotkey thread. assign() can be called any time to (re)register keys.
    Results arrive on the queue as ('hotkey-result', {'capture': 'ok'|'error text', ...})."""

    def __init__(self, q):
        self.q = q
        self.want = {}
        self.tid = None
        self.lock = threading.Lock()
        if IS_WIN:
            threading.Thread(target=self.run, daemon=True).start()

    def assign(self, mapping):
        with self.lock:
            self.want = dict(mapping)
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, 0x8000, 0, 0)   # WM_APP: re-register

    def _register(self, u32):
        with self.lock:
            want = dict(self.want)
        result = {}
        for i, name in enumerate(("capture", "record"), start=1):
            u32.UnregisterHotKey(None, i)
            text = want.get(name)
            if not text:
                result[name] = "off"
                continue
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as ex:
                result[name] = f"invalid ({ex})"
                continue
            if u32.RegisterHotKey(None, i, mods | 0x4000, vk):
                result[name] = "ok"
            else:
                result[name] = "already used by another program (or by Windows)"
        self.q.put(("hotkey-result", result))

    def run(self):
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.tid = k32.GetCurrentThreadId()
        msg = wintypes.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # create the thread message queue
        self._register(u32)
        self.alive = True
        try:
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    self.q.put("capture" if msg.wParam == 1 else "record")
                elif msg.message == 0x8000:
                    self._register(u32)
        except Exception:
            log.exception("hotkey thread died")
        self.alive = False

    def ensure_alive(self):
        """Watchdog: restart the hotkey thread if it ever stops (keys silently stop working otherwise)."""
        if IS_WIN and not getattr(self, "alive", True):
            log.warning("hotkey thread restarted")
            self.alive = True
            self.tid = None
            threading.Thread(target=self.run, daemon=True).start()


def single_instance():
    """Return False if another SnapPatch is already running (two copies fight over the hotkeys)."""
    if not IS_WIN:
        return True
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "Local\\SnapPatch-single-instance")
    return k32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def virtual_screen_origin():
    if IS_WIN:
        u32 = ctypes.windll.user32
        return u32.GetSystemMetrics(76), u32.GetSystemMetrics(77)
    return 0, 0


def grab_screen():
    try:
        return ImageGrab.grab(all_screens=True).convert("RGB")
    except TypeError:
        return ImageGrab.grab().convert("RGB")


def list_window_rects():
    """Visible, real top-level windows (top of z-order first) + their child controls."""
    if not IS_WIN:
        return []
    from ctypes import wintypes
    u32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    HWND = wintypes.HWND
    u32.IsWindowVisible.argtypes = [HWND]
    u32.IsIconic.argtypes = [HWND]
    u32.GetWindowTextLengthW.argtypes = [HWND]
    u32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    u32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
    u32.GetClassNameW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
    dwm.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
    u32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
    u32.EnumChildWindows.argtypes = [HWND, EnumProc, wintypes.LPARAM]
    SKIP_CLASSES = {"Progman", "WorkerW", "Windows.UI.Core.CoreWindow"}

    def frame_rect(h):
        r = wintypes.RECT()
        if dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            u32.GetWindowRect(h, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def cloaked(h):
        v = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(v), ctypes.sizeof(v))
        return v.value != 0

    result = []

    def on_top(h, _):
        try:
            if not u32.IsWindowVisible(h) or u32.IsIconic(h) or cloaked(h):
                return True
            ex = u32.GetWindowLongW(h, -20) & 0xFFFFFFFF
            if ex & 0x20:  # WS_EX_TRANSPARENT: click-through overlays (GPU overlays, etc.)
                return True
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, cls, 256)
            if cls.value in SKIP_CLASSES:
                return True
            if u32.GetWindowTextLengthW(h) == 0 and not (ex & 0x40000):  # untitled, not app window
                return True
            r = frame_rect(h)
            if r[2] - r[0] <= 20 or r[3] - r[1] <= 20:
                return True
            kids = []

            def on_child(c, _):
                if u32.IsWindowVisible(c):
                    cr = wintypes.RECT()
                    u32.GetWindowRect(c, ctypes.byref(cr))
                    if cr.right - cr.left > 8 and cr.bottom - cr.top > 8:
                        kids.append((cr.left, cr.top, cr.right, cr.bottom))
                return True

            u32.EnumChildWindows(h, EnumProc(on_child), 0)
            result.append({"rect": r, "children": kids})
        except Exception:
            pass
        return True

    u32.EnumWindows(EnumProc(on_top), 0)
    return result


def set_cursor_pos(x, y):
    if IS_WIN:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))


def get_cursor_pos():
    if IS_WIN:
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    return None


# =========================================================================== #
#  Smart detection of rectangular UI elements from the pixels themselves
# =========================================================================== #
def _thin(values):
    """Collapse runs of neighbouring edges (e.g. etched/double borders) to the outermost one."""
    out, prev = [], None
    for v in values:
        if prev is not None and abs(v - prev) <= 1:
            out[-1] = int(v)
        else:
            out.append(int(v))
        prev = v
    return out


class SmartDetector:
    """Finds boxes (panels, group boxes, fields, buttons, cells) around a point by looking
    for straight edges in the screenshot. Works for apps that don't expose child windows."""

    def __init__(self, img, thresh=14):
        self.gray = np.asarray(img.convert("L"), dtype=np.uint8)
        g = self.gray.astype(np.int16)
        H, W = g.shape
        self.W, self.H = W, H
        it = np.int32 if max(W, H) > 32000 else np.int16
        dv = np.zeros((H, W), np.uint8)
        dv[:, 1:] = np.abs(g[:, 1:] - g[:, :-1]) > thresh
        dh = np.zeros((H, W), np.uint8)
        dh[1:, :] = np.abs(g[1:, :] - g[:-1, :]) > thresh
        del g
        self.cv = np.vstack([np.zeros((1, W), it), np.cumsum(dv, axis=0, dtype=it)])
        del dv
        self.ch = np.hstack([np.zeros((H, 1), it), np.cumsum(dh, axis=1, dtype=it)])

    def boxes_at(self, x, y, K=24):
        W, H, cv, ch = self.W, self.H, self.cv, self.ch
        if not (0 <= x < W and 0 <= y < H):
            return []
        lefts, rights, tops, bottoms = set(), set(), set(), set()
        for off, reach in ((0, 6), (-40, 10), (40, 10), (-80, 10), (80, 10)):
            py = y + off
            if 0 <= py < H:
                y0, y1 = max(0, py - reach), min(H, py + reach + 1)
                col = cv[y1, :].astype(np.int32) - cv[y0, :]
                need = (y1 - y0) * 0.9
                lefts.update((np.nonzero(col[1:x + 1] >= need)[0] + 1).tolist())
                rights.update((np.nonzero(col[x + 1:] >= need)[0] + x + 1).tolist())
            px = x + off
            if 0 <= px < W:
                x0, x1 = max(0, px - reach), min(W, px + reach + 1)
                row = ch[:, x1].astype(np.int32) - ch[:, x0]
                need = (x1 - x0) * 0.9
                tops.update((np.nonzero(row[1:y + 1] >= need)[0] + 1).tolist())
                bottoms.update((np.nonzero(row[y + 1:] >= need)[0] + y + 1).tolist())
        L = np.array(sorted(lefts, reverse=True)[:K], dtype=np.int64)
        R = np.array(sorted(rights)[:K], dtype=np.int64)
        tops = sorted(tops, reverse=True)[:K]
        bottoms = sorted(bottoms)[:K]
        if not len(L) or not len(R):
            return []

        found = []
        for t in tops:
            cvt, cht = cv[t], ch[t]
            for b in bottoms:
                h = b - t
                if h < 6:
                    continue
                cvb, chb = cv[b], ch[b]
                Ls = L[(cvb[L].astype(np.int32) - cvt[L]) >= 0.8 * h]
                if not len(Ls):
                    continue
                Rs = R[(cvb[R].astype(np.int32) - cvt[R]) >= 0.8 * h]
                if not len(Rs):
                    continue
                w = Rs[None, :] - Ls[:, None]
                top_cov = cht[Rs].astype(np.int32)[None, :] - cht[Ls][:, None]
                bot_cov = chb[Rs].astype(np.int32)[None, :] - chb[Ls][:, None]
                ok = (w >= 6) & (top_cov >= 0.65 * w) & (bot_cov >= 0.8 * w)
                for i, j in zip(*np.nonzero(ok)):
                    found.append((int(Ls[i]), int(t), int(Rs[j]), int(b)))
        if not found:
            return []
        # Ladder of distinct boxes, smallest first. Within a cluster of nearly identical boxes
        # (double / etched borders) keep the outermost one.
        F = np.array(found, dtype=np.int64)
        area = (F[:, 2] - F[:, 0]) * (F[:, 3] - F[:, 1])
        order = np.argsort(area, kind="stable")
        F, area = F[order], area[order]
        alive = np.ones(len(F), bool)
        out = []
        while alive.any() and len(out) < 20:
            i = int(np.argmax(alive))
            dist = np.abs(F - F[i]).max(axis=1)
            idx = np.nonzero(alive & (dist <= 3))[0]
            rep = F[idx[np.argmax(area[idx])]]
            out.append(tuple(int(v) for v in rep))
            alive &= ~(dist <= 12)
            alive &= np.abs(F - rep).max(axis=1) > 12
        return [self._with_title(bx) for bx in out]

    def _with_title(self, bx):
        """Group boxes have their title drawn over the top line - extend the box to include it."""
        l, t, r, b = bx
        q = l + max(8, (r - l) // 4)
        line = int(self.gray[min(b - 1, t + 3), l])
        seg = self.gray[t, l + 2:q].astype(np.int16)
        if seg.size and np.mean(np.abs(seg - line) <= 12) < 0.7:
            return l, max(0, t - 7), r, b
        return bx


def dedupe_boxes(boxes, tol=12):
    out = []
    for b in sorted(set(boxes), key=lambda bx: (bx[2] - bx[0]) * (bx[3] - bx[1])):
        if not any(all(abs(b[i] - k[i]) <= tol for i in range(4)) for k in out):
            out.append(b)
    return out


# =========================================================================== #
#  Fonts, geometry, annotation objects
# =========================================================================== #
_font_cache = {}


def get_font(size, bold=False):
    size = max(6, int(size))
    key = (size, bold)
    if key not in _font_cache:
        names = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else \
                ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")
        font = None
        for n in names:
            try:
                font = ImageFont.truetype(n, size)
                break
            except Exception:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(size)
            except TypeError:
                font = ImageFont.load_default()
        _font_cache[key] = font
    return _font_cache[key]


_measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
BOX_TYPES = ("rect", "rrect", "frect", "ellipse", "star", "triangle", "diamond", "arc", "symbol", "highlight", "blur",
             "spotlight")
DASHABLE = ("line", "arrow", "darrow", "pen", "rect", "ellipse", "arc")


def shape_points(t, box):
    """Outline points for the polygon shapes, fitted into box."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    if t == "triangle":
        return [(cx, y0), (x1, y1), (x0, y1)]
    if t == "diamond":
        return [(cx, y0), (x1, cy), (cx, y1), (x0, cy)]
    pts = []                                              # 5-point star
    for i in range(10):
        a = math.radians(-90 + i * 36)
        f = 1.0 if i % 2 == 0 else 0.42
        pts.append((cx + rx * f * math.cos(a), cy + ry * f * math.sin(a)))
    return pts
LINE_TYPES = ("line", "arrow", "darrow")


def norm_box(a, b):
    (x0, y0), (x1, y1) = a, b
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def inside(box, x, y):
    return box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]


def hex_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def norm_obj(o):
    if o["type"] in BOX_TYPES:
        o["x0"], o["x1"] = sorted((o["x0"], o["x1"]))
        o["y0"], o["y1"] = sorted((o["y0"], o["y1"]))


_wrap_cache = {}


def wrapped_text(o):
    """Text with line breaks inserted so it fits the object's width (o['w'], if set)."""
    text, w, size = o.get("text") or " ", o.get("w"), o["size"]
    if not w:
        return text
    key = (text, int(w), int(size))
    if key in _wrap_cache:
        return _wrap_cache[key]
    font = get_font(size)
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if font.getlength(trial) <= w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    out = "\n".join(lines)
    if len(_wrap_cache) > 500:
        _wrap_cache.clear()
    _wrap_cache[key] = out
    return out


def text_pad(o):
    return max(6, o["size"] // 2) if o["type"] == "callout" else 0


def text_bbox(o):
    """Box around the text using full line height (not just the ink), so it is stable and grabbable."""
    txt = wrapped_text(o)
    font = get_font(o["size"])
    l, _, r, _ = _measure.multiline_textbbox((o["x0"], o["y0"]), txt, font=font, spacing=4)
    probe = "\n".join(["Ag"] * (txt.count("\n") + 1))
    _, t, _, b = _measure.multiline_textbbox((o["x0"], o["y0"]), probe, font=font, spacing=4)
    if o.get("w"):
        r = max(r, o["x0"] + o["w"])
    return l, t - 1, r + 1, b + 1


def step_radius(o):
    return max(10, int(o["size"] * 0.85))


ROTATABLE = ("rect", "rrect", "frect", "ellipse", "star", "triangle", "diamond", "arc", "symbol", "text", "callout",
             "line", "arrow", "darrow", "pen")


def rotate_pt(p, c, deg):
    """Rotate point p around centre c by deg degrees clockwise (screen coordinates, y down)."""
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a)


def unrotated(o):
    """Copy of the object without its rotation (geometry as drawn)."""
    o2 = dict(o)
    o2.pop("angle", None)
    return o2


def obj_bbox(o):
    ang = o.get("angle", 0) % 360
    if ang and o["type"] in ROTATABLE:
        l, t, r, b = obj_bbox(unrotated(o))
        c = ((l + r) / 2, (t + b) / 2)
        pts = [rotate_pt(p, c, ang) for p in ((l, t), (r, t), (l, b), (r, b))]
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    t = o["type"]
    if t == "pen":
        xs = [p[0] for p in o["pts"]]
        ys = [p[1] for p in o["pts"]]
        w = o.get("width", 3) / 2
        return min(xs) - w, min(ys) - w, max(xs) + w, max(ys) + w
    if t == "text":
        return text_bbox(o)
    if t == "callout":
        l, tp, r, b = text_bbox(o)
        p = text_pad(o)
        return l - p, tp - p, r + p, b + p
    if t == "step":
        r = step_radius(o)
        return o["x0"] - r, o["y0"] - r, o["x0"] + r, o["y0"] + r
    w = o.get("width", 0) / 2
    return (min(o["x0"], o["x1"]) - w, min(o["y0"], o["y1"]) - w,
            max(o["x0"], o["x1"]) + w, max(o["y0"], o["y1"]) + w)


def seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def hit_object(objects, x, y, tol):
    for o in reversed(objects):
        if o["type"] == "pen":
            pts = o["pts"]
            if any(seg_dist((x, y), pts[i], pts[i + 1]) <= tol + o["width"] / 2 for i in range(len(pts) - 1)):
                return o
            continue
        if o["type"] in LINE_TYPES:
            if seg_dist((x, y), (o["x0"], o["y0"]), (o["x1"], o["y1"])) <= tol + o["width"] / 2:
                return o
        else:
            l, t, r, b = obj_bbox(o)
            if l - tol <= x <= r + tol and t - tol <= y <= b + tol:
                if o["type"] in ("rect", "rrect", "ellipse", "spotlight") and not o.get("fill"):
                    # outline shapes: only the outline is clickable, so you can draw inside them
                    m = tol + o.get("width", 3) / 2 + 2
                    if l + m < x < r - m and t + m < y < b - m:
                        continue
                return o
    return None


def obj_handles(o):
    if o.get("angle", 0) % 360:
        return []            # rotated objects: move / rotate / restyle only; set angle to 0 to resize by handles
    if o["type"] in LINE_TYPES:
        return [(o["x0"], o["y0"]), (o["x1"], o["y1"])]
    if o["type"] in BOX_TYPES:
        x0, y0, x1, y1 = o["x0"], o["y0"], o["x1"], o["y1"]
        return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    if o["type"] in ("text", "callout"):
        l, t, r, b = obj_bbox(o)
        mx, my = (l + r) / 2, (t + b) / 2
        # 0-3 corners (width + text size), 4/5 left/right edge (width), 6/7 top/bottom edge (text size)
        return [(l, t), (r, t), (l, b), (r, b), (l, my), (r, my), (mx, t), (mx, b)]
    return []


def set_handle(o, i, x, y):
    if o["type"] in ("text", "callout"):
        l, t, r, b = obj_bbox(o)
        pad = text_pad(o)
        h = max(4, b - t)
        if i in (0, 1, 2, 3, 6, 7):            # change text size by the vertical stretch
            new_h = (b - y) if i in (0, 1, 6) else (y - t)
            if new_h > 4:
                o["size"] = int(max(6, min(200, round(o["size"] * new_h / h))))
            if i in (0, 1, 6):                 # keep the bottom edge where it was
                nb = obj_bbox(o)[3]
                o["y0"] += b - nb
        if i in (1, 3, 5):                     # right side: width follows the mouse
            o["w"] = int(max(24, x - o["x0"] - pad))
        elif i in (0, 2, 4):                   # left side: move the left edge, keep the right edge
            new_l = min(x, r - 24)
            o["x0"] += new_l - l
            o["w"] = int(max(24, r - pad - o["x0"]))
        return
    if o["type"] in LINE_TYPES:
        o["x0" if i == 0 else "x1"] = x
        o["y0" if i == 0 else "y1"] = y
    else:
        o["x0" if i in (0, 2) else "x1"] = x
        o["y0" if i in (0, 1) else "y1"] = y


def move_obj(o, dx, dy):
    if o["type"] == "pen":
        o["pts"] = [(px + dx, py + dy) for px, py in o["pts"]]
        return
    for k in ("x0", "x1"):
        if k in o:
            o[k] += dx
    for k in ("y0", "y1"):
        if k in o:
            o[k] += dy


def _head(d, tip, ang, L, color):
    d.polygon([tip, (tip[0] - L * math.cos(ang - 0.45), tip[1] - L * math.sin(ang - 0.45)),
               (tip[0] - L * math.cos(ang + 0.45), tip[1] - L * math.sin(ang + 0.45))], fill=color)


def polyline(d, pts, color, w, dash=None, mid_arrows=False, head_len=None):
    """Draw a path; dash = (on, off) px for a dashed line; mid_arrows puts an arrowhead at the middle
    of each segment (or every ~70 px of a long path) pointing in the drawing direction."""
    if len(pts) < 2:
        return
    if not dash:
        d.line(pts, fill=color, width=w, joint="curve")
    else:
        on, off = dash
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg == 0:
                continue
            ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
            pos = 0.0
            while pos < seg:
                end = min(seg, pos + on)
                d.line([(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)], fill=color, width=w)
                pos += on + off
    if mid_arrows:
        L = head_len or max(10, w * 3.5)
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        step = 70 if total > 140 else total / 2
        target, run = step, 0.0
        for a, b in zip(pts, pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            while seg > 0 and target <= run + seg:
                f = (target - run) / seg
                tip = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                _head(d, tip, math.atan2(b[1] - a[1], b[0] - a[0]), L, color)
                target += step
            run += seg


def draw_arrow(d, a, b, color, w, arrow, both=False, dash=None, mid_arrows=False):
    if not arrow:
        polyline(d, [a, b], color, w, dash, mid_arrows)
        return
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    L = max(12, w * 4)
    base_b = (b[0] - L * 0.8 * math.cos(ang), b[1] - L * 0.8 * math.sin(ang))
    base_a = (a[0] + L * 0.8 * math.cos(ang), a[1] + L * 0.8 * math.sin(ang)) if both else a
    if math.hypot(b[0] - a[0], b[1] - a[1]) > L * (1.6 if both else 0.8):
        polyline(d, [base_a, base_b], color, w, dash, mid_arrows)
    _head(d, b, ang, L, color)
    if both:
        _head(d, a, ang + math.pi, L, color)


def ellipse_points(box, n=72):
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def arc_points(box, start, end, n=48):
    """Points along an ellipse arc from start to end degrees (clockwise on screen, 0 = right)."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    if end <= start:
        end += 360
    return [(cx + rx * math.cos(math.radians(start + (end - start) * i / n)),
             cy + ry * math.sin(math.radians(start + (end - start) * i / n))) for i in range(n + 1)]


SYMBOLS = [("no", "No / not allowed (red circle + slash)"), ("probe", "Probe – standard tip"),
           ("ruby", "Probe – ruby tip"), ("stopwatch", "Stopwatch (feed rate)"),
           ("thermo", "Thermometer (temperature)"), ("plusminus", "± sign"), ("plus", "+ sign"), ("minus", "− sign"),
           ("rotate_cw", "Rotation clockwise"), ("rotate_ccw", "Rotation anti-clockwise")]


def draw_symbol(d, kind, box, color, w):
    """Vector versions of the common ToolRoom help-image objects, fitted into box."""
    x0, y0, x1, y1 = box
    bw, bh = max(2, x1 - x0), max(2, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if kind == "no":
        lw = max(3, int(min(bw, bh) * 0.11))
        d.ellipse((x0 + lw / 2, y0 + lw / 2, x1 - lw / 2, y1 - lw / 2), outline="#ff0000", width=lw)
        r = (min(bw, bh) - lw) / 2 * 0.72
        d.line([(cx - r, cy - r), (cx + r, cy + r)], fill="#ff0000", width=lw)
    elif kind in ("probe", "ruby"):
        ball = min(bw, bh * 0.28) / 2
        stem_w = max(2, ball * 0.45)
        d.line([(cx, y0), (cx, y1 - ball * 2)], fill="#e8e8e8", width=int(stem_w * 2))
        d.line([(cx - stem_w * 0.5, y0), (cx - stem_w * 0.5, y1 - ball * 2)], fill="#ffffff", width=max(1, int(stem_w * 0.6)))
        d.line([(cx + stem_w * 0.6, y0), (cx + stem_w * 0.6, y1 - ball * 2)], fill="#b0b0b0", width=max(1, int(stem_w * 0.5)))
        bc, hl = ("#c8102e", "#ff7b8a") if kind == "ruby" else ("#d9534f", "#ffb3ae")
        d.ellipse((cx - ball, y1 - ball * 2, cx + ball, y1), fill=bc, outline="#7a0f1f", width=1)
        d.ellipse((cx - ball * 0.55, y1 - ball * 1.75, cx - ball * 0.1, y1 - ball * 1.25), fill=hl)
    elif kind == "stopwatch":
        r = min(bw, bh * 0.8) / 2
        cyy = y1 - r
        lw = max(2, int(r * 0.12))
        d.rectangle((cx - r * 0.18, y0, cx + r * 0.18, y0 + r * 0.35), fill="#333333")
        d.rectangle((cx - r * 0.35, y0 + r * 0.25, cx + r * 0.35, y0 + r * 0.45), fill="#333333")
        d.ellipse((cx - r, cyy - r, cx + r, cyy + r), fill="#ffffff", outline="#333333", width=lw)
        for k in range(12):
            a = math.radians(k * 30)
            d.line([(cx + r * 0.8 * math.cos(a), cyy + r * 0.8 * math.sin(a)),
                    (cx + r * 0.92 * math.cos(a), cyy + r * 0.92 * math.sin(a))], fill="#333333", width=max(1, lw // 2))
        d.line([(cx, cyy), (cx + r * 0.55, cyy - r * 0.45)], fill="#d9534f", width=max(2, lw))
        d.ellipse((cx - lw, cyy - lw, cx + lw, cyy + lw), fill="#333333")
    elif kind == "thermo":
        tw = max(4, bw * 0.35)
        bulb = min(bw, tw * 2) / 2
        lw = max(2, int(tw * 0.25))
        d.rounded_rectangle((cx - tw / 2, y0, cx + tw / 2, y1 - bulb), radius=tw / 2, fill="#ffffff",
                            outline="#111111", width=lw)
        d.rectangle((cx - tw * 0.18, y0 + bh * 0.35, cx + tw * 0.18, y1 - bulb), fill="#e00000")
        d.ellipse((cx - bulb, y1 - bulb * 2, cx + bulb, y1), fill="#e00000", outline="#111111", width=lw)
    elif kind in ("plus", "minus", "plusminus"):
        lw = max(2, int(min(bw, bh) * 0.14))
        if kind == "plusminus":
            d.line([(x0, cy - bh * 0.15), (x1, cy - bh * 0.15)], fill=color, width=lw)
            d.line([(cx, y0), (cx, cy + bh * 0.05)], fill=color, width=lw)
            d.line([(x0, y1 - lw / 2), (x1, y1 - lw / 2)], fill=color, width=lw)
        else:
            d.line([(x0, cy), (x1, cy)], fill=color, width=lw)
            if kind == "plus":
                d.line([(cx, y0), (cx, y1)], fill=color, width=lw)
    elif kind in ("rotate_cw", "rotate_ccw"):
        lw = max(2, w)
        pts = arc_points((x0 + lw, y0 + lw, x1 - lw, y1 - lw), 300, 210)   # open ring, gap at top-right
        if kind == "rotate_ccw":
            pts = pts[::-1]
        polyline(d, pts, color, lw)
        a, b = pts[-2], pts[-1]
        _head(d, b, math.atan2(b[1] - a[1], b[0] - a[0]), max(10, lw * 3.5), color)


def clip_box(box, size):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    return max(0, x0), max(0, y0), min(size[0], x1), min(size[1], y1)


def paste_layer(out, layer, x, y):
    """Alpha-composite an RGBA layer onto out at (x, y); parts outside the image are clipped."""
    x, y = int(round(x)), int(round(y))
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(layer.width, out.width - x), min(layer.height, out.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return out
    if out.mode != "RGBA":
        out = out.convert("RGBA")
    out.alpha_composite(layer.crop((sx0, sy0, sx1, sy1)), (x + sx0, y + sy0))
    return out


def draw_objects(img, objects):
    """Copy of img with the annotation objects rendered on top, in order."""
    if not objects:
        return img
    mode = img.mode
    out = img.copy()
    d = ImageDraw.Draw(out)
    for o in objects:
        t = o["type"]
        ang = o.get("angle", 0) % 360
        if ang and t in ROTATABLE:
            if t in LINE_TYPES or t == "pen":
                # vector shapes: rotate the geometry itself around the centre
                l, tp, r, b = obj_bbox(unrotated(o))
                cen = ((l + r) / 2, (tp + b) / 2)
                o = unrotated(o)
                if t == "pen":
                    o["pts"] = [rotate_pt(p, cen, ang) for p in o["pts"]]
                else:
                    (o["x0"], o["y0"]) = rotate_pt((o["x0"], o["y0"]), cen, ang)
                    (o["x1"], o["y1"]) = rotate_pt((o["x1"], o["y1"]), cen, ang)
            else:
                # raster shapes / text: draw unrotated on a transparent layer, rotate it, composite
                o2 = unrotated(o)
                l, tp, r, b = obj_bbox(o2)
                pad = int(o.get("width", 3)) + 6
                l, tp, r, b = int(l) - pad, int(tp) - pad, int(r) + pad + 1, int(b) + pad + 1
                move_obj(o2, -l, -tp)
                layer = draw_objects(Image.new("RGBA", (max(1, r - l), max(1, b - tp)), (0, 0, 0, 0)), [o2])
                rot = layer.rotate(-ang, resample=Image.BICUBIC, expand=True)
                out = paste_layer(out, rot, (l + r) / 2 - rot.width / 2, (tp + b) / 2 - rot.height / 2)
                d = ImageDraw.Draw(out)
                continue
        c, w = o.get("color", "#ff0000"), int(o.get("width", 3))
        if t in BOX_TYPES:
            box = (min(o["x0"], o["x1"]), min(o["y0"], o["y1"]), max(o["x0"], o["x1"]), max(o["y0"], o["y1"]))
        if t == "rect":
            if o.get("dash") and not o.get("fill"):
                bx0, by0, bx1, by1 = box
                polyline(d, [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1), (bx0, by0)], c, w, (w * 3, w * 2))
            else:
                d.rectangle(box, fill=c if o.get("fill") else None, outline=c, width=w)
        elif t in ("star", "triangle", "diamond"):
            pts = shape_points(t, box)
            d.line(pts + [pts[0]], fill=c, width=w, joint="curve")
            if o.get("fill"):
                d.polygon(pts, fill=c)
        elif t == "rrect":
            d.rounded_rectangle(box, radius=max(4, min(24, (box[2] - box[0]) // 6, (box[3] - box[1]) // 6)),
                                fill=c if o.get("fill") else None, outline=c, width=w)
        elif t == "frect":
            b = clip_box(box, out.size)
            if b[2] - b[0] >= 1 and b[3] - b[1] >= 1:
                region = out.crop(b).convert("RGBA")
                ov = Image.new("RGBA", region.size, hex_rgb(c) + (int(o.get("alpha", 90)),))
                out.paste(Image.alpha_composite(region, ov).convert(out.mode), b[:2])
                d = ImageDraw.Draw(out)
            d.rectangle(box, outline=c, width=w)
        elif t == "spotlight":
            dark = Image.new("RGBA", out.size, (0, 0, 0, int(o.get("alpha", 150))))
            b = clip_box(box, out.size)
            if b[2] - b[0] >= 1 and b[3] - b[1] >= 1:
                ImageDraw.Draw(dark).rectangle((b[0], b[1], b[2] - 1, b[3] - 1), fill=(0, 0, 0, 0))
            out = Image.alpha_composite(out.convert("RGBA"), dark).convert(out.mode)
            d = ImageDraw.Draw(out)
        elif t == "pen":
            polyline(d, o["pts"], c, w, (w * 3, w * 2) if o.get("dash") else None, o.get("midarrow"))
            if not o.get("dash"):
                r = w / 2
                for px, py in (o["pts"][0], o["pts"][-1]):
                    d.ellipse((px - r, py - r, px + r, py + r), fill=c)
        elif t == "ellipse":
            if o.get("dash") and not o.get("fill"):
                polyline(d, ellipse_points(box), c, w, (w * 3, w * 2))
            else:
                d.ellipse(box, fill=c if o.get("fill") else None, outline=c, width=w)
        elif t == "arc":
            pts = arc_points(box, o.get("start", 0), o.get("end", 90))
            polyline(d, pts, c, w, (w * 3, w * 2) if o.get("dash") else None)
            if o.get("heads", True) and len(pts) > 2:
                L = max(10, w * 3.5)
                _head(d, pts[-1], math.atan2(pts[-1][1] - pts[-3][1], pts[-1][0] - pts[-3][0]), L, c)
                _head(d, pts[0], math.atan2(pts[0][1] - pts[2][1], pts[0][0] - pts[2][0]), L, c)
        elif t == "symbol":
            draw_symbol(d, o.get("kind", "no"), box, c, w)
        elif t in ("highlight", "blur"):
            b = clip_box(box, out.size)
            if b[2] - b[0] < 1 or b[3] - b[1] < 1:
                continue
            region = out.crop(b)
            if t == "highlight":
                ov = Image.new("RGBA", region.size, hex_rgb(c) + (110,))
                region = Image.alpha_composite(region.convert("RGBA"), ov).convert(out.mode)
            else:
                k = max(3, int(o.get("block", 8)))
                small = region.resize((max(1, region.width // k), max(1, region.height // k)), Image.BILINEAR)
                region = small.resize(region.size, Image.NEAREST)
            out.paste(region, b[:2])
            d = ImageDraw.Draw(out)
        elif t in LINE_TYPES:
            draw_arrow(d, (o["x0"], o["y0"]), (o["x1"], o["y1"]), c, w, t != "line", both=(t == "darrow"),
                       dash=(w * 3, w * 2) if o.get("dash") else None, mid_arrows=o.get("midarrow"))
        elif t == "text":
            d.multiline_text((o["x0"], o["y0"]), wrapped_text(o), fill=c, font=get_font(o["size"]), spacing=4)
        elif t == "callout":
            d.rounded_rectangle(obj_bbox(o), radius=8, fill="white", outline=c, width=max(2, w))
            d.multiline_text((o["x0"], o["y0"]), wrapped_text(o), fill=c, font=get_font(o["size"]), spacing=4)
        elif t == "step":
            r, (cx, cy) = step_radius(o), (o["x0"], o["y0"])
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c, outline="white", width=2)
            d.text((cx, cy), str(o["n"]), fill="white", font=get_font(r * 1.1, True), anchor="mm")
    return out if out.mode == mode else out.convert(mode)


# =========================================================================== #
#  Small widgets
# =========================================================================== #
class Tooltip:
    _all = []

    @classmethod
    def hide_all(cls):
        for t in cls._all:
            t.hide()

    def __init__(self, widget, text):
        Tooltip._all.append(self)
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _):
        self.hide()
        self.job = self.w.after(450, self.show)

    def show(self):
        self.job = None
        try:
            if self.tip or not self.w.winfo_viewable():
                return
        except tk.TclError:
            return
        x, y = self.w.winfo_rootx() + self.w.winfo_width() + 6, self.w.winfo_rooty() + 4
        self.tip = tk.Toplevel(self.w)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, bg="#2b2b2b", fg="white", font=("Segoe UI", 9),
                 padx=8, pady=4, justify="left").pack()
        self.tip.geometry(f"+{x}+{y}")

    def hide(self, _=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class TextDialog(tk.Toplevel):
    def __init__(self, master, title, initial=""):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        ttk.Label(self, text="Type your text  (Ctrl+Enter = OK)").pack(anchor="w", padx=12, pady=(10, 4))
        self.txt = tk.Text(self, width=44, height=5, font=("Segoe UI", 11), wrap="word", relief="solid", bd=1)
        self.txt.pack(padx=12)
        self.txt.insert("1.0", initial)
        self.txt.tag_add("sel", "1.0", "end")
        bf = ttk.Frame(self)
        bf.pack(pady=10)
        ttk.Button(bf, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Control-Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.geometry(f"+{master.winfo_pointerx() - 40}+{master.winfo_pointery() - 40}")
        self.grab_set()
        self.txt.focus_set()
        master.wait_window(self)

    def ok(self):
        self.result = self.txt.get("1.0", "end-1c").rstrip("\n")
        self.destroy()


class HotkeyDialog(tk.Toplevel):
    PRESETS = ["Ctrl+Shift+F9", "Ctrl+Shift+F10", "Ctrl+Alt+F9", "Ctrl+Alt+F10", "Ctrl+Shift+S", "Ctrl+Shift+R",
               "PrintScreen", "Ctrl+PrintScreen", "Shift+PrintScreen", "Ctrl+Shift+PrintScreen",
               "Ctrl+Shift+Alt+1", "Ctrl+Shift+Alt+2", "Ctrl+Shift+F12", "Ctrl+Shift+F11", "Ctrl+Shift+Alt+S",
               "Ctrl+Shift+Alt+R", "F9", "F10", "F11", "F12", "ScrollLock", "Pause", "Win+Shift+X", "Win+Shift+V"]
    TK_KEYS = {"Print": "PrintScreen", "Scroll_Lock": "ScrollLock", "Pause": "Pause", "Insert": "Insert",
               "Delete": "Delete", "Home": "Home", "End": "End", "Prior": "PageUp", "Next": "PageDown",
               "space": "Space", "Tab": "Tab", "Escape": "Esc"}

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Hotkeys")
        self.transient(app)
        self.resizable(False, False)
        self.vars = {}
        frm = ttk.Frame(self, padding=14)
        frm.pack()
        ttk.Label(frm, text="Click a box and press the key combination you want, or pick one from the list.\n"
                            "A SnapPatch hotkey is global: while SnapPatch runs, that combination is taken away from\n"
                            "every other program – so prefer combos nobody uses (the defaults Ctrl+Shift+F9 / F10 are).\n"
                            "Note: Windows blocks hotkeys while a program running *as administrator* has the focus;\n"
                            "click a normal window first, or run SnapPatch as administrator too.",
                  justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        for r, (key, label) in enumerate((("capture", "Capture screenshot"), ("record", "Start / stop recording")), 1):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            v = tk.StringVar(value=app.hotkey_map.get(key, ""))
            self.vars[key] = v
            cb = ttk.Combobox(frm, textvariable=v, values=self.PRESETS, width=26)
            cb.grid(row=r, column=1, pady=4)
            cb.bind("<KeyPress>", lambda e, v=v: self.on_key(e, v))
            cb.bind("<KeyRelease>", lambda e, v=v: self.on_key(e, v, release=True))
            st = app.hotkey_state.get(key, "")
            ttk.Label(frm, text=("✔" if st == "ok" else st), foreground="#2e7d32" if st == "ok" else "#c62828",
                      width=34).grid(row=r, column=2, sticky="w", padx=(8, 0))
        self.msg = ttk.Label(frm, text="", foreground="#c62828")
        self.msg.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        bf = ttk.Frame(frm)
        bf.grid(row=4, column=0, columnspan=3, pady=(12, 0), sticky="e")
        ttk.Button(bf, text="Reset to defaults", command=self.reset).pack(side="left", padx=4)
        ttk.Button(bf, text="Apply", style="Accent.TButton", command=self.apply).pack(side="left", padx=4)
        ttk.Button(bf, text="Close", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.geometry(f"+{app.winfo_rootx() + 120}+{app.winfo_rooty() + 120}")
        self.grab_set()

    def on_key(self, e, var, release=False):
        name = e.keysym
        if name in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Win_L", "Win_R",
                    "Super_L", "Super_R", "Meta_L", "Meta_R"):
            return "break"
        if release and name != "Print":          # PrintScreen only arrives as a key-release on Windows
            return "break"
        mods = []
        if e.state & 0x4:
            mods.append("Ctrl")
        if e.state & 0x20000 or e.state & 0x8:
            mods.append("Alt")
        if e.state & 0x1:
            mods.append("Shift")
        if e.state & 0x40:
            mods.append("Win")
        key = self.TK_KEYS.get(name, name)
        if len(key) == 1:
            key = key.upper()
        text = "+".join(mods + [key])
        try:
            var.set(normalize_hotkey(text))
            self.msg.config(text="")
        except ValueError as ex:
            self.msg.config(text=f"{text}: {ex}")
        return "break"

    def reset(self):
        for k, v in DEFAULT_HOTKEYS.items():
            self.vars[k].set(v)
        self.app.settings.pop("custom_hotkeys", None)

    def apply(self):
        mapping = {}
        for k, v in self.vars.items():
            text = v.get().strip()
            if text:
                try:
                    text = normalize_hotkey(text)
                except ValueError as ex:
                    self.msg.config(text=f"{text}: {ex}")
                    return
            mapping[k] = text
        if mapping.get("capture") and mapping.get("capture") == mapping.get("record"):
            self.msg.config(text="Capture and record can't use the same hotkey.")
            return
        self.app.settings["hotkeys"] = mapping
        self.app.settings["custom_hotkeys"] = {k: bool(v) for k, v in mapping.items()}
        self.app.tried = {}
        save_settings(self.app.settings)
        self.app.apply_hotkeys(mapping)
        self.destroy()


class Countdown(tk.Toplevel):
    def __init__(self, master, seconds, done):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.done, self.left = done, seconds
        self.lbl = tk.Label(self, font=("Segoe UI", 26, "bold"), bg="#222", fg="white", padx=24, pady=10)
        self.lbl.pack()
        self.geometry(f"+{self.winfo_screenwidth() - 260}+{self.winfo_screenheight() - 170}")
        self.tick()

    def tick(self):
        if self.left <= 0:
            self.destroy()
            self.done()
            return
        self.lbl.config(text=f"Capture in {self.left}…")
        self.left -= 1
        self.after(1000, self.tick)


# =========================================================================== #
#  Full-screen region picker
# =========================================================================== #
class RegionPicker(tk.Toplevel):
    """Dimmed screenshot. The un-dimmed area is a child canvas that is only *moved and resized*
    (no image re-encoding on hover), so it stays smooth even on 4K screens."""
    MAG_N, MAG_S = 15, 8

    def __init__(self, master, shot, origin, windows, smart, callback, smart_note="", mode="image"):
        super().__init__(master)
        self.mode = mode
        self.smart_note = smart_note
        self._err_shown = False
        self.shot, self.origin, self.windows, self.smart, self.cb = shot, origin, windows, smart, callback
        self.W, self.H = shot.size
        self.start = None
        self.dragging = False
        self.cur = (self.W // 2, self.H // 2)
        self.cands, self.level, self.box = [], 0, (0, 0, self.W, self.H)
        self._job = None
        self._detect_job = None
        self._last_cand_pt = None
        self.done = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{self.W}x{self.H}+{origin[0]}+{origin[1]}")
        self.configure(cursor="crosshair")

        self.cv = tk.Canvas(self, width=self.W, height=self.H, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack()
        self.ph_dim = ImageTk.PhotoImage(Image.blend(shot, Image.new("RGB", shot.size), 0.5))
        self.ph_full = ImageTk.PhotoImage(shot)
        self.cv.create_image(0, 0, anchor="nw", image=self.ph_dim)
        self.cv.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(3, 3), tags="hx")
        self.cv.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(3, 3), tags="hy")
        self.cv.create_rectangle(0, 0, 0, 0, outline="#ff3b30", width=2, tags="border")

        self.win = tk.Canvas(self.cv, highlightthickness=0, bd=0, cursor="crosshair")
        self.win.create_image(0, 0, anchor="nw", image=self.ph_full, tags="full")
        self.win.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(3, 3), tags="hx")
        self.win.create_line(0, 0, 0, 0, fill="#00e5ff", dash=(3, 3), tags="hy")

        n, s = self.MAG_N, self.MAG_S
        size = n * s
        self.mag = tk.Canvas(self.cv, width=size, height=size + 22, bg="#222", highlightthickness=1,
                             highlightbackground="white", bd=0, cursor="crosshair")
        self.mag.create_image(0, 0, anchor="nw", tags="img")
        c = (n // 2) * s
        self.mag.create_line(0, c + s // 2, size, c + s // 2, fill="#00e5ff")
        self.mag.create_line(c + s // 2, 0, c + s // 2, size, fill="#00e5ff")
        self.mag.create_rectangle(c, c, c + s, c + s, outline="red")
        self.mag.create_text(6, size + 11, anchor="w", fill="white", font=("Consolas", 10), tags="txt")
        self.ph_mag = None

        self.size_lbl = tk.Label(self.cv, bg="#222", fg="white", font=("Segoe UI", 10, "bold"), padx=6, pady=1)
        self.hint = tk.Frame(self.cv, bg="#222")
        self.mode_btns = {}
        for key, label in (("image", "📷  Image"), ("video", "🎥  Video")):
            b = tk.Label(self.hint, text=label, fg="white", bg="#222", font=("Segoe UI", 11, "bold"),
                         padx=14, pady=6, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, k=key: self.set_mode(k))
            self.mode_btns[key] = b
        self.hint_lbl = tk.Label(self.hint, bg="#222", fg="#ddd", font=("Segoe UI", 10), padx=12, pady=6)
        self.hint_lbl.pack(side="left")
        self.hint.place(relx=0.5, y=14, anchor="n", in_=self.cv)
        self.set_mode(mode)
        self.bind("<Tab>", lambda e: self.set_mode("video" if self.mode == "image" else "image"))
        self.bind("<KeyPress-v>", lambda e: self.set_mode("video"))
        self.bind("<KeyPress-i>", lambda e: self.set_mode("image"))
        self.diag = tk.Label(self.cv, bg="#222", fg="#9fe6a0" if smart is not None else "#ff8080",
                             font=("Segoe UI", 9), padx=8, pady=3, anchor="w")
        self.diag.place(x=8, rely=1.0, y=-8, anchor="sw", in_=self.cv)

        for w in (self.cv, self.win, self.mag, self.size_lbl, self.diag):
            w.bind("<Motion>", self.on_motion)
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<ButtonPress-3>", lambda e: self.finish(None))
            w.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Escape>", lambda e: self.finish(None))
        self.bind("<Return>", lambda e: self.finish_box((0, 0, self.W, self.H)))
        for key, dx, dy in [("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)]:
            self.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy))
            self.bind(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx * 10, dy * 10))
        self.after(20, self.grab_focus)
        self.after(30, self.init_pointer)

    def grab_focus(self, retry=True):
        try:
            if IS_WIN:
                ctypes.windll.user32.SetForegroundWindow(int(self.wm_frame(), 16))
            self.focus_force()
            self.cv.focus_set()
        except Exception:
            pass
        if retry:
            self.after(120, lambda: None if self.done else self.grab_focus(False))

    # ------------------------------------------------------------------ #
    def local(self, e):
        return e.x_root - self.origin[0], e.y_root - self.origin[1]

    def init_pointer(self):
        x = self.winfo_pointerx() - self.origin[0]
        y = self.winfo_pointery() - self.origin[1]
        if 0 <= x < self.W and 0 <= y < self.H:
            self.cur = (x, y)
        self.update_view()

    def candidates(self, x, y):
        ox, oy = self.origin
        sx, sy = x + ox, y + oy
        boxes, win_box = [], None
        for w in self.windows:
            l, t, r, b = w["rect"]
            if l <= sx < r and t <= sy < b:
                win_box = (max(0, l - ox), max(0, t - oy), min(self.W, r - ox), min(self.H, b - oy))
                boxes.append(win_box)
                for c in w["children"]:
                    if c[0] <= sx < c[2] and c[1] <= sy < c[3]:
                        boxes.append((max(0, c[0] - ox), max(0, c[1] - oy),
                                      min(self.W, c[2] - ox), min(self.H, c[3] - oy)))
                break
        self.n_smart = 0
        if self.smart is not None:
            try:
                sb = self.smart.boxes_at(x, y)
                self.n_smart = len(sb)
                boxes.extend(sb)
            except Exception:
                self.report("smart detection failed")
        boxes.append((0, 0, self.W, self.H))
        return [b for b in dedupe_boxes(boxes) if inside(b, x, y) and b[2] - b[0] >= 8 and b[3] - b[1] >= 8]

    # ------------------------------------------------------------------ #
    def on_motion(self, e):
        self.cur = self.local(e)
        if self.start and not self.dragging and \
                (abs(self.cur[0] - self.start[0]) > 3 or abs(self.cur[1] - self.start[1]) > 3):
            self.dragging = True
        if self._job is None:
            self._job = self.after(16, self.update_view)
        if not self.dragging:
            # detection is the expensive part: run it when the mouse slows down
            if self._detect_job is not None:
                self.after_cancel(self._detect_job)
            self._detect_job = self.after(35, self.detect_now)

    def detect_now(self):
        self._detect_job = None
        if self.done or self.dragging:
            return
        try:
            x, y = self.cur
            if self._last_cand_pt != (x, y):
                self._last_cand_pt = (x, y)
                self.cands = self.candidates(x, y) or [(0, 0, self.W, self.H)]
                self.level = 0
            self.show_box(self.cands[min(self.level, len(self.cands) - 1)])
            self.update_diag()
        except Exception:
            self.report("auto-detect failed")

    def on_wheel(self, e):
        if self.dragging or not self.cands:
            return
        self.level = max(0, min(len(self.cands) - 1, self.level + (1 if e.delta > 0 else -1)))
        self.show_box(self.cands[self.level])

    def on_press(self, e):
        self.start, self.dragging = self.local(e), False

    def on_release(self, e):
        if not self.start:
            return
        x, y = self.local(e)
        if self.dragging:
            x0, y0, x1, y1 = norm_box(self.start, (x, y))
            box = (max(0, x0), max(0, y0), min(self.W, x1 + 1), min(self.H, y1 + 1))
        else:
            if self._last_cand_pt != (x, y):
                self.cur = (x, y)
                self.detect_now()
            box = self.box
        self.start, self.dragging = None, False
        if box[2] - box[0] >= 2 and box[3] - box[1] >= 2:
            self.finish_box(box)

    def set_mode(self, mode):
        self.mode = mode
        for k, b in self.mode_btns.items():
            b.config(bg=("#c62828" if k == "video" else ACCENT) if k == mode else "#222",
                     fg="white" if k == mode else "#aaa")
        self.hint_lbl.config(text=("Choose the area to RECORD:  " if mode == "video" else "") +
                                  "hover + click, or drag  •  wheel = bigger / smaller  •  "
                                  "Tab = switch Image / Video  •  Enter = full screen  •  Esc = cancel")
        try:
            self.cv.itemconfigure("border", outline="#ff3b30" if mode == "video" else "#ff3b30")
        except tk.TclError:
            pass

    def finish_box(self, box):
        if self.mode == "video":
            ox, oy = self.origin
            self.finish((box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy))
        else:
            self.finish(self.shot.crop(box))

    def nudge(self, dx, dy):
        x = min(self.W - 1, max(0, self.cur[0] + dx))
        y = min(self.H - 1, max(0, self.cur[1] + dy))
        if IS_WIN:
            set_cursor_pos(x + self.origin[0], y + self.origin[1])
        self.cur = (x, y)
        self.update_view()

    def finish(self, result):
        if self.done:
            return
        self.done = True
        for job in (self._job, self._detect_job):
            if job:
                self.after_cancel(job)
        self.destroy()
        self.cb(result, self.mode)

    # ------------------------------------------------------------------ #
    def show_box(self, box):
        self.box = box
        x0, y0, x1, y1 = box
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        self.win.place(x=x0, y=y0, width=w, height=h, in_=self.cv)
        self.win.coords("full", -x0, -y0)
        self.cv.coords("border", x0 - 1, y0 - 1, x1, y1)
        self.size_lbl.config(text=f"{w} × {h}")
        ly = y0 - 26 if y0 > 60 else min(self.H - 26, y1 + 4)
        self.size_lbl.place(x=max(0, x0), y=ly, in_=self.cv)
        self.position_crosshair()

    def position_crosshair(self):
        x, y = self.cur
        self.cv.coords("hx", 0, y, self.W, y)
        self.cv.coords("hy", x, 0, x, self.H)
        x0, y0 = self.box[:2]
        self.win.coords("hx", 0, y - y0, self.W, y - y0)
        self.win.coords("hy", x - x0, 0, x - x0, self.H)

    def report(self, what):
        log.exception("Picker: %s", what)
        if not self._err_shown:
            self._err_shown = True
            try:
                self.diag.config(fg="#ff8080", text=f"Error: {what} – details in {LOG_FILE}")
            except Exception:
                pass

    def update_view(self):
        self._job = None
        if self.done:
            return
        try:
            self._update_view()
        except Exception:
            self.report("hover update failed")

    def _update_view(self):
        x, y = self.cur
        if self.dragging and self.start:
            x0, y0, x1, y1 = norm_box(self.start, (x, y))
            self.show_box((max(0, x0), max(0, y0), min(self.W, x1 + 1), min(self.H, y1 + 1)))
        elif not self.cands:
            self.detect_now()
        else:
            self.position_crosshair()

        # magnifier
        n, s = self.MAG_N, self.MAG_S
        h = n // 2
        size = n * s
        self.ph_mag = ImageTk.PhotoImage(self.shot.crop((x - h, y - h, x + h + 1, y + h + 1))
                                         .resize((size, size), Image.NEAREST))
        self.mag.itemconfigure("img", image=self.ph_mag)
        px = self.shot.getpixel((min(max(x, 0), self.W - 1), min(max(y, 0), self.H - 1)))
        self.mag.itemconfigure("txt", text=f"{x}, {y}   #%02x%02x%02x" % px[:3])
        mx = x + 26 if x + 26 + size + 4 < self.W else x - 26 - size
        my = y + 26 if y + 26 + size + 26 < self.H else y - 26 - size - 24
        self.mag.place(x=mx, y=my, in_=self.cv)
        self.mag.tk.call("raise", self.mag._w)  # Canvas.lift() means tag_raise, so raise the widget directly
        self.hint.lift()
        self.update_diag()

    def update_diag(self):
        if not self._err_shown:
            smart_txt = (f"edge detection ON – {getattr(self, 'n_smart', 0)} areas here"
                         if self.smart is not None else f"edge detection OFF: {self.smart_note}")
            self.diag.config(text=f"Auto-detect: {smart_txt}  •  {len(self.windows)} windows  •  "
                                  f"{len(self.cands)} choices (wheel)  •  screen {self.W}×{self.H}")
        self.diag.lift()


# =========================================================================== #
#  Screen recording
# =========================================================================== #
class Recorder(threading.Thread):
    """Grabs a screen region at a fixed rate and streams it to ffmpeg (MP4).
    Without ffmpeg the frames are kept and saved as a GIF when stopped."""

    def __init__(self, bbox, path, fps=12, show_cursor=True):
        super().__init__(daemon=True)
        l, t, r, b = bbox
        r -= (r - l) % 2            # H.264 needs even sizes
        b -= (b - t) % 2
        self.bbox = (l, t, max(l + 2, r), max(t + 2, b))
        self.size = (self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])
        self.path = Path(path)
        self.fps = fps
        self.show_cursor = show_cursor
        self.stop_flag = threading.Event()
        self.paused = threading.Event()
        self.error = None
        self.frames_written = 0
        self.recorded_seconds = 0.0
        self.proc = None
        self.gif_frames = None
        self.first_frame = None
        self.ffmpeg = ffmpeg_exe()
        if self.ffmpeg is None:
            self.path = self.path.with_suffix(".gif")
            self.gif_frames = []

    def grab(self):
        try:
            im = ImageGrab.grab(bbox=self.bbox, all_screens=True)
        except TypeError:
            im = ImageGrab.grab(bbox=self.bbox)
        im = im.convert("RGB")
        if im.size != self.size:
            im = im.resize(self.size)
        if self.show_cursor:
            pos = get_cursor_pos()
            if pos:
                cx, cy = pos[0] - self.bbox[0], pos[1] - self.bbox[1]
                if 0 <= cx < self.size[0] and 0 <= cy < self.size[1]:
                    d = ImageDraw.Draw(im, "RGBA")
                    d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=(255, 220, 0, 110), outline=(200, 120, 0, 200))
                    d.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=(0, 0, 0, 230))
        return im

    def write(self, frame):
        if self.proc is not None:
            self.proc.stdin.write(frame.tobytes())
        else:
            if len(self.gif_frames) < 3000:   # ~5 min at 10 fps; keeps memory bounded
                self.gif_frames.append(frame)
        self.frames_written += 1

    def run(self):
        try:
            if self.ffmpeg is not None:
                cmd = [self.ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                       "-s", f"{self.size[0]}x{self.size[1]}", "-r", str(self.fps), "-i", "-",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                       "-movflags", "+faststart", str(self.path)]
                flags = {"creationflags": 0x08000000} if IS_WIN else {}   # CREATE_NO_WINDOW
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, **flags)
            else:
                self.fps = min(self.fps, 10)
            interval = 1.0 / self.fps
            next_t = time.perf_counter()
            frame = None
            while not self.stop_flag.is_set():
                if self.paused.is_set():
                    time.sleep(0.05)
                    next_t = time.perf_counter()
                    continue
                frame = self.grab()
                if self.first_frame is None:
                    self.first_frame = frame.copy()
                now = time.perf_counter()
                written = 0
                while next_t <= now and written < self.fps:   # keep wall-clock timing if a grab was slow
                    self.write(frame)
                    next_t += interval
                    written += 1
                if written == 0:
                    self.write(frame)
                    next_t += interval
                self.recorded_seconds = self.frames_written / self.fps
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            if self.proc is not None:
                self.proc.stdin.close()
                err = self.proc.stderr.read().decode("utf-8", "ignore")
                if self.proc.wait() != 0:
                    raise RuntimeError(f"ffmpeg failed: {err.strip()[-400:]}")
            elif self.gif_frames:
                first, rest = self.gif_frames[0], self.gif_frames[1:]
                first.save(str(self.path), save_all=True, append_images=rest,
                           duration=int(1000 / self.fps), loop=0, optimize=False)
        except Exception as ex:
            log.exception("Recording failed")
            self.error = f"{ex.__class__.__name__}: {ex}"
            try:
                if self.proc is not None and self.proc.poll() is None:
                    self.proc.kill()
            except Exception:
                pass

    def stop(self):
        self.stop_flag.set()


class RecordCountdown(tk.Toplevel):
    """4..3..2..1 before a recording starts, with a chance to take a still screenshot instead or cancel."""

    def __init__(self, master, bbox, on_go, on_screenshot, on_cancel, seconds=4):
        super().__init__(master)
        self.on_go, self.on_screenshot, self.on_cancel = on_go, on_screenshot, on_cancel
        self.left, self.done = seconds, False
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#1e1e1e", highlightthickness=1, highlightbackground="#555")
        self.num = tk.Label(self, text=str(seconds), fg="#ff3b30", bg="#1e1e1e", font=("Segoe UI", 44, "bold"),
                            padx=18)
        self.num.pack(side="left")
        col = tk.Frame(self, bg="#1e1e1e")
        col.pack(side="left", padx=(0, 14), pady=10)
        tk.Label(col, text="Recording starts in…", fg="white", bg="#1e1e1e", font=("Segoe UI", 11, "bold")
                 ).pack(anchor="w")
        row = tk.Frame(col, bg="#1e1e1e")
        row.pack(anchor="w", pady=(6, 0))
        for text, cmd, bg in (("📷  Screenshot instead", self.shot, "#0a64ff"), ("▶  Start now", self.go, "#2e7d32"),
                              ("✕  Cancel", self.cancel, "#3a3a3a")):
            b = tk.Label(row, text=text, bg=bg, fg="white", padx=10, pady=4, cursor="hand2", font=("Segoe UI", 10))
            b.pack(side="left", padx=3)
            b.bind("<Button-1>", lambda e, c=cmd: c())
        tk.Label(col, text="S = screenshot   Enter = start   Esc = cancel", fg="#888", bg="#1e1e1e",
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        l, t, r, b = bbox
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min((l + r) // 2 - w // 2, sw - w))
        y = t - h - 8 if t - h - 8 >= 0 else (b + 8 if b + 8 + h <= sh else max(0, (t + b) // 2 - h // 2))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.go())
        self.bind("<KeyPress-s>", lambda e: self.shot())
        self.after(30, self.focus_force)
        self.after(1000, self.tick)

    def tick(self):
        if self.done:
            return
        self.left -= 1
        if self.left <= 0:
            self.go()
            return
        self.num.config(text=str(self.left))
        self.after(1000, self.tick)

    def _finish(self, cb):
        if self.done:
            return
        self.done = True
        self.destroy()
        cb()

    def go(self):
        self._finish(self.on_go)

    def shot(self):
        self._finish(self.on_screenshot)

    def cancel(self):
        self._finish(self.on_cancel)


class RecordBar(tk.Toplevel):
    """Floating recorder control, placed just outside the recorded area. Draggable."""
    BG, FG = "#1e1e1e", "#f2f2f2"

    def __init__(self, master, bbox, fps, on_stop, on_pause, on_cancel):
        super().__init__(master)
        self.on_stop, self.on_pause, self.on_cancel = on_stop, on_pause, on_cancel
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.BG, highlightthickness=1, highlightbackground="#555")
        l, t, r, b = bbox
        row = tk.Frame(self, bg=self.BG)
        row.pack(fill="x")
        self.dot = tk.Label(row, text="●", fg="#ff3b30", bg=self.BG, font=("Segoe UI", 16, "bold"))
        self.dot.pack(side="left", padx=(12, 4))
        self.lbl = tk.Label(row, text="00:00", fg=self.FG, bg=self.BG, font=("Consolas", 18, "bold"))
        self.lbl.pack(side="left", padx=(0, 10))
        self.info = tk.Label(row, text=f"{r - l} × {b - t}  •  {fps} fps", fg="#9aa", bg=self.BG,
                             font=("Segoe UI", 9))
        self.info.pack(side="left", padx=(0, 14))

        def btn(text, cmd, bg, fg="white", bold=False):
            w = tk.Label(row, text=text, bg=bg, fg=fg, padx=12, pady=5, cursor="hand2",
                         font=("Segoe UI", 10, "bold" if bold else "normal"))
            w.pack(side="left", padx=3, pady=6)
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e, w=w: w.config(relief="raised"))
            w.bind("<Leave>", lambda e, w=w: w.config(relief="flat"))
            return w
        self.pause_btn = btn("❚❚  Pause", self.toggle_pause, "#3a3a3a")
        btn("■  Stop & save", on_stop, "#c62828", bold=True)
        btn("✕", on_cancel, "#3a3a3a", fg="#ccc")
        Tooltip(self.pause_btn, "Pause / resume (recording time stops)")
        Tooltip(self.pause_btn.master.winfo_children()[-1], "Cancel – discard this recording")
        self.tip = tk.Label(self, text="Drag this bar to move it  •  hotkey or Esc = stop", fg="#777", bg=self.BG,
                            font=("Segoe UI", 8))
        self.tip.pack(fill="x", padx=12, pady=(0, 4))

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(r - w, sw - w))
        y = t - h - 6 if t - h - 6 >= 0 else (b + 6 if b + 6 + h <= sh else max(0, t + 6))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.paused = False
        self.bind("<Escape>", lambda e: on_stop())
        for wdg in (self, row, self.lbl, self.info, self.tip, self.dot):
            wdg.bind("<ButtonPress-1>", self.drag_start)
            wdg.bind("<B1-Motion>", self.drag_move)
        self.blink_on = True
        self.after(600, self.blink)

        # thin red frame around the recorded area (outside it, so it is not in the video)
        self.frame_parts = []
        for geo in ((l - 3, t - 3, r - l + 6, 3), (l - 3, b, r - l + 6, 3), (l - 3, t - 3, 3, b - t + 6),
                    (r, t - 3, 3, b - t + 6)):
            fx, fy, fw, fh = geo
            if fw > 0 and fh > 0:
                part = tk.Toplevel(master)
                part.overrideredirect(True)
                part.attributes("-topmost", True)
                part.configure(bg="#ff3b30")
                part.geometry(f"{fw}x{fh}+{fx}+{fy}")
                self.frame_parts.append(part)

    def drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def drag_move(self, e):
        self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def blink(self):
        if not self.winfo_exists():
            return
        self.blink_on = not self.blink_on
        self.dot.config(fg="#888" if self.paused else ("#ff3b30" if self.blink_on else "#7a1c1c"))
        self.after(600, self.blink)

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="▶  Resume" if self.paused else "❚❚  Pause",
                              bg="#2e7d32" if self.paused else "#3a3a3a")
        for part in self.frame_parts:
            part.configure(bg="#888" if self.paused else "#ff3b30")
        self.on_pause(self.paused)

    def set_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        self.lbl.config(text=f"{m:02d}:{s:02d}", fg="#aaa" if self.paused else self.FG)

    def close(self):
        for part in self.frame_parts:
            try:
                part.destroy()
            except Exception:
                pass
        self.destroy()


# =========================================================================== #
#  Video editor (trim / crop / export)
# =========================================================================== #
def ffmpeg_flags():
    return {"creationflags": 0x08000000} if IS_WIN else {}


def video_info(path):
    """(duration_seconds, width, height, fps) by parsing `ffmpeg -i` output."""
    import re
    exe = ffmpeg_exe()
    if exe is None:
        raise RuntimeError("ffmpeg not available – install imageio-ffmpeg")
    out = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True, **ffmpeg_flags()).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", out)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    m = re.search(r"(\d+(?:\.\d+)?) fps", out)
    fps = float(m.group(1)) if m else 10.0
    return dur, w, h, fps


class ImageSaveDialog(tk.Toplevel):
    """Format + quality preset + size for saving a screenshot.  result = dict or None."""
    PRESETS = [("high", "High quality", "PNG, original size – lossless, best for documentation"),
               ("screen", "Screen", "PNG, fitted to 1920 px wide – sharp, smaller when the shot is huge"),
               ("standard", "Standard", "JPG 85 %, original size – small files for e-mail / chat"),
               ("custom", "Custom", "choose format, size and quality yourself")]

    def __init__(self, master, src_w, src_h, last=None):
        super().__init__(master)
        self.title("Save image")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.src_w, self.src_h = src_w, src_h
        last = last or {}
        f = ttk.Frame(self, padding=14)
        f.pack()
        ttk.Label(f, text="Quality preset", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        self.preset = tk.StringVar(value=last.get("preset", "high"))
        for i, (key, name, desc) in enumerate(self.PRESETS, start=1):
            ttk.Radiobutton(f, text=name, value=key, variable=self.preset,
                            command=self.on_preset).grid(row=i, column=0, sticky="w", padx=(0, 10))
            ttk.Label(f, text=desc, foreground="#555").grid(row=i, column=1, columnspan=2, sticky="w")
        r = len(self.PRESETS) + 1
        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
        r += 1
        ttk.Label(f, text="Format").grid(row=r, column=0, sticky="w", pady=3)
        self.fmt = tk.StringVar(value=last.get("fmt", "png"))
        ff = ttk.Frame(f)
        ff.grid(row=r, column=1, columnspan=2, sticky="w")
        for key, name in (("png", "PNG"), ("jpg", "JPG"), ("gif", "GIF"), ("bmp", "BMP")):
            ttk.Radiobutton(ff, text=name, value=key, variable=self.fmt, command=self.custom).pack(side="left", padx=3)
        r += 1
        ttk.Label(f, text="Size").grid(row=r, column=0, sticky="w", pady=3)
        self.scale = tk.IntVar(value=last.get("scale", 100))
        sf = ttk.Frame(f)
        sf.grid(row=r, column=1, columnspan=2, sticky="w")
        for pct in (100, 75, 50, 33, 25):
            ttk.Radiobutton(sf, text=f"{pct}%", value=pct, variable=self.scale, command=self.custom).pack(side="left", padx=2)
        ttk.Label(sf, text="  or width").pack(side="left")
        self.width = tk.StringVar(value="")
        we = ttk.Entry(sf, textvariable=self.width, width=7)
        we.pack(side="left", padx=4)
        we.bind("<KeyRelease>", lambda e: self.custom())
        ttk.Label(sf, text="px").pack(side="left")
        r += 1
        ttk.Label(f, text="Quality").grid(row=r, column=0, sticky="w", pady=3)
        self.quality = tk.IntVar(value=last.get("quality", 85))
        qf = ttk.Frame(f)
        qf.grid(row=r, column=1, columnspan=2, sticky="w")
        self.q_scale = ttk.Scale(qf, from_=10, to=100, variable=self.quality, length=200,
                                 command=lambda v: self.custom())
        self.q_scale.pack(side="left")
        self.q_lbl = ttk.Label(qf, width=26)
        self.q_lbl.pack(side="left", padx=6)
        r += 1
        self.preview = ttk.Label(f, foreground="#333", font=("Segoe UI", 10, "bold"))
        self.preview.grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 0))
        r += 1
        self.remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Use these settings for Ctrl+S next time", variable=self.remember
                        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        bf = ttk.Frame(f)
        bf.grid(row=r + 1, column=0, columnspan=3, pady=(12, 0), sticky="e")
        ttk.Button(bf, text="Save…", style="Accent.TButton", command=self.ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._updating = False
        self.on_preset()
        self.geometry(f"+{master.winfo_rootx() + 160}+{master.winfo_rooty() + 120}")
        self.grab_set()
        master.wait_window(self)

    def on_preset(self):
        p = self.preset.get()
        self._updating = True
        if p == "high":
            self.fmt.set("png"); self.scale.set(100); self.width.set("")
        elif p == "screen":
            self.fmt.set("png"); self.scale.set(100)
            self.width.set(str(min(1920, self.src_w)) if self.src_w > 1920 else "")
        elif p == "standard":
            self.fmt.set("jpg"); self.scale.set(100); self.width.set(""); self.quality.set(85)
        self._updating = False
        self.refresh()

    def custom(self):
        if not self._updating:
            self.preset.set("custom")
        self.refresh()

    def out_size(self):
        try:
            w = int(self.width.get())
            if 16 <= w <= 20000:
                return w, max(1, round(self.src_h * w / self.src_w))
        except ValueError:
            pass
        pct = self.scale.get() / 100
        return max(1, round(self.src_w * pct)), max(1, round(self.src_h * pct))

    def refresh(self):
        fmt, q = self.fmt.get(), int(self.quality.get())
        w, h = self.out_size()
        lossy = fmt in ("jpg", "gif")
        self.q_scale.state(["!disabled"] if lossy else ["disabled"])
        if fmt == "jpg":
            self.q_lbl.config(text=f"{q} %  ({'best' if q >= 90 else 'good' if q >= 70 else 'small file'})")
            est = w * h * (0.05 + 0.45 * (q / 100) ** 2) / 1024
        elif fmt == "gif":
            self.q_lbl.config(text=f"{32 + int((q - 10) / 90 * 224)} colours")
            est = w * h * 0.5 / 1024
        elif fmt == "png":
            self.q_lbl.config(text="lossless")
            est = w * h * 0.9 / 1024
        else:
            self.q_lbl.config(text="uncompressed")
            est = w * h * 3 / 1024
        self.preview.config(text=f"→ {fmt.upper()}  {w} × {h} px,  roughly {est:,.0f} KB")

    def ok(self):
        w, h = self.out_size()
        q = int(self.quality.get())
        self.result = {"preset": self.preset.get(), "fmt": self.fmt.get(), "w": w, "h": h, "quality": q,
                       "colours": 32 + int((q - 10) / 90 * 224), "scale": self.scale.get(),
                       "remember": self.remember.get()}
        self.destroy()


class ExportOptions(tk.Toplevel):
    """Size / quality dialog used for MP4, GIF and JPG export. result = dict or None."""

    def __init__(self, master, kind, src_w, src_h, fps=None, est_seconds=None):
        super().__init__(master)
        self.title(f"Save as {kind.upper()} – options")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.kind, self.src_w, self.src_h = kind, src_w, src_h
        f = ttk.Frame(self, padding=14)
        f.pack()
        ttk.Label(f, text="Size").grid(row=0, column=0, sticky="w", pady=4)
        self.scale = tk.IntVar(value=100)
        sf = ttk.Frame(f)
        sf.grid(row=0, column=1, sticky="w")
        for pct in (100, 75, 50, 33, 25):
            ttk.Radiobutton(sf, text=f"{pct}%", value=pct, variable=self.scale,
                            command=self.update_preview).pack(side="left", padx=2)
        ttk.Label(f, text="or width (px)").grid(row=1, column=0, sticky="w", pady=4)
        self.width = tk.StringVar(value="")
        we = ttk.Entry(f, textvariable=self.width, width=8)
        we.grid(row=1, column=1, sticky="w")
        we.bind("<KeyRelease>", lambda e: self.update_preview())
        ttk.Label(f, text="Quality").grid(row=2, column=0, sticky="w", pady=4)
        self.quality = tk.IntVar(value=80 if kind == "jpg" else 70)
        qf = ttk.Frame(f)
        qf.grid(row=2, column=1, sticky="w")
        self.q_scale = ttk.Scale(qf, from_=10, to=100, variable=self.quality, length=180,
                                 command=lambda v: self.update_preview())
        self.q_scale.pack(side="left")
        self.q_lbl = ttk.Label(qf, width=22)
        self.q_lbl.pack(side="left", padx=6)
        row = 3
        if kind == "gif":
            ttk.Label(f, text="Frames / second").grid(row=row, column=0, sticky="w", pady=4)
            self.fps = tk.IntVar(value=int(min(10, fps or 10)))
            ttk.Spinbox(f, from_=2, to=30, width=5, textvariable=self.fps,
                        command=self.update_preview).grid(row=row, column=1, sticky="w")
            row += 1
        elif kind == "mp4":
            ttk.Label(f, text="Frames / second").grid(row=row, column=0, sticky="w", pady=4)
            self.fps = tk.IntVar(value=int(round(fps or 12)))
            ttk.Spinbox(f, from_=2, to=60, width=5, textvariable=self.fps).grid(row=row, column=1, sticky="w")
            row += 1
        self.preview = ttk.Label(f, foreground="#555")
        self.preview.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        bf = ttk.Frame(f)
        bf.grid(row=row + 1, column=0, columnspan=2, pady=(12, 0), sticky="e")
        ttk.Button(bf, text="Save", style="Accent.TButton", command=self.ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.est_seconds = est_seconds
        self.update_preview()
        self.geometry(f"+{master.winfo_rootx() + 160}+{master.winfo_rooty() + 140}")
        self.grab_set()
        master.wait_window(self)

    def out_size(self):
        try:
            w = int(self.width.get())
            if w >= 16:
                w = min(w, 7680)
                return w // 2 * 2, max(2, int(self.src_h * w / self.src_w)) // 2 * 2
        except ValueError:
            pass
        pct = self.scale.get() / 100
        return max(2, int(self.src_w * pct)) // 2 * 2, max(2, int(self.src_h * pct)) // 2 * 2

    def update_preview(self):
        q = int(self.quality.get())
        w, h = self.out_size()
        if self.kind == "jpg":
            self.q_lbl.config(text=f"{q}  ({'best' if q >= 90 else 'good' if q >= 70 else 'small file'})")
            est = w * h * (0.05 + 0.45 * (q / 100) ** 2) / 1024
            self.preview.config(text=f"Output: {w} × {h} px, roughly {est:,.0f} KB")
        elif self.kind == "gif":
            colours = 32 + int((q - 10) / 90 * 224)
            self.q_lbl.config(text=f"{colours} colours")
            secs = self.est_seconds or 0
            est = w * h * secs * self.fps.get() * 0.08 / 1024
            self.preview.config(text=f"Output: {w} × {h} px, {self.fps.get()} fps, roughly {est:,.0f} KB")
        else:
            crf = int(round(35 - (q - 10) / 90 * 19))       # 10%->35 (small), 100%->16 (best)
            self.q_lbl.config(text=f"{'best' if q >= 85 else 'good' if q >= 50 else 'small file'}  (crf {crf})")
            secs = self.est_seconds or 0
            est = w * h * secs * 0.0035 * (q / 70) / 1024
            self.preview.config(text=f"Output: {w} × {h} px, roughly {est:,.0f} KB")

    def ok(self):
        w, h = self.out_size()
        q = int(self.quality.get())
        self.result = {"w": w, "h": h, "quality": q,
                       "crf": int(round(35 - (q - 10) / 90 * 19)),
                       "colours": 32 + int((q - 10) / 90 * 224),
                       "fps": int(self.fps.get()) if hasattr(self, "fps") else None}
        self.destroy()


class VideoEditor(tk.Toplevel):
    MAX_FRAMES = 900

    def __init__(self, master, path, on_saved=None):
        super().__init__(master)
        self.path = Path(path)
        self.on_saved = on_saved
        self.title(f"{self.path.name} – video editor")
        self.geometry("980x720")
        self.minsize(640, 480)
        self.frames, self.photo = [], None
        self.playing = False
        self.crop = None
        self.crop_start = None
        try:
            self.duration, self.vw, self.vh, self.src_fps = video_info(self.path)
        except Exception as ex:
            self.destroy()
            messagebox.showerror(APP_NAME, f"Could not read video:\n{ex}", parent=master)
            return
        self.pfps = max(1.0, min(self.src_fps, self.MAX_FRAMES / max(self.duration, 0.1)))
        self.scale = min(1.0, 900 / max(1, self.vw), 460 / max(1, self.vh))
        self.pw, self.ph = max(2, int(self.vw * self.scale)) // 2 * 2, max(2, int(self.vh * self.scale)) // 2 * 2
        self.build()
        self.after(50, self.load_frames)

    # ---------------------------------------------------------------- UI --
    def build(self):
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        self.play_btn = ttk.Button(top, text="▶ Play", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left")
        ttk.Button(top, text="[ Set start", command=lambda: self.set_mark("start")).pack(side="left", padx=(12, 2))
        ttk.Button(top, text="Set end ]", command=lambda: self.set_mark("end")).pack(side="left", padx=2)
        ttk.Button(top, text="Reset trim", command=self.reset_trim).pack(side="left", padx=(2, 12))
        self.crop_btn = ttk.Button(top, text="Crop: drag on video", command=self.clear_crop)
        self.crop_btn.pack(side="left")
        ttk.Button(top, text="Save as MP4…", style="Accent.TButton", command=lambda: self.export("mp4")).pack(side="right")
        ttk.Button(top, text="Save as GIF…", command=lambda: self.export("gif")).pack(side="right", padx=6)
        ttk.Button(top, text="Open with Windows", command=lambda: os.startfile(str(self.path)) if IS_WIN else None
                   ).pack(side="right", padx=6)

        self.cv = tk.Canvas(self, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True, padx=8)
        self.cv.bind("<ButtonPress-1>", self.crop_press)
        self.cv.bind("<B1-Motion>", self.crop_drag)
        self.cv.bind("<ButtonRelease-1>", self.crop_release)
        self.cv.bind("<Configure>", lambda e: self.show_frame())

        tl = ttk.Frame(self, padding=(8, 4))
        tl.pack(fill="x")
        self.pos = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(tl, from_=0, to=max(0.01, self.duration), variable=self.pos, command=self.on_scrub)
        self.slider.pack(fill="x")
        self.tl_cv = tk.Canvas(tl, height=22, bg="#ddd", highlightthickness=0)
        self.tl_cv.pack(fill="x", pady=(2, 0))
        self.tl_cv.bind("<Button-1>", self.tl_click)
        self.tl_cv.bind("<B1-Motion>", self.tl_click)
        self.tl_cv.bind("<Configure>", lambda e: self.draw_timeline())

        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")
        self.info = ttk.Label(bar, text="Loading frames…")
        self.info.pack(side="left")
        self.status = ttk.Label(bar, text="", foreground="#666")
        self.status.pack(side="right")
        self.t_start, self.t_end = 0.0, self.duration
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<bracketleft>", lambda e: self.set_mark("start"))
        self.bind("<bracketright>", lambda e: self.set_mark("end"))
        self.bind("<Escape>", lambda e: self.destroy())

    # ------------------------------------------------------------ frames --
    def load_frames(self):
        def work():
            try:
                exe = ffmpeg_exe()
                cmd = [exe, "-loglevel", "error", "-i", str(self.path), "-vf",
                       f"fps={self.pfps:.4f},scale={self.pw}:{self.ph}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **ffmpeg_flags())
                n = self.pw * self.ph * 3
                frames = []
                while len(frames) < self.MAX_FRAMES + 5:
                    buf = proc.stdout.read(n)
                    if len(buf) < n:
                        break
                    frames.append(Image.frombytes("RGB", (self.pw, self.ph), buf))
                proc.stdout.close()
                proc.wait()
                self.frames = frames
                self.after(0, self.frames_ready)
            except Exception as ex:
                log.exception("video decode failed")
                msg = f"Could not decode video: {ex}"
                self.after(0, lambda m=msg: self.info.config(text=m))
        threading.Thread(target=work, daemon=True).start()

    def frames_ready(self):
        if not self.frames:
            self.info.config(text="No frames could be read from this video.")
            return
        self.duration = len(self.frames) / self.pfps
        self.t_end = self.duration
        self.slider.config(to=self.duration)
        self.info.config(text=f"{self.vw} × {self.vh}  •  {self.duration:.1f} s  •  {self.src_fps:g} fps")
        self.draw_timeline()
        self.show_frame()

    def frame_at(self, t):
        if not self.frames:
            return None
        return self.frames[max(0, min(len(self.frames) - 1, int(t * self.pfps)))]

    def show_frame(self):
        fr = self.frame_at(self.pos.get())
        if fr is None:
            return
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        z = min(cw / self.pw, ch / self.ph, 1.5)
        dw, dh = max(1, int(self.pw * z)), max(1, int(self.ph * z))
        self.disp = (z, (cw - dw) // 2, (ch - dh) // 2)
        self.photo = ImageTk.PhotoImage(fr.resize((dw, dh), Image.BILINEAR) if z != 1 else fr)
        self.cv.delete("all")
        self.cv.create_image(self.disp[1], self.disp[2], anchor="nw", image=self.photo)
        if self.crop:
            x0, y0, x1, y1 = self.crop_to_canvas(self.crop)
            self.cv.create_rectangle(x0, y0, x1, y1, outline="#00e5ff", width=2, dash=(6, 3))
            self.cv.create_text(x0 + 4, y0 - 10, anchor="w", fill="white", font=("Segoe UI", 9, "bold"),
                                text=f"crop {self.crop[2] - self.crop[0]} × {self.crop[3] - self.crop[1]}")
        self.status.config(text=f"{self.pos.get():6.2f} s     trim {self.t_start:.2f} – {self.t_end:.2f} s")

    # ---------------------------------------------------------- playback --
    def on_scrub(self, _=None):
        self.show_frame()

    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.config(text="❚❚ Pause" if self.playing else "▶ Play")
        if self.playing:
            if self.pos.get() >= self.t_end - 0.01:
                self.pos.set(self.t_start)
            self._tick()

    def _tick(self):
        if not self.playing:
            return
        t = self.pos.get() + 1 / self.pfps
        if t > self.t_end:
            t = self.t_start
        self.pos.set(t)
        self.show_frame()
        self.after(int(1000 / self.pfps), self._tick)

    def step(self, n):
        self.playing = False
        self.pos.set(max(0, min(self.duration, self.pos.get() + n / self.pfps)))
        self.show_frame()

    # ---------------------------------------------------------- trimming --
    def set_mark(self, which):
        t = self.pos.get()
        if which == "start":
            self.t_start = min(t, self.t_end - 1 / self.pfps)
        else:
            self.t_end = max(t, self.t_start + 1 / self.pfps)
        self.draw_timeline()
        self.show_frame()

    def reset_trim(self):
        self.t_start, self.t_end = 0.0, self.duration
        self.draw_timeline()
        self.show_frame()

    def draw_timeline(self):
        c = self.tl_cv
        c.delete("all")
        w = max(1, c.winfo_width())
        if self.duration <= 0:
            return
        x0, x1 = self.t_start / self.duration * w, self.t_end / self.duration * w
        c.create_rectangle(0, 0, w, 22, fill="#bbb", outline="")
        c.create_rectangle(x0, 0, x1, 22, fill="#4caf50", outline="")
        c.create_text(w / 2, 11, text="kept part – click here or drag the slider, then use Set start / Set end",
                      fill="white", font=("Segoe UI", 8))

    def tl_click(self, e):
        w = max(1, self.tl_cv.winfo_width())
        self.pos.set(max(0, min(self.duration, e.x / w * self.duration)))
        self.show_frame()

    # ------------------------------------------------------------ cropping --
    def canvas_to_video(self, x, y):
        z, ox, oy = self.disp
        return (int(max(0, min(self.vw, (x - ox) / z / self.scale))),
                int(max(0, min(self.vh, (y - oy) / z / self.scale))))

    def crop_to_canvas(self, box):
        z, ox, oy = self.disp
        return [v * self.scale * z + (ox if i % 2 == 0 else oy) for i, v in enumerate(box)]

    def crop_press(self, e):
        if self.frames:
            self.crop_start = self.canvas_to_video(e.x, e.y)

    def crop_drag(self, e):
        if self.crop_start:
            self.crop = norm_box(self.crop_start, self.canvas_to_video(e.x, e.y))
            self.show_frame()

    def crop_release(self, e):
        if self.crop_start:
            box = norm_box(self.crop_start, self.canvas_to_video(e.x, e.y))
            self.crop_start = None
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                self.crop = None
            else:
                self.crop = box
            self.crop_btn.config(text="Clear crop" if self.crop else "Crop: drag on video")
            self.show_frame()

    def clear_crop(self):
        self.crop = None
        self.crop_btn.config(text="Crop: drag on video")
        self.show_frame()

    # -------------------------------------------------------------- export --
    def export(self, kind):
        if not self.frames:
            return
        ext = ".mp4" if kind == "mp4" else ".gif"
        suggested = self.path.with_name(self.path.stem + "_edited" + ext)
        out = filedialog.asksaveasfilename(parent=self, initialdir=str(self.path.parent), initialfile=suggested.name,
                                           defaultextension=ext, filetypes=[("MP4 video", "*.mp4")] if kind == "mp4"
                                           else [("GIF", "*.gif")])
        if not out:
            return
        if Path(out).resolve() == self.path.resolve():
            messagebox.showwarning(APP_NAME, "Please save under a different name – the original is kept.", parent=self)
            return
        self.playing = False
        if self.crop:
            cw, ch = self.crop[2] - self.crop[0], self.crop[3] - self.crop[1]
        else:
            cw, ch = self.vw, self.vh
        opts = ExportOptions(self, kind, cw, ch, fps=self.src_fps, est_seconds=self.t_end - self.t_start).result
        if not opts:
            return
        self.status.config(text="Saving…")
        self.update_idletasks()
        try:
            self.run_export(kind, out, opts)
        except Exception as ex:
            log.exception("video export failed")
            messagebox.showerror(APP_NAME, f"Could not save:\n{ex}", parent=self)
            return
        self.status.config(text=f"Saved {Path(out).name}")
        if self.on_saved:
            self.on_saved(Path(out))

    def run_export(self, kind, out, opts=None):
        opts = opts or {}
        exe = ffmpeg_exe()
        vf = []
        if self.crop:
            x0, y0, x1, y1 = self.crop
            w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
            vf.append(f"crop={w}:{h}:{x0}:{y0}")
        if opts.get("w"):
            vf.append(f"scale={opts['w']}:{opts['h']}:flags=lanczos")
        cmd = [exe, "-y", "-loglevel", "error", "-ss", f"{self.t_start:.3f}", "-to", f"{self.t_end:.3f}",
               "-i", str(self.path)]
        if kind == "mp4":
            if opts.get("fps"):
                vf.insert(0, f"fps={opts['fps']}")
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(opts.get("crf", 23)),
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        else:
            vf.insert(0, f"fps={opts.get('fps') or min(10, self.src_fps):g}")
            vf.append(f"split[a][b];[a]palettegen=max_colors={opts.get('colours', 256)}:stats_mode=diff[p];"
                      f"[b][p]paletteuse=dither=bayer")
            cmd += ["-vf", ",".join(vf), "-loop", "0", out]
        r = subprocess.run(cmd, capture_output=True, text=True, **ffmpeg_flags())
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-500:] or "ffmpeg failed")
        if kind == "mp4":
            try:
                fr = self.frame_at(self.t_start).copy()
                if self.crop:
                    fr = fr.crop(tuple(int(v * self.scale) for v in self.crop))
                fr.thumbnail((320, 320))
                fr.save(out + ".jpg", quality=80)
            except Exception:
                pass


# =========================================================================== #
#  Library panel
# =========================================================================== #
class HistoryPanel(ttk.Frame):
    """Horizontal strip of recent captures and recordings (newest first)."""
    THUMB_H = 84
    VIDEO_EXT = (".mp4", ".gif")

    def __init__(self, master, on_open):
        super().__init__(master, padding=(8, 2, 8, 2))
        self.on_open = on_open
        self.thumbs = []
        self.selected = None
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="History", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.count_lbl = ttk.Label(head, text="", foreground="#666")
        self.count_lbl.pack(side="left", padx=8)
        ttk.Button(head, text="Open folder", command=self.open_folder).pack(side="right")
        ttk.Button(head, text="⟳", width=3, command=self.refresh).pack(side="right", padx=4)
        self.cv = tk.Canvas(self, height=self.THUMB_H + 36, highlightthickness=0, bg="#f3f3f3")
        sb = ttk.Scrollbar(self, orient="horizontal", command=self.cv.xview)
        self.cv.configure(xscrollcommand=sb.set)
        self.cv.pack(fill="x")
        sb.pack(fill="x")
        self.inner = tk.Frame(self.cv, bg="#f3f3f3")
        self.cv.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        for w in (self.cv, self.inner):
            w.bind("<MouseWheel>", self.wheel)
            w.bind("<Shift-MouseWheel>", self.wheel)

    def wheel(self, e):
        self.cv.xview_scroll(-1 if e.delta > 0 else 1, "units")

    def open_folder(self):
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(LIBRARY_DIR))

    @staticmethod
    def is_video(p):
        return p.suffix.lower() in HistoryPanel.VIDEO_EXT

    def items(self):
        if not LIBRARY_DIR.exists():
            return []
        files = [p for p in LIBRARY_DIR.iterdir()
                 if p.suffix.lower() in (".png",) + self.VIDEO_EXT and not p.name.endswith(".mp4.jpg")]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:80]

    def refresh(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.thumbs.clear()
        cache = getattr(self, "_thumb_cache", {})
        files = self.items()
        self.count_lbl.config(text=f"{len(files)} items in {LIBRARY_DIR}")
        new_cache = {}
        for p in files:
            video = self.is_video(p)
            try:
                key = (str(p), p.stat().st_mtime)
                ph = cache.get(key)          # thumbnails are cached, so a refresh is instant
                if ph is None:
                    src = Path(str(p) + ".jpg") if video else p
                    if video and not src.exists():
                        im = Image.new("RGB", (160, self.THUMB_H), "#333")
                    else:
                        im = Image.open(src)
                        im.draft("RGB", (400, 400))      # fast decode for big PNG/JPG
                        im.thumbnail((200, self.THUMB_H))
                    if video:
                        d = ImageDraw.Draw(im, "RGBA")
                        cx, cy = im.width // 2, im.height // 2
                        d.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=(0, 0, 0, 150))
                        d.polygon([(cx - 6, cy - 9), (cx - 6, cy + 9), (cx + 9, cy)], fill="white")
                    ph = ImageTk.PhotoImage(im)
                new_cache[key] = ph
            except Exception:
                continue
            self.thumbs.append(ph)
            f = tk.Frame(self.inner, bg="#f3f3f3", padx=4, pady=3)
            f.pack(side="left")
            lbl = tk.Label(f, image=ph, bd=2, relief="solid", cursor="hand2", bg="white", highlightthickness=0)
            lbl.pack()
            when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %H:%M")
            kind = "video" if video else "image"
            cap = tk.Label(f, text=f"{when}  ·  {kind}", bg="#f3f3f3", fg="#555", font=("Segoe UI", 8))
            cap.pack()
            for w in (f, lbl, cap):
                w.bind("<Button-1>", lambda e, p=p, l=lbl: self.click(p, l))
                w.bind("<Double-Button-1>", lambda e, p=p: self.open_external(p))
                w.bind("<Button-3>", lambda e, p=p: self.context(e, p))
                w.bind("<MouseWheel>", self.wheel)
            Tooltip(lbl, f"{p.name}\n{'Click = trim / crop' if video else 'Click = open in editor'}  •  "
                         "Double-click = open with Windows  •  Right-click = more")
        self._thumb_cache = new_cache

    def click(self, p, lbl):
        if self.selected is not None:
            try:
                self.selected.config(bg="white")
            except tk.TclError:
                pass
        lbl.config(bg=ACCENT)
        self.selected = lbl
        if self.is_video(p):
            self.edit_video(p)
        else:
            self.on_open(p)

    def edit_video(self, p):
        if p.suffix.lower() == ".gif" or ffmpeg_exe() is None:
            self.open_external(p)
            return
        VideoEditor(self.winfo_toplevel(), p, on_saved=lambda _: self.refresh())

    def open_external(self, p):
        try:
            if IS_WIN:
                os.startfile(str(p))
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open {p.name}:\n{ex}", parent=self)

    def context(self, e, p):
        m = tk.Menu(self, tearoff=False)
        if self.is_video(p):
            m.add_command(label="Edit (trim / crop)…", command=lambda: self.edit_video(p))
            m.add_command(label="Play with Windows", command=lambda: self.open_external(p))
        else:
            m.add_command(label="Open in editor", command=lambda: self.on_open(p))
            m.add_command(label="Open with Windows", command=lambda: self.open_external(p))
        if not self.is_video(p):
            m.add_command(label="Copy image to clipboard", command=lambda: self.copy_image(p))
            m.add_command(label="Paste into current image as layer", command=lambda: self.paste_into(p))
        m.add_command(label="Copy file path", command=lambda: (self.clipboard_clear(), self.clipboard_append(str(p))))
        m.add_separator()
        m.add_command(label="Delete", command=lambda: self.delete(p))
        m.tk_popup(e.x_root, e.y_root)

    def copy_image(self, p):
        try:
            im = Image.open(p)
            im.load()
            copy_image_to_clipboard(im, self.winfo_toplevel().winfo_id(), p)
            self.winfo_toplevel().set_status(f"Copied {p.name} to the clipboard.")
        except Exception as ex:
            log.exception("history copy failed")
            messagebox.showerror(APP_NAME, f"Could not copy:\n{ex}", parent=self)

    def paste_into(self, p):
        try:
            im = Image.open(p)
            im.load()
            self.winfo_toplevel().add_layer(im.convert("RGB"))
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open:\n{ex}", parent=self)

    def delete(self, p):
        if messagebox.askyesno(APP_NAME, f"Delete {p.name}?", parent=self):
            for f in (p, Path(str(p) + ".jpg")):
                try:
                    if f.exists():
                        f.unlink()
                except Exception as ex:
                    messagebox.showerror(APP_NAME, str(ex), parent=self)
            self.refresh()


# =========================================================================== #
#  Editor
# =========================================================================== #
# key, icon, label, shortcut, tooltip
TOOLS = [
    ("select", "⬚", "Select", "s", "Click a field / button / panel to select it, or drag an area.\nClick an annotation to select, move or resize it."),
    ("move", "✥", "Move", "m", "Select an area first, then drag it somewhere else.\nThe hole is filled with the background colour."),
    ("crop", "⊡", "Crop", "c", "Drag the area you want to keep."),
    ("erase", "⌫", "Erase", "e", "Click a field / button / panel – or drag over anything – to remove it.\nThe gap is filled to match the surroundings automatically."),
    None,
    ("rect", "▭", "Box", "b", "Drag to draw a box (click a field / panel to box it)."),
    ("rrect", "▢", "Round box", "r", "Drag to draw a rounded box."),
    ("frect", "▰", "Filled box", "f", "Drag to draw a semi-transparent filled box."),
    ("ellipse", "◯", "Ellipse", "o", "Drag to draw an ellipse."),
    ("star", "★", "Star", "j", "Drag to draw a star (tick Filled in the bar to fill it)."),
    ("triangle", "△", "Triangle", "y", "Drag to draw a triangle."),
    ("diamond", "◇", "Diamond", "w", "Drag to draw a diamond."),
    ("arrow", "➜", "Arrow", "a", "Drag to draw an arrow."),
    ("darrow", "⟷", "2-way arrow", "d", "Drag to draw an arrow with heads at both ends."),
    ("line", "╱", "Line", "l", "Drag to draw a line."),
    ("pen", "✎", "Pen", "p", "Draw freehand."),
    ("highlight", "▓", "Highlight", "h", "Drag to highlight an area (click a field / panel to highlight it)."),
    ("spotlight", "◐", "Spotlight", "g", "Drag an area – everything else is darkened."),
    ("blur", "▦", "Blur", "u", "Drag over sensitive info to pixelate it (click a field to blur it)."),
    ("text", "A", "Text", "t", "Click to add text. Double-click text to edit it."),
    ("callout", "❝", "Callout", "k", "Click to add a text box with a border."),
    ("step", "①", "Step", "n", "Click to add the next step number (1, 2, 3…)."),
    None,
    ("arc", "◠", "Angle arc", "q", "Drag a box – an arc with arrowheads is drawn in it.\nSet start / end angle in the bar; tick Dashed for reference arcs."),
    ("symbol", "☉", "Symbol", "x", "Click to place a help-image symbol: probe, ruby probe, stopwatch,\nthermometer, 'no' sign, ±, rotation…"),
    None,
    ("pick", "◉", "Pick fill", "i", "Click a pixel to use its colour for Erase / Move fill."),
]
TOOL_LIST = [t for t in TOOLS if t]
OBJECT_DRAG_TOOLS = ("rect", "rrect", "frect", "ellipse", "star", "triangle", "diamond", "arc", "arrow", "darrow",
                     "line", "pen", "highlight", "spotlight", "blur")
OBJECT_CLICK_TOOLS = ("text", "callout", "step", "symbol")
ANNOTATION_TOOLS = OBJECT_DRAG_TOOLS + OBJECT_CLICK_TOOLS


class SnapPatch(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1360x860")
        try:
            self.iconbitmap(default=resource_path("snappatch.ico"))
        except Exception:
            pass
        self.minsize(820, 500)
        self.option_add("*Font", UI_FONT)

        # document state
        self.img = None
        self.objects = []
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        # interaction state
        self.sel = None
        self.sel_obj = None
        self.float = None
        self.drag_mode = None
        self.start = self.last = None
        self.handle_idx = None
        self.drag_pushed = False
        self._drag_base = None
        self.capturing = False
        self.recorder = None
        self.record_bar = None
        self._syncing = False
        # view state
        self.zoom = 1.0
        self.off = (0, 0)
        self.flat = None
        self.photo = None
        self._render_job = None
        self._render_fast = True
        self._blit_job = None
        self._ctx_job = None

        self.tool = tk.StringVar(value="select")
        self.ann_color = "#ff0000"
        self.fill_color = (238, 238, 238)
        self.auto_fill = tk.BooleanVar(value=True)
        self.line_width = tk.IntVar(value=3)
        self.font_size = tk.IntVar(value=18)
        self.delay = tk.IntVar(value=0)
        self.show_library = tk.BooleanVar(value=True)
        self.autosave = tk.BooleanVar(value=True)
        self.copy_after = tk.BooleanVar(value=False)
        self.settings = load_settings()
        self.rec_fps = tk.IntVar(value=int(self.settings.get("rec_fps", 12)))
        self.rec_cursor = tk.BooleanVar(value=True)
        self.draw_dashed = tk.BooleanVar(value=False)      # new lines / shapes dashed?
        self.draw_midarrow = tk.BooleanVar(value=False)    # arrowheads along the path?

        self.report_callback_exception = self.on_tk_error
        log.info("Start %s %s: Python %s, Pillow %s, numpy %s, %s", APP_NAME, APP_VERSION, sys.version.split()[0],
                 Image.__version__ if hasattr(Image, "__version__") else "?",
                 np.__version__ if np is not None else "MISSING", sys.executable)
        self.build_styles()
        self.build_menu()
        self.build_ui()
        self.bind_keys()
        self.rec_fps.trace_add("write", lambda *a: (self.settings.update(rec_fps=self.rec_fps.get()),
                                                    save_settings(self.settings)))
        self.line_width.trace_add("write", lambda *a: self.apply_style("width"))
        self.font_size.trace_add("write", lambda *a: self.apply_style("size"))
        self.tool.trace_add("write", lambda *a: self.on_tool_change())

        self.events = queue.Queue()
        self.hotkeys = HotkeyService(self.events)
        self.hotkey_state = {}
        self.tried = {}
        self.announce_hotkeys = False
        if self.settings.get("hotkey_scheme") != APP_VERSION_HOTKEYS and not self.settings.get("custom_hotkeys"):
            self.settings.pop("hotkeys", None)           # old automatic choice (e.g. Ctrl+Alt+2) – start over
            self.settings["hotkey_scheme"] = APP_VERSION_HOTKEYS
            self.settings["hotkeys_announced"] = False
        self.apply_hotkeys(self.settings.get("hotkeys", DEFAULT_HOTKEYS))
        self.after(150, self.poll_events)
        self.library.refresh()
        self.update_context()

        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        if "--minimized" in sys.argv:
            self.after(10, self.iconify)
            self.after(400, self.iconify)
        if args:
            self.after(150, lambda: self.open_file(args[0]))
        else:
            self.after(50, self.show_placeholder)
        self.after(60, lambda: self.set_status("Ready") if self.img is None else None)

    # ============================================================ UI build ===
    def build_styles(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista" if IS_WIN else "clam")
        except tk.TclError:
            pass
        st.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        st.configure("Ctx.TFrame", background="#eef3fb")
        st.configure("Ctx.TLabel", background="#eef3fb")
        st.configure("Ctx.TCheckbutton", background="#eef3fb")

    def build_menu(self):
        mb = tk.Menu(self)
        f = tk.Menu(mb, tearoff=False)
        f.add_command(label="New capture", accelerator="Ctrl+N", command=self.capture)
        dm = tk.Menu(f, tearoff=False)
        for s in (0, 3, 5, 10):
            dm.add_radiobutton(label="No delay" if s == 0 else f"{s} seconds", variable=self.delay, value=s)
        f.add_cascade(label="Capture delay", menu=dm)
        f.add_checkbutton(label="Auto-save captures to History", variable=self.autosave)
        f.add_checkbutton(label="Copy captures to clipboard", variable=self.copy_after)
        f.add_separator()
        self.record_menu_item = f
        f.add_command(label="Record video", accelerator=RECORD_HOTKEY_TEXT, command=self.record)
        f.add_command(label="Hotkeys…", command=self.show_hotkeys)
        fm = tk.Menu(f, tearoff=False)
        for v in (8, 12, 15, 20, 30):
            fm.add_radiobutton(label=f"{v} frames / second", variable=self.rec_fps, value=v)
        f.add_cascade(label="Recording frame rate", menu=fm)
        f.add_checkbutton(label="Show mouse cursor in recordings", variable=self.rec_cursor)
        f.add_separator()
        self.startup_var = tk.BooleanVar(value=startup_enabled())
        f.add_checkbutton(label="Start SnapPatch with Windows (minimized, hotkeys ready)", variable=self.startup_var,
                          command=self.toggle_startup)
        f.add_separator()
        f.add_command(label="New blank worksheet…", accelerator="Ctrl+Shift+N", command=self.new_blank_canvas)
        f.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_dialog)
        f.add_command(label="Paste as new image", accelerator="Ctrl+Shift+V", command=self.paste_as_new)
        f.add_separator()
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        f.add_command(label="Save as…", accelerator="Ctrl+Shift+S", command=self.save_as)
        f.add_command(label="Save as project (annotations stay editable)…", command=lambda: self.save_as(project=True))
        f.add_separator()
        f.add_command(label="Minimize to background (hotkeys stay active)", command=self.on_close)
        f.add_command(label="Exit SnapPatch completely", command=self.quit_app)
        mb.add_cascade(label="File", menu=f)

        e = tk.Menu(mb, tearoff=False)
        e.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        e.add_separator()
        e.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy)
        e.add_command(label="Paste as layer", accelerator="Ctrl+V", command=self.paste_layer)
        e.add_command(label="Insert image file…", command=self.insert_image)
        e.add_command(label="Copy selection as a layer", accelerator="Ctrl+D", command=self.duplicate)
        e.add_command(label="Delete", accelerator="Del", command=self.delete_key)
        e.add_separator()
        e.add_command(label="Select all", accelerator="Ctrl+A", command=self.select_all)
        mb.add_cascade(label="Edit", menu=e)

        im = tk.Menu(mb, tearoff=False)
        im.add_command(label="Crop to selection", command=self.crop_selection)
        im.add_command(label="Erase selection", command=self.erase_selection)
        im.add_command(label="Cut out rows (close gap ↕)", command=self.cut_rows)
        im.add_command(label="Cut out columns (close gap ↔)", command=self.cut_cols)
        im.add_separator()
        im.add_command(label="Flatten annotations into image", command=self.flatten_objects)
        mb.add_cascade(label="Image", menu=im)

        v = tk.Menu(mb, tearoff=False)
        v.add_command(label="Zoom in", accelerator="Ctrl++", command=self.zoom_in)
        v.add_command(label="Zoom out", accelerator="Ctrl+-", command=self.zoom_out)
        v.add_command(label="Actual size", accelerator="Ctrl+1", command=self.zoom_100)
        v.add_command(label="Fit to window", accelerator="Ctrl+0", command=self.zoom_fit)
        v.add_separator()
        v.add_checkbutton(label="History strip", variable=self.show_library, command=self.toggle_library)
        mb.add_cascade(label="View", menu=v)

        hm = tk.Menu(mb, tearoff=False)
        nm = tk.Menu(hm, tearoff=False)
        for name in HELP_PRESETS:
            nm.add_command(label=name, command=lambda n=name: self.new_help_canvas(n))
        hm.add_cascade(label="New help image (green canvas)", menu=nm)
        hm.add_command(label="Insert CIM3D screenshot from clipboard as layer", accelerator="Ctrl+V",
                       command=self.paste_layer)
        hm.add_command(label="Insert image file as layer…", command=self.insert_image)
        hm.add_separator()
        hm.add_command(label="Replace background with guideline green (click a background pixel)",
                       command=self.start_replace_background)
        fm2 = tk.Menu(hm, tearoff=False)
        for name in HELP_PRESETS:
            fm2.add_command(label=name, command=lambda n=name: self.fit_to_help_preset(n))
        hm.add_cascade(label="Fit current image onto a help canvas", menu=fm2)
        hm.add_separator()
        hm.add_command(label="Check against guidelines", command=lambda: self.check_help_guidelines(show=True))
        hm.add_command(label="Export help image…", accelerator="Ctrl+Shift+E", command=self.export_help_image)
        hm.add_separator()
        hm.add_command(label="Open the Help Image Design Guide (Confluence)",
                       command=lambda: os.startfile("https://ancagroup.atlassian.net/wiki/spaces/TRKB/pages/"
                                                    "8851496898/Help+Image+Design+Guide") if IS_WIN else None)
        mb.add_cascade(label="Help image", menu=hm)

        h = tk.Menu(mb, tearoff=False)
        h.add_command(label="Hotkeys…", command=self.show_hotkeys)
        h.add_command(label="Diagnostics…", command=self.show_diagnostics)
        h.add_separator()
        h.add_command(label=f"About – {APP_NAME} {APP_VERSION}", command=lambda: messagebox.showinfo(
            APP_NAME, f"{APP_NAME} {APP_VERSION}\n\nRunning from:\n{sys.executable if not getattr(sys, 'frozen', False) else sys.executable}"
                      f"\n{os.path.abspath(sys.argv[0])}", parent=self))
        h.add_command(label="Open log file", command=lambda: os.startfile(str(LOG_FILE)) if IS_WIN else None)
        mb.add_cascade(label="Help", menu=h)
        self.config(menu=mb)

    def build_ui(self):
        # --- top toolbar -------------------------------------------------------
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        cap = ttk.Button(top, text="◉  Capture", style="Accent.TButton", command=self.capture)
        cap.pack(side="left")
        self.cap_btn = cap
        Tooltip(cap, f"Take a screenshot ({HOTKEY_TEXT} works anywhere)")
        dmb = ttk.Menubutton(top, text="Delay: off", width=10)
        dmenu = tk.Menu(dmb, tearoff=False)
        for s in (0, 3, 5, 10):
            dmenu.add_radiobutton(label="No delay" if s == 0 else f"{s} s delay", variable=self.delay, value=s)
        dmb["menu"] = dmenu
        self.delay.trace_add("write", lambda *a: dmb.config(
            text="Delay: off" if not self.delay.get() else f"Delay: {self.delay.get()} s"))
        dmb.pack(side="left", padx=(2, 12))
        Tooltip(dmb, "Capture delay – use it to capture open menus and tooltips")
        rec = ttk.Button(top, text="●  Record", command=self.record)
        rec.pack(side="left", padx=(0, 12))
        self.rec_btn = rec
        Tooltip(rec, f"Record a video of part of the screen ({RECORD_HOTKEY_TEXT} starts and stops)")
        for text, cmd, tip, wd in [("▦ New", self.new_blank_canvas, "New blank worksheet to arrange images on", 7),
                                   ("📂 Open", self.open_dialog, "Open an image or project (Ctrl+O)", 8),
                                   ("💾 Save", self.save, "Save (Ctrl+S)", 8),
                                   ("💾 Save as", self.save_as, "Save as – choose format, size, quality (Ctrl+Shift+S)", 10),
                                   ("📤 Export", self.export_help_image, "Export as ToolRoom help image (Ctrl+Shift+E)", 9),
                                   ("📋 Copy", self.copy, "Copy to clipboard – pastes into Office, Teams or a folder (Ctrl+C)", 8)]:
            b = ttk.Button(top, text=text, command=cmd, width=wd)
            b.pack(side="left", padx=1)
            Tooltip(b, tip)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        for text, cmd, tip in [("↶", self.undo, "Undo (Ctrl+Z)"), ("↷", self.redo, "Redo (Ctrl+Y)")]:
            b = ttk.Button(top, text=text, command=cmd, width=3)
            b.pack(side="left", padx=1)
            Tooltip(b, tip)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="−", width=3, command=self.zoom_out).pack(side="left")
        self.zoom_lbl = ttk.Label(top, text="100%", width=6, anchor="center")
        self.zoom_lbl.pack(side="left")
        ttk.Button(top, text="+", width=3, command=self.zoom_in).pack(side="left")
        ttk.Button(top, text="Fit", width=4, command=self.zoom_fit).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="1:1", width=4, command=self.zoom_100).pack(side="left", padx=2)
        ttk.Checkbutton(top, text="History", variable=self.show_library,
                        command=self.toggle_library).pack(side="right")

        # --- context bar (changes with tool / selection) -------------------------
        self.ctx = ttk.Frame(self, style="Ctx.TFrame", padding=(10, 5))
        self.ctx.pack(fill="x")

        # --- body: tool palette | canvas | library ------------------------------
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)

        pal = tk.Frame(self.body, bg="#f7f7f7", padx=4, pady=6)
        pal.pack(side="left", fill="y")
        row = col = 0
        for t in TOOLS:
            if t is None:
                if col:
                    row += 1
                    col = 0
                tk.Frame(pal, height=1, bg="#cfcfcf").grid(row=row, column=0, columnspan=2, sticky="ew", pady=5, padx=4)
                row += 1
                continue
            key, icon, label, sc, tip = t
            b = tk.Radiobutton(pal, text=f"{icon} {label}", value=key, variable=self.tool, indicatoron=False,
                               anchor="w", width=12, padx=4, pady=5, bd=0, relief="flat", offrelief="flat",
                               bg="#f7f7f7", activebackground="#e3ecfa", selectcolor="#cfe0fb",
                               font=("Segoe UI", 9), cursor="hand2")
            b.grid(row=row, column=col, sticky="ew", padx=1, pady=1)
            Tooltip(b, f"{label}  ({sc.upper()})\n{tip}")
            col += 1
            if col == 2:
                col = 0
                row += 1

        self.edit = ttk.Frame(self.body)
        self.edit.pack(side="left", fill="both", expand=True)
        self.cv = tk.Canvas(self.edit, bg="#4b4f54", highlightthickness=0, cursor="crosshair")
        xs = ttk.Scrollbar(self.edit, orient="horizontal", command=self.xview)
        ys = ttk.Scrollbar(self.edit, orient="vertical", command=self.yview)
        self.cv.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        self.cv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.edit.rowconfigure(0, weight=1)
        self.edit.columnconfigure(0, weight=1)
        self.img_item = self.cv.create_image(0, 0, anchor="nw")

        # --- history strip -----------------------------------------------------------
        self.library = HistoryPanel(self, self.open_library_item)
        self.library.pack(fill="x", side="top")

        # --- status bar ----------------------------------------------------------
        self.status = tk.StringVar()
        self.info = tk.StringVar()
        bar = ttk.Frame(self, padding=(8, 3))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status, foreground="#333").pack(side="left")
        ttk.Label(bar, textvariable=self.info, foreground="#666").pack(side="right")

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<Double-Button-1>", self.on_double)
        self.cv.bind("<ButtonPress-3>", self.on_right_click)
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Configure>", lambda e: self.schedule_layout())
        self.cv.bind("<Control-MouseWheel>", self.on_ctrl_wheel)
        self.cv.bind("<MouseWheel>", lambda e: self.yview("scroll", -1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self.xview("scroll", -1 if e.delta > 0 else 1, "units"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_tk_error(self, exc, val, tb):
        log.error("Unhandled error:\n%s", "".join(traceback.format_exception(exc, val, tb)))
        try:
            self.set_status(f"Something went wrong ({exc.__name__}: {val}). Details: Help → Open log file")
        except Exception:
            pass

    # --- hotkeys ---------------------------------------------------------------------
    def apply_hotkeys(self, mapping):
        global HOTKEY_TEXT, RECORD_HOTKEY_TEXT
        self.hotkey_map = dict(mapping)
        HOTKEY_TEXT = mapping.get("capture") or "(no hotkey)"
        RECORD_HOTKEY_TEXT = mapping.get("record") or "(no hotkey)"
        self.hotkeys.assign(mapping)
        for w in (getattr(self, "cap_btn", None), getattr(self, "rec_btn", None)):
            if w is not None:
                for t in Tooltip._all:
                    if t.w is w:
                        t.text = (f"Take a screenshot ({HOTKEY_TEXT} works anywhere)" if w is self.cap_btn
                                  else f"Record a video of part of the screen ({RECORD_HOTKEY_TEXT} starts and stops)")
        if self.img is None:
            self.after(10, self.show_placeholder)

    def on_hotkey_result(self, result):
        self.hotkey_state = result
        bad = {k: v for k, v in result.items() if v not in ("ok", "off")}
        if not bad:
            self.set_status(f"Hotkeys ready:  {HOTKEY_TEXT} = capture   •   {RECORD_HOTKEY_TEXT} = record")
            if self.announce_hotkeys or not self.settings.get("hotkeys_announced"):
                self.announce_hotkeys = False
                self.settings["hotkeys_announced"] = True
                save_settings(self.settings)
                messagebox.showinfo(APP_NAME, f"Your hotkeys:\n\n   {HOTKEY_TEXT}   =   capture a screenshot\n"
                                    f"   {RECORD_HOTKEY_TEXT}   =   start / stop a recording\n\n"
                                    "(The capture screen has an Image / Video switch too.)\n"
                                    "Change them any time under File → Hotkeys.", parent=self)
            return
        log.warning("Hotkey problems: %s", bad)
        # Keys the user did not choose themselves: move on to the next candidate in the chain.
        custom = self.settings.get("custom_hotkeys", {})
        changed = dict(self.hotkey_map)
        for k in bad:
            if custom.get(k):
                continue
            chain = HOTKEY_CHAINS[k]
            cur = self.hotkey_map.get(k)
            nxt = [c for c in chain if c not in self.tried.setdefault(k, set()) and c != cur]
            if nxt:
                self.tried[k].add(cur)
                changed[k] = nxt[0]
        if changed != self.hotkey_map:
            self.settings["hotkeys"] = changed
            save_settings(self.settings)
            self.apply_hotkeys(changed)
            self.announce_hotkeys = True
            return
        txt = ";  ".join(f"{k}: {self.hotkey_map.get(k)} – {v}" for k, v in bad.items())
        self.set_status(f"Hotkey problem – {txt}.  Change it under File → Hotkeys.")
        messagebox.showwarning(APP_NAME, "A hotkey could not be registered:\n\n" + txt.replace(";  ", "\n") +
                               "\n\nPick another one under File → Hotkeys.", parent=self)

    def show_hotkeys(self):
        HotkeyDialog(self)

    def toggle_startup(self):
        try:
            set_startup(self.startup_var.get())
            self.set_status("SnapPatch will start with Windows." if self.startup_var.get()
                            else "SnapPatch will no longer start with Windows.")
        except Exception as ex:
            log.exception("startup toggle failed")
            messagebox.showerror(APP_NAME, f"Could not change the start-up setting:\n{ex}", parent=self)

    # --- ToolRoom help images ---------------------------------------------------------
    def new_help_canvas(self, preset):
        w, h, fmt = HELP_PRESETS[preset]
        if self.img is not None and self.dirty:
            if not messagebox.askyesno(APP_NAME, "Discard the current image and start a new help canvas?", parent=self):
                return
        self.load_image(Image.new("RGB", (w, h), HELP_BG))
        self.help_preset = preset
        self.set_ann_color(HELP_COLOURS[0][1])
        self.line_width.set(2)
        self.font_size.set(14)
        self.tool.set("arrow")
        self.zoom_100()
        self.schedule_context()
        self.set_status(f"Help canvas {w} × {h} (green). Paste the CIM3D view as a layer (Ctrl+V), "
                        "then draw with the guideline colours. Ctrl+Shift+E exports.")

    def match_help_background(self, piece, tol=40):
        """A CIM3D screenshot pasted onto a help canvas: turn its own background (the most common colour
        along its border – CIM3D's grey or its slightly different green) into the exact guideline green,
        so the pasted rectangle disappears into the canvas."""
        if np is None:
            return piece
        rgb = piece.convert("RGB")
        a = np.asarray(rgb).astype(np.int16)
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        cols, counts = np.unique(border, axis=0, return_counts=True)
        ref = cols[counts.argmax()]
        if counts.max() < 0.3 * len(border):          # no dominant background colour – leave it alone
            return piece
        if tuple(int(v) for v in ref) == HELP_BG:
            return piece
        mask = np.abs(a - ref).max(axis=2) <= tol
        a[mask] = HELP_BG
        out = Image.fromarray(a.astype(np.uint8), "RGB")
        if piece.mode == "RGBA":
            out.putalpha(piece.split()[3])
        self.set_status(f"Pasted image: background RGB{tuple(int(v) for v in ref)} changed to the guideline green "
                        f"({int(mask.sum()):,} px). Undo with Ctrl+Z if that was wrong.")
        return out

    def new_blank_canvas(self):
        """A plain worksheet (white / grey / green / custom) to arrange several captures on."""
        if self.img is not None and self.dirty and not messagebox.askyesno(
                APP_NAME, "Discard the current image and start a new worksheet?", parent=self):
            return
        dlg = tk.Toplevel(self)
        dlg.title("New worksheet")
        dlg.transient(self)
        dlg.resizable(False, False)
        f = ttk.Frame(dlg, padding=14)
        f.pack()
        ttk.Label(f, text="Size").grid(row=0, column=0, sticky="w", pady=4)
        sizes = ["1920 × 1080  (Full HD)", "1600 × 900", "1280 × 720", "1024 × 768", "800 × 600", "A4 landscape 1754 × 1240",
                 "A4 portrait 1240 × 1754", "Custom"]
        sv = tk.StringVar(value=self.settings.get("ws_size", sizes[0]))
        ttk.Combobox(f, values=sizes, textvariable=sv, width=28, state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Custom W × H").grid(row=1, column=0, sticky="w", pady=4)
        cw, ch = tk.StringVar(value="1400"), tk.StringVar(value="900")
        cf = ttk.Frame(f)
        cf.grid(row=1, column=1, sticky="w")
        ttk.Entry(cf, textvariable=cw, width=6).pack(side="left")
        ttk.Label(cf, text=" × ").pack(side="left")
        ttk.Entry(cf, textvariable=ch, width=6).pack(side="left")
        ttk.Label(f, text="Background").grid(row=2, column=0, sticky="w", pady=4)
        bgs = {"White": "#ffffff", "Light grey": "#f0f0f0", "Dark": "#2b2b2b", "Help-image green": "#004b2c"}
        bv = tk.StringVar(value=self.settings.get("ws_bg", "White"))
        ttk.Combobox(f, values=list(bgs), textvariable=bv, width=28, state="readonly").grid(row=2, column=1, sticky="w")
        result = {}

        def ok():
            try:
                if sv.get() == "Custom":
                    w, h = int(cw.get()), int(ch.get())
                else:
                    nums = [int(s) for s in sv.get().replace("×", " ").split() if s.isdigit()]
                    w, h = nums[-2], nums[-1]
                if not (16 <= w <= 20000 and 16 <= h <= 20000):
                    raise ValueError
            except (ValueError, IndexError):
                messagebox.showerror(APP_NAME, "Please enter a width and height between 16 and 20000.", parent=dlg)
                return
            result.update(w=w, h=h, bg=bgs[bv.get()])
            self.settings.update(ws_size=sv.get(), ws_bg=bv.get())
            save_settings(self.settings)
            dlg.destroy()

        bf = ttk.Frame(f)
        bf.grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="e")
        ttk.Button(bf, text="Create", style="Accent.TButton", command=ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="left", padx=4)
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.geometry(f"+{self.winfo_rootx() + 200}+{self.winfo_rooty() + 150}")
        dlg.grab_set()
        self.wait_window(dlg)
        if not result:
            return
        self.load_image(Image.new("RGB", (result["w"], result["h"]), result["bg"]))
        self.tool.set("select")
        self.set_status("Blank worksheet. Ctrl+V pastes captures as layers; drag them around, resize with corners, "
                        "Enter to apply. Right-click a History thumbnail → 'Paste into current image as layer' also works.")

    def choose_symbol_dir(self):
        p = filedialog.askdirectory(parent=self, title="Folder with symbol images (PNG / BMP)")
        if p:
            self.settings["symbol_dir"] = p
            save_settings(self.settings)
            self.set_status(f"Symbol folder set: {p} – the Symbol tool now lists its images.")

    def insert_symbol_file(self, path, x, y):
        try:
            im = Image.open(path)
            im.load()
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open {path}:\n{ex}", parent=self)
            return
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        self.sel = (x - im.width // 2, y - im.height // 2, x + im.width // 2, y + im.height // 2)
        self.add_layer(im)

    def start_replace_background(self):
        if not self.need_img():
            return
        self.pending_pick = "helpbg"
        self.set_status("Click a pixel of the background you want replaced with the guideline green.")

    def replace_background(self, x, y, tol=38):
        """Replace every pixel similar to the clicked one with the guideline green (CIM3D grey -> green)."""
        if np is None:
            messagebox.showerror(APP_NAME, "numpy is needed for this.", parent=self)
            return
        if not (0 <= x < self.img.width and 0 <= y < self.img.height):
            return
        self.commit_float()
        self.push_undo()
        a = np.asarray(self.img).astype(np.int16)
        ref = a[y, x]
        mask = np.abs(a - ref).max(axis=2) <= tol
        a[mask] = HELP_BG
        self.img = Image.fromarray(a.astype(np.uint8), "RGB")
        self.render()
        self.set_status(f"Replaced {int(mask.sum()):,} background pixels with green (undo with Ctrl+Z).")

    def fit_to_help_preset(self, preset):
        """Scale the whole image (and its annotations) to fit a help canvas, centred on green."""
        if not self.need_img():
            return
        w, h, fmt = HELP_PRESETS[preset]
        self.commit_float()
        self.push_undo()
        iw, ih = self.img.size
        f = min(w / iw, h / ih)
        nw, nh = max(1, round(iw * f)), max(1, round(ih * f))
        canvas = Image.new("RGB", (w, h), HELP_BG)
        ox, oy = (w - nw) // 2, (h - nh) // 2
        canvas.paste(self.img.resize((nw, nh), Image.LANCZOS), (ox, oy))
        self.img = canvas
        for o in self.objects:
            if o["type"] == "pen":
                o["pts"] = [(px * f + ox, py * f + oy) for px, py in o["pts"]]
            else:
                for k in ("x0", "x1"):
                    if k in o:
                        o[k] = o[k] * f + ox
                for k in ("y0", "y1"):
                    if k in o:
                        o[k] = o[k] * f + oy
            for k in ("width", "size", "w"):
                if o.get(k):
                    o[k] = max(1, int(round(o[k] * f)))
        self.help_preset = preset
        self.sel = None
        self.zoom_100()
        self.schedule_context()
        self.set_status(f"Fitted onto {w} × {h} help canvas (scale {f:.2f}).")

    def check_help_guidelines(self, show=False):
        """Return a list of guideline problems with the current image (empty = fine)."""
        if self.img is None:
            return ["No image."]
        preset = getattr(self, "help_preset", None) or list(HELP_PRESETS)[0]
        w, h, fmt = HELP_PRESETS[preset]
        issues = []
        if self.img.size != (w, h):
            issues.append(f"Size is {self.img.width} × {self.img.height}; the guideline is {w} × {h} "
                          f"(Help image → Fit current image onto a help canvas).")
        allowed = {c.lower() for _, c in HELP_COLOURS}
        for o in self.objects:
            t = o["type"]
            if t in ("text", "callout"):
                if any(ch not in HELP_TEXT_OK for ch in o.get("text", "")):
                    issues.append(f"Text '{o['text'][:30]}' – help images must not contain words "
                                  "(numbers and +/- are fine) so they work in every language.")
            elif t == "step":
                issues.append("Step numbers are text-like; guideline images use arrows and symbols only.")
            if t in ("arrow", "darrow", "line", "pen", "rect", "rrect", "ellipse", "star", "triangle", "diamond") \
                    and o.get("color", "").lower() not in allowed:
                issues.append(f"{t} drawn in {o.get('color')} – guideline colours are orange (arrows/dimensions), "
                              "purple (reference lines) and black (axes).")
        if np is not None:
            a = np.asarray(self.img)
            edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
            if (np.abs(edge.astype(np.int16) - np.array(HELP_BG)).max(axis=1) > 30).mean() > 0.5:
                issues.append("Background around the edge is not the guideline green RGB(0,75,44) "
                              "(Help image → Replace background).")
        if show:
            messagebox.showinfo(APP_NAME, "Meets the ToolRoom help-image guidelines." if not issues else
                                "Guideline check:\n\n• " + "\n• ".join(issues), parent=self)
        return issues

    def export_help_image(self):
        if not self.need_img():
            return
        issues = self.check_help_guidelines()
        if issues and not messagebox.askyesno(APP_NAME, "Guideline check:\n\n• " + "\n• ".join(issues) +
                                              "\n\nExport anyway?", parent=self):
            return
        preset = getattr(self, "help_preset", None) or list(HELP_PRESETS)[0]
        w, h, fmt = HELP_PRESETS[preset]
        out = self.flattened()
        if out.size != (w, h):
            f = min(w / out.width, h / out.height)
            scaled = out.resize((max(1, round(out.width * f)), max(1, round(out.height * f))), Image.LANCZOS)
            canvas = Image.new("RGB", (w, h), HELP_BG)
            canvas.paste(scaled, ((w - scaled.width) // 2, (h - scaled.height) // 2))
            out = canvas
        base = Path(self.path).stem if self.path else "help_image"
        p = filedialog.asksaveasfilename(parent=self, defaultextension="." + fmt, initialfile=f"{base}.{fmt}",
                                         filetypes=[("PNG", "*.png")] if fmt == "png" else [("JPEG", "*.jpg")])
        if not p:
            return
        try:
            if fmt == "jpg":
                out.save(p, quality=92)
            else:
                out.save(p, optimize=True)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not save:\n{ex}", parent=self)
            return
        self.set_status(f"Help image exported: {p}  ({w} × {h} {fmt.upper()})")

    def show_diagnostics(self):
        lines = [f"{APP_NAME} {APP_VERSION}  –  {os.path.abspath(sys.argv[0])}",
                 f"Python: {sys.version.split()[0]}  ({sys.executable})",
                 f"Pillow: {getattr(Image, '__version__', '?')}",
                 f"numpy: {np.__version__ if np is not None else 'MISSING – edge auto-detect is off'}",
                 f"Virtual screen origin: {virtual_screen_origin()}"]
        try:
            shot = grab_screen()
            lines.append(f"Screen capture: {shot.width} × {shot.height}")
            wins = list_window_rects()
            lines.append(f"Windows found: {len(wins)}")
            if np is not None:
                t0 = time.time()
                det = SmartDetector(shot)
                cx, cy = shot.width // 2, shot.height // 2
                boxes = det.boxes_at(cx, cy)
                lines.append(f"Edge detector: built in {(time.time() - t0) * 1000:.0f} ms, "
                             f"{len(boxes)} areas at screen centre")
        except Exception as ex:
            log.exception("Diagnostics failed")
            lines.append(f"ERROR: {ex.__class__.__name__}: {ex}")
        lines.append(f"Log file: {LOG_FILE}")
        log.info("Diagnostics:\n%s", "\n".join(lines))
        messagebox.showinfo(f"{APP_NAME} diagnostics", "\n".join(lines), parent=self)

    def toggle_library(self):
        if self.show_library.get():
            self.library.pack(fill="x", side="top", after=self.body)
        else:
            self.library.pack_forget()

    def bind_keys(self):
        k = self.bind
        k("<Control-n>", lambda e: self.capture())
        k("<Control-o>", lambda e: self.open_dialog())
        k("<Control-s>", lambda e: self.save())
        k("<Control-S>", lambda e: self.save_as())
        k("<Control-c>", lambda e: None if self.typing(e) else self.copy())
        k("<Control-v>", lambda e: None if self.typing(e) else self.paste_layer())
        k("<Control-V>", lambda e: self.paste_as_new())
        k("<Control-d>", lambda e: self.duplicate())
        k("<Control-z>", lambda e: self.undo())
        k("<Control-y>", lambda e: self.redo())
        k("<Control-a>", lambda e: None if self.typing(e) else self.select_all())
        k("<Control-equal>", lambda e: self.zoom_in())
        k("<Control-plus>", lambda e: self.zoom_in())
        k("<Control-minus>", lambda e: self.zoom_out())
        k("<Control-0>", lambda e: self.zoom_fit())
        k("<Control-1>", lambda e: self.zoom_100())
        k("<Delete>", lambda e: None if self.typing(e) else self.delete_key())
        k("<Return>", lambda e: None if self.typing(e) else self.commit_float())
        k("<Escape>", lambda e: self.escape())
        k("<Control-E>", lambda e: self.export_help_image())
        k("<Control-N>", lambda e: self.new_blank_canvas())
        k("<Control-bracketleft>", lambda e: self.rotate_selected(-5))
        k("<Control-bracketright>", lambda e: self.rotate_selected(5))
        for key, dx, dy in [("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)]:
            k(f"<{key}>", lambda e, dx=dx, dy=dy: None if self.typing(e) else self.nudge(dx, dy))
            k(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: None if self.typing(e) else self.nudge(dx * 10, dy * 10))
        for key, _, _, sc, _ in TOOL_LIST:
            k(f"<KeyPress-{sc}>", lambda e, t=key: None if self.typing(e) else self.tool.set(t))

    @staticmethod
    def typing(e):
        return isinstance(e.widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox, ttk.Combobox))

    # ========================================================= context bar ===
    def schedule_context(self):
        if self._ctx_job is None:
            self._ctx_job = self.after_idle(self.update_context)

    def update_context(self):
        self._ctx_job = None
        for w in self.ctx.winfo_children():
            w.destroy()
        c = self.ctx

        def lbl(text, bold=False):
            ttk.Label(c, text=text, style="Ctx.TLabel",
                      font=("Segoe UI", 10, "bold") if bold else UI_FONT).pack(side="left", padx=(0, 8))

        def btn(text, cmd):
            ttk.Button(c, text=text, command=cmd).pack(side="left", padx=2)

        def colours():
            ttk.Label(c, text="Colour", style="Ctx.TLabel").pack(side="left")
            self.ann_swatch = tk.Button(c, width=3, bg=self.ann_color, activebackground=self.ann_color,
                                        relief="sunken", bd=2, command=self.choose_ann_color, cursor="hand2")
            self.ann_swatch.pack(side="left", padx=(4, 6))
            for col in PALETTE:
                tk.Button(c, width=2, bg=col, activebackground=col, relief="flat", bd=0, cursor="hand2",
                          highlightthickness=1, highlightbackground="#999",
                          command=lambda col=col: self.set_ann_color(col)).pack(side="left", padx=1, ipady=1)

        def spin(text, var, lo, hi):
            ttk.Label(c, text=text, style="Ctx.TLabel").pack(side="left", padx=(14, 4))
            ttk.Spinbox(c, from_=lo, to=hi, width=4, textvariable=var).pack(side="left")

        def fill_opts():
            ttk.Checkbutton(c, text="Auto fill colour", variable=self.auto_fill,
                            style="Ctx.TCheckbutton").pack(side="left", padx=(14, 4))
            self.fill_swatch = tk.Button(c, width=3, bg=self.rgb_hex(self.fill_color), relief="groove",
                                         command=self.choose_fill_color, cursor="hand2")
            self.fill_swatch.pack(side="left")

        if getattr(self, "help_preset", None):
            # guideline colour strip – always visible while working on a help image
            w, h, fmt = HELP_PRESETS[self.help_preset]
            ttk.Label(c, text=f"Help image {w}×{h}:", style="Ctx.TLabel",
                      font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
            for name, col in HELP_COLOURS:
                b = tk.Button(c, text=name, bg=col, fg="white", activebackground=col, relief="flat", bd=0,
                              padx=6, cursor="hand2", font=("Segoe UI", 8), command=lambda col=col: self.set_ann_color(col))
                b.pack(side="left", padx=1)
            ttk.Button(c, text="Check", width=6, command=lambda: self.check_help_guidelines(show=True)).pack(side="left", padx=(6, 0))
            ttk.Button(c, text="Export", width=7, command=self.export_help_image).pack(side="left", padx=2)
            ttk.Separator(c, orient="vertical").pack(side="left", fill="y", padx=8)

        def rotate_controls(get_angle, set_angle):
            ttk.Label(c, text="Rotate", style="Ctx.TLabel").pack(side="left", padx=(12, 2))
            ttk.Button(c, text="↺", width=3, command=lambda: set_angle(get_angle() - 15)).pack(side="left")
            var = tk.StringVar(value=str(int(get_angle())))
            sp = ttk.Spinbox(c, from_=-180, to=360, increment=5, width=5, textvariable=var,
                             command=lambda: self._spin_angle(var, set_angle))
            sp.pack(side="left", padx=2)
            sp.bind("<Return>", lambda e: self._spin_angle(var, set_angle))
            ttk.Button(c, text="↻", width=3, command=lambda: set_angle(get_angle() + 15)).pack(side="left")
            ttk.Label(c, text="°", style="Ctx.TLabel").pack(side="left", padx=(0, 6))

        t = self.tool.get()
        if self.float:
            fw, fh = self.float["size"]
            ow, oh = self.float["orig"].size
            lbl(f"Pasted piece {fw} × {fh}:", True)
            lbl("drag to move • corners resize (Shift = free) • Ctrl+[ ] rotate")
            ttk.Label(c, text="Scale %", style="Ctx.TLabel").pack(side="left", padx=(6, 4))
            self.float_scale = tk.StringVar(value=str(round(fw / ow * 100)))
            sp = ttk.Spinbox(c, from_=5, to=800, width=5, textvariable=self.float_scale,
                             command=self.apply_float_scale)
            sp.pack(side="left")
            sp.bind("<Return>", lambda e: self.apply_float_scale())
            rotate_controls(lambda: self.float["angle"], lambda a: self.rotate_float(absolute=a))
            if (fw, fh) != (ow, oh) or self.float["angle"] % 360:
                btn("Reset size / angle", self.reset_float_size)
            btn("✔ Apply  (Enter)", self.commit_float)
            btn("✖ Cancel  (Esc)", self.cancel_float)
        elif self.sel_obj is not None:
            o = self.sel_obj
            lbl(f"{o['type'].capitalize()}:", True)
            if o["type"] not in ("highlight", "blur", "spotlight"):
                colours()
            if "width" in o and o["type"] not in ("text", "blur", "step", "spotlight"):
                spin("Width", self.line_width, 1, 30)
            if "size" in o:
                spin("Size", self.font_size, 6, 120)
            if o["type"] in ROTATABLE:
                rotate_controls(lambda: o.get("angle", 0), lambda a: self.rotate_object(absolute=a))
            if o["type"] in ("star", "triangle", "diamond", "ellipse", "rect", "rrect"):
                fv = tk.BooleanVar(value=bool(o.get("fill")))
                ttk.Checkbutton(c, text="Filled", variable=fv, style="Ctx.TCheckbutton",
                                command=lambda: self.set_obj_prop("fill", fv.get())).pack(side="left", padx=(8, 0))
            if o["type"] in DASHABLE:
                dv = tk.BooleanVar(value=bool(o.get("dash")))
                ttk.Checkbutton(c, text="Dashed", variable=dv, style="Ctx.TCheckbutton",
                                command=lambda: self.set_obj_prop("dash", dv.get())).pack(side="left", padx=(8, 0))
            if o["type"] in ("line", "arrow", "darrow", "pen"):
                mv = tk.BooleanVar(value=bool(o.get("midarrow")))
                ttk.Checkbutton(c, text="Arrows along path", variable=mv, style="Ctx.TCheckbutton",
                                command=lambda: self.set_obj_prop("midarrow", mv.get())).pack(side="left", padx=(8, 0))
            if o["type"] == "arc":
                for key, label in (("start", "Start °"), ("end", "End °")):
                    ttk.Label(c, text=label, style="Ctx.TLabel").pack(side="left", padx=(8, 2))
                    av = tk.StringVar(value=str(int(o.get(key, 0 if key == "start" else 90))))
                    sb = ttk.Spinbox(c, from_=-360, to=720, increment=5, width=5, textvariable=av,
                                     command=lambda k=key, v=av: self._set_num_prop(k, v))
                    sb.pack(side="left")
                    sb.bind("<Return>", lambda e, k=key, v=av: self._set_num_prop(k, v))
                hv = tk.BooleanVar(value=bool(o.get("heads", True)))
                ttk.Checkbutton(c, text="Arrowheads", variable=hv, style="Ctx.TCheckbutton",
                                command=lambda: self.set_obj_prop("heads", hv.get())).pack(side="left", padx=(8, 0))
            if o["type"] == "symbol":
                ttk.Label(c, text="Symbol", style="Ctx.TLabel").pack(side="left", padx=(8, 2))
                names = [n for _, n in SYMBOLS]
                sv = tk.StringVar(value=dict(SYMBOLS).get(o.get("kind"), names[0]))
                cb = ttk.Combobox(c, values=names, textvariable=sv, width=26, state="readonly")
                cb.pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda e: self.set_obj_prop(
                    "kind", next(k for k, n in SYMBOLS if n == sv.get())))
            ttk.Frame(c, width=16, style="Ctx.TFrame").pack(side="left")
            if o["type"] in ("text", "callout"):
                btn("Edit text", lambda: self.edit_text(o))
            btn("Duplicate", self.duplicate)
            btn("Bring to front", self.obj_to_front)
            btn("Delete", self.delete_object)
        elif self.sel and self.img is not None:
            x0, y0, x1, y1 = self.sel
            lbl(f"Selection {x1 - x0} × {y1 - y0}:", True)
            btn("Copy as layer", self.duplicate)
            btn("Copy to clipboard", self.copy)
            btn("Crop image to this", self.crop_selection)
            btn("Erase", self.erase_selection)
            btn("Cut out rows ↕", self.cut_rows)
            btn("Cut out columns ↔", self.cut_cols)
            fill_opts()
        elif t in ANNOTATION_TOOLS:
            if t not in ("highlight", "blur", "spotlight"):
                colours()
            if t in ("rect", "rrect", "frect", "ellipse", "star", "triangle", "diamond", "arc", "arrow", "darrow",
                     "line", "pen", "callout"):
                spin("Width", self.line_width, 1, 30)
            if t in DASHABLE:
                ttk.Checkbutton(c, text="Dashed", variable=self.draw_dashed, style="Ctx.TCheckbutton"
                                ).pack(side="left", padx=(8, 0))
            if t in ("line", "arrow", "darrow", "pen"):
                ttk.Checkbutton(c, text="Arrows along path", variable=self.draw_midarrow,
                                style="Ctx.TCheckbutton").pack(side="left", padx=(8, 0))
            if t in ("text", "callout", "step"):
                spin("Size", self.font_size, 6, 120)
            lbl("    " + next(tip for k, _, _, _, tip in TOOL_LIST if k == t).split("\n")[0])
        elif t in ("erase", "move", "pick"):
            lbl(next(tip for k, _, _, _, tip in TOOL_LIST if k == t).replace("\n", " "))
            fill_opts()
        else:
            lbl(next(tip for k, _, _, _, tip in TOOL_LIST if k == t).replace("\n", " "))

    # ============================================================= helpers ===
    @staticmethod
    def rgb_hex(c):
        return "#%02x%02x%02x" % tuple(c[:3])

    def set_status(self, text):
        self.status.set(text)

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.title(f"{'● ' if self.dirty else ''}{name} – {APP_NAME} {APP_VERSION}")

    def show_placeholder(self):
        self.cv.delete("ph")
        self.cv.itemconfigure(self.img_item, image="")
        w, h = max(400, self.cv.winfo_width()), max(300, self.cv.winfo_height())
        self.cv.create_text(w // 2, h // 2 - 30, fill="white", font=("Segoe UI", 18, "bold"), tags="ph",
                            text=f"Press {HOTKEY_TEXT} or click  ◉ Capture")
        self.cv.create_text(w - 12, h - 10, anchor="se", fill="#8a8f94", font=("Segoe UI", 9), tags="ph",
                            text=f"{APP_NAME} {APP_VERSION}")
        self.cv.create_text(w // 2, h // 2 + 64, fill="#9fb3c8", font=("Segoe UI", 10), tags="ph",
                            text=f"{RECORD_HOTKEY_TEXT} records a video   •   change hotkeys under File → Hotkeys")
        self.cv.create_text(w // 2, h // 2 + 14, fill="#d0d0d0", font=("Segoe UI", 11), tags="ph",
                            text="Hover to auto-detect a window, panel or button and click it – or drag any area.\n"
                                 "You can also open a file, pick one from the History strip, or paste (Ctrl+Shift+V).",
                            justify="center")

    def need_img(self):
        if self.img is None:
            self.set_status("Capture or open an image first.")
            return False
        return True

    def need_sel(self):
        if not self.need_img():
            return False
        if self.float:
            self.commit_float()
        if not self.sel:
            self.set_status("Select an area first (Select tool, drag on the image).")
            return False
        return True

    def snapshot(self):
        return self.img.copy(), copy.deepcopy(self.objects)

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True
        self.update_title()

    def restore(self, state):
        self.img, self.objects = state
        self.sel_obj = None
        self.sel = None
        self.schedule_context()

    def to_img(self, e):
        z = self.zoom
        return (int((self.cv.canvasx(e.x) - self.off[0]) // z), int((self.cv.canvasy(e.y) - self.off[1]) // z))

    def cxy(self, x, y):
        return x * self.zoom + self.off[0], y * self.zoom + self.off[1]

    def clamp_box(self, box):
        W, H = self.img.size
        x0, y0, x1, y1 = box
        return max(0, x0), max(0, y0), min(W, x1), min(H, y1)

    def float_box(self):
        f = self.float
        return f["x"], f["y"], f["x"] + f["img"].width, f["y"] + f["img"].height

    def set_float(self, piece, x, y):
        self.float = {"img": piece, "orig": piece.copy(), "size": piece.size, "angle": 0, "x": int(x), "y": int(y)}

    def rebuild_float(self, final=False):
        """Recreate the floating piece from the original + size + angle, keeping its centre in place."""
        f = self.float
        cx = f["x"] + f["img"].width / 2
        cy = f["y"] + f["img"].height / 2
        img = f["orig"]
        if f["size"] != img.size:
            img = img.resize(f["size"], Image.LANCZOS if final else Image.BILINEAR)
        if f["angle"] % 360:
            img = img.convert("RGBA").rotate(-f["angle"], resample=Image.BICUBIC, expand=True)
        f["img"] = img
        f["x"], f["y"] = int(round(cx - img.width / 2)), int(round(cy - img.height / 2))

    def rotate_float(self, delta=None, absolute=None):
        if not self.float:
            return
        self.float["angle"] = (absolute if absolute is not None else self.float["angle"] + delta) % 360
        self.rebuild_float(final=True)
        self.render()
        self.schedule_context()

    def float_handles(self):
        x0, y0, x1, y1 = self.float_box()
        return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]

    def hit_float_handle(self, x, y):
        if not self.float:
            return None
        tol = max(5, 9 / self.zoom)
        best, best_d = None, None
        for i, (hx, hy) in enumerate(self.float_handles()):
            d = max(abs(hx - x), abs(hy - y))
            if d <= tol and (best_d is None or d < best_d):
                best, best_d = i, d
        return best

    def resize_float(self, w, h, final=False):
        f = self.float
        w, h = max(4, int(w)), max(4, int(h))
        if (w, h) == f["size"] and not final:
            return
        f["size"] = (w, h)
        if f["angle"] % 360:
            self.rebuild_float(final)
        else:
            f["img"] = f["orig"].resize((w, h), Image.LANCZOS if final else Image.BILINEAR)

    def apply_float_scale(self):
        if not self.float:
            return
        try:
            pct = float(self.float_scale.get())
        except (ValueError, tk.TclError):
            return
        ow, oh = self.float["orig"].size
        self.resize_float(ow * pct / 100, oh * pct / 100, final=True)
        self.render()

    def reset_float_size(self):
        if self.float:
            self.float["size"] = self.float["orig"].size
            self.float["angle"] = 0
            self.rebuild_float(final=True)
            self.render()
            self.schedule_context()

    def edge_color(self, box):
        x0, y0, x1, y1 = box
        W, H = self.img.size
        px = self.img.load()
        cnt = Counter()
        for x in range(max(0, x0 - 1), min(W, x1 + 1)):
            for y in (y0 - 1, y1):
                if 0 <= y < H:
                    cnt[px[x, y]] += 1
        for y in range(max(0, y0 - 1), min(H, y1 + 1)):
            for x in (x0 - 1, x1):
                if 0 <= x < W:
                    cnt[px[x, y]] += 1
        return cnt.most_common(1)[0][0] if cnt else self.fill_color

    def fill_for(self, box):
        return self.edge_color(box) if self.auto_fill.get() else self.fill_color

    def fill_box(self, box):
        """Erase a box. Auto mode: smart fill – continues the surrounding pixels row-by-row / column-by-column
        (handles stripes, gradients and table rows) and falls back to the dominant edge colour."""
        x0, y0, x1, y1 = box
        if not self.auto_fill.get() or np is None or x1 - x0 < 1 or y1 - y0 < 1:
            ImageDraw.Draw(self.img).rectangle((x0, y0, x1 - 1, y1 - 1), fill=self.fill_for(box))
            return
        W, H = self.img.size
        a = np.asarray(self.img).astype(np.int16)
        left = a[y0:y1, x0 - 1] if x0 > 0 else None
        right = a[y0:y1, x1] if x1 < W else None
        top = a[y0 - 1, x0:x1] if y0 > 0 else None
        bottom = a[y1, x0:x1] if y1 < H else None
        hv = np.abs(left - right).mean() if left is not None and right is not None else (0 if left is not None or right is not None else 1e9)
        vv = np.abs(top - bottom).mean() if top is not None and bottom is not None else (0 if top is not None or bottom is not None else 1e9)
        if hv == 1e9 and vv == 1e9:
            ImageDraw.Draw(self.img).rectangle((x0, y0, x1 - 1, y1 - 1), fill=self.fill_for(box))
            return
        patch = a[y0:y1, x0:x1].copy()
        if hv <= vv:
            l = left if left is not None else right
            r = right if right is not None else left
            t = np.linspace(0, 1, x1 - x0, dtype=np.float32)[None, :, None]
            patch = (l[:, None, :] * (1 - t) + r[:, None, :] * t)
        else:
            tp = top if top is not None else bottom
            bt = bottom if bottom is not None else top
            t = np.linspace(0, 1, y1 - y0, dtype=np.float32)[:, None, None]
            patch = (tp[None, :, :] * (1 - t) + bt[None, :, :] * t)
        self.img.paste(Image.fromarray(np.clip(patch, 0, 255).astype(np.uint8), "RGB"), (x0, y0))

    def detector(self):
        """Edge detector for the current image (built lazily, rebuilt when the image changes)."""
        if np is None:
            return None
        key = (id(self.img), self.img.size)
        if getattr(self, "_det_key", None) != key:
            try:
                self._det = SmartDetector(self.img)
                self._det_key = key
            except Exception:
                log.exception("detector failed")
                self._det = None
        return self._det

    def element_at(self, x, y):
        """Smallest UI element (field / button / panel) under the point, or None."""
        d = self.detector()
        if d is None:
            return None
        boxes = [b for b in d.boxes_at(x, y) if b[2] - b[0] >= 6 and b[3] - b[1] >= 6]
        return boxes[0] if boxes else None

    def shift_objects(self, dx, dy, pred=lambda o: True):
        for o in self.objects:
            if pred(o):
                move_obj(o, dx, dy)

    # =========================================================== rendering ===
    def composite(self):
        if not self.float:
            return self.img
        out = self.img.copy()
        f = self.float["img"]
        out.paste(f, (self.float["x"], self.float["y"]), f if f.mode == "RGBA" else None)
        return out

    def flattened(self):
        self.commit_float()
        return draw_objects(self.img, self.objects)

    def request_render(self, fast=False):
        """Coalesce redraws (max ~60/s). fast=True only during drags."""
        self._render_fast = self._render_fast and fast if self._render_job else fast
        if self._render_job is None:
            self._render_job = self.after(14, self._do_render)

    def _do_render(self):
        self._render_job = None
        self.render(fast=self._render_fast)

    def render(self, fast=False):
        if self.img is None:
            self.show_placeholder()
            return
        self.cv.delete("ph")
        if fast and self._drag_base is not None:
            if self.drag_mode in ("float", "float_resize", "rotate_float") and self.float:
                flat = self._drag_base.copy()
                f = self.float["img"]
                flat.paste(f, (self.float["x"], self.float["y"]), f if f.mode == "RGBA" else None)
                flat = draw_objects(flat, self.objects)
            elif self.sel_obj is not None:
                flat = draw_objects(self._drag_base, [self.sel_obj])
            else:
                flat = draw_objects(self.composite(), self.objects)
        else:
            flat = draw_objects(self.composite(), self.objects)
        self.flat = flat
        self.layout()
        self.blit(fast)
        self.draw_overlays()
        self.update_info()

    def layout(self):
        if self.flat is None:
            return
        z = self.zoom
        dw, dh = round(self.flat.width * z), round(self.flat.height * z)
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        self.off = (max(0, (cw - dw) // 2), max(0, (ch - dh) // 2))
        self.cv.config(scrollregion=(0, 0, max(dw, cw), max(dh, ch)))
        self.zoom_lbl.config(text=f"{round(z * 100)}%")

    def blit(self, fast=False):
        """Draw only the visible part of the image (fast at any zoom / image size)."""
        if self.flat is None:
            return
        z = self.zoom
        W, H = self.flat.size
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        vx, vy = self.cv.canvasx(0) - self.off[0], self.cv.canvasy(0) - self.off[1]
        ix0, iy0 = max(0, int(vx / z)), max(0, int(vy / z))
        ix1, iy1 = min(W, int((vx + cw) / z) + 2), min(H, int((vy + ch) / z) + 2)
        if ix1 <= ix0 or iy1 <= iy0:
            return
        region = self.flat.crop((ix0, iy0, ix1, iy1))
        if z != 1:
            size = (max(1, round((ix1 - ix0) * z)), max(1, round((iy1 - iy0) * z)))
            region = region.resize(size, Image.NEAREST if z > 1 else (Image.BILINEAR if fast else Image.LANCZOS))
        self.photo = ImageTk.PhotoImage(region)
        self.cv.itemconfigure(self.img_item, image=self.photo)
        self.cv.coords(self.img_item, ix0 * z + self.off[0], iy0 * z + self.off[1])
        self.cv.tag_lower(self.img_item)

    def schedule_blit(self):
        if self._blit_job is None:
            self._blit_job = self.after(10, self._do_blit)

    def _do_blit(self):
        self._blit_job = None
        self.blit()

    def schedule_layout(self):
        if self.flat is not None:
            self.layout()
            self.schedule_blit()
            self.draw_overlays()
        elif self.img is None:
            self.show_placeholder()

    def xview(self, *a):
        self.cv.xview(*a)
        self.schedule_blit()

    def yview(self, *a):
        self.cv.yview(*a)
        self.schedule_blit()

    ROT_HANDLE_DIST = 26     # screen px above the selection box

    def rotate_handle_pos(self, target):
        """Image coordinates of the rotate handle for 'float' or 'obj' (None if not applicable)."""
        if target == "float":
            if not self.float:
                return None
            l, t, r, b = self.float_box()
        else:
            if self.sel_obj is None or self.sel_obj["type"] not in ROTATABLE:
                return None
            l, t, r, b = obj_bbox(self.sel_obj)
        return (l + r) / 2, t - self.ROT_HANDLE_DIST / self.zoom

    def hit_rotate_handle(self, x, y):
        tol = max(6, 10 / self.zoom)
        for target in ("float", "obj"):
            p = self.rotate_handle_pos(target)
            if p is not None and abs(p[0] - x) <= tol and abs(p[1] - y) <= tol:
                return target
        return None

    def draw_rotate_handle(self, target, colour, tag):
        p = self.rotate_handle_pos(target)
        if p is None:
            return
        hx, hy = self.cxy(*p)
        self.cv.create_line(hx, hy + 7, hx, hy + self.ROT_HANDLE_DIST - 4, fill=colour, tags=tag)
        self.cv.create_oval(hx - 7, hy - 7, hx + 7, hy + 7, fill="white", outline=colour, width=2, tags=tag)
        self.cv.create_text(hx, hy, text="↻", fill=colour, font=("Segoe UI", 9, "bold"), tags=tag)

    def draw_overlays(self):
        self.cv.delete("sel", "flt", "objsel")
        if self.sel:
            x0, y0 = self.cxy(*self.sel[:2])
            x1, y1 = self.cxy(*self.sel[2:])
            self.cv.create_rectangle(x0, y0, x1, y1, outline="white", tags="sel")
            self.cv.create_rectangle(x0, y0, x1, y1, outline=ACCENT, dash=(4, 3), tags="sel")
        if self.float:
            fb = self.float_box()
            x0, y0 = self.cxy(*fb[:2])
            x1, y1 = self.cxy(*fb[2:])
            self.cv.create_rectangle(x0 - 1, y0 - 1, x1, y1, outline="#ff8c00", dash=(5, 3), width=2, tags="flt")
            for hx, hy in self.float_handles():
                hx, hy = self.cxy(hx, hy)
                self.cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="white", outline="#ff8c00", width=2,
                                         tags="flt")
            fw, fh = self.float["img"].size
            ow, oh = self.float["orig"].size
            pct = f"  ({round(fw / ow * 100)}%)" if (fw, fh) != (ow, oh) else ""
            ang = f"  {self.float['angle']:.0f}°" if self.float["angle"] % 360 else ""
            self.cv.create_text(x1 + 6, y0, anchor="nw", fill="white", font=("Segoe UI", 9, "bold"), tags="flt",
                                text=f"{fw} × {fh}{pct}{ang}\ndrag = move\ncorners = resize\n↻ handle = rotate\n"
                                     "Enter = apply")
            self.draw_rotate_handle("float", "#ff8c00", "flt")
        if self.sel_obj is not None:
            l, t, r, b = obj_bbox(self.sel_obj)
            l, t = self.cxy(l, t)
            r, b = self.cxy(r, b)
            self.cv.create_rectangle(l - 3, t - 3, r + 3, b + 3, outline=ACCENT, dash=(3, 2), tags="objsel")
            for hx, hy in obj_handles(self.sel_obj):
                hx, hy = self.cxy(hx, hy)
                self.cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="white", outline=ACCENT, width=2,
                                         tags="objsel")
            self.draw_rotate_handle("obj", ACCENT, "objsel")
            if self.sel_obj.get("angle", 0) % 360:
                self.cv.create_text(r + 6, t, anchor="nw", fill="white", font=("Segoe UI", 9, "bold"),
                                    tags="objsel", text=f"{self.sel_obj['angle']:.0f}°")

    def update_info(self, pos=None):
        if self.img is None:
            self.info.set("")
            return
        parts = [f"{self.img.width} × {self.img.height} px"]
        if self.objects:
            parts.append(f"{len(self.objects)} annotation{'s' if len(self.objects) > 1 else ''}")
        if pos:
            x, y = pos
            if 0 <= x < self.img.width and 0 <= y < self.img.height:
                parts.append(f"x {x}  y {y}   {self.rgb_hex(self.img.getpixel((x, y)))}")
        self.info.set("     ".join(parts))

    # ================================================================ zoom ===
    def set_zoom(self, z, anchor=None):
        if self.img is None:
            return
        z = max(0.05, min(8.0, z))
        if anchor is None:
            anchor = (self.cv.winfo_width() / 2, self.cv.winfo_height() / 2)
        ax, ay = anchor
        ix = (self.cv.canvasx(ax) - self.off[0]) / self.zoom
        iy = (self.cv.canvasy(ay) - self.off[1]) / self.zoom
        self.zoom = z
        if self.flat is None:
            self.flat = draw_objects(self.composite(), self.objects)
        self.layout()
        sr = [float(v) for v in str(self.cv.cget("scrollregion")).split()]
        tw, th = sr[2] or 1, sr[3] or 1
        self.cv.xview_moveto(max(0, (ix * z + self.off[0] - ax) / tw))
        self.cv.yview_moveto(max(0, (iy * z + self.off[1] - ay) / th))
        self.blit()
        self.draw_overlays()

    def on_ctrl_wheel(self, e):
        self.set_zoom(self.zoom * (1.2 if e.delta > 0 else 1 / 1.2), (e.x, e.y))

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.25)

    def zoom_100(self):
        self.set_zoom(1.0)

    def zoom_fit(self):
        if not self.img:
            return
        self.update_idletasks()
        cw, ch = max(50, self.cv.winfo_width() - 16), max(50, self.cv.winfo_height() - 16)
        self.zoom = min(1.0, cw / self.img.width, ch / self.img.height)
        self.cv.xview_moveto(0)
        self.cv.yview_moveto(0)
        self.render()

    # =============================================================== mouse ===
    def on_tool_change(self):
        t = self.tool.get()
        if self.float and t not in ("select", "move"):
            self.commit_float()
        if self.sel_obj is not None and t not in ("select",) + ANNOTATION_TOOLS:
            self.sel_obj = None
            self.draw_overlays()
        self.cv.config(cursor="fleur" if t == "move" else "xterm" if t == "text" else "crosshair")
        self.set_status(next(tip for k, _, _, _, tip in TOOL_LIST if k == t).replace("\n", " "))
        self.schedule_context()

    def on_motion(self, e):
        if self.img is None:
            return
        x, y = self.to_img(e)
        self.update_info((x, y))
        # cursor feedback
        cur = None
        if self.hit_rotate_handle(x, y) is not None:
            cur = "exchange"
        elif self.float and self.hit_float_handle(x, y) is not None:
            cur = ("size_nw_se", "size_ne_sw", "size_ne_sw", "size_nw_se")[self.hit_float_handle(x, y)]
        elif self.float and inside(self.float_box(), x, y):
            cur = "fleur"
        elif self.sel_obj is not None and self.hit_handle(x, y) is not None:
            h = self.hit_handle(x, y)
            if self.sel_obj["type"] in ("text", "callout"):
                cur = ("size_nw_se", "size_ne_sw", "size_ne_sw", "size_nw_se", "size_we",
                       "size_we", "size_ns", "size_ns")[h]
            else:
                cur = "sizing"
        elif self.tool.get() not in ("crop", "erase", "pick", "move") and \
                hit_object(self.objects, x, y, max(3, 5 / self.zoom)) is not None:
            cur = "hand2"
        elif self.tool.get() == "move" and inside(self.sel, x, y):
            cur = "fleur"
        want = cur or ("fleur" if self.tool.get() == "move" else "xterm" if self.tool.get() == "text" else "crosshair")
        if self.cv.cget("cursor") != want:
            self.set_cursor(want)

    def set_cursor(self, name):
        for cand in (name, {"size_we": "sb_h_double_arrow", "size_ns": "sb_v_double_arrow"}.get(name, "sizing"),
                     "sizing", "crosshair"):
            try:
                self.cv.config(cursor=cand)
                return
            except tk.TclError:
                continue

    def hit_handle(self, x, y):
        if self.sel_obj is None:
            return None
        tol = max(5, 9 / self.zoom)
        best, best_d = None, None
        for i, (hx, hy) in enumerate(obj_handles(self.sel_obj)):
            d = max(abs(hx - x), abs(hy - y))
            if d <= tol and (best_d is None or d < best_d):
                best, best_d = i, d
        return best

    def select_object(self, o):
        changed = o is not self.sel_obj
        self.sel_obj = o
        if o is not None:
            self.sel = None
            self._syncing = True
            if "width" in o:
                self.line_width.set(o["width"])
            if "size" in o:
                self.font_size.set(o["size"])
            if o["type"] not in ("highlight", "blur", "spotlight"):
                self.ann_color = o.get("color", self.ann_color)
            self._syncing = False
            if o["type"] in ("text", "callout"):
                self.set_status("Drag inside to move • side handles = width (text wraps) • top/bottom = text size "
                                "• corners = both • double-click = edit text • Delete removes")
            else:
                self.set_status("Drag to move • handles resize • Delete removes • right-click for more")
        self.draw_overlays()
        if changed:
            self.schedule_context()

    def start_fast_drag(self):
        if self.drag_mode in ("float", "float_resize", "rotate_float"):
            self._drag_base = self.img
        elif self.sel_obj is not None:
            self._drag_base = draw_objects(self.composite(), [o for o in self.objects if o is not self.sel_obj])

    def on_press(self, e):
        if self.img is None:
            return
        self.cv.focus_set()
        x, y = self.to_img(e)
        self.start = self.last = (x, y)
        self.drag_mode = None
        self.drag_pushed = False
        t = self.tool.get()

        if getattr(self, "pending_pick", None) == "helpbg":
            self.pending_pick = None
            self.replace_background(x, y)
            return
        rt = self.hit_rotate_handle(x, y)
        if rt is not None:
            # rotate by dragging: remember where the drag started relative to the object's centre
            if rt == "float":
                l, tp, r, b = self.float_box()
                base = self.float["angle"]
            else:
                l, tp, r, b = obj_bbox(self.sel_obj)
                base = self.sel_obj.get("angle", 0)
                self.push_undo()
                self.drag_pushed = True
            self.rot_centre = ((l + r) / 2, (tp + b) / 2)
            self.rot_start = math.degrees(math.atan2(y - self.rot_centre[1], x - self.rot_centre[0])) - base
            self.drag_mode = "rotate_" + rt
            self.start_fast_drag()
            return
        fh = self.hit_float_handle(x, y)
        if fh is not None:
            self.drag_mode, self.handle_idx = "float_resize", fh
            fx0, fy0, fx1, fy1 = self.float_box()
            self.float_anchor = [(fx1, fy1), (fx0, fy1), (fx1, fy0), (fx0, fy0)][fh]   # opposite corner stays put
            self.drag_mode = "float_resize"
            self.start_fast_drag()
            return
        if self.float and inside(self.float_box(), x, y):
            self.drag_mode = "float"
            self.float_off = (x - self.float["x"], y - self.float["y"])
            self.start_fast_drag()
            return
        if self.float:
            self.commit_float()

        h = self.hit_handle(x, y)
        if h is not None:
            norm_obj(self.sel_obj)
            self.drag_mode, self.handle_idx = "handle", h
            self.start_fast_drag()
            return
        if t not in ("crop", "erase", "pick", "move"):
            o = hit_object(self.objects, x, y, max(3, 5 / self.zoom))
            if o is not None:
                self.select_object(o)
                self.drag_mode = "obj"
                self.start_fast_drag()
                return
        if self.sel_obj is not None:
            self.select_object(None)

        if t == "move" and inside(self.sel, x, y):
            self.lift_selection()
            self.drag_mode = "float"
            self.float_off = (x - self.float["x"], y - self.float["y"])
            self.start_fast_drag()
            return
        if t in OBJECT_CLICK_TOOLS:
            self.add_click_object(t, x, y)
            return
        if t == "pick":
            self.pick_fill(x, y)
            return
        if self.sel and t in ("select", "move"):
            self.sel = None
            self.draw_overlays()
            self.schedule_context()
        self.pen_pts = [(x, y)]
        self.drag_mode = "shape"

    def begin_obj_change(self):
        if not self.drag_pushed:
            self.push_undo()
            self.drag_pushed = True

    def on_drag(self, e):
        if self.img is None or not self.drag_mode:
            return
        x, y = self.to_img(e)
        if self.drag_mode == "float":
            self.float["x"] = x - self.float_off[0]
            self.float["y"] = y - self.float_off[1]
            self.request_render(fast=True)
            return
        if self.drag_mode in ("rotate_float", "rotate_obj"):
            ang = math.degrees(math.atan2(y - self.rot_centre[1], x - self.rot_centre[0])) - self.rot_start
            if e.state & 0x1:                       # Shift: snap to 15° steps
                ang = round(ang / 15) * 15
            ang = round(ang) % 360
            if self.drag_mode == "rotate_float":
                if ang != self.float["angle"]:
                    self.float["angle"] = ang
                    self.rebuild_float(final=False)
                    self.request_render(fast=True)
            else:
                if ang != self.sel_obj.get("angle", 0):
                    self.sel_obj["angle"] = ang
                    self.request_render(fast=True)
            return
        if self.drag_mode == "float_resize":
            ax, ay = self.float_anchor
            w, h = abs(x - ax), abs(y - ay)
            ow, oh = self.float["orig"].size
            if not (e.state & 0x1):                      # keep proportions unless Shift is held
                sc = max(w / ow, h / oh)
                w, h = ow * sc, oh * sc
            w, h = max(4, int(round(w))), max(4, int(round(h)))
            self.resize_float(w, h)
            self.float["x"] = int(ax if x >= ax else ax - w)
            self.float["y"] = int(ay if y >= ay else ay - h)
            self.request_render(fast=True)
            return
        if self.drag_mode == "obj":
            dx, dy = x - self.last[0], y - self.last[1]
            if dx or dy:
                self.begin_obj_change()
                move_obj(self.sel_obj, dx, dy)
                self.last = (x, y)
                self.request_render(fast=True)
            return
        if self.drag_mode == "handle":
            self.begin_obj_change()
            set_handle(self.sel_obj, self.handle_idx, x, y)
            self.request_render(fast=True)
            return

        # rubber-band preview (canvas items only – no image work)
        self.cv.delete("tmp")
        t = self.tool.get()
        sx, sy = self.cxy(*self.start)
        ex, ey = self.cxy(x, y)
        if t == "pen":
            self.pen_pts.append((x, y))
            if len(self.pen_pts) > 1:
                flat = [v for px, py in self.pen_pts for v in self.cxy(px, py)]
                self.cv.create_line(*flat, fill=self.ann_color, width=max(1, self.line_width.get() * self.zoom),
                                    smooth=True, capstyle="round", tags="tmp")
            return
        if t in LINE_TYPES:
            self.cv.create_line(sx, sy, ex, ey, fill=self.ann_color, width=max(1, self.line_width.get() * self.zoom),
                                arrow="both" if t == "darrow" else "last" if t == "arrow" else None,
                                arrowshape=(16, 20, 6), tags="tmp")
        else:
            color = {"crop": "#00c853", "erase": "#ff1744", "rect": self.ann_color, "rrect": self.ann_color,
                     "frect": self.ann_color, "ellipse": self.ann_color, "star": self.ann_color,
                     "triangle": self.ann_color, "diamond": self.ann_color, "arc": self.ann_color,
                     "spotlight": "#ffffff",
                     "highlight": HIGHLIGHT_COLOR, "blur": "#888888"}.get(t, ACCENT)
            x0, y0, x1, y1 = min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey)
            if t == "ellipse":
                self.cv.create_oval(x0, y0, x1, y1, outline=color, width=2, tags="tmp")
            elif t == "arc":
                self.cv.create_arc(x0, y0, x1, y1, start=0, extent=-90, style="arc", outline=self.ann_color,
                                   width=2, tags="tmp")
            elif t in ("star", "triangle", "diamond"):
                pts = shape_points(t, (x0, y0, x1, y1))
                self.cv.create_polygon(*[v for p in pts for v in p], outline=color, fill="", width=2, tags="tmp")
            elif t == "highlight":
                self.cv.create_rectangle(x0, y0, x1, y1, outline=color, fill=color, stipple="gray50", tags="tmp")
            else:
                self.cv.create_rectangle(x0, y0, x1, y1, outline=color,
                                         dash=None if t in OBJECT_DRAG_TOOLS else (4, 3), width=2, tags="tmp")
            bw, bh = abs(x - self.start[0]), abs(y - self.start[1])
            self.cv.create_text(x1 + 6, y1 + 6, anchor="nw", text=f"{bw} × {bh}", fill="white",
                                font=("Segoe UI", 9, "bold"), tags="tmp")

    def on_release(self, e):
        if self.img is None or not self.drag_mode:
            return
        mode, self.drag_mode = self.drag_mode, None
        self.cv.delete("tmp")
        if mode in ("float", "float_resize", "obj", "handle", "rotate_float", "rotate_obj"):
            if mode == "float_resize" and self.float:
                self.resize_float(*self.float["size"], final=True)
                self.schedule_context()
            if mode == "rotate_float" and self.float:
                self.rebuild_float(final=True)
                self.schedule_context()
            if mode == "rotate_obj":
                self.schedule_context()
            if mode in ("obj", "handle") and self.sel_obj is not None:
                norm_obj(self.sel_obj)
            self._drag_base = None
            if self._render_job:
                self.after_cancel(self._render_job)
                self._render_job = None
            self.render()
            return
        end = self.to_img(e)
        t = self.tool.get()
        if t == "pen":
            pts = self.pen_pts + [end]
            # thin out points that are closer than 2 px
            slim = [pts[0]]
            for pt in pts[1:]:
                if abs(pt[0] - slim[-1][0]) + abs(pt[1] - slim[-1][1]) >= 2:
                    slim.append(pt)
            if len(slim) > 1:
                self.add_object({"type": "pen", "pts": slim, "color": self.ann_color, "width": self.line_width.get(),
                                 "dash": self.draw_dashed.get(), "midarrow": self.draw_midarrow.get()})
            return
        if t in LINE_TYPES:
            if abs(end[0] - self.start[0]) + abs(end[1] - self.start[1]) > 2:
                self.add_object({"type": t, "x0": self.start[0], "y0": self.start[1],
                                 "x1": end[0], "y1": end[1], "color": self.ann_color,
                                 "width": self.line_width.get(), "dash": self.draw_dashed.get(),
                                 "midarrow": self.draw_midarrow.get()})
            return
        box = norm_box(self.start, end)
        if box[2] - box[0] <= 2 and box[3] - box[1] <= 2 and t in ("select", "erase", "crop", "blur", "highlight",
                                                                   "rect", "move"):
            # a click (no drag): act on the UI element under the cursor
            el = self.element_at(*self.start)
            if el is None:
                if t in ("select", "move") and self.sel:
                    self.sel = None
                    self.draw_overlays()
                    self.schedule_context()
                return
            box = el
            self.set_status(f"Auto-selected element {el[2] - el[0]} × {el[3] - el[1]} – drag for a custom area")
        if t in ("crop", "erase"):
            box = self.clamp_box(box)
        valid = box[2] - box[0] > 1 and box[3] - box[1] > 1
        if t in ("select", "move"):
            box = self.clamp_box(box)
            self.sel = box if valid and box[2] > box[0] and box[3] > box[1] else None
            self.draw_overlays()
            self.schedule_context()
            if self.sel:
                self.set_status("Area selected – choose an action above, or switch to Move (M) to drag it.")
        elif not valid:
            return
        elif t == "crop":
            self.sel = self.clamp_box(box)
            self.crop_selection()
        elif t == "erase":
            box = self.clamp_box(box)
            self.push_undo()
            self.fill_box(box)
            self.render()
        elif t in BOX_TYPES:
            o = {"type": t, "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3],
                 "color": HIGHLIGHT_COLOR if t == "highlight" else self.ann_color,
                 "width": self.line_width.get()}
            if t == "blur":
                o["block"] = 8
            if t in ("frect", "spotlight"):
                o["alpha"] = 90 if t == "frect" else 150
            if t in DASHABLE:
                o["dash"] = self.draw_dashed.get()
            if t == "arc":
                o.update(start=0, end=90, heads=True)
            self.add_object(o)

    def on_double(self, e):
        if self.img is None:
            return
        x, y = self.to_img(e)
        o = hit_object(self.objects, x, y, max(3, 5 / self.zoom))
        if o is not None and o["type"] in ("text", "callout"):
            self.drag_mode = None
            self.edit_text(o)
        else:
            self.on_press(e)

    def on_right_click(self, e):
        if self.img is None:
            return
        x, y = self.to_img(e)
        o = hit_object(self.objects, x, y, max(3, 5 / self.zoom))
        if o is not None:
            self.select_object(o)
        m = tk.Menu(self, tearoff=False)
        if self.float:
            m.add_command(label="Apply piece", command=self.commit_float)
            m.add_command(label="Cancel piece", command=self.cancel_float)
        elif self.sel_obj is not None:
            if self.sel_obj["type"] in ("text", "callout"):
                m.add_command(label="Edit text…", command=lambda: self.edit_text(self.sel_obj))
            m.add_command(label="Duplicate (Ctrl+D)", command=self.duplicate)
            m.add_command(label="Bring to front", command=self.obj_to_front)
            m.add_command(label="Delete", command=self.delete_object)
        elif self.sel:
            m.add_command(label="Copy selection as a layer (Ctrl+D)", command=self.duplicate)
            m.add_command(label="Copy selection to clipboard (Ctrl+C)", command=self.copy)
            m.add_separator()
            m.add_command(label="Crop image to selection", command=self.crop_selection)
            m.add_command(label="Erase selection", command=self.erase_selection)
            m.add_command(label="Cut out rows ↕", command=self.cut_rows)
            m.add_command(label="Cut out columns ↔", command=self.cut_cols)
        m.add_separator()
        m.add_command(label="Paste from clipboard as layer (Ctrl+V)", command=self.paste_layer)
        m.add_command(label="Copy whole image to clipboard", command=self.copy_whole)
        if self.objects:
            m.add_command(label="Flatten annotations", command=self.flatten_objects)
        m.tk_popup(e.x_root, e.y_root)

    # ============================================================= objects ===
    def add_object(self, o):
        self.push_undo()
        self.objects.append(o)
        self.render()
        self.select_object(o)

    def _spin_angle(self, var, set_angle):
        try:
            set_angle(float(var.get()))
        except (ValueError, tk.TclError):
            pass

    def rotate_object(self, delta=None, absolute=None):
        o = self.sel_obj
        if o is None or o["type"] not in ROTATABLE:
            return
        new = (absolute if absolute is not None else o.get("angle", 0) + delta) % 360
        if new == o.get("angle", 0) % 360:
            return
        self.push_undo()
        o["angle"] = new
        self.render()
        self.schedule_context()

    def rotate_selected(self, delta):
        """Ctrl+[ / Ctrl+] : rotate the selected annotation or the pasted piece."""
        if self.float:
            self.rotate_float(delta=delta)
        elif self.sel_obj is not None:
            self.rotate_object(delta=delta)

    def set_obj_prop(self, key, value):
        if self.sel_obj is None or self.sel_obj.get(key) == value:
            return
        self.push_undo()
        self.sel_obj[key] = value
        self.render()

    def _set_num_prop(self, key, var):
        try:
            self.set_obj_prop(key, float(var.get()))
        except (ValueError, tk.TclError):
            pass

    def place_symbol(self, kind, x, y):
        sizes = {"probe": (26, 70), "ruby": (26, 70), "thermo": (22, 70), "stopwatch": (44, 52)}
        w, h = sizes.get(kind, (44, 44))
        self.add_object({"type": "symbol", "kind": kind, "x0": x - w // 2, "y0": y - h // 2,
                         "x1": x - w // 2 + w, "y1": y - h // 2 + h, "color": self.ann_color,
                         "width": self.line_width.get()})
        self.set_status("Symbol placed – drag to move, corner handles to resize, ↻ to rotate.")

    def add_click_object(self, t, x, y):
        if t == "step":
            n = max([o["n"] for o in self.objects if o["type"] == "step"] + [0]) + 1
            self.add_object({"type": "step", "x0": x, "y0": y, "n": n,
                             "color": self.ann_color, "size": self.font_size.get()})
            return
        if t == "symbol":
            m = tk.Menu(self, tearoff=False)
            for kind, name in SYMBOLS:
                m.add_command(label=name, command=lambda k=kind: self.place_symbol(k, x, y))
            folder = self.settings.get("symbol_dir")
            if folder and os.path.isdir(folder):
                files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".png", ".bmp", ".jpg")))
                if files:
                    m.add_separator()
                    for f in files[:40]:
                        m.add_command(label=f"Image: {f}", command=lambda f=f: self.insert_symbol_file(
                            os.path.join(folder, f), x, y))
            m.add_separator()
            m.add_command(label="Choose a folder of symbol images (e.g. the imagesource/objects repo)…",
                          command=self.choose_symbol_dir)
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
            return
        d = TextDialog(self, "Add text" if t == "text" else "Add callout")
        if not d.result:
            return
        pad = max(6, self.font_size.get() // 2) if t == "callout" else 0
        font = get_font(self.font_size.get())
        natural = max(font.getlength(line) for line in d.result.split("\n")) + 2
        maxw = max(80, self.img.width - x - pad - 4)
        self.add_object({"type": t, "x0": x + pad, "y0": y + pad, "text": d.result,
                         "color": self.ann_color, "size": self.font_size.get(),
                         "width": self.line_width.get(), "w": int(min(natural, maxw))})

    def edit_text(self, o):
        d = TextDialog(self, "Edit text", o["text"])
        if d.result is not None and d.result != o["text"]:
            self.push_undo()
            o["text"] = d.result
            self.render()

    def apply_style(self, key):
        if self._syncing or self.sel_obj is None:
            return
        try:
            v = (self.line_width if key == "width" else self.font_size).get()
        except (tk.TclError, ValueError):
            return
        if key not in self.sel_obj or self.sel_obj[key] == v:
            return
        self.push_undo()
        self.sel_obj[key] = v
        self.request_render()

    def delete_object(self):
        if self.sel_obj is None:
            self.set_status("Click an annotation first.")
            return
        self.push_undo()
        self.objects.remove(self.sel_obj)
        self.sel_obj = None
        self.render()
        self.schedule_context()

    def obj_to_front(self):
        if self.sel_obj is not None:
            self.push_undo()
            self.objects.remove(self.sel_obj)
            self.objects.append(self.sel_obj)
            self.render()

    def flatten_objects(self):
        if not self.need_img() or not self.objects:
            return
        self.commit_float()
        self.push_undo()
        self.img = draw_objects(self.img, self.objects)
        self.objects, self.sel_obj = [], None
        self.render()
        self.schedule_context()
        self.set_status("Annotations merged into the image.")

    def delete_key(self):
        if self.sel_obj is not None:
            self.delete_object()
        else:
            self.erase_selection()

    # ============================================================= colours ===
    def set_ann_color(self, c):
        self.ann_color = c
        try:
            self.ann_swatch.config(bg=c, activebackground=c)
        except (AttributeError, tk.TclError):
            pass
        if self.sel_obj is not None and self.sel_obj.get("color") != c and \
                self.sel_obj["type"] not in ("highlight", "blur", "spotlight"):
            self.push_undo()
            self.sel_obj["color"] = c
            self.request_render()

    def choose_ann_color(self):
        c = colorchooser.askcolor(self.ann_color, parent=self)[1]
        if c:
            self.set_ann_color(c)

    def choose_fill_color(self):
        c = colorchooser.askcolor(self.rgb_hex(self.fill_color), parent=self)
        if c[0]:
            self.fill_color = tuple(int(v) for v in c[0])
            self.auto_fill.set(False)
            self.schedule_context()

    def pick_fill(self, x, y):
        if 0 <= x < self.img.width and 0 <= y < self.img.height:
            self.fill_color = self.img.getpixel((x, y))[:3]
            self.auto_fill.set(False)
            self.schedule_context()
            self.set_status(f"Fill colour set to {self.rgb_hex(self.fill_color)} (auto fill turned off)")

    # ====================================================== pixel patching ===
    def nudge(self, dx, dy):
        if self.sel_obj is not None:
            self.push_undo()
            move_obj(self.sel_obj, dx, dy)
            self.request_render()
        elif self.float:
            self.float["x"] += dx
            self.float["y"] += dy
            self.request_render()
        elif self.sel and self.tool.get() == "move":
            self.lift_selection()
            self.nudge(dx, dy)

    def escape(self):
        if self.float:
            self.cancel_float()
        elif self.sel_obj is not None:
            self.select_object(None)
        elif self.sel:
            self.sel = None
            self.draw_overlays()
            self.schedule_context()

    def select_all(self):
        if self.need_img():
            self.select_object(None)
            self.sel = (0, 0, self.img.width, self.img.height)
            self.draw_overlays()
            self.schedule_context()

    def lift_selection(self):
        self.push_undo()
        box = self.sel
        piece = self.img.crop(box)
        self.fill_box(box)
        self.set_float(piece, box[0], box[1])
        self.sel = None
        self.schedule_context()
        self.set_status("Drag to move, corner handles to resize, arrow keys to nudge.  Enter = apply,  Esc = cancel.")

    def duplicate(self):
        if self.sel_obj is not None:
            o = copy.deepcopy(self.sel_obj)
            move_obj(o, 12, 12)
            if o["type"] == "step":
                o["n"] = max(x["n"] for x in self.objects if x["type"] == "step") + 1
            self.add_object(o)
            return
        if not self.need_sel():
            return
        self.push_undo()
        piece = self.img.crop(self.sel)
        self.set_float(piece, self.sel[0] + 10, self.sel[1] + 10)
        self.sel = None
        self.render()
        self.schedule_context()
        self.set_status("Copy of the selection is floating over the image – drag it into place.  "
                        "Enter = apply,  Esc = cancel.")

    def add_layer(self, piece):
        if self.img is None:
            self.load_image(piece)
            return
        if self.float:
            self.commit_float()
        self.push_undo()
        if piece.mode not in ("RGB", "RGBA"):
            piece = piece.convert("RGBA")
        if getattr(self, "help_preset", None):
            piece = self.match_help_background(piece)
        if self.sel:
            x, y = self.sel[:2]
        else:
            x = max(0, int((self.cv.canvasx(20) - self.off[0]) / self.zoom))
            y = max(0, int((self.cv.canvasy(20) - self.off[1]) / self.zoom))
        self.set_float(piece, x, y)
        self.sel = None
        self.render()
        self.schedule_context()
        self.set_status("Image pasted – drag to move, corner handles to resize.  Enter = apply,  Esc = cancel.")

    def commit_float(self):
        if not self.float:
            return
        f = self.float
        self.img.paste(f["img"], (f["x"], f["y"]), f["img"] if f["img"].mode == "RGBA" else None)
        self.float = None
        self._drag_base = None
        self.dirty = True
        self.update_title()
        self.render()
        self.schedule_context()
        self.set_status("Piece applied.")

    def cancel_float(self):
        if not self.float:
            return
        self.float = None
        self._drag_base = None
        if self.undo_stack:
            self.restore(self.undo_stack.pop())
        self.render()
        self.schedule_context()
        self.set_status("Cancelled.")

    def crop_selection(self):
        if not self.need_sel():
            return
        self.push_undo()
        x0, y0 = self.sel[:2]
        self.img = self.img.crop(self.sel)
        self.shift_objects(-x0, -y0)
        self.sel = None
        self.schedule_context()
        self.zoom_fit()

    def erase_selection(self):
        if not self.need_sel():
            return
        self.push_undo()
        self.fill_box(self.sel)
        self.render()

    def cut_rows(self):
        if not self.need_sel():
            return
        _, y0, _, y1 = self.sel
        W, H = self.img.size
        if y1 - y0 >= H:
            return
        self.push_undo()
        out = Image.new("RGB", (W, H - (y1 - y0)))
        out.paste(self.img.crop((0, 0, W, y0)), (0, 0))
        out.paste(self.img.crop((0, y1, W, H)), (0, y0))
        self.img, self.sel = out, None
        self.shift_objects(0, -(y1 - y0), lambda o: obj_bbox(o)[1] >= y1)
        self.render()
        self.schedule_context()

    def cut_cols(self):
        if not self.need_sel():
            return
        x0, _, x1, _ = self.sel
        W, H = self.img.size
        if x1 - x0 >= W:
            return
        self.push_undo()
        out = Image.new("RGB", (W - (x1 - x0), H))
        out.paste(self.img.crop((0, 0, x0, H)), (0, 0))
        out.paste(self.img.crop((x1, 0, W, H)), (x0, 0))
        self.img, self.sel = out, None
        self.shift_objects(-(x1 - x0), 0, lambda o: obj_bbox(o)[0] >= x1)
        self.render()
        self.schedule_context()

    # =========================================================== undo/redo ===
    def undo(self):
        if self.float:
            self.cancel_float()
            return
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())
            self.render()

    def redo(self):
        if self.float:
            self.commit_float()
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())
            self.render()

    # ============================================================= capture ===
    def record(self):
        if self.recorder is not None:
            self.stop_recording()
            return
        self.capture(mode="video")

    def capture(self, mode="image"):
        if self.capturing or self.recorder is not None:
            return
        self.capture_mode = mode
        self.capturing = True
        Tooltip.hide_all()
        if self.float:
            self.commit_float()
        self.withdraw()
        secs = self.delay.get()
        try:
            if secs:
                Countdown(self, secs, lambda: self.after(250, self._grab))
            else:
                self.after(250, self._grab)
        except Exception:
            self.after(250, self._grab)

    def _grab(self):
        try:
            windows = list_window_rects()
        except Exception:
            log.exception("list_window_rects failed")
            windows = []
        try:
            shot = grab_screen()
        except Exception as ex:
            self.capturing = False
            self.deiconify()
            messagebox.showerror(APP_NAME, f"Screen capture failed:\n{ex}", parent=self)
            return
        smart, note = None, NUMPY_ERROR or ""
        if np is not None:
            try:
                t0 = time.time()
                smart = SmartDetector(shot)
                log.info("Capture %sx%s, %d windows, edge detector built in %.0f ms",
                         shot.width, shot.height, len(windows), (time.time() - t0) * 1000)
            except Exception as ex:
                log.exception("SmartDetector failed")
                note = f"{ex.__class__.__name__}: {ex}"
        else:
            log.warning(NUMPY_ERROR)
        try:
            RegionPicker(self, shot, virtual_screen_origin(), windows, smart, self._on_picked, note,
                         self.capture_mode)
        except Exception as ex:
            self._on_region(None)
            messagebox.showerror(APP_NAME, f"Could not open the capture screen:\n{ex}", parent=self)

    def _on_picked(self, result, mode):
        if result is None:
            self._on_region(None)
        elif mode == "video":
            self._on_record_region(result)
        else:
            self._on_region(result)

    def _on_region(self, img):
        self.capturing = False
        self.deiconify()
        self.lift()
        self.focus_force()
        if img is None:
            return
        saved = None
        if self.autosave.get():
            try:
                LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
                saved = LIBRARY_DIR / f"Capture_{datetime.now():%Y%m%d_%H%M%S}.png"
                img.save(saved)
                self.library.refresh()
            except Exception:
                saved = None
        self.load_image(img, keep_history=True)
        self.tool.set("select")
        if self.copy_after.get():
            try:
                copy_image_to_clipboard(img, self.winfo_id())
            except Exception:
                log.exception("copy after capture failed")
        self.set_status(f"Captured {img.width} × {img.height}" + (" – saved to History" if saved else "") +
                        ".   Ctrl+S save   •   Ctrl+C copy")

    # --- video ---------------------------------------------------------------------
    def _on_record_region(self, bbox):
        self.capturing = False
        if bbox is None:
            self.deiconify()
            self.lift()
            return
        RecordCountdown(self, bbox, on_go=lambda: self._start_recording(bbox),
                        on_screenshot=lambda: self._screenshot_instead(bbox),
                        on_cancel=lambda: (self.deiconify(), self.lift()))

    def _screenshot_instead(self, bbox):
        """Countdown: user chose a still image of the same area instead of a video."""
        try:
            try:
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            except TypeError:
                img = ImageGrab.grab(bbox=bbox)
            self._on_region(img.convert("RGB"))
        except Exception as ex:
            log.exception("screenshot instead of recording failed")
            self.deiconify()
            messagebox.showerror(APP_NAME, f"Screenshot failed:\n{ex}", parent=self)

    def _start_recording(self, bbox):
        try:
            LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
            path = LIBRARY_DIR / f"Record_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            self.recorder = Recorder(bbox, path, fps=self.rec_fps.get(), show_cursor=self.rec_cursor.get())
            self.record_bar = RecordBar(self, self.recorder.bbox, self.rec_fps.get(), self.stop_recording,
                                        lambda p: self.recorder.paused.set() if p else self.recorder.paused.clear(),
                                        self.cancel_recording)
            self.recorder.start()
            log.info("Recording %s at %s fps -> %s (ffmpeg=%s)", self.recorder.bbox, self.rec_fps.get(),
                     self.recorder.path, self.recorder.ffmpeg)
            self.after(250, self._tick_recording)
        except Exception as ex:
            log.exception("Could not start recording")
            self.recorder = None
            self.deiconify()
            messagebox.showerror(APP_NAME, f"Could not start recording:\n{ex}", parent=self)

    def _tick_recording(self):
        r = self.recorder
        if r is None:
            return
        if not r.is_alive() and r.error:
            self.stop_recording()
            return
        try:
            self.record_bar.set_time(r.recorded_seconds)
        except Exception:
            pass
        self.after(250, self._tick_recording)

    def cancel_recording(self):
        r = self.recorder
        if r is None:
            return
        if not messagebox.askyesno(APP_NAME, "Discard this recording?", parent=self.record_bar):
            return
        self.stop_recording(discard=True)

    def stop_recording(self, discard=False):
        r, self.recorder = self.recorder, None
        if r is None:
            return
        if self.record_bar is not None:
            self.record_bar.close()
            self.record_bar = None
        r.stop()
        r.join(timeout=15)
        self.deiconify()
        self.lift()
        self.focus_force()
        if r.error:
            messagebox.showerror(APP_NAME, f"Recording failed:\n{r.error}", parent=self)
            return
        if discard:
            try:
                r.path.unlink()
            except Exception:
                pass
            self.set_status("Recording discarded.")
            return
        try:
            if r.first_frame is not None:
                thumb = r.first_frame.copy()
                thumb.thumbnail((320, 320))
                thumb.save(str(r.path) + ".jpg", quality=80)
        except Exception:
            log.exception("thumbnail failed")
        self.library.refresh()
        note = "" if r.ffmpeg else "  (saved as GIF – install imageio-ffmpeg for MP4)"
        self.set_status(f"Recorded {r.recorded_seconds:.0f} s → {r.path.name}{note}   •   click it in History to trim / crop / play")
        if r.ffmpeg and r.frames_written:
            VideoEditor(self, r.path, on_saved=lambda _: self.library.refresh())

    def poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "capture":
                    if self.recorder is None:
                        self.capture()
                elif ev == "record":
                    self.record()
                elif isinstance(ev, tuple) and ev[0] == "hotkey-result":
                    self.on_hotkey_result(ev[1])
        except queue.Empty:
            pass
        self._poll_n = getattr(self, "_poll_n", 0) + 1
        if self._poll_n % 200 == 0:          # every ~30 s
            self.hotkeys.ensure_alive()
        self.after(150, self.poll_events)

    # ========================================================= file / clip ===
    def load_image(self, img, path=None, keep_history=False, objects=None):
        if img.mode in ("RGBA", "LA") or "transparency" in img.info:
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        else:
            img = img.convert("RGB")
        if keep_history and self.img is not None:
            self.push_undo()
        else:
            self.undo_stack.clear()
            self.redo_stack.clear()
        self.img, self.path, self.sel, self.float = img, path, None, None
        # a new image leaves help-image mode unless it already has a help-canvas size
        self.help_preset = next((n for n, (w, h, _) in HELP_PRESETS.items() if img.size == (w, h)), None)
        self.objects, self.sel_obj = (objects or []), None
        self.dirty = path is None
        self.update_title()
        self.schedule_context()
        self.zoom_fit()

    def open_file(self, path):
        path = str(path)
        try:
            if path.lower().endswith(PROJECT_EXT):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                img = Image.open(io.BytesIO(base64.b64decode(data["image"])))
                img.load()
                self.load_image(img, path, objects=data.get("objects", []))
                return
            img = Image.open(path)
            img.load()
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open file:\n{ex}", parent=self)
            return
        self.load_image(img, path)

    def open_library_item(self, p):
        try:
            img = Image.open(p)
            img.load()
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not open file:\n{ex}", parent=self)
            return
        self.load_image(img, keep_history=True)
        self.set_status(f"Opened {p.name} from History.")

    def open_dialog(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[
            ("Images & projects", f"*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *{PROJECT_EXT}"),
            ("SnapPatch project", f"*{PROJECT_EXT}"), ("All files", "*.*")])
        if p:
            self.open_file(p)

    def clipboard_image(self):
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            data = None
        if isinstance(data, Image.Image):
            return data
        if isinstance(data, list):
            for p in data:
                try:
                    im = Image.open(p)
                    im.load()
                    return im
                except Exception:
                    continue
        self.set_status("No image on the clipboard.")
        return None

    def paste_as_new(self):
        im = self.clipboard_image()
        if im is not None:
            self.load_image(im, keep_history=True)

    def paste_layer(self):
        im = self.clipboard_image()
        if im is not None:
            self.add_layer(im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB"))

    def insert_image(self):
        if not self.need_img():
            return
        p = filedialog.askopenfilename(parent=self, filetypes=[
            ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"), ("All files", "*.*")])
        if p:
            try:
                im = Image.open(p)
                im.load()
            except Exception as ex:
                messagebox.showerror(APP_NAME, f"Could not open file:\n{ex}", parent=self)
                return
            self.add_layer(im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB"))

    def save(self):
        if not self.need_img():
            return
        if self.path:
            self._write(self.path)
        else:
            self.save_as()

    def save_as(self, project=False):
        if not self.need_img():
            return
        if project:
            p = filedialog.asksaveasfilename(parent=self, defaultextension=PROJECT_EXT, filetypes=[
                ("SnapPatch project (annotations stay editable)", f"*{PROJECT_EXT}")])
            if p:
                self._write(p)
            return
        opts = ImageSaveDialog(self, self.img.width, self.img.height, self.settings.get("save_opts")).result
        if not opts:
            return
        if opts.get("remember"):
            self.settings["save_opts"] = opts
            save_settings(self.settings)
        ext = "." + opts["fmt"]
        names = {"png": "PNG", "jpg": "JPEG", "gif": "GIF", "bmp": "Bitmap"}
        base = Path(self.path).stem if self.path else f"Capture_{datetime.now():%Y%m%d_%H%M%S}"
        p = filedialog.asksaveasfilename(parent=self, defaultextension=ext, initialfile=base + ext,
                                         initialdir=str(Path(self.path).parent if self.path else LIBRARY_DIR),
                                         filetypes=[(names[opts["fmt"]], "*" + ext), ("All files", "*.*")])
        if p:
            self._write(p, opts)

    def _write(self, path, opts=None):
        self.commit_float()
        try:
            if path.lower().endswith(PROJECT_EXT):
                buf = io.BytesIO()
                self.img.save(buf, "PNG")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"app": APP_NAME, "version": 3,
                               "image": base64.b64encode(buf.getvalue()).decode("ascii"),
                               "objects": self.objects}, fh)
            else:
                out = draw_objects(self.img, self.objects)
                low = path.lower()
                if opts is None:
                    opts = dict(self.settings.get("save_opts") or {})
                    opts.pop("w", None)
                    opts.pop("h", None)       # remembered size is relative, recompute from scale
                    if opts.get("scale") and opts["scale"] != 100:
                        opts["w"] = max(1, round(out.width * opts["scale"] / 100))
                        opts["h"] = max(1, round(out.height * opts["scale"] / 100))
                if opts.get("w") and (opts["w"], opts["h"]) != out.size:
                    out = out.resize((opts["w"], opts["h"]), Image.LANCZOS)
                if low.endswith(".gif"):
                    out = out.convert("P", palette=Image.ADAPTIVE, colors=opts.get("colours", 256))
                    out.save(path)
                elif low.endswith((".jpg", ".jpeg")):
                    out.save(path, quality=opts.get("quality", 85), optimize=True)
                else:
                    out.save(path)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Could not save:\n{ex}", parent=self)
            return
        self.path, self.dirty = path, False
        self.update_title()
        self.set_status(f"Saved {path}")

    def copy_whole(self):
        self.sel = None
        self.draw_overlays()
        self.schedule_context()
        self.copy()

    def copy(self):
        if not self.need_img():
            return
        flat = self.flattened()
        target = flat.crop(self.sel) if self.sel else flat
        try:
            # also write a PNG so Ctrl+V works in Explorer / Outlook attachments (not just Excel / PowerPoint)
            clip_dir = LIBRARY_DIR / "clipboard"
            clip_dir.mkdir(parents=True, exist_ok=True)
            for old in sorted(clip_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-10]:
                try:
                    old.unlink()
                except Exception:
                    pass
            base = Path(self.path).stem if self.path else "SnapPatch"
            fpath = clip_dir / f"{base}_{datetime.now():%Y%m%d_%H%M%S}.png"
            target.save(fpath)
            copy_image_to_clipboard(target, self.winfo_id(), fpath)
            self.set_status(f"Copied {'selection' if self.sel else 'image'} ({target.width} × {target.height}) – "
                            "Ctrl+V pastes it into Teams / Word / Excel / PowerPoint, into a folder as a PNG file, "
                            "or back here as a layer.")
        except Exception as ex:
            log.exception("copy failed")
            messagebox.showerror(APP_NAME, str(ex), parent=self)

    def on_close(self):
        """Window X button: keep running minimized so the hotkeys stay active (like Snagit's tray icon).
        File -> Exit really quits."""
        if self.recorder is not None:
            self.stop_recording()
        if not self.settings.get("close_hint_shown"):
            self.settings["close_hint_shown"] = True
            save_settings(self.settings)
            messagebox.showinfo(APP_NAME, "SnapPatch keeps running minimized so that the hotkeys\n"
                                f"({HOTKEY_TEXT} / {RECORD_HOTKEY_TEXT}) work anywhere.\n\n"
                                "Click its taskbar button to open it again.\n"
                                "To quit completely use File → Exit.", parent=self)
        self.iconify()
        self.set_status(f"Running in the background – {HOTKEY_TEXT} captures, {RECORD_HOTKEY_TEXT} records. "
                        "File → Exit quits.")

    def quit_app(self):
        if self.recorder is not None:
            self.stop_recording()
        if self.img is not None and self.dirty:
            self.deiconify()
            r = messagebox.askyesnocancel(APP_NAME, "Save changes before closing?", parent=self)
            if r is None:
                return
            if r:
                self.save()
                if self.dirty:
                    return
        self.destroy()


def main():
    if not single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, "SnapPatch is already running – look for it in the taskbar.\n"
                                      "(Running two copies makes the hotkeys unreliable.)")
        return
    SnapPatch().mainloop()


if __name__ == "__main__":
    main()
