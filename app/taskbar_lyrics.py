"""把歌词窗口**真正嵌入 Windows 任务栏内部**（TrafficMonitor 式重父化实现）。

与上一版的区别
==============
* **背景全透明**：不再画胶囊底色，改用 ``UpdateLayeredWindow`` 提交 32-bit BGRA
  逐像素 alpha 画面（GDI 黑字白底生成灰度覆盖率蒙版，再用 numpy 合成抗锯齿文字）。
* **固定宽度**：窗口宽度恒定（默认 480 物理像素，空隙不足时收窄），不吃满整段空隙。
* **超长行横向跑马灯**：单行宽度超出窗口时，先停顿展示开头，再匀速向左滚出、末尾停顿后循环。
* **换行走垂直滚动动画**：旧行向上移出并淡出，新行同时自下而上进入（OutCubic，约 240ms）。

实现要点
========
1. ``FindWindowW("Shell_TrayWnd")`` 找任务栏，``WS_CHILD + WS_EX_LAYERED`` 子窗口经
   ``SetParent`` 重父化到任务栏、``SetWindowPos`` 提到兄弟窗口最顶（Win11 铺满整栏的 XAML
   层默认会盖住新子窗口），右对齐到托盘通知区左侧空隙。
2. 所有 HWND/GDI 操作与逐帧渲染集中在**专用渲染线程**（Win32 窗口有线程亲和），对外方法
   只做线程安全的状态写入；约 60fps，仅当画面真的变化时才 ``UpdateLayeredWindow``，静止不耗 CPU。
3. 渲染线程周期性自检任务栏句柄/几何，Explorer 重启、DPI/分辨率变化后自动重建与重定位。

非 Windows 平台导入即失败，由 :mod:`main_window` 捕获并优雅降级。
"""

from __future__ import annotations

import ctypes
import threading
import winreg
from ctypes import wintypes

import numpy as np

# ----------------------------------------------------------------------------
# Win32 原型声明（64 位下必须显式声明，否则句柄会被按 32 位截断）
# ----------------------------------------------------------------------------
_u32 = ctypes.windll.user32
_g32 = ctypes.windll.gdi32
_k32 = ctypes.windll.kernel32

LRESULT = ctypes.c_longlong
_H = wintypes.HANDLE
_WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)

_u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.DefWindowProcW.restype = LRESULT
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                          ctypes.POINTER(wintypes.RECT), wintypes.UINT]
_u32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
_u32.CreateWindowExW.restype = wintypes.HWND
_u32.DestroyWindow.argtypes = [wintypes.HWND]
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
_u32.GetParent.argtypes = [wintypes.HWND]
_u32.GetParent.restype = wintypes.HWND
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowW.restype = wintypes.HWND
_u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowExW.restype = wintypes.HWND
_u32.RegisterClassW.argtypes = [ctypes.c_void_p]
_u32.RegisterClassW.restype = wintypes.ATOM
_u32.GetDC.argtypes = [wintypes.HWND]
_u32.GetDC.restype = wintypes.HDC
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
    ctypes.c_void_p, wintypes.DWORD]
_u32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                             wintypes.UINT, wintypes.UINT]
_u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_u32.DispatchMessageW.restype = LRESULT

_g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.DeleteDC.argtypes = [wintypes.HDC]
_g32.SelectObject.argtypes = [wintypes.HDC, _H]
_g32.SelectObject.restype = _H
_g32.DeleteObject.argtypes = [_H]
_g32.CreateFontW.restype = _H
_g32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
_g32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
_g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), _H, wintypes.DWORD]
_g32.CreateDIBSection.restype = _H
_g32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                      ctypes.POINTER(wintypes.SIZE)]

_k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_k32.GetModuleHandleW.restype = wintypes.HMODULE
_k32.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(ctypes.c_int64)]
_k32.QueryPerformanceCounter.argtypes = [ctypes.POINTER(ctypes.c_int64)]

