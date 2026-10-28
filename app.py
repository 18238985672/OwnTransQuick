import asyncio
import ctypes
import ctypes.wintypes
import datetime
import logging
import logging.handlers
import webbrowser
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
import winreg
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

import requests
import pystray
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageGrab, ImageOps, ImageTk

APP_NAME = "Own翻译"
APP_VERSION = "1.1.0"
CONFIG_APP_NAME = "NiuTransQuick"
API_URL = "https://api.niutrans.com/NiuTransServer/translation"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / CONFIG_APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "log.txt"
LANGUAGES = {"中文": "zh", "日本語": "ja", "English": "en"}
AUTO_SOURCE = "自动识别"
# Config key -> default hotkey. "hotkey" copies the selection first; "clipboard_hotkey" translates the clipboard as-is.
# "ocr_hotkey" lets the user select a screen region whose text is recognized (Windows OCR) and translated.
DEFAULT_HOTKEYS = {"hotkey": "ctrl+alt+1", "clipboard_hotkey": "ctrl+alt+2", "ocr_hotkey": "ctrl+alt+3"}
# Chinese/Japanese characters and full-width punctuation; Windows OCR puts spaces between them.
CJK_CHARS = "　-〿぀-ヿ㐀-䶿一-鿿＀-￯"
CJK_GAP_RE = re.compile(f"(?<=[{CJK_CHARS}]) (?=[{CJK_CHARS}])")
# Share of kana in the Japanese engine's result above which the text is taken as Japanese.
OCR_KANA_RATIO = 0.15
OCR_MISSING_LANGUAGE = "系统没有可用的文字识别语言。请在“设置 → 时间和语言 → 语言和区域”中添加中文或日语，并安装“光学字符识别”。"
# NiuTrans text translation accepts at most this many characters per request.
NIUTRANS_MAX_CHARS = 5000
DEFAULT_MAX_CHARS = 1000
# Friendly messages for common NiuTrans error codes.
NIUTRANS_ERRORS = {
    "10000": "原文为空，没有可翻译的内容。",
    "10001": "请求太频繁，请稍后再试。",
    "10003": "原文太长，超过了小牛翻译的单次长度限制。",
    "10005": "原文编码有问题（不是 UTF-8）。",
    "13001": "API Key 无效、没有访问权限，或字符额度已用完。",
    "13002": "还没有填写 API Key。",
    "13003": "原文内容未通过小牛翻译的内容审核。",
    "13007": "小牛翻译不支持这个语言方向。",
    "13008": "小牛翻译处理超时，请稍后再试。",
}
# Seconds to wait for a translation response; other failures are retried once after a short pause.
REQUEST_TIMEOUT = 10
RETRY_DELAY = 0.5
# Modifier names in the order they are written in a hotkey string such as "ctrl+alt+1".
HOTKEY_MODIFIERS = ["ctrl", "alt", "shift", "windows"]
# RegisterHotKey modifier flags and the virtual-key codes that show each modifier is held.
MOD_FLAGS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "windows": 0x0008}
MOD_NOREPEAT = 0x4000
MODIFIER_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "windows": (0x5B, 0x5C)}
# Windows virtual-key codes of modifier keys (Shift/Ctrl/Alt, left/right variants, Win).
MODIFIER_VK_CODES = {16, 17, 18, 91, 92, 160, 161, 162, 163, 164, 165}
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP_SET_HOTKEYS = 0x8001  # WM_APP + 1
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
# Seconds to wait for the user to release the hotkey, and for the target app to update the clipboard.
HOTKEY_RELEASE_TIMEOUT = 1.0
CLIPBOARD_UPDATE_TIMEOUT = 1.0
CACHE_SIZE = 200
# Line limits for each text box when the popup is fitted to its content.
MIN_TEXT_LINES = 2
MAX_TEXT_LINES = 12
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SINGLE_INSTANCE_MUTEX = "Local\\OwnTranslation.SingleInstance"
ERROR_ALREADY_EXISTS = 183
KANJI_RE = re.compile(r"[㐀-䶿一-鿿々]")
# The popup ignores focus changes this soon after being shown, so it is not closed by its own activation.
FOCUS_OUT_GRACE = 0.4
# Clipboard formats that hold GDI or private handles instead of plain memory; they cannot be copied byte-for-byte.
CLIPBOARD_SKIP_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E, *range(0x200, 0x400)}
CLIPBOARD_BACKUP_LIMIT = 32 * 1024 * 1024
CF_UNICODETEXT = 13

# Color palettes for the Windows light and dark app themes.
THEMES = {
    "light": {
        "bg": "#f3f7fb", "card": "#ffffff", "card_header": "#f7fafc", "border": "#dce7f0", "separator": "#e8eef4",
        "text": "#20334d", "heading": "#253b56", "muted": "#63758b", "status": "#526a83", "hint": "#8a9bad",
        "accent": "#168bd2", "accent_active": "#0878bb", "icon": "#1682bd", "on_accent": "#ffffff",
        "soft_bg": "#eaf4fc", "soft_fg": "#1677b8", "soft_active": "#d6eaf8",
        "plain_fg": "#24506f", "hover": "#eaf4fb", "input_border": "#c5d3e0",
        "nav_selected": "#e1e9f2", "nav_hover": "#e9eff6", "select": "#b9def5", "disabled": "#9aabba",
        "error": "#b42318", "success": "#1a7f37", "kana": "#6b8bb5",
        "toggle_off": "#7f8e9e", "knob_off": "#5f6f80", "toast_bg": "#20334d", "toast_fg": "#ffffff",
    },
    "dark": {
        "bg": "#1e2228", "card": "#2a2f37", "card_header": "#252a31", "border": "#3a414c", "separator": "#363c46",
        "text": "#e6edf3", "heading": "#e6edf3", "muted": "#9aa7b4", "status": "#a3b0bd", "hint": "#7f8b98",
        "accent": "#2b9be0", "accent_active": "#1c86c8", "icon": "#5ab4ec", "on_accent": "#ffffff",
        "soft_bg": "#23384a", "soft_fg": "#7cc4f2", "soft_active": "#2b4660",
        "plain_fg": "#c9d6e2", "hover": "#323843", "input_border": "#4a5260",
        "nav_selected": "#2f353f", "nav_hover": "#272c33", "select": "#2f5a7a", "disabled": "#5f6b78",
        "error": "#ff7b72", "success": "#56d364", "kana": "#8fb3d9",
        "toggle_off": "#8b96a3", "knob_off": "#c3ccd5", "toast_bg": "#3a414c", "toast_fg": "#ffffff",
    },
}

_instance_mutex = None
_kakasi = None
_kakasi_lock = threading.Lock()


class TranslationError(Exception):
    """A translation failure with a message that can be shown to the user as-is."""


class TranslationTimeout(TranslationError):
    """The translation request timed out; not retried."""


def load_config():
    defaults = {
        "api_key": "",
        **DEFAULT_HOTKEYS,
        "close_after_copy": True,
        "close_on_click_outside": True,
        # Top-left corner and width of the popup after the user moved it; None means the bottom-right default.
        "popup_position": None,
        "popup_width": None,
        "show_kana": True,
        "max_chars": DEFAULT_MAX_CHARS,
        "always_on_top": True,
        "usage_date": datetime.date.today().isoformat(),
        "today_translated_chars": 0,
    }
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        defaults.update(data if isinstance(data, dict) else {})
    except (OSError, json.JSONDecodeError):
        pass
    # The request timeout is now fixed (REQUEST_TIMEOUT); drop the value older versions saved.
    defaults.pop("timeout", None)
    today = datetime.date.today().isoformat()
    if defaults.get("usage_date") != today:
        defaults["usage_date"] = today
        defaults["today_translated_chars"] = 0
    return defaults


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def language_name(code):
    return {value: key for key, value in LANGUAGES.items()}.get(code, code)


def extract_translation(data):
    if not isinstance(data, dict):
        raise TranslationError("小牛翻译返回了无法识别的数据。")
    code = data.get("error_code")
    if code not in (None, "", "0", 0, "000000"):
        message = NIUTRANS_ERRORS.get(str(code)) or f"小牛翻译返回错误：{data.get('error_msg') or '未知错误'}"
        raise TranslationError(f"{message}（错误码 {code}）")
    result = data.get("tgt_text")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        found = []

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ("content", "tgt_text", "text") and isinstance(child, str):
                        found.append(child)
                    else:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)
        if found:
            return "\n".join(dict.fromkeys(s.strip() for s in found if s.strip()))
    raise TranslationError(f"小牛翻译没有返回译文：{json.dumps(data, ensure_ascii=False)[:300]}")


