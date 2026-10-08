"""Explicit exam-only native key grabs. Process exit releases every grab/hook."""
import ctypes
import json
import os
import signal
import sys
import time
import threading
import queue


def report(kind, **fields):
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)


def x11_guard():
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise RuntimeError("Wayland не позволяет подтвердить требуемый перехват клавиш")
    x = ctypes.CDLL("libX11.so.6")
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XOpenDisplay.restype = ctypes.c_void_p
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Не удалось открыть дисплей X11")
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    root = x.XDefaultRootWindow(display)
    x.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x.XStringToKeysym.restype = ctypes.c_ulong
    x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x.XKeysymToKeycode.restype = ctypes.c_uint
    x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XPending.argtypes = [ctypes.c_void_p]
    x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    errors = []
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    error_handler = callback_type(lambda _display, _event: errors.append(True) or 0)
    x.XSetErrorHandler.argtypes = [callback_type]
    x.XSetErrorHandler(error_handler)
    bindings = [("Tab", 8), ("Tab", 9), ("Super_L", 0), ("Super_R", 0), ("Print", 0), ("c", 4), ("v", 4)]
    grabs = []
    names = {}
    try:
        for name, modifier in bindings:
            code = x.XKeysymToKeycode(display, x.XStringToKeysym(name.encode()))
            if not code:
                raise RuntimeError(f"Клавиша {name} отсутствует в раскладке X11")
            names[code] = name
            for locks in ((32768,) if name in ("Super_L", "Super_R", "Print") else (0, 2, 16, 18)):
                x.XGrabKey(display, code, modifier | locks, root, 0, 1, 1)
                grabs.append((code, modifier | locks))
        x.XSync(display, 0)
        if errors:
            raise RuntimeError("Часть клавиш занята оконным менеджером. Строгий экзамен недоступен")
        class KeyEvent(ctypes.Structure):
            _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int), ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong), ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int), ("x_root", ctypes.c_int), ("y_root", ctypes.c_int), ("state", ctypes.c_uint), ("keycode", ctypes.c_uint), ("same_screen", ctypes.c_int)]
        class Event(ctypes.Union):
            _fields_ = [("key", KeyEvent), ("pad", ctypes.c_long * 24)]
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        report("ready", method="X11 XGrabKey")
        event = Event()
        while True:
            while x.XPending(display):
                x.XNextEvent(display, ctypes.byref(event))
                if event.key.type == 2:
                    report("blocked", key=names.get(event.key.keycode, "key"))
            time.sleep(0.02)
    finally:
        for code, modifier in grabs:
            x.XUngrabKey(display, code, modifier, root)
        x.XSync(display, 0)
        x.XCloseDisplay(display)


def windows_guard():
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    class KeyboardEvent(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    events = queue.SimpleQueue()
    def write_events():
        while True:
            try:
                report("blocked", key=events.get())
            except BrokenPipeError:
                os._exit(0)
    threading.Thread(target=write_events, daemon=True).start()
    def hook(code, message, data):
        if code >= 0:
            key = ctypes.cast(data, ctypes.POINTER(KeyboardEvent)).contents
            ctrl = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
            alt = bool(key.flags & 0x20)
            if key.vkCode in (0x5B, 0x5C, 0x2C) or (key.vkCode == 0x09 and alt) or (ctrl and key.vkCode in (0x43, 0x56)):
                if message in (0x100, 0x104):
                    events.put(f"VK_{key.vkCode:02X}")
                return 1
        return user32.CallNextHookEx(None, code, message, data)
    callback = callback_type(hook)
    handle = user32.SetWindowsHookExW(13, callback, kernel32.GetModuleHandleW(None), 0)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        report("ready", method="Windows WH_KEYBOARD_LL")
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    finally:
        user32.UnhookWindowsHookEx(handle)


if __name__ == "__main__":
    # A closed parent pipe releases native grabs even when Electron is killed without cleanup.
    threading.Thread(target=lambda: (sys.stdin.buffer.read(), os._exit(0)), daemon=True).start()
    try:
        if sys.platform == "win32":
            windows_guard()
        elif sys.platform.startswith("linux"):
            x11_guard()
        else:
            raise RuntimeError("Строгая защита не поддерживается на этой ОС")
    except Exception as exc:
        report("error", message=str(exc))
        sys.exit(1)