# 窗口/绘制常量
_WS_CHILD = 0x40000000
_WS_EX_LAYERED, _WS_EX_NOACTIVATE = 0x00080000, 0x08000000
_SW_SHOWNOACTIVATE, _SW_HIDE = 4, 0
_DT_LEFT, _DT_VCENTER, _DT_SINGLELINE, _DT_NOPREFIX = 0x0, 0x4, 0x20, 0x800
_ULW_ALPHA = 0x2
_AC_SRC_OVER, _AC_SRC_ALPHA = 0x00, 0x01
_BI_RGB, _DIB_RGB_COLORS = 0, 0
_PM_REMOVE = 0x1
_CLASS_NAME = "XianYueTaskbarLyric"

# 布局/动画参数（物理像素、毫秒）
FIXED_W = 480           # 固定窗口宽度（不吃满空隙；空隙不足时收窄）
PAD_X = 10              # 文字左右内边距
HOLD_HEAD_MS = 1300     # 超长行：开头停留
HOLD_TAIL_MS = 1600     # 超长行：末尾停留
SCROLL_PX_PER_SEC = 55  # 跑马灯匀速
SWITCH_MS = 240         # 换行走位动画时长
FRAME_S = 1.0 / 60.0

# 文字颜色（B,G,R）。按系统任务栏深浅色主题二选一；背景始终透明。
_FG_LIGHT = (38, 30, 27)    # 浅任务栏：深色文字 #1B1E26
_FG_DARK = (255, 255, 255)  # 深任务栏：白色文字

# 高精度时间基准（毫秒），不依赖 Qt。
_qpf = ctypes.c_int64(0)
_k32.QueryPerformanceFrequency(ctypes.byref(_qpf))
_QPF = float(_qpf.value) or 1.0
_qpc = ctypes.c_int64()


def _now_ms() -> int:
    _k32.QueryPerformanceCounter(ctypes.byref(_qpc))
    return int(_qpc.value / _QPF * 1000)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