def request_translation(api_key, text, source, target, timeout=REQUEST_TIMEOUT):
    payload = {"from": source, "to": target, "apikey": api_key.strip(), "src_text": text}
    try:
        response = requests.post(API_URL, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        raise TranslationTimeout(f"请求超时（超过 {timeout} 秒没有响应），请检查网络或稍后再试。") from None
    except requests.ConnectionError:
        raise TranslationError("无法连接小牛翻译服务器，请检查网络。") from None
    except requests.HTTPError as exc:
        raise TranslationError(f"小牛翻译服务暂时不可用（HTTP {exc.response.status_code}）。") from None
    except requests.RequestException:
        raise TranslationError("请求小牛翻译失败，请稍后再试。") from None
    except ValueError:
        raise TranslationError("小牛翻译返回了无法识别的数据。") from None
    return extract_translation(data)


def get_kakasi():
    """Load pykakasi lazily (its dictionaries take a moment); returns None if unavailable."""
    global _kakasi
    with _kakasi_lock:
        if _kakasi is None:
            try:
                import pykakasi
                _kakasi = pykakasi.kakasi()
            except Exception:
                _kakasi = False
        return _kakasi or None


def kana_reading(text):
    """Return the kana reading of Japanese text: kanji words become hiragana, existing kana stays as-is."""
    kks = get_kakasi()
    if kks is None:
        return text
    lines = []
    # pykakasi repeats words around newlines, so convert one line at a time.
    for line in text.split("\n"):
        items = kks.convert(line) if line else []
        if "".join(item["orig"] for item in items) != line:
            lines.append(line)
            continue
        lines.append("".join(item["hira"] if KANJI_RE.search(item["orig"]) and item["hira"] else item["orig"]
                             for item in items))
    return "\n".join(lines)


class OcrError(Exception):
    """Text recognition failed with a message that can be shown to the user as-is."""


def ocr_image(image):
    """Recognize the text in a PIL image with the Windows OCR engine (Chinese or Japanese, chosen automatically)."""
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        logging.exception("Windows OCR bindings are missing")
        raise OcrError("缺少 Windows 文字识别组件，无法截图翻译。") from None

    # Screen text is small (12-16 px on a 100 % monitor) and is recognized far better when doubled, whatever
    # the size of the selection (12 px Chinese: 72 % -> 93 % accurate); stay within the engine's size limit.
    scale = min(2.0, OcrEngine.max_image_dimension / max(image.size))
    if scale > 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    blue, green, red, alpha = image.convert("RGBA").split()
    writer = DataWriter()
    writer.write_bytes(Image.merge("RGBA", (red, green, blue, alpha)).tobytes())  # RGBA -> BGRA byte order
    bitmap = SoftwareBitmap.create_copy_with_alpha_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height, BitmapAlphaMode.PREMULTIPLIED)

    tags = [language.language_tag for language in OcrEngine.available_recognizer_languages]
    engines = {}
    for prefix in ("ja", "zh-Hans", "zh", "en"):
        tag = next((tag for tag in tags if tag.startswith(prefix)), None)
        if tag and prefix[:2] not in engines:
            engines[prefix[:2]] = OcrEngine.try_create_from_language(Language(tag))
    if not any(engines.values()):
        engines = {"user": OcrEngine.try_create_from_user_profile_languages()}
        if not engines["user"]:
            raise OcrError(OCR_MISSING_LANGUAGE)

    def recognize(engine):
        return asyncio.run(_recognize_async(engine, bitmap))

    # The wrong engine produces garbage, so read with the Japanese engine first and pick by content:
    # real Japanese is full of kana; mostly Latin letters means English, which the Japanese engine reads
    # better than the Chinese one; anything else is Chinese.
    if engines.get("ja"):
        text = recognize(engines["ja"])
        letters = [ch for ch in text if not ch.isspace()]
        kana = sum("぀" <= ch <= "ヿ" for ch in letters)
        han = len(KANJI_RE.findall(text))
        latin = sum(ch.isascii() and ch.isalpha() for ch in letters)
        if letters and kana / len(letters) >= OCR_KANA_RATIO:
            return text
        if latin > 2 * han:
            return recognize(engines["en"]) if engines.get("en") else text
        if not engines.get("zh"):
            return text
    elif engines.get("en") and not engines.get("zh"):
        return recognize(engines["en"])
    return recognize(engines.get("zh") or engines.get("en") or engines["user"])


async def _recognize_async(engine, bitmap):
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        rects = [word.bounding_rect for word in line.words]
        top = min(rect.y for rect in rects)
        bottom = max(rect.y + rect.height for rect in rects)
        lines.append((top, bottom, CJK_GAP_RE.sub("", line.text)))
    # Join wrapped lines back into paragraphs: no separator between Chinese/Japanese characters, a space
    # between other words, and a line break where the vertical gap shows a new paragraph.
    text = ""
    for index, (top, bottom, line) in enumerate(lines):
        if index:
            previous_top, previous_bottom, previous = lines[index - 1]
            height = max(bottom - top, previous_bottom - previous_top, 1)
            if top - previous_bottom > 0.8 * height:
                text += "\n"
            elif not (re.search(f"[{CJK_CHARS}]$", previous) and re.match(f"[{CJK_CHARS}]", line)):
                text += " "
        text += line
    return text.strip()


def get_cursor_position():
    point = ctypes.wintypes.POINT()
    try:
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y
    except Exception:
        return 100, 100


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.wintypes.DWORD)]


def get_work_area(x, y):
    """(left, top, right, bottom) of the work area (screen minus taskbar) of the monitor nearest to (x, y)."""
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if monitor and user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
            work = info.rcWork
            return work.left, work.top, work.right, work.bottom
    except Exception:
        logging.exception("Could not read monitor work area")
    return 0, 0, ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)


def monitor_rects(x, y):
    """Bounds of the monitor at (x, y): (in this process's coordinates, in physical pixels).

    They differ on a monitor whose scale is not the system DPI; Pillow captures the screen in physical pixels.
    """
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    monitor = ctypes.c_void_p(user32.MonitorFromPoint(ctypes.wintypes.POINT(int(x), int(y)), 2))

    def bounds():
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        rect = info.rcMonitor
        return rect.left, rect.top, rect.right, rect.bottom

    logical = bounds()
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    try:
        physical = bounds()
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
    return logical, physical


def frame_offsets(window):
    """How far the window frame extends beyond Tk's geometry (x, y, width, height) on each side.

    Tk positions the outer window but sizes the client area, so the title bar and borders are extra;
    returns (left, top, right, bottom) offsets of the frame relative to x, y, x + width, y + height.
    The borders include Windows' invisible resize margin, so the visible frame ends up slightly inside.
    """
    # Measured with Tk's own coordinates only. (DWM frame bounds are in physical pixels and disagree with this
    # system-DPI-aware process on monitors with a different scale factor, which pushed the window off-screen.)
    border = window.winfo_rootx() - window.winfo_x()  # side border (client area starts after it)
    caption = window.winfo_rooty() - window.winfo_y()  # title bar plus top border
    limit = 200  # Anything larger is a stale reading from a window that is not laid out yet.
    if not (0 <= border <= limit and 0 <= caption <= limit):
        border, caption = 8, 32
    return 0, 0, 2 * border, caption + border


def clamp(value, low, high):
    return max(low, min(value, max(low, high)))


def detect_language(text):
    # Kana means Japanese; Han characters outnumbering Latin words means Chinese.
    if re.search(r"[぀-ヿ]", text):
        return "日本語"
    han_count = len(re.findall(r"[一-鿿㐀-䶿]", text))
    latin_words = len(re.findall(r"[A-Za-z]+", text))
    if han_count and han_count >= latin_words:
        return "中文"
    if latin_words:
        return "English"
    return None


def get_clipboard_sequence():
    try:
        return ctypes.windll.user32.GetClipboardSequenceNumber()
    except Exception:
        return None


# Separate DLL handles so these prototypes do not affect ctypes.windll used elsewhere.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [ctypes.c_void_p]
_user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
_user32.EnumClipboardFormats.restype = ctypes.c_uint
_user32.GetClipboardData.argtypes = [ctypes.c_uint]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]


def open_clipboard(hwnd):
    # Another program may hold the clipboard for a moment; retry briefly.
    for _ in range(20):
        if _user32.OpenClipboard(hwnd):
            return True
        time.sleep(0.02)
    return False


def backup_clipboard(hwnd):
    """Copy every memory-based clipboard format as [(format, bytes)]; None if the clipboard can't be saved."""
    if not open_clipboard(hwnd):
        return None
    try:
        items, total, fmt = [], 0, 0
        while True:
            fmt = _user32.EnumClipboardFormats(fmt)
            if not fmt:
                return items
            if fmt in CLIPBOARD_SKIP_FORMATS:
                continue
            handle = _user32.GetClipboardData(fmt)
            pointer = _kernel32.GlobalLock(handle) if handle else None
            if not pointer:
                continue
            try:
                size = _kernel32.GlobalSize(handle)
                total += size
                if total > CLIPBOARD_BACKUP_LIMIT:
                    logging.warning("Clipboard too large to back up (%d bytes)", total)
                    return None
                items.append((fmt, ctypes.string_at(pointer, size)))
            finally:
                _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def restore_clipboard(hwnd, items):
    if items is None or not open_clipboard(hwnd):
        return
    try:
        _user32.EmptyClipboard()
        for fmt, data in items:
            handle = _kernel32.GlobalAlloc(0x0002, max(len(data), 1))  # GMEM_MOVEABLE
            pointer = _kernel32.GlobalLock(handle) if handle else None
            if not pointer:
                continue
            ctypes.memmove(pointer, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(fmt, handle):
                _kernel32.GlobalFree(handle)  # Ownership only passes to Windows on success.
    finally:
        _user32.CloseClipboard()


def read_clipboard_text(hwnd):
    if not open_clipboard(hwnd):
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        pointer = _kernel32.GlobalLock(handle) if handle else None
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def system_theme():
    """"dark" or "light", following the Windows app theme setting."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def set_title_bar_theme(window, dark):
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    except Exception:
        logging.exception("Could not set title bar theme")


def setup_logging():
    """Log warnings and errors to LOG_FILE (rotated at 256 KB) so problems on other machines can be diagnosed."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    sys.excepthook = lambda *exc_info: logging.error("Unhandled exception", exc_info=exc_info)
    threading.excepthook = lambda args: logging.error(
        "Unhandled exception in thread %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def write_version_file(path):
    """Write the PyInstaller version resource, so the exe's Properties > Details show name and version."""
    numbers = tuple((list(map(int, APP_VERSION.split("."))) + [0, 0, 0, 0])[:4])
    Path(path).write_text(f"""# -*- coding: utf-8 -*-
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404b0', [
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', 'OwnTranslation'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}'),
      StringStruct('Comments', '选中即译的快捷翻译工具，翻译服务由小牛翻译提供')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""", encoding="utf-8")


@lru_cache(maxsize=None)
def make_logo(size):
    """Draw the app logo: a blue tile with an "O"-shaped circular arrow (Own + translation) around 译."""
    scale = 4  # Supersample, then downscale for smooth edges.
    canvas = 256 * scale
    k = lambda value: value * scale
    # Diagonal gradient: average of a vertical and a horizontal linear gradient, then colorized.
    vertical = Image.linear_gradient("L")
    diagonal = ImageChops.add(vertical, vertical.rotate(90), scale=2)
    gradient = ImageOps.colorize(diagonal, (42, 167, 234), (11, 98, 176)).resize((canvas, canvas))
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle((k(8), k(8), k(248), k(248)), radius=k(58), fill=255)
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    image.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    center, radius, width = k(128), k(80), k(17)
    if size > 24:
        # Circular arrow, clockwise from the upper right round to the top, leaving a gap for the arrowhead.
        start, end = -22, 282
        bbox = (center - radius, center - radius, center + radius, center + radius)
        draw.arc(bbox, start, end, fill="white", width=width)
        phi = math.radians(end)
        px, py = center + (radius - width / 2) * math.cos(phi), center + (radius - width / 2) * math.sin(phi)
        tangent = (-math.sin(phi), math.cos(phi))
        normal = (math.cos(phi), math.sin(phi))
        head, half = k(34), k(25)
        tip = (px + tangent[0] * head, py + tangent[1] * head)
        draw.polygon([tip, (px + normal[0] * half, py + normal[1] * half),
                      (px - normal[0] * half, py - normal[1] * half)], fill="white")
        font_size = k(96)
    else:
        font_size = k(170)  # Tiny icons: only the character, as large as possible.
    for font_name in ("msyhbd.ttc", "msyh.ttc"):
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except OSError:
            font = None
    if font:
        left, top_, right, bottom_ = draw.textbbox((0, 0), "译", font=font)
        draw.text((center - (left + right) / 2, center - (top_ + bottom_) / 2), "译", font=font, fill="white")
    return image.resize((size, size), Image.LANCZOS)


def save_logo_ico(path):
    sizes = [256, 128, 64, 48, 32, 24, 16]
    images = [make_logo(size) for size in sizes]
    images[0].save(path, format="ICO", sizes=[(size, size) for size in sizes], append_images=images[1:])


def vk_to_key_name(vk):
    """Map a Windows virtual-key code to a hotkey key name; only letters, digits and F1-F12."""
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x60 <= vk <= 0x69:  # Numpad digits register as the same digit hotkey.
        return str(vk - 0x60)
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x7B:
        return f"f{vk - 0x6F}"
    return None


def key_vks(key):
    """Virtual-key codes for a hotkey's main key; a digit also matches the same numpad digit."""
    if len(key) == 1 and key.isdigit():
        return [0x30 + int(key), 0x60 + int(key)]
    if len(key) == 1 and "a" <= key <= "z":
        return [ord(key.upper())]
    if key[:1] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return [0x6F + int(key[1:])]
    raise ValueError(f"unsupported key {key!r}")


def parse_hotkey(hotkey):
    """"ctrl+alt+1" -> (RegisterHotKey modifier flags, main key VKs, every VK that is held while pressing it)."""
    *modifiers, key = hotkey.lower().split("+")
    if not modifiers or any(name not in MOD_FLAGS for name in modifiers):
        raise ValueError(f"invalid hotkey {hotkey!r}")
    vks = key_vks(key)
    held = [vk for name in modifiers for vk in MODIFIER_VKS[name]] + vks
    return sum(MOD_FLAGS[name] for name in set(modifiers)), vks, held


def key_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def pressed_modifiers():
    return [name for name in HOTKEY_MODIFIERS if any(key_down(vk) for vk in MODIFIER_VKS[name])]


def send_ctrl_c():
    for vk, flags in ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)):  # Ctrl down, C down, C up, Ctrl up
        ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


