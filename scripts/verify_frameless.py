"""验证无边框窗口边缘缩放（app/frameless.py）。

覆盖两点：
1. 进程内其它库把共享的 ctypes.windll.user32.GetWindowRect 参数类型绑定成“别的 RECT”
   后，frameless 的独立 WINFUNCTYPE 绑定仍能正常调用（用户实机报过 ArgumentError）。
2. WM_NCHITTEST 命中测试：客户区 / 四边 / 四角 / 非命中消息 的返回码正确。

仅 Windows 有意义；非 Windows 直接跳过。
"""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import frameless  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


class _FakeWidget:
    def __init__(self, maximized=False):
        self._max = maximized

    def isMaximized(self):
        return self._max


def main():
    if not sys.platform.startswith("win"):
        print("非 Windows，跳过边缘缩放验证")
        return 0

    user32 = ctypes.windll.user32

    # 1) 模拟其它库把共享 GetWindowRect 绑定成它们自己的 RECT 类型
    class _OtherRECT(ctypes.Structure):
        _fields_ = [("a", ctypes.c_long), ("b", ctypes.c_long),
                    ("c", ctypes.c_long), ("d", ctypes.c_long)]

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_OtherRECT)]

    hwnd = user32.GetDesktopWindow()
    rect = wintypes.RECT()
    ok = frameless._GetWindowRect(hwnd, ctypes.byref(rect))
    check("独立绑定在 argtypes 被污染后仍可调用", bool(ok))
    check("取到的窗口矩形尺寸为正", rect.right > rect.left and rect.bottom > rect.top)

    # 2) 命中测试
    keep = []  # 保持 MSG 存活

    def hit_at(lx, ly, maximized=False):
        m = wintypes.MSG()
        m.hWnd = hwnd
        m.message = 0x0084  # WM_NCHITTEST
        m.lParam = (ly << 16) | (lx & 0xFFFF)
        keep.append(m)
        return frameless.hit_test(_FakeWidget(maximized), ctypes.addressof(m))

    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    check("中心命中客户区 HTCLIENT", hit_at(cx, cy) == frameless.HTCLIENT)
    check("左边缘 HTLEFT", hit_at(rect.left + 2, cy) == frameless.HTLEFT)
    check("右边缘 HTRIGHT", hit_at(rect.right - 2, cy) == frameless.HTRIGHT)
    check("上边缘 HTTOP", hit_at(cx, rect.top + 2) == frameless.HTTOP)
    check("下边缘 HTBOTTOM", hit_at(cx, rect.bottom - 2) == frameless.HTBOTTOM)
    check("左上角 HTTOPLEFT", hit_at(rect.left + 2, rect.top + 2) == frameless.HTTOPLEFT)
    check("右下角 HTBOTTOMRIGHT", hit_at(rect.right - 2, rect.bottom - 2) == frameless.HTBOTTOMRIGHT)
    check("最大化时中心返回 HTCLIENT（不缩放）", hit_at(cx, cy, maximized=True) == frameless.HTCLIENT)
    check("最大化时边缘也不缩放", hit_at(rect.left + 2, cy, maximized=True) == frameless.HTCLIENT)

    other = wintypes.MSG()
    other.message = 0x0001  # 非 WM_NCHITTEST
    check("非命中消息返回 None", frameless.hit_test(_FakeWidget(), ctypes.addressof(other)) is None)

    print(f"\n结果: 通过 {PASS}，失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