class _Blend(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


class _WndClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", _H), ("hIcon", _H), ("hCursor", _H),
                ("hbrBackground", _H), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


def _ease_out_cubic(p: float) -> float:
    return 1.0 - (1.0 - p) ** 3


class TaskbarLyrics:
    """任务栏内嵌透明歌词窗。对外方法线程安全；渲染在内部专用线程进行。"""

    _class_registered = False
    _wndproc_keep = None

    def __init__(self) -> None:
        self._hwnd = None
        self._visible = False
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._wake = threading.Event()
        self._pending_text = ""
        self._has_pending = False
        self._reassert_req = False
        self._thread: threading.Thread | None = None
        self._cur = ""
        self._hinst = _k32.GetModuleHandleW(None)
        self._ensure_class()

    # ------------------------------ 类注册 ------------------------------
    def _ensure_class(self) -> None:
        if TaskbarLyrics._class_registered:
            return
        TaskbarLyrics._wndproc_keep = _WNDPROC(TaskbarLyrics._window_proc)
        wc = _WndClass()
        wc.lpfnWndProc = TaskbarLyrics._wndproc_keep
        wc.hInstance = self._hinst
        wc.lpszClassName = _CLASS_NAME
        _u32.RegisterClassW(ctypes.byref(wc))
        TaskbarLyrics._class_registered = True

    @staticmethod
    def _window_proc(hwnd, msg, wp, lp):
        return _u32.DefWindowProcW(hwnd, msg, wp, lp)

    # ------------------------------ 主题 ------------------------------
    @staticmethod
    def _read_fg():
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            light, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            winreg.CloseKey(key)
        except OSError:
            light = 1
        return _FG_LIGHT if light else _FG_DARK

    # --------------------------- 任务栏几何 ---------------------------
    @staticmethod
    def _find_taskbar():
        return _u32.FindWindowW("Shell_TrayWnd", None)

    @staticmethod
    def _rect(hwnd):
        rc = wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(rc))
        return rc

    def _compute_geometry(self):
        """返回 (parent, x, y, w, h) 物理像素，固定宽度、右对齐；找不到返回 None。"""
        taskbar = self._find_taskbar()
        if not taskbar:
            return None
        tb = self._rect(taskbar)
        bar_h = tb.bottom - tb.top
        if bar_h <= 0:
            return None
        left = tb.left
        for cls in ("Start", "ReBarWindow32"):
            child = _u32.FindWindowExW(taskbar, None, cls, None)
            if child:
                left = max(left, self._rect(child).right)
        right = tb.right
        tray = _u32.FindWindowExW(taskbar, None, "TrayNotifyWnd", None)
        if tray:
            right = self._rect(tray).left
        pad = 12
        avail = right - left - pad * 2
        if avail < 120:
            avail = tb.right - tb.left - pad * 2
        w = min(FIXED_W, max(0, avail))
        x = right - pad - w          # 右对齐到托盘左侧空隙
        h = bar_h - 8                # 上下各留 4px
        return taskbar, x, 4, w, h

    # ============================== 对外 API ==============================
    def is_supported(self) -> bool:
        return bool(self._find_taskbar())

    def show(self) -> bool:
        if not self.is_supported():
            return False
        with self._lock:
            self._visible = True
            self._start_thread_locked()
            self._wake.set()
        return True

    def hide(self) -> None:
        with self._lock:
            self._visible = False
            self._wake.set()

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def _text(self) -> str:
        return self._cur or self._pending_text or ""

    def set_text(self, text: str) -> None:
        text = text or ""
        with self._lock:
            if text == self._pending_text and not self._has_pending and self._cur == text:
                return
            self._pending_text = text
            self._has_pending = True
            self._start_thread_locked()
            self._wake.set()

    def reassert(self) -> None:
        """外部可请求立即复核几何（渲染线程本身也会周期性自检）。"""
        with self._lock:
            self._reassert_req = True
            self._wake.set()

    def close(self) -> None:
        self._stop_evt.set()
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(1200)
        self._thread = None

    def _start_thread_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._run, name="TaskbarLyricRender",
                                            daemon=True)
            self._thread.start()

    # ====================== 渲染线程 ======================
    def _run(self) -> None:
        try:
            self._render_loop()
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()

    def _render_loop(self) -> None:
        geom = self._compute_geometry()
        if geom is None:
            return
        taskbar, x, y, w, h = geom
        fg = self._read_fg()
        font = self._create_font(h)
        screen_dc = _u32.GetDC(None)
        canvas_dc, canvas_bmp, canvas = self._make_dib(screen_dc, w, h)
        mask_dc, mask_bmp, mask = self._make_dib(screen_dc, w, h)
        _g32.SelectObject(mask_dc, font)

        ex = _WS_EX_LAYERED | _WS_EX_NOACTIVATE
        hwnd = _u32.CreateWindowExW(ex, _CLASS_NAME, "tblyric", _WS_CHILD,
                                    x, y, w, h, taskbar, 0, self._hinst, None)
        if not hwnd:
            self._destroy_gdi(screen_dc, canvas_dc, canvas_bmp, mask_dc, mask_bmp, font)
            return
        self._hwnd = hwnd
        self._place(hwnd, x, y, w, h)
        _u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)

        self._W, self._H = w, h
        self._fg = fg
        self._cur = self._pending_text or ""
        self._out = ""
        self._vprog = 1.0
        self._v_start = 0
        self._mq_text = None
        self._mq_t0 = 0
        self._measure_cache: dict[str, int] = {}
        self._frame_sig = None
        last_sig = None
        last_geom = (x, y, w, h)
        last_theme = fg
        last_selfcheck = 0
        shown_once = False

        src_pt = wintypes.POINT(0, 0)
        blend = _Blend(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)

        while not self._stop_evt.is_set():
            self._pump_messages()
            now = _now_ms()
            want_visible = self._visible

            # 主题变化
            fg_now = self._read_fg()
            if fg_now != last_theme:
                last_theme = fg_now
                self._fg = fg_now

            # 新文本 -> 触发垂直滚动切换
            if self._has_pending:
                with self._lock:
                    new_text = self._pending_text
                    self._has_pending = False
                if new_text != self._cur:
                    self._out = self._cur
                    self._cur = new_text
                    self._vprog = 0.0
                    self._v_start = now
                    self._mq_reset(now)

            # 推进垂直动画
            if self._vprog < 1.0:
                self._vprog = min(1.0, (now - self._v_start) / SWITCH_MS)

            # 自检/重定位（约每秒，或外部请求）
            if self._reassert_req or (now - last_selfcheck > 1000):
                self._reassert_req = False
                last_selfcheck = now
                geo_now = self._compute_geometry()
                if geo_now is not None:
                    tb2, x2, y2, w2, h2 = geo_now
                    parent_ok = _u32.IsWindow(hwnd) and _u32.GetParent(hwnd) == tb2
                    if not parent_ok:
                        self._recreate(tb2, x2, y2, w2, h2, screen_dc, canvas_dc, canvas_bmp,
                                       mask_dc, mask_bmp, font)
                        canvas_dc, canvas_bmp, canvas = self._new_canvas
                        mask_dc, mask_bmp, mask = self._new_mask
                        hwnd = self._hwnd
                        _g32.SelectObject(mask_dc, font)
                        if h2 != self._H or w2 != self._W:
                            self._measure_cache.clear()
                        self._W, self._H = w2, h2
                        last_geom = (x2, y2, w2, h2)
                        last_sig = None
                        continue
                    if (x2, y2, w2, h2) != last_geom:
                        if w2 != self._W or h2 != self._H:
                            self._rebuild_dibs(screen_dc, w2, h2, canvas_dc, canvas_bmp,
                                               mask_dc, mask_bmp)
                            canvas_dc, canvas_bmp, canvas = self._new_canvas
                            mask_dc, mask_bmp, mask = self._new_mask
                            self._W, self._H = w2, h2
                            self._measure_cache.clear()
                            _g32.SelectObject(mask_dc, font)
                            # 尺寸变了字号也要变
                            new_font = self._create_font(h2)
                            _g32.DeleteObject(font)
                            font = new_font
                            _g32.SelectObject(mask_dc, font)
                        self._place(hwnd, x2, y2, w2, h2)
                        last_geom = (x2, y2, w2, h2)
                        last_sig = None

            # 先合成（内部记录本帧签名），仅当画面变化或显隐切换时提交
            self._compose(canvas, mask_dc, mask, font, now)
            sig = self._frame_sig if want_visible else ("hidden",)
            if want_visible:
                _u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
                if sig != last_sig or not shown_once:
                    size = wintypes.SIZE(self._W, self._H)
                    dst = wintypes.POINT(last_geom[0], last_geom[1])
                    ok = _u32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(dst),
                                                 ctypes.byref(size), canvas_dc,
                                                 ctypes.byref(src_pt), 0,
                                                 ctypes.byref(blend), _ULW_ALPHA)
                    shown_once = bool(ok) or shown_once
            elif _u32.IsWindow(hwnd):
                _u32.ShowWindow(hwnd, _SW_HIDE)
            last_sig = sig

            self._wake.wait(FRAME_S)
            self._wake.clear()

        # --- 退出清理 ---
        if _u32.IsWindow(hwnd):
            _u32.DestroyWindow(hwnd)
        self._destroy_gdi(screen_dc, canvas_dc, canvas_bmp, mask_dc, mask_bmp, font)
        self._hwnd = None

    # ------------------------------ 资源 ------------------------------
    @staticmethod
    def _create_font(h: int) -> int:
        font_h = -max(15, int(h * 0.36))
        # 第 12 参 cQuality=4(ANTIALIASED_QUALITY)：灰度抗锯齿，便于生成覆盖率蒙版。
        return _g32.CreateFontW(font_h, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 4, 0,
                                "Microsoft YaHei UI")

    @staticmethod
    def _make_dib(screen_dc, w, h):
        """建一块与 numpy 共享内存的 32bit top-down BGRA DIB。"""
        bi = _BitmapInfo()
        hdr = bi.bmiHeader
        hdr.biSize = ctypes.sizeof(_BitmapInfoHeader)
        hdr.biWidth, hdr.biHeight = w, -h
        hdr.biPlanes, hdr.biBitCount = 1, 32
        hdr.biCompression = _BI_RGB
        bits = ctypes.c_void_p()
        bmp = _g32.CreateDIBSection(screen_dc, ctypes.byref(bi), _DIB_RGB_COLORS,
                                    ctypes.byref(bits), None, 0)
        buf_t = ctypes.POINTER(ctypes.c_ubyte * (w * h * 4))
        buf = ctypes.cast(bits, buf_t).contents
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        dc = _g32.CreateCompatibleDC(screen_dc)
        _g32.SelectObject(dc, bmp)  # DIB 保持选中，DC 即以此为画布
        return dc, bmp, arr

    @staticmethod
    def _destroy_gdi(screen_dc, canvas_dc, canvas_bmp, mask_dc, mask_bmp, font):
        # 先 DeleteDC 解除选中，再 DeleteObject 释放位图。
        for dc in (canvas_dc, mask_dc):
            if dc:
                _g32.DeleteDC(dc)
        for bmp in (canvas_bmp, mask_bmp):
            if bmp:
                _g32.DeleteObject(bmp)
        if font:
            _g32.DeleteObject(font)
        if screen_dc:
            _u32.ReleaseDC(None, screen_dc)

    def _rebuild_dibs(self, screen_dc, w, h, cdc, cbmp, mdc, mbmp):
        if cdc:
            _g32.DeleteDC(cdc)
        if mdc:
            _g32.DeleteDC(mdc)
        if cbmp:
            _g32.DeleteObject(cbmp)
        if mbmp:
            _g32.DeleteObject(mbmp)
        self._new_canvas = self._make_dib(screen_dc, w, h)
        self._new_mask = self._make_dib(screen_dc, w, h)

    def _recreate(self, taskbar, x, y, w, h, screen_dc, cdc, cbmp, mdc, mbmp, font):
        if self._hwnd and _u32.IsWindow(self._hwnd):
            _u32.DestroyWindow(self._hwnd)
        self._rebuild_dibs(screen_dc, w, h, cdc, cbmp, mdc, mbmp)
        hwnd = _u32.CreateWindowExW(_WS_EX_LAYERED | _WS_EX_NOACTIVATE, _CLASS_NAME,
                                    "tblyric", _WS_CHILD, x, y, w, h, taskbar, 0,
                                    self._hinst, None)
        self._hwnd = hwnd
        self._place(hwnd, x, y, w, h)
        _u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)

    @staticmethod
    def _place(hwnd, x, y, w, h):
        # HWND_TOP(0) 提顶；NOACTIVATE|SHOWWINDOW|FRAMECHANGED，不含 NOZORDER。
        _u32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0010 | 0x0040 | 0x0020)

    @staticmethod
    def _pump_messages():
        msg = wintypes.MSG()
        while _u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------ 动画布局 ------------------------------
    def _text_width(self, mdc, font, text):
        tw = self._measure_cache.get(text)
        if tw is None:
            size = wintypes.SIZE()
            buf = ctypes.create_unicode_buffer(text)
            _g32.GetTextExtentPoint32W(mdc, buf, len(text), ctypes.byref(size))
            tw = size.cx
            self._measure_cache[text] = tw
        return tw

    def _mq_reset(self, now):
        self._mq_text = None
        self._mq_t0 = now

    def _line_x(self, mdc, font, text, now, allow_scroll):
        """返回某行文字左上角 x；超长时按 停顿-左滚-停顿 周期取模计算（无状态、任意时刻可算）。"""
        tw = self._text_width(mdc, font, text)
        avail = self._W - 2 * PAD_X
        if tw <= avail:
            return (self._W - tw) // 2
        if not allow_scroll or self._vprog < 1.0:
            return PAD_X  # 垂直走位期间固定展示开头
        if self._mq_text != text:
            self._mq_text = text
            self._mq_t0 = now
        dist = tw - avail
        scroll_ms = max(1, int(dist / SCROLL_PX_PER_SEC * 1000))
        cycle = HOLD_HEAD_MS + scroll_ms + HOLD_TAIL_MS
        el = (now - self._mq_t0) % max(1, cycle)
        if el < HOLD_HEAD_MS:
            return PAD_X
        if el < HOLD_HEAD_MS + scroll_ms:
            p = (el - HOLD_HEAD_MS) / scroll_ms
            return PAD_X - int(dist * min(1.0, p))
        return PAD_X - dist

    # ------------------------------ 合成绘制 ------------------------------
    def _compose(self, canvas, mdc, mask_arr, font, now):
        h, w = canvas.shape[:2]
        canvas.fill(0)  # 全透明背景
        e = _ease_out_cubic(self._vprog)
        out_x = cur_x = 0
        if self._out and self._vprog < 1.0:
            out_x = self._line_x(mdc, font, self._out, now, False)
            self._blit_line(canvas, mdc, mask_arr, font, self._out,
                            out_x, int(-e * h), 1.0 - e)
        if self._cur:
            cur_x = self._line_x(mdc, font, self._cur, now, True)
            self._blit_line(canvas, mdc, mask_arr, font, self._cur,
                            cur_x, int((1.0 - e) * h), e)
        # 本帧签名：文本、垂直进度（量化到帧）、各行 x、颜色，决定是否需要提交。
        self._frame_sig = (self._cur, self._out, int(self._vprog * 1000),
                           cur_x, out_x, self._fg)

    def _blit_line(self, canvas, mdc, mask_arr, font, text, dx, dy, alpha):
        if not text or alpha <= 0.0:
            return
        h, w = canvas.shape[:2]
        mask_arr.fill(255)  # 白底
        _g32.SelectObject(mdc, font)
        _g32.SetBkMode(mdc, 1)  # TRANSPARENT
        _g32.SetTextColor(mdc, 0)  # 黑字
        rc = wintypes.RECT(dx, dy, dx + w + 8, dy + h)
        buf = ctypes.create_unicode_buffer(text)
        _u32.DrawTextW(mdc, buf, -1, ctypes.byref(rc),
                       _DT_LEFT | _DT_VCENTER | _DT_SINGLELINE | _DT_NOPREFIX)

        # 白底黑字 -> 覆盖率（灰度抗锯齿，三通道相等取 R 即可）
        cov = (255 - mask_arr[:, :, 0]).astype(np.float32) / 255.0
        cov *= max(0.0, min(1.0, alpha))
        nz = cov > 0.003
        if not nz.any():
            return
        sa = cov[nz][:, None]
        da = canvas[nz, 3:4].astype(np.float32) / 255.0
        oa = sa + da * (1.0 - sa)
        b, g, r = self._fg
        src = np.array([b, g, r], dtype=np.float32)[None, :]
        dst = canvas[nz, 0:3].astype(np.float32)
        blended = (src * sa + dst * da * (1.0 - sa)) / np.maximum(oa, 1e-6)
        canvas[nz, 0:3] = blended.astype(np.uint8)
        canvas[nz, 3] = (oa[:, 0] * 255.0).astype(np.uint8)