_user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
_user32.PeekMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
_user32.PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]


class HotkeyManager:
    """Global hotkeys through RegisterHotKey, served by a dedicated message-loop thread.

    Unlike a low-level keyboard hook, Windows does not silently drop these after sleep or a slow moment.
    WM_HOTKEY goes to the thread that registered the hotkey, so registering also happens on that thread;
    `set` hands the new hotkeys over with a thread message and waits for the result.
    """

    def __init__(self, callback):
        self.callback = callback  # Called with the config key ("hotkey"/"clipboard_hotkey") on the hotkey thread.
        self.ids = {}
        self.current = {}
        self.request, self.result = {}, None
        self.done = threading.Event()
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="hotkeys").start()
        ready.wait(5)

    def set(self, hotkeys):
        """Replace all hotkeys with {config key: "ctrl+alt+1"}; returns None, or an error message and keeps the old ones."""
        self.request, self.result, self.done = dict(hotkeys), "快捷键线程没有响应", threading.Event()
        _user32.PostThreadMessageW(self.thread_id, WM_APP_SET_HOTKEYS, 0, 0)
        self.done.wait(5)
        return self.result

    def stop(self):
        _user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def _run(self, ready):
        msg = ctypes.wintypes.MSG()
        # Create the thread's message queue before anyone posts to it.
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self.thread_id = _kernel32.GetCurrentThreadId()
        ready.set()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in self.ids:
                try:
                    self.callback(self.ids[msg.wParam])
                except Exception:
                    logging.exception("Hotkey callback failed")
            elif msg.message == WM_APP_SET_HOTKEYS:
                self.result = self._apply(self.request)
                self.done.set()
        self._unregister()

    def _apply(self, hotkeys):
        old = dict(self.current)
        self._unregister()
        error = self._register(hotkeys)
        if error:
            logging.warning("Could not register hotkeys %s: %s", hotkeys, error)
            self._unregister()
            self._register(old)
            return error
        self.current = dict(hotkeys)
        return None

    def _register(self, hotkeys):
        next_id = 1
        for name, hotkey in hotkeys.items():
            try:
                flags, vks, _held = parse_hotkey(hotkey)
            except ValueError:
                return f"{hotkey_label(hotkey)} 不是有效的快捷键"
            for index, vk in enumerate(vks):
                if _user32.RegisterHotKey(None, next_id, flags | MOD_NOREPEAT, vk):
                    self.ids[next_id] = name
                elif index == 0:  # The numpad variant of a digit is optional.
                    error = ctypes.get_last_error()
                    reason = "已被其他程序占用" if error == ERROR_HOTKEY_ALREADY_REGISTERED else f"注册失败（错误 {error}）"
                    return f"{hotkey_label(hotkey)} {reason}"
                next_id += 1
        return None

    def _unregister(self):
        for hotkey_id in self.ids:
            _user32.UnregisterHotKey(None, hotkey_id)
        self.ids.clear()


def hotkey_label(hotkey):
    return "+".join("Win" if key == "windows" else key.capitalize() for key in hotkey.split("+"))


def acquire_single_instance():
    global _instance_mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    # The handle is intentionally kept open for the lifetime of the process.
    _instance_mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def get_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


