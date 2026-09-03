"""无边框窗口的原生边缘缩放支持（Windows）。

FramelessWindowHint 去掉了系统边框，默认无法从边缘拖拽调整大小。这里通过处理
WM_NCHITTEST（0x0084）命中测试，把窗口边缘/角落回报为对应的 HT* 区域，交由系统
完成缩放：自动遵守 setMinimumSize/setMaximumSize、支持边缘光标与 Aero 连续拖拽。
非 Windows 平台安全降级（返回 None，走默认行为）。

注意：进程内 PySide6 / pywin32 等库可能改写共享的 ``ctypes.windll.user32.GetWindowRect``
的 argtypes（绑定到它们各自的 RECT 类型）。为避免类型冲突，这里统一使用标准
``ctypes.wintypes.RECT``，并用独立的 WINFUNCTYPE 绑定一份私有函数对象，不与其它库
共享、也不会被后续 import 覆盖签名。
"""
import ctypes
import sys
from ctypes import wintypes
from typing import Optional

# WM_NCHITTEST 的返回值
HTCLIENT = 1
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

# 其它需要打磨的 Win32 消息
WM_NCCALCSIZE = 0x0083
WM_ERASEBKGND = 0x0014

_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    # 独立函数对象：返回 BOOL，参数为 HWND 与指向标准 wintypes.RECT 的指针。
    # 不复用 ctypes.windll.user32.GetWindowRect，避免其 argtypes 被其它库改写。
    _GetWindowRect = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT)
    )(("GetWindowRect", ctypes.windll.user32))
else:  # pragma: no cover - 非 Windows 平台
    _GetWindowRect = None


def _signed_loword(value: int) -> int:
    # GET_X_LPARAM：低 16 位按有符号 short 解释（多显示器负坐标需要）
    return ctypes.c_short(value & 0xFFFF).value


def _signed_hiword(value: int) -> int:
    return ctypes.c_short((value >> 16) & 0xFFFF).value


def hit_test(widget, message_ptr, border: int = 7) -> Optional[int]:
    """在 MainWindow.nativeEvent 中调用。

    返回 HT* 命中码表示应把该处作为缩放边缘；返回 HTCLIENT/None 表示交由默认逻辑。
    border 为物理像素的边缘热区宽度（角落优先判定，便于斜向缩放）。
    任何 ctypes/平台异常都安全降级为 None，绝不让命中测试抛到 Qt 事件循环外。
    """
    if not _IS_WINDOWS:
        return None
    try:
        msg = wintypes.MSG.from_address(int(message_ptr))
    except Exception:  # noqa: BLE001
        return None
    if msg.message != 0x0084:  # WM_NCHITTEST
        return None
    # 最大化时不允许边缘缩放（避免把最大化窗口拖乱）
    if widget.isMaximized():
        return HTCLIENT

    x = _signed_loword(msg.lParam)
    y = _signed_hiword(msg.lParam)

    rect = wintypes.RECT()
    try:
        ok = _GetWindowRect(msg.hWnd, ctypes.byref(rect))
    except Exception:  # noqa: BLE001
        return None
    if not ok:
        return HTCLIENT

    on_left = rect.left <= x < rect.left + border
    on_right = rect.right - border <= x <= rect.right
    on_top = rect.top <= y < rect.top + border
    on_bottom = rect.bottom - border <= y <= rect.bottom

    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM
    return HTCLIENT


def polish_native(widget, message_ptr):
    """消除无边框窗口缩放时的闪烁/边框残影。

    返回 ``(True, result)`` 表示已拦截该消息；返回 None 表示交给 Qt 默认处理。

    - WM_NCCALCSIZE：wParam 为真时返回 0，让客户区直接占满整窗，去掉 DWM 为系统
      边框预留的非客户区——否则拖拽缩放过程中系统会反复擦画这条边，表现为整窗闪烁。
      最大化时不拦截（交给默认，避免盖住任务栏）。
    - WM_ERASEBKGND：返回 1，跳过系统背景擦除，改由 Qt 的绘制一次性完成，进一步去闪。
    """
    if not _IS_WINDOWS:
        return None
    try:
        msg = wintypes.MSG.from_address(int(message_ptr))
    except Exception:  # noqa: BLE001
        return None
    if msg.message == WM_NCCALCSIZE and msg.wParam == 1:
        try:
            maximized = bool(widget.isMaximized())
        except Exception:  # noqa: BLE001
            maximized = False
        if not maximized:
            return True, 0
    elif msg.message == WM_ERASEBKGND:
        return True, 1
    return None