class TranslatorApp:
    def __init__(self):
        self.config = load_config()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *exc_info: logging.error("Error in Tk callback", exc_info=exc_info)
        # Pixel sizes below are designed for 96 DPI and scaled to the real screen DPI.
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.theme_name = system_theme()
        self.c = THEMES[self.theme_name]
        # Any window of this process can own the clipboard while it is being restored.
        self.clipboard_hwnd = self.root.winfo_id()
        self.pinned = False
        self.pin_button = None
        self.popup_shown_at = 0.0
        # (x, y, width) the popup was last placed at by fit_popup; a difference means the user moved it.
        self.placed_geometry = None
        self.is_capturing = False
        self.paused = False
        # True while the settings window is recording a new hotkey, so pressing it does not translate.
        self.recording_hotkey = False
        self.tray_icon = None
        self.popup = None
        self.settings_win = None
        self.toast = None
        self.hotkey_callbacks = {"hotkey": self.on_hotkey, "clipboard_hotkey": self.on_clipboard_hotkey,
                                 "ocr_hotkey": self.on_ocr_hotkey}
        self.snip_win = None
        self.hotkey_manager = HotkeyManager(lambda name: self.hotkey_callbacks[name]())
        self.hotkey_error = None
        self.original_text = ""
        self.truncated_note = ""
        self.translated_text = ""
        self.translation_ok = False
        self.request_id = 0
        self.cache = OrderedDict()
        self.popup_position = (100, 100)
        self.source_var = tk.StringVar(value=AUTO_SOURCE)
        self.target_var = tk.StringVar(value="中文")
        self.direction_label = None
        self.original_box = None
        self.result_box = None
        self.status_label = None
        self.translate_button = None
        self.is_translating = False
        self.settings_usage_label = None
        # Default title-bar/taskbar icon for every window; keep references so Tk does not drop the images.
        self.icon_images = [ImageTk.PhotoImage(make_logo(64)), ImageTk.PhotoImage(make_logo(32))]
        self.root.iconphoto(True, *self.icon_images)
        self.setup_style()
        self.register_startup_hotkeys()
        if self.config.get("show_kana", True):
            # Warm up the kana dictionary so the first Japanese translation is not delayed.
            threading.Thread(target=get_kakasi, daemon=True).start()
        if get_autostart():
            # Keep the registered path current in case the program was moved.
            try:
                set_autostart(True)
            except OSError:
                pass
        self.start_tray()
        self.root.after(300, self.first_run_check)
        self.root.mainloop()

    def s(self, *values):
        scaled = tuple(int(round(value * self.scale)) for value in values)
        return scaled[0] if len(scaled) == 1 else scaled

    def setup_style(self):
        c = self.c
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Own.TCombobox", padding=self.s(6, 4), font=("Microsoft YaHei UI", 9), arrowsize=self.s(12),
                        fieldbackground=c["card"], background=c["card"], foreground=c["text"], arrowcolor=c["soft_fg"],
                        bordercolor=c["input_border"], lightcolor=c["card"], darkcolor=c["card"])
        style.map("Own.TCombobox", fieldbackground=[("readonly", c["card"])], selectbackground=[("readonly", c["card"])],
                  selectforeground=[("readonly", c["text"])], background=[("active", c["hover"])])
        # The combobox drop-down list is a classic listbox styled through the option database.
        self.root.option_add("*TCombobox*Listbox.background", c["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
        # Classic tk checkbuttons keep a tiny fixed-size indicator on high-DPI screens; the ttk one can be scaled.
        # Checkboxes only appear inside the settings cards.
        style.configure("Own.TCheckbutton", background=c["card"], foreground=c["heading"], font=("Microsoft YaHei UI", 9),
                        indicatorsize=self.s(13), indicatormargin=self.s(0, 0, 6, 0), indicatorbackground=c["card"],
                        upperbordercolor=c["input_border"], lowerbordercolor=c["input_border"])
        style.map("Own.TCheckbutton", background=[("active", c["card"])],
                  indicatorbackground=[("selected", c["accent"])], indicatorforeground=[("selected", c["on_accent"])])
        # A slim scrollbar without arrows: just a rounded-looking thumb on the card color.
        style.layout("Own.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Own.Vertical.TScrollbar", troughcolor=c["card"], background=c["input_border"],
                        bordercolor=c["card"], lightcolor=c["input_border"], darkcolor=c["input_border"],
                        arrowsize=self.s(7), gripcount=0)
        style.map("Own.Vertical.TScrollbar", background=[("active", c["toggle_off"])],
                  lightcolor=[("active", c["toggle_off"])], darkcolor=[("active", c["toggle_off"])])

    def refresh_theme(self):
        """Follow a change of the Windows light/dark setting; the popup is rebuilt with the new colors."""
        name = system_theme()
        if name == self.theme_name:
            return
        self.theme_name, self.c = name, THEMES[name]
        self.setup_style()
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None

    def apply_hotkeys(self, hotkeys):
        """Register {config key: hotkey}; on failure the previous hotkeys stay active, the reason is in
        self.hotkey_error and False is returned."""
        self.hotkey_error = self.hotkey_manager.set(hotkeys)
        if self.hotkey_error:
            return False
        self.config.update(hotkeys)
        if self.tray_icon:
            self.tray_icon.title = self.tray_title()
        return True

    def register_startup_hotkeys(self):
        """Register the configured hotkeys; any that fail (e.g. taken by another program) are skipped, not all."""
        wanted = {name: self.config.get(name, default) for name, default in DEFAULT_HOTKEYS.items()}
        if self.apply_hotkeys(wanted):
            return
        working, problems = {}, []
        for name, hotkey in wanted.items():
            if self.hotkey_manager.set({**working, name: hotkey}) is None:
                working[name] = hotkey
            else:
                problems.append(hotkey_label(hotkey))
        self.hotkey_manager.set(working)
        message = f"快捷键 {'、'.join(problems)} 已被其他程序占用或无效，可在设置中修改"
        self.root.after(1000, lambda: self.show_toast(message))

    def suspend_hotkeys(self):
        # Registered hotkeys swallow their key presses, so they must be off while a new one is recorded.
        self.hotkey_manager.set({})

    def resume_hotkeys(self):
        self.hotkey_manager.set({name: self.config.get(name, default) for name, default in DEFAULT_HOTKEYS.items()})

    def first_run_check(self):
        if not self.config.get("api_key"):
            self.show_settings()

    def on_hotkey(self):
        # Runs on the hotkey thread; keep it short so further hotkey messages are not delayed.
        if self.paused or self.recording_hotkey or self.is_capturing:
            return
        self.is_capturing = True
        self.popup_position = get_cursor_position()
        threading.Thread(target=self.copy_selection_worker, daemon=True).start()

    def on_clipboard_hotkey(self):
        if self.paused or self.recording_hotkey:
            return
        self.popup_position = get_cursor_position()
        self.root.after(0, self.capture_clipboard_and_translate)

    def on_ocr_hotkey(self):
        if self.paused or self.recording_hotkey:
            return
        self.root.after(0, self.start_screenshot)

    def start_screenshot(self):
        """Freeze the monitor under the mouse and let the user drag a rectangle to recognize and translate."""
        if self.snip_win and self.snip_win.winfo_exists():
            return
        cursor = get_cursor_position()
        self.popup_position = cursor
        logical, physical = monitor_rects(*cursor)
        shot = ImageGrab.grab(bbox=physical, all_screens=True)
        left, top, right, bottom = logical
        width, height = right - left, bottom - top
        # The capture is in physical pixels; show it at this process's (possibly DPI-scaled) size.
        view = shot if shot.size == (width, height) else shot.resize((width, height), Image.LANCZOS)
        scale_x, scale_y = shot.width / width, shot.height / height

        win = tk.Toplevel(self.root)
        self.snip_win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{width}x{height}+{left}+{top}")
        canvas = tk.Canvas(win, width=width, height=height, highlightthickness=0, bd=0, cursor="crosshair")
        canvas.pack()
        images = {"dim": ImageTk.PhotoImage(ImageEnhance.Brightness(view).enhance(0.55)), "bright": None}
        canvas.create_image(0, 0, anchor="nw", image=images["dim"])
        hint = "拖动鼠标框选要翻译的文字，Esc 或右键取消"
        hint_item = canvas.create_text(width // 2, self.s(40), text=hint, font=("Microsoft YaHei UI", 11),
                                       fill="#ffffff")
        x1, y1, x2, y2 = canvas.bbox(hint_item)
        pad = self.s(12)
        canvas.tag_lower(canvas.create_rectangle(x1 - pad, y1 - pad // 2, x2 + pad, y2 + pad // 2,
                                                 fill="#20334d", outline=""), hint_item)
        selection = {"start": None, "box": None, "pending": False}
        bright_item = canvas.create_image(0, 0, anchor="nw")
        border_item = canvas.create_rectangle(0, 0, 0, 0, outline=self.c["accent"], width=max(1, self.s(2)), state="hidden")

        def current_box(event):
            sx, sy = selection["start"]
            return min(sx, event.x), min(sy, event.y), max(sx, event.x), max(sy, event.y)

        def redraw():
            # Coalesced: re-cropping the bright area on every mouse event would lag on large monitors.
            selection["pending"] = False
            box = selection["box"]
            if not box or box[2] - box[0] < 1 or box[3] - box[1] < 1:
                return
            images["bright"] = ImageTk.PhotoImage(view.crop(box))
            canvas.itemconfigure(bright_item, image=images["bright"])
            canvas.coords(bright_item, box[0], box[1])
            canvas.coords(border_item, *box)
            canvas.itemconfigure(border_item, state="normal")

        def press(event):
            selection["start"] = (event.x, event.y)

        def drag(event):
            if selection["start"]:
                selection["box"] = current_box(event)
                if not selection["pending"]:
                    selection["pending"] = True
                    win.after(15, redraw)

        def release(event):
            if not selection["start"]:
                return
            box = current_box(event)
            close()
            if box[2] - box[0] < self.s(6) or box[3] - box[1] < self.s(6):
                return  # A click without a real drag.
            crop = shot.crop((round(box[0] * scale_x), round(box[1] * scale_y),
                              round(box[2] * scale_x), round(box[3] * scale_y)))
            threading.Thread(target=self.ocr_worker, args=(crop,), daemon=True).start()

        def close(_event=None):
            if win.winfo_exists():
                win.destroy()
            self.snip_win = None

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        canvas.bind("<Button-3>", close)
        win.bind("<Escape>", close)
        win.images = images  # Keep the PhotoImages alive with the window.
        win.focus_force()
        win.grab_set()

    def ocr_worker(self, image):
        try:
            text = ocr_image(image)
        except OcrError as exc:
            message = str(exc)
            self.root.after(0, lambda: self.show_toast(message))
            return
        except Exception:
            logging.exception("Text recognition failed")
            self.root.after(0, lambda: self.show_toast("文字识别失败，详情见日志"))
            return
        self.root.after(0, lambda: self.capture_clipboard_and_translate(text, empty_message="没有识别到文字"))

    def copy_selection_worker(self):
        try:
            # Wait until the hotkey is released, otherwise the target app would see e.g. Ctrl+Alt+C.
            try:
                held = parse_hotkey(self.config.get("hotkey", DEFAULT_HOTKEYS["hotkey"]))[2]
            except ValueError:
                held = []
            deadline = time.monotonic() + HOTKEY_RELEASE_TIMEOUT
            while time.monotonic() < deadline and any(key_down(vk) for vk in held):
                time.sleep(0.01)
            # Save what the user had copied, so it can be put back after reading the selection.
            backup = backup_clipboard(self.clipboard_hwnd)
            if backup is None:
                logging.warning("Clipboard could not be backed up; it will not be restored")
            before = get_clipboard_sequence()
            send_ctrl_c()
            deadline = time.monotonic() + CLIPBOARD_UPDATE_TIMEOUT
            while get_clipboard_sequence() == before:
                if time.monotonic() >= deadline:
                    self.root.after(0, lambda: self.show_toast("未检测到选中的文字"))
                    return
                time.sleep(0.02)
            # Give the source app a moment to finish writing all clipboard formats.
            time.sleep(0.05)
            text = read_clipboard_text(self.clipboard_hwnd)
            restore_clipboard(self.clipboard_hwnd, backup)
            self.root.after(0, lambda: self.capture_clipboard_and_translate(text))
        finally:
            self.is_capturing = False

    def show_toast(self, text):
        """Show a short message next to the mouse cursor that disappears by itself."""
        if self.toast and self.toast.winfo_exists():
            self.toast.destroy()
        toast = tk.Toplevel(self.root)
        self.toast = toast
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        tk.Label(toast, text=text, font=("Microsoft YaHei UI", 9), bg=self.c["toast_bg"], fg=self.c["toast_fg"],
                 padx=self.s(12), pady=self.s(6)).pack()
        toast.update_idletasks()
        x, y = get_cursor_position()
        left, top, right, bottom = get_work_area(x, y)
        x = clamp(x + self.s(14), left, right - toast.winfo_reqwidth())
        y = clamp(y + self.s(18), top, bottom - toast.winfo_reqheight())
        toast.geometry(f"+{x}+{y}")
        toast.after(1500, toast.destroy)

    def capture_clipboard_and_translate(self, text=None, empty_message="剪贴板中没有文字"):
        """Translate `text`, or the current clipboard text if it is None."""
        if text is None:
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                text = ""
        text = text.strip()
        if not text:
            self.show_toast(empty_message)
            return
        self.original_text = text
        self.translated_text = ""
        self.translation_ok = False
        if not self.config.get("api_key"):
            self.show_settings()
            return
        # Source language defaults to automatic detection; target defaults to Japanese for Chinese text, else Chinese.
        self.source_var.set(AUTO_SOURCE)
        self.target_var.set("日本語" if detect_language(text) == "中文" else "中文")
        self.show_popup(position=self.popup_position)
        self.start_translation()

    def start_translation(self):
        # The original text box is editable, so always translate what it currently shows.
        if self.original_box and self.original_box.winfo_exists():
            self.original_text = self.original_box.get("1.0", "end-1c").strip()
        if not self.original_text:
            return
        if not self.config.get("api_key"):
            self.show_settings()
            return
        source_name = self.source_var.get()
        source = "auto" if source_name == AUTO_SOURCE else LANGUAGES.get(source_name, "auto")
        target_name = self.target_var.get()
        target = LANGUAGES.get(target_name, "zh")
        detected = detect_language(self.original_text) if source_name == AUTO_SOURCE else None
        # Cache under the detected language, so "auto" and an explicitly chosen language share entries.
        cache_source = LANGUAGES.get(detected, source) if detected else source
        limit = self.max_chars()
        self.truncated_note = ""
        # The limit only guards API credits, so cached text (e.g. a long translation swapped back) is never cut.
        if (cache_source, target, self.original_text) not in self.cache and len(self.original_text) > limit:
            # Silently cut overly long text to the configured limit; the status line mentions it afterwards.
            self.original_text = self.original_text[:limit]
            self.update_original_box()
            self.truncated_note = f"，只译了前 {limit} 字"
        text = self.original_text
        shown_source = f"{AUTO_SOURCE}（{detected}）" if detected else source_name
        self.direction_label.configure(text=f"原文语言：{shown_source}  →  翻译为：{target_name}")
        self.request_id += 1
        key = (cache_source, target, text)
        if key in self.cache:
            self.cache.move_to_end(key)
            self.finish_translation(self.request_id, key, self.cache[key], target_name, from_cache=True)
            return
        self.is_translating = True
        self.translate_button.configure(state="disabled")
        self.status_label.configure(text=f"正在翻译为{target_name}……")
        self.set_result_text("正在翻译……")
        # Translations are usually about as long as the original, so reserve that much room up front.
        self.fit_popup(result_lines=self.display_lines(self.original_box))
        threading.Thread(target=self.translate_worker, args=(self.request_id, key, target_name, source), daemon=True).start()

    def max_chars(self):
        try:
            return min(max(int(self.config.get("max_chars", DEFAULT_MAX_CHARS)), 1), NIUTRANS_MAX_CHARS)
        except (TypeError, ValueError):
            return DEFAULT_MAX_CHARS

    def translate_worker(self, request_id, key, target_name, source):
        """`key` is the cache key; `source` is what the API gets ("auto" stays "auto")."""
        _cache_source, target, text = key
        for attempt in range(2):
            try:
                translated = request_translation(self.config.get("api_key", ""), text, source, target)
                self.root.after(0, lambda: self.finish_translation(request_id, key, translated, target_name))
                return
            except TranslationError as exc:
                # Timeouts already waited the full REQUEST_TIMEOUT, so only other failures get one retry.
                logging.warning("Translation attempt %d failed (%s -> %s): %s", attempt + 1, source, target, exc)
                if attempt == 0 and not isinstance(exc, TranslationTimeout):
                    self.root.after(0, lambda: self.set_status(request_id, "请求失败，正在重试……"))
                    time.sleep(RETRY_DELAY)
                    continue
                message = str(exc)
                self.root.after(0, lambda: self.finish_translation(request_id, key, message, target_name, is_error=True))
                return

    def set_status(self, request_id, text):
        if request_id == self.request_id and self.status_label and self.status_label.winfo_exists():
            self.status_label.configure(text=text)

    def finish_translation(self, request_id, key, translated, target_name, is_error=False, from_cache=False):
        if not is_error and not from_cache:
            source, target, text = key
            self.cache[key] = translated
            self.cache.move_to_end(key)
            # Also remember the opposite direction, so swapping languages afterwards needs no request.
            reverse = (target, source, translated)
            if source != "auto" and source != target and reverse not in self.cache:
                self.cache[reverse] = text
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
            # Cached results cost no API credits, so only fresh results are counted.
            today = datetime.date.today().isoformat()
            if self.config.get("usage_date") != today:
                self.config["usage_date"] = today
                self.config["today_translated_chars"] = 0
            self.config["today_translated_chars"] = int(self.config.get("today_translated_chars", 0)) + len(key[2])
            save_config(self.config)
            self.refresh_settings_usage()
        # Ignore stale results if a newer translation was started while this request was running.
        if request_id != self.request_id:
            return
        self.translated_text = translated
        self.translation_ok = not is_error
        show_kana = not is_error and target_name == "日本語" and self.config.get("show_kana", True)
        self.set_result_text(translated, is_error, kana=show_kana, is_translation=not is_error)
        if self.status_label and self.status_label.winfo_exists():
            if is_error:
                status = "翻译失败"
            else:
                notes = ("，来自缓存" if from_cache else "") + self.truncated_note
                status = f"已翻译为{target_name}" + (f"（{notes[1:]}）" if notes else "")
            self.status_label.configure(text=status)
        if self.translate_button and self.translate_button.winfo_exists():
            self.translate_button.configure(state="normal")
        self.is_translating = False
        self.fit_popup()

    def set_result_text(self, text, is_error=False, kana=False, is_translation=False):
        if not self.result_box or not self.result_box.winfo_exists():
            return
        # Only a finished translation gets a count; placeholders and errors leave it empty.
        self.result_count.configure(text=f"{len(text):,} 字" if is_translation else "")
        self.result_box.configure(state="normal", fg=self.c["error"] if is_error else self.c["text"])
        self.result_box.delete("1.0", "end")
        self.result_box.insert("1.0", text)
        if kana:
            # Like Google Translate: the full reading goes in smaller text below the translation.
            # "复制译文" copies self.translated_text, so the reading is never copied.
            reading = kana_reading(text)
            if reading != text:
                self.result_box.insert("end", "\n" + reading, "kana")
        self.result_box.configure(state="disabled")

    def swap_languages(self):
        source_name = self.source_var.get()
        target_name = self.target_var.get()
        if source_name == AUTO_SOURCE:
            source_name = detect_language(self.original_text) or ("日本語" if target_name == "中文" else "中文")
        # Like common translators, the current translation becomes the new original text.
        if self.translation_ok and self.translated_text:
            self.original_text = self.translated_text
            self.update_original_box()
        self.source_var.set(target_name)
        self.target_var.set(source_name)
        self.start_translation()

    def show_popup(self, position=None):
        self.refresh_theme()
        if self.popup and self.popup.winfo_exists():
            win = self.popup
            self.update_original_box()
            self.set_result_text("")
            win.deiconify()
            self.fit_popup(monitor_point=position)
            self.focus_popup()
            return

        c = self.c
        win = tk.Toplevel(self.root)
        self.popup = win
        win.title(f"{APP_NAME} - 快捷翻译")
        win.geometry(f"{self.config.get('popup_width') or self.s(560)}x{self.s(360)}")
        win.minsize(*self.s(420, 240))
        win.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        win.configure(bg=c["bg"])
        set_title_bar_theme(win, self.theme_name == "dark")
        win.protocol("WM_DELETE_WINDOW", self.hide_popup)
        win.bind("<Escape>", lambda _event: self.hide_popup())
        # The toplevel's binding also fires for focus changes of every widget inside it.
        win.bind("<FocusOut>", lambda _event: self.root.after(150, self.close_if_focus_left))

        shell = tk.Frame(win, bg=c["bg"])
        shell.pack(fill="both", expand=True, padx=self.s(12), pady=self.s(10))

        # Language controls
        controls = tk.Frame(shell, bg=c["bg"])
        controls.pack(fill="x", pady=(0, self.s(8)))
        tk.Label(controls, text="原文语言", font=("Microsoft YaHei UI", 9, "bold"), fg=c["heading"], bg=c["bg"]).pack(side="left", padx=(0, self.s(6)))
        source_box = ttk.Combobox(controls, textvariable=self.source_var, values=[AUTO_SOURCE, *LANGUAGES.keys()], state="readonly", width=9, style="Own.TCombobox")
        source_box.pack(side="left")
        source_box.bind("<<ComboboxSelected>>", lambda _event: self.start_translation())
        tk.Button(controls, text="⇄", command=self.swap_languages, font=("Segoe UI Symbol", 13), fg=c["icon"], bg=c["bg"],
                  activebackground=c["hover"], activeforeground=c["accent_active"], relief="flat", bd=0,
                  padx=self.s(6), pady=0, cursor="hand2").pack(side="left", padx=self.s(4))
        tk.Label(controls, text="翻译为", font=("Microsoft YaHei UI", 9, "bold"), fg=c["heading"], bg=c["bg"]).pack(side="left", padx=(0, self.s(6)))
        target_box = ttk.Combobox(controls, textvariable=self.target_var, values=list(LANGUAGES.keys()), state="readonly", width=8, style="Own.TCombobox")
        target_box.pack(side="left")
        target_box.bind("<<ComboboxSelected>>", lambda _event: self.start_translation())
        settings_btn = tk.Button(controls, text="⚙ 设置", command=self.show_settings, font=("Microsoft YaHei UI", 9),
                                 bg=c["card"], fg=c["plain_fg"], activebackground=c["hover"], activeforeground=c["plain_fg"],
                                 relief="flat", bd=0, padx=self.s(10), pady=self.s(4), cursor="hand2")
        settings_btn.pack(side="right")
        # Pinned: clicking outside no longer closes the popup (Esc and "复制译文" still do).
        self.pin_button = tk.Button(controls, command=self.toggle_pin, font=(self.icon_font(), 11), relief="flat", bd=0,
                                    padx=self.s(8), pady=self.s(4), cursor="hand2")
        self.pin_button.pack(side="right", padx=(0, self.s(6)))
        self.update_pin_button()
        self.direction_label = tk.Label(shell, text=f"原文语言：{AUTO_SOURCE}  →  翻译为：中文", font=("Microsoft YaHei UI", 8), fg=c["muted"], bg=c["bg"], anchor="w")
        self.direction_label.pack(fill="x", pady=(0, self.s(6)))

        # Pack the footer before the cards so it keeps its space when the window is small.
        footer = tk.Frame(shell, bg=c["bg"])
        footer.pack(side="bottom", fill="x")

        def action_button(parent, text, command, primary=False):
            bg = c["accent"] if primary else c["card"]
            fg = c["on_accent"] if primary else c["plain_fg"]
            active_bg = c["accent_active"] if primary else c["hover"]
            btn = tk.Button(parent, text=text, command=command, font=("Microsoft YaHei UI", 9),
                            bg=bg, fg=fg, activebackground=active_bg, activeforeground=fg,
                            relief="flat", bd=0, padx=self.s(10), pady=self.s(5), cursor="hand2", disabledforeground=c["disabled"])
            return btn

        self.translate_button = action_button(footer, "⟳ 重新翻译", self.start_translation)
        self.translate_button.pack(side="right", padx=(self.s(6), 0))
        action_button(footer, "▢ 复制译文", self.copy_translation, primary=True).pack(side="right", padx=(self.s(6), 0))
        action_button(footer, "关闭", self.hide_popup).pack(side="right")
        # Packed after the buttons so a long status message cannot squeeze them.
        self.status_label = tk.Label(footer, text="等待翻译", bg=c["bg"], fg=c["status"], anchor="w", font=("Microsoft YaHei UI", 9))
        self.status_label.pack(side="left", fill="x", expand=True)

        def make_card(parent, title, icon, hint=""):
            card = tk.Frame(parent, bg=c["card"], highlightbackground=c["border"], highlightcolor=c["border"], highlightthickness=1, bd=0)
            heading = tk.Frame(card, bg=c["card_header"])
            heading.pack(fill="x")
            tk.Label(heading, text=icon, font=("Segoe UI Symbol", 11), fg=c["icon"], bg=c["card_header"]).pack(side="left", padx=self.s(10, 6), pady=self.s(4))
            tk.Label(heading, text=title, font=("Microsoft YaHei UI", 9, "bold"), fg=c["heading"], bg=c["card_header"]).pack(side="left", pady=self.s(4))
            if hint:
                tk.Label(heading, text=hint, font=("Microsoft YaHei UI", 8), fg=c["hint"], bg=c["card_header"]).pack(side="right", padx=self.s(10))
            return card

        def make_text(parent):
            box = tk.Text(parent, height=MIN_TEXT_LINES, wrap="word", font=("Microsoft YaHei UI", 10),
                          bg=c["card"], fg=c["text"], relief="flat", bd=0, highlightthickness=0, padx=self.s(10), pady=self.s(8),
                          selectbackground=c["select"], selectforeground=c["text"], insertbackground=c["text"],
                          spacing1=self.s(1), spacing3=self.s(1))
            # The scrollbar is laid over the box's right padding instead of packed beside it, so showing or
            # hiding it never changes the text width (which could otherwise flip it on and off forever).
            bar = ttk.Scrollbar(parent, orient="vertical", command=box.yview, style="Own.Vertical.TScrollbar")

            def on_scroll(first, last):
                bar.set(first, last)
                if float(first) > 0 or float(last) < 1:
                    bar.place(in_=box, relx=1.0, rely=0, relheight=1.0, x=-self.s(2), anchor="ne", width=self.s(7))
                    bar.lift(box)
                else:
                    bar.place_forget()

            box.configure(yscrollcommand=on_scroll)
            return box

        def make_counter(card):
            # Character count in the card's bottom-right corner; packed before the text box so it keeps its row.
            label = tk.Label(card, text="", font=("Microsoft YaHei UI", 8), fg=c["hint"], bg=c["card"], anchor="e")
            label.pack(side="bottom", fill="x", padx=self.s(10), pady=(0, self.s(4)))
            return label

        original_card = make_card(shell, "原文", "▤", hint="可编辑，Ctrl+Enter 重新翻译")
        original_card.pack(fill="both", expand=True, pady=(0, self.s(8)))
        self.original_count = make_counter(original_card)
        self.original_box = make_text(original_card)
        self.original_box.pack(fill="both", expand=True, padx=self.s(2), pady=(0, self.s(2)))
        self.original_box.bind("<Control-Return>", lambda _event: (self.start_translation(), "break")[1])
        self.original_box.bind("<<Modified>>", lambda _event: self.update_original_count())

        result_card = make_card(shell, "译文", "▣")
        result_card.pack(fill="both", expand=True, pady=(0, self.s(8)))
        self.result_count = make_counter(result_card)
        self.result_box = make_text(result_card)
        self.result_box.pack(fill="both", expand=True, padx=self.s(2), pady=(0, self.s(2)))
        self.result_box.tag_configure("kana", font=("Microsoft YaHei UI", 9), foreground=c["kana"], spacing1=self.s(6))
        self.result_box.configure(state="disabled")

        self.update_original_box()
        self.fit_popup(monitor_point=position or self.popup_position)
        self.focus_popup()

    def icon_font(self):
        return "Segoe Fluent Icons" if "Segoe Fluent Icons" in tkfont.families(self.root) else "Segoe MDL2 Assets"

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.update_pin_button()

    def update_pin_button(self):
        if not self.pin_button or not self.pin_button.winfo_exists():
            return
        c = self.c
        #  is a filled pin,  an outlined one.
        if self.pinned:
            self.pin_button.configure(text="", bg=c["soft_bg"], fg=c["accent"], activebackground=c["soft_active"],
                                      activeforeground=c["accent"])
        else:
            self.pin_button.configure(text="", bg=c["card"], fg=c["plain_fg"], activebackground=c["hover"],
                                      activeforeground=c["plain_fg"])

    def close_if_focus_left(self):
        """Hide the popup once focus has moved to another program (option "点击窗口外部自动关闭")."""
        win = self.popup
        if (self.pinned or not self.config.get("close_on_click_outside", True) or not win or not win.winfo_exists()
                or win.state() != "normal" or time.monotonic() - self.popup_shown_at < FOCUS_OUT_GRACE):
            return
        if not self.app_has_focus():
            self.hide_popup()

    def app_has_focus(self):
        # Tk's "focus" is empty only when no window of this program has focus; the combobox list and settings count as ours.
        return bool(self.root.tk.call("focus"))

    def hide_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.remember_user_position()
            self.popup.withdraw()

    def remember_user_position(self):
        """If the user dragged or resized the popup since it was last placed, save its position and width."""
        win = self.popup
        if not self.placed_geometry or not win or not win.winfo_exists() or win.state() != "normal":
            return
        current = (win.winfo_x(), win.winfo_y(), win.winfo_width())
        if all(abs(a - b) <= 3 for a, b in zip(current, self.placed_geometry)):
            return
        self.config["popup_position"] = [current[0], current[1]]
        self.config["popup_width"] = current[2]
        self.placed_geometry = current
        save_config(self.config)

    def reset_popup_position(self):
        self.config["popup_position"] = None
        self.config["popup_width"] = None
        save_config(self.config)
        if self.popup and self.popup.winfo_exists():
            self.placed_geometry = None
            win = self.popup
            win.geometry(f"{self.s(560)}x{win.winfo_height()}")
            if win.state() == "normal":
                self.fit_popup(monitor_point=(win.winfo_x() + win.winfo_width() // 2, win.winfo_y()))

    def focus_popup(self):
        win = self.popup
        self.popup_shown_at = time.monotonic()
        win.lift()
        win.focus_force()
        self.original_box.focus_set()
        self.original_box.mark_set("insert", "end-1c")

    def update_original_box(self):
        if not self.original_box or not self.original_box.winfo_exists():
            return
        self.original_box.delete("1.0", "end")
        self.original_box.insert("1.0", self.original_text)

    def update_original_count(self):
        """Show "N / limit 字" under the original text; red once it exceeds the per-request limit."""
        box = self.original_box
        box.edit_modified(False)  # Re-arm <<Modified>>, which only fires on the first change otherwise.
        count, limit = len(box.get("1.0", "end-1c").strip()), self.max_chars()
        self.original_count.configure(text=f"{count:,} / {limit:,} 字",
                                      fg=self.c["error"] if count > limit else self.c["hint"])

    def display_lines(self, box):
        # Count wrapped lines at the box's current width.
        count = box.count("1.0", "end", "update", "displaylines")
        if isinstance(count, tuple):
            count = count[0]
        return count or 1

    def fit_popup(self, monitor_point=None, result_lines=None):
        """Size the popup to its text and place it.

        The popup goes where the user last dragged it (config "popup_position", its top-left corner); until then it
        sits in the bottom-right corner of the work area (screen minus taskbar) of the monitor at `monitor_point`
        (the mouse when a translation starts), or of the monitor it is already on. It is always kept inside the
        work area of its monitor.
        """
        win = self.popup
        if not win or not win.winfo_exists():
            return
        # Pick up a drag made since the last placement before placing the window again.
        self.remember_user_position()
        win.update_idletasks()
        original_lines = self.display_lines(self.original_box)
        if result_lines is None:
            result_lines = self.display_lines(self.result_box)
            if self.result_box.tag_ranges("kana"):
                result_lines += 1  # Room for the extra spacing above the reading line.
        lines = lambda count: min(max(count, MIN_TEXT_LINES), MAX_TEXT_LINES)
        self.original_box.configure(height=lines(original_lines))
        self.result_box.configure(height=lines(result_lines))
        win.update_idletasks()
        width = win.winfo_width()
        saved = self.config.get("popup_position")
        if saved:
            x, y = saved
            left, top, right, bottom = get_work_area(x + width // 2, y + self.s(10))
        else:
            point = monitor_point or (win.winfo_x() + width // 2, win.winfo_y() + self.s(10))
            left, top, right, bottom = get_work_area(*point)
        height = min(win.winfo_reqheight(), int((bottom - top) * 0.85))
        # Keep the visible frame (title bar and borders included) inside the work area.
        off_left, off_top, off_right, off_bottom = frame_offsets(win)
        if not saved:
            # Default: bottom-right corner; the bottom edge stays put as the height changes.
            margin = self.s(16)
            x, y = right - width - off_right - margin, bottom - height - off_bottom - margin
        x = clamp(x, left - off_left, right - width - off_right)
        y = clamp(y, top - off_top, bottom - height - off_bottom)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.update_idletasks()
        self.placed_geometry = (win.winfo_x(), win.winfo_y(), win.winfo_width())

    def copy_translation(self):
        if self.translated_text:
            self.copy_text(self.translated_text)
            if self.config.get("close_after_copy", True):
                self.hide_popup()

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        if self.popup and self.popup.winfo_exists():
            self.popup.lift()

    def show_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        self.refresh_theme()
        c = self.c
        bg, card_bg, border, accent = c["bg"], c["card"], c["border"], c["accent"]
        text_color, muted = c["text"], c["muted"]
        icon_font = self.icon_font()
        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.title(f"{APP_NAME} 设置")
        # Center on the monitor the mouse is on, inside its work area.
        width, height = self.s(800, 560)
        left, top, right, bottom = get_work_area(*get_cursor_position())
        x = clamp(left + (right - left - width) // 2, left, right - width)
        y = clamp(top + (bottom - top - height) // 3, top, bottom - height)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=bg)
        set_title_bar_theme(win, self.theme_name == "dark")

        def rounded_rect(canvas, x1, y1, x2, y2, radius, **options):
            points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2,
                      x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
            return canvas.create_polygon(points, smooth=True, **options)

        def button(parent, text, command, kind="soft"):
            """kind: "primary" (filled blue), "soft" (tinted, for use on cards) or "plain" (card colored)."""
            colors = {"primary": (accent, c["on_accent"], c["accent_active"]),
                      "soft": (c["soft_bg"], c["soft_fg"], c["soft_active"]),
                      "plain": (card_bg, c["plain_fg"], c["hover"])}[kind]
            return tk.Button(parent, text=text, command=command, font=("Microsoft YaHei UI", 9),
                             bg=colors[0], fg=colors[1], activebackground=colors[2], activeforeground=colors[1],
                             relief="flat", bd=0, padx=self.s(14), pady=self.s(5), cursor="hand2",
                             disabledforeground=c["disabled"])

        def input_border(widget):
            """Flat input with a thin border that turns blue while focused."""
            widget.configure(relief="flat", bd=0, highlightthickness=1, highlightbackground=c["input_border"],
                             highlightcolor=accent, bg=card_bg, fg=text_color, insertbackground=text_color,
                             selectbackground=c["select"], selectforeground=text_color)
            return widget

        def checkbox(parent, text, variable, command=None):
            return ttk.Checkbutton(parent, text=text, variable=variable, command=command, style="Own.TCheckbutton")

        def toggle(parent, variable):
            """A Windows-style on/off switch bound to a BooleanVar."""
            w, h = self.s(40, 20)
            canvas = tk.Canvas(parent, width=w + 2, height=h + 2, bg=card_bg, highlightthickness=0, cursor="hand2")

            def pill(inset, color):
                x1, y1, x2, y2 = 1 + inset, 1 + inset, w - inset, h - inset
                d = y2 - y1
                canvas.create_oval(x1, y1, x1 + d, y2, fill=color, outline="")
                canvas.create_oval(x2 - d, y1, x2, y2, fill=color, outline="")
                canvas.create_rectangle(x1 + d / 2, y1, x2 - d / 2, y2, fill=color, outline="")

            def draw(*_args):
                canvas.delete("all")
                on = variable.get()
                if on:
                    pill(0, accent)
                else:
                    pill(0, c["toggle_off"])
                    pill(max(1, self.s(1)), card_bg)
                radius = self.s(5)
                cx, cy = (w - h // 2 + 1 if on else h // 2 + 1), h // 2 + 1
                canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                   fill=c["on_accent"] if on else c["knob_off"], outline="")

            canvas.bind("<Button-1>", lambda _event: variable.set(not variable.get()))
            variable.trace_add("write", draw)
            draw()
            return canvas

        def card(parent, title=None, desc=None):
            """A white rounded card; returns the inner frame to put content in."""
            holder = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0, height=1)
            holder.pack(fill="x", pady=(0, self.s(12)))
            inner = tk.Frame(holder, bg=card_bg)
            pad_x, pad_y = self.s(20, 16)
            item = holder.create_window(pad_x, pad_y, window=inner, anchor="nw")

            def redraw(_event=None):
                card_width = holder.winfo_width()
                if card_width <= 1:
                    return
                holder.itemconfigure(item, width=card_width - 2 * pad_x)
                card_height = inner.winfo_reqheight() + 2 * pad_y
                if int(holder.cget("height")) != card_height:
                    holder.configure(height=card_height)
                holder.delete("bg")
                rounded_rect(holder, 1, 1, card_width - 2, card_height - 2, self.s(8),
                             fill=card_bg, outline=border, tags="bg")
                holder.tag_lower("bg")

            holder.bind("<Configure>", redraw)
            inner.bind("<Configure>", redraw)
            if title:
                tk.Label(inner, text=title, font=("Microsoft YaHei UI", 10, "bold"), fg=text_color, bg=card_bg,
                         anchor="w").pack(fill="x")
            if desc:
                tk.Label(inner, text=desc, font=("Microsoft YaHei UI", 9), fg=muted, bg=card_bg, anchor="w",
                         justify="left", wraplength=self.s(480)).pack(fill="x", pady=(self.s(2), 0))
            return inner

        def option_row(parent, title, desc, make_widget, first=False):
            """A card row with a title and description on the left and a control on the right."""
            if not first:
                tk.Frame(parent, bg=c["separator"], height=1).pack(fill="x", pady=self.s(4))
            row = tk.Frame(parent, bg=card_bg)
            row.pack(fill="x", pady=self.s(4))
            make_widget(row).pack(side="right")
            text = tk.Frame(row, bg=card_bg)
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=title, font=("Microsoft YaHei UI", 10), fg=text_color, bg=card_bg, anchor="w").pack(fill="x")
            tk.Label(text, text=desc, font=("Microsoft YaHei UI", 9), fg=muted, bg=card_bg, anchor="w",
                     justify="left", wraplength=self.s(360)).pack(fill="x")

        # Layout: sidebar on the left; title, page cards and a save/cancel footer on the right.
        sidebar = tk.Frame(win, bg=bg, width=self.s(220))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        content = tk.Frame(win, bg=bg)
        content.pack(side="left", fill="both", expand=True, padx=(self.s(8), self.s(28)))

        header = tk.Frame(sidebar, bg=bg)
        header.pack(fill="x", padx=self.s(22), pady=self.s(26, 20))
        win.logo_image = ImageTk.PhotoImage(make_logo(self.s(34)))
        tk.Label(header, image=win.logo_image, bg=bg).pack(side="left")
        tk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold"), fg=text_color, bg=bg).pack(side="left", padx=self.s(10))

        footer = tk.Frame(content, bg=bg)
        footer.pack(side="bottom", fill="x", pady=self.s(14, 18))
        page_title = tk.Label(content, text="", font=("Microsoft YaHei UI", 15, "bold"), fg=text_color, bg=bg, anchor="w")
        page_title.pack(fill="x", pady=self.s(28, 14))
        page_holder = tk.Frame(content, bg=bg)
        page_holder.pack(fill="both", expand=True)

        pages = {}
        nav_items = {}
        current = {"page": None}

        def select_page(name):
            current["page"] = name
            for page in pages.values():
                page.pack_forget()
            pages[name].pack(fill="both", expand=True)
            page_title.configure(text=name)
            for draw in nav_items.values():
                draw()

        def nav_item(name, icon):
            item_w, item_h = self.s(188, 42)
            canvas = tk.Canvas(sidebar, width=item_w, height=item_h, bg=bg, highlightthickness=0, cursor="hand2")
            canvas.pack(padx=self.s(16), pady=self.s(2), anchor="w")
            hover = {"on": False}

            def draw():
                canvas.delete("all")
                selected = current["page"] == name
                if selected or hover["on"]:
                    rounded_rect(canvas, 0, 0, item_w - 1, item_h - 1, self.s(6),
                                 fill=c["nav_selected"] if selected else c["nav_hover"], outline="")
                if selected:
                    canvas.create_rectangle(0, self.s(12), self.s(3), item_h - self.s(12), fill=accent, outline="")
                canvas.create_text(self.s(24), item_h // 2, text=icon, font=(icon_font, 13), fill=text_color)
                canvas.create_text(self.s(52), item_h // 2, text=name, anchor="w", font=("Microsoft YaHei UI", 10), fill=text_color)

            def set_hover(on):
                hover["on"] = on
                draw()

            canvas.bind("<Enter>", lambda _event: set_hover(True))
            canvas.bind("<Leave>", lambda _event: set_hover(False))
            canvas.bind("<Button-1>", lambda _event: select_page(name))
            nav_items[name] = draw
            pages[name] = tk.Frame(page_holder, bg=bg)
            return pages[name]

        # 常规
        page = nav_item("常规", "")
        key_card = card(page, "小牛翻译 API Key", "用于调用小牛翻译的文本翻译接口，可在小牛翻译开放平台的控制台中获取。")
        key_var = tk.StringVar(value=self.config.get("api_key", ""))
        key_entry = input_border(tk.Entry(key_card, textvariable=key_var, show="*", font=("Microsoft YaHei UI", 10)))
        key_entry.pack(fill="x", pady=self.s(10, 8), ipady=self.s(3))
        key_row = tk.Frame(key_card, bg=card_bg)
        key_row.pack(fill="x")
        show_var = tk.BooleanVar(value=False)
        checkbox(key_row, "显示密钥", show_var, lambda: key_entry.configure(show="" if show_var.get() else "*")).pack(side="left")

        def test_key():
            api_key = key_var.get().strip()
            if not api_key:
                test_result.configure(text="请先填写 API Key。", fg=c["error"])
                return
            test_button.configure(state="disabled")
            test_result.configure(text="正在测试……", fg=c["status"])

            def worker():
                try:
                    request_translation(api_key, "hello", "en", "zh")
                    result = ("✓ API Key 可用", c["success"])
                except TranslationError as exc:
                    result = (str(exc), c["error"])

                def show():
                    if win.winfo_exists():
                        test_result.configure(text=result[0], fg=result[1])
                        test_button.configure(state="normal")
                self.root.after(0, show)

            threading.Thread(target=worker, daemon=True).start()

        test_button = button(key_row, "测试 API Key", test_key)
        test_button.pack(side="right")
        test_result = tk.Label(key_row, text="", font=("Microsoft YaHei UI", 9), bg=card_bg, justify="left",
                               wraplength=self.s(260))
        test_result.pack(side="right", padx=self.s(8))

        general_card = card(page)
        close_var = tk.BooleanVar(value=bool(self.config.get("close_after_copy", True)))
        option_row(general_card, "复制译文后自动关闭窗口", "点击“复制译文”后自动隐藏翻译窗口",
                   lambda parent: toggle(parent, close_var), first=True)
        outside_var = tk.BooleanVar(value=bool(self.config.get("close_on_click_outside", True)))
        option_row(general_card, "点击窗口外部自动关闭", "切换到其他窗口时自动隐藏翻译窗口；点窗口上的图钉可临时固定",
                   lambda parent: toggle(parent, outside_var))

        def position_button(parent):
            def reset():
                self.reset_popup_position()
                reset_button.configure(text="已恢复", state="disabled")
            reset_button = button(parent, "恢复到右下角", reset)
            if not self.config.get("popup_position"):
                reset_button.configure(state="disabled")
            return reset_button

        option_row(general_card, "翻译窗口位置", "默认显示在屏幕右下角；拖动后会记住位置，下次翻译仍在那里",
                   position_button)
        autostart_var = tk.BooleanVar(value=get_autostart())
        option_row(general_card, "开机自动启动", "登录 Windows 后自动在后台运行",
                   lambda parent: toggle(parent, autostart_var))

        # 快捷键: recorded as modifiers plus one main key, so the result always parses with parse_hotkey.
        page = nav_item("快捷键", "")
        def hotkey_recorder(parent, variable):
            """A key box: click it, then press a key combination to record it into `variable`."""
            idle_bg, recording_bg = c["card_header"], c["soft_bg"]
            box = tk.Label(parent, font=("Microsoft YaHei UI", 10), bg=idle_bg, width=18, padx=self.s(10),
                           pady=self.s(5), cursor="hand2", takefocus=1, highlightthickness=1,
                           highlightbackground=c["input_border"], highlightcolor=accent)
            recording = {"on": False}

            def show(text=None, color=text_color):
                box.configure(text=text or hotkey_label(variable.get()).replace("+", " + "), fg=color)

            def stop(_event=None):
                if recording["on"]:
                    recording["on"] = False
                    self.recording_hotkey = False
                    self.resume_hotkeys()
                box.configure(bg=idle_bg)
                show()

            def start(_event=None):
                if not recording["on"]:
                    recording["on"] = True
                    # Global hotkeys are released while recording, so the window receives the key presses
                    # and pressing the current hotkey does not translate.
                    self.recording_hotkey = True
                    self.suspend_hotkeys()
                error_label.configure(text="")
                box.configure(bg=recording_bg)
                box.focus_set()
                show("请按下快捷键……", accent)

            def on_key(event):
                if not recording["on"]:
                    return None
                # Read modifiers from the system key state, which also sees the Win key.
                modifiers = pressed_modifiers()
                if event.keycode in MODIFIER_VK_CODES:
                    show(" + ".join(hotkey_label(name) for name in modifiers) + " + …", accent)
                elif event.keycode == 27 and not modifiers:  # Esc cancels.
                    stop()
                elif (key := vk_to_key_name(event.keycode)) is None:
                    show("只支持字母、数字和 F1–F12", c["error"])
                elif not modifiers:
                    show("需要配合 Ctrl / Alt / Shift / Win", c["error"])
                else:
                    variable.set("+".join([*modifiers, key]))
                    stop()
                return "break"

            box.bind("<Button-1>", start)
            box.bind("<KeyPress>", on_key)
            box.bind("<FocusOut>", stop)
            show()
            return box

        hotkey_card = card(page, "全局快捷键", "点击右侧的按键框，然后直接按下新的组合键；按 Esc 取消。"
                                               "组合键需要包含 Ctrl / Alt / Shift / Win 中的至少一个。")
        tk.Frame(hotkey_card, bg=card_bg, height=self.s(6)).pack(fill="x")
        hotkey_vars = {}
        hotkey_rows = [("hotkey", "翻译选中文字", "在任意软件中选中文字后按下，自动复制并翻译"),
                       ("clipboard_hotkey", "翻译剪贴板", "直接翻译剪贴板里已有的文字，适合无法自动复制的软件"),
                       ("ocr_hotkey", "截图翻译", "框选屏幕上的区域，识别其中的文字（Windows 自带 OCR）并翻译")]
        for index, (config_key, title, desc) in enumerate(hotkey_rows):
            var = tk.StringVar(value=self.config.get(config_key, DEFAULT_HOTKEYS[config_key]))
            hotkey_vars[config_key] = var
            option_row(hotkey_card, title, desc, lambda parent, var=var: hotkey_recorder(parent, var), first=index == 0)

        # 翻译
        page = nav_item("翻译", "")
        translate_card = card(page)
        max_chars_var = tk.StringVar(value=str(self.max_chars()))
        option_row(translate_card, "单次翻译字数上限", f"超出部分不翻译，最多 {NIUTRANS_MAX_CHARS} 字",
                   lambda parent: input_border(tk.Spinbox(parent, from_=1, to=NIUTRANS_MAX_CHARS, increment=100,
                                                          textvariable=max_chars_var, width=6,
                                                          font=("Microsoft YaHei UI", 10), buttonbackground=card_bg)),
                   first=True)
        kana_var = tk.BooleanVar(value=bool(self.config.get("show_kana", True)))
        option_row(translate_card, "日文译文显示假名读音", "在译文下方以小字显示整句的假名读音，复制译文时不包含读音",
                   lambda parent: toggle(parent, kana_var))

        # 统计
        page = nav_item("统计", "")
        usage_card = card(page, "今日成功翻译")
        count_row = tk.Frame(usage_card, bg=card_bg)
        count_row.pack(fill="x", pady=self.s(6, 2))
        self.settings_usage_label = tk.Label(count_row, text="", font=("Microsoft YaHei UI", 22, "bold"), fg=accent, bg=card_bg)
        self.settings_usage_label.pack(side="left")
        tk.Label(count_row, text="字符", font=("Microsoft YaHei UI", 10), fg=muted, bg=card_bg).pack(side="left", anchor="s", padx=self.s(6), pady=self.s(0, 8))
        tk.Label(usage_card, text="只统计本机成功翻译的原文字符，命中缓存的翻译不计入，每天零点清零。",
                 font=("Microsoft YaHei UI", 9), fg=muted, bg=card_bg, anchor="w").pack(fill="x")
        self.refresh_settings_usage()
        credit_card = card(page, "剩余积分", "小牛翻译 API 无法查询剩余积分，请到官方平台的积分/用量页面查看。")
        button(credit_card, "查看小牛积分查询说明",
               lambda: webbrowser.open("https://niutrans.com/documents/overview?id=3")).pack(anchor="w", pady=self.s(10, 0))

        # 关于
        page = nav_item("关于", "")
        about_card = card(page)
        about_row = tk.Frame(about_card, bg=card_bg)
        about_row.pack(fill="x")
        win.about_image = ImageTk.PhotoImage(make_logo(self.s(64)))
        tk.Label(about_row, image=win.about_image, bg=card_bg).pack(side="left", padx=(0, self.s(16)))
        about_text = tk.Frame(about_row, bg=card_bg)
        about_text.pack(side="left", fill="x")
        tk.Label(about_text, text=f"{APP_NAME}  {APP_VERSION}", font=("Microsoft YaHei UI", 14, "bold"), fg=text_color, bg=card_bg, anchor="w").pack(fill="x")
        tk.Label(about_text, text="选中即译 · 中文 / 日本語 / English 快捷翻译", font=("Microsoft YaHei UI", 9),
                 fg=muted, bg=card_bg, anchor="w").pack(fill="x")
        tk.Label(about_text, text="翻译服务由小牛翻译提供", font=("Microsoft YaHei UI", 9),
                 fg=muted, bg=card_bg, anchor="w").pack(fill="x")

        def open_config_dir():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(CONFIG_DIR)

        config_card = card(page, "配置与日志", f"配置：{CONFIG_FILE}\n日志：{LOG_FILE}\n"
                                              "遇到问题时可以把日志文件发给开发者排查，日志不含 API Key 和翻译内容。")
        button(config_card, "打开所在目录", open_config_dir).pack(anchor="w", pady=self.s(10, 0))

        error_label = tk.Label(footer, text="", font=("Microsoft YaHei UI", 9), fg=c["error"], bg=bg,
                               justify="left", anchor="w", wraplength=self.s(360))

        def close():
            if self.recording_hotkey:
                self.recording_hotkey = False
                self.resume_hotkeys()
            win.destroy()
            self.settings_usage_label = None

        def save():
            hotkeys = {config_key: var.get() for config_key, var in hotkey_vars.items()}
            if len(set(hotkeys.values())) < len(hotkeys):
                select_page("快捷键")
                error_label.configure(text="几个快捷键不能相同。")
                return
            try:
                max_chars = int(max_chars_var.get())
            except ValueError:
                max_chars = 0
            if not 1 <= max_chars <= NIUTRANS_MAX_CHARS:
                select_page("翻译")
                error_label.configure(text=f"单次翻译字数上限需要是 1 到 {NIUTRANS_MAX_CHARS} 之间的整数。")
                return
            if not self.apply_hotkeys(hotkeys):
                select_page("快捷键")
                error_label.configure(text=f"无法注册快捷键：{self.hotkey_error}，请换一个组合。")
                return
            try:
                set_autostart(autostart_var.get())
            except OSError as exc:
                logging.warning("Could not change autostart", exc_info=True)
                select_page("常规")
                error_label.configure(text=f"设置开机自动启动失败：{exc}")
                return
            self.config["api_key"] = key_var.get().strip()
            self.config["close_after_copy"] = close_var.get()
            self.config["close_on_click_outside"] = outside_var.get()
            self.config["show_kana"] = kana_var.get()
            self.config["max_chars"] = max_chars
            save_config(self.config)
            # The popup's "N / limit 字" counter otherwise keeps the old limit until the text changes.
            if self.original_box and self.original_box.winfo_exists():
                self.update_original_count()
            close()

        button(footer, "取消", close, kind="plain").pack(side="right")
        button(footer, "保存", save, kind="primary").pack(side="right", padx=self.s(8))
        error_label.pack(side="left", fill="x", expand=True)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda _event: close())
        select_page("常规")
        win.focus_force()

    def refresh_settings_usage(self):
        label = getattr(self, "settings_usage_label", None)
        if label is None:
            return
        try:
            if not label.winfo_exists():
                self.settings_usage_label = None
                return
        except tk.TclError:
            self.settings_usage_label = None
            return
        today = datetime.date.today().isoformat()
        if self.config.get("usage_date") != today:
            self.config["usage_date"] = today
            self.config["today_translated_chars"] = 0
        count = int(self.config.get("today_translated_chars", 0))
        label.configure(text=f"{count:,}")

    def tray_title(self):
        label = lambda name: hotkey_label(self.config.get(name, DEFAULT_HOTKEYS[name]))
        # Windows shows each line of the tray tooltip on its own row (128 characters at most).
        header = f"{APP_NAME}  {APP_VERSION}" + ("（已暂停）" if self.paused else "")
        return (f"{header}\n选中翻译：{label('hotkey')}\n剪贴板翻译：{label('clipboard_hotkey')}"
                f"\n截图翻译：{label('ocr_hotkey')}")

    def show_last_translation(self):
        if not (self.popup and self.popup.winfo_exists() and self.original_text):
            self.show_toast("还没有翻译记录")
            return
        self.popup.deiconify()
        self.fit_popup()
        self.focus_popup()

    def start_tray(self):
        image = make_logo(64)
        menu = pystray.Menu(
            # The default item also runs when the tray icon is left-clicked.
            pystray.MenuItem("显示上次翻译", lambda icon, item: self.root.after(0, self.show_last_translation), default=True),
            # Short delay so the tray menu has closed before the screen is captured.
            pystray.MenuItem("截图翻译", lambda icon, item: self.root.after(300, self.start_screenshot)),
            pystray.MenuItem("设置", lambda icon, item: self.root.after(0, self.show_settings)),
            pystray.MenuItem("暂停/恢复快捷键", self.toggle_pause),
            pystray.MenuItem("退出", self.quit_app),
        )
        self.tray_icon = pystray.Icon(APP_NAME, image, self.tray_title(), menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def toggle_pause(self, icon, item):
        self.paused = not self.paused
        if self.tray_icon:
            self.tray_icon.title = self.tray_title()

    def quit_app(self, icon=None, item=None):
        try:
            self.hotkey_manager.stop()
        except Exception:
            pass
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    setup_logging()
    if not acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} 已在运行，请在系统托盘中查看。", APP_NAME, 0x40)
        sys.exit(0)
    enable_dpi_awareness()
    logging.info("%s %s started", APP_NAME, APP_VERSION)
    TranslatorApp()
