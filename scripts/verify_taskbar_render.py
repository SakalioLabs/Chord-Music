"""验证任务栏歌词渲染层（不创建真实任务栏窗口，只用 GDI 内存位图）。

覆盖：固定宽度、背景全透明、短文本居中、换段从下往上进入、超长行横向跑马灯。
真实 Windows 会话运行（需要桌面 GDI），但不向任务栏挂窗。
"""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import taskbar_lyrics as T

u32, g32 = T._u32, T._g32
W, H = 480, 52
fail = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        fail.append(name)


screen = u32.GetDC(None)
font = T.TaskbarLyrics._create_font(H)
cdc, cbmp, canvas = T.TaskbarLyrics._make_dib(screen, W, H)
mdc, mbmp, mask = T.TaskbarLyrics._make_dib(screen, W, H)
g32.SelectObject(mdc, font)

obj = T.TaskbarLyrics.__new__(T.TaskbarLyrics)  # 不走 __init__，避免注册窗口类/起线程
obj._W, obj._H = W, H
obj._fg = T._FG_LIGHT
obj._measure_cache = {}
obj._mq_text, obj._mq_phase, obj._mq_t0 = None, "head", 0
obj._vprog, obj._cur, obj._out = 1.0, "", ""


def text_w(s):
    return obj._text_width(mdc, font, s)


def compose(now):
    obj._compose(canvas, mdc, mask, font, now)


# 1) 短文本水平居中
short = "妈妈 月光之下"
tw = text_w(short)
obj._cur = short
x = obj._line_x(mdc, font, short, 0, True)
check("短文本居中", x == (W - tw) // 2)

# 2) 稳定帧：背景全透明、存在不透明文字像素、颜色为前景色
obj._vprog = 1.0
compose(1000)
alpha = canvas[:, :, 3]
check("背景四角透明", all(canvas[y, x_, 3] == 0 for (y, x_) in
                          [(0, 0), (0, W - 1), (H - 1, 0), (H - 1, W - 1)]))
check("存在文字像素", int(alpha.max()) == 255)
check("文字颜色为深色", tuple(int(v) for v in canvas[alpha == 255][0, :3]) == T._FG_LIGHT)
check("非文字区域全透明", int((alpha == 0).sum()) > W * H * 0.6)

# 3) 换段从下往上：vprog=0 新行完全在画布下方 -> 全透明
obj._out, obj._cur, obj._vprog = "", short, 0.0
compose(0)
check("进场起点全透明(新行在下方)", int(canvas[:, :, 3].max()) == 0)
# vprog=0.5 新行进入一半 -> 有半透明/不透明像素
obj._vprog = 0.5
compose(120)
check("进场中段有文字", int(canvas[:, :, 3].max()) > 0)
# vprog=1 到位，旧行消失，画面只剩居中当前行
obj._vprog = 1.0
compose(240)
check("进场结束有文字", int(canvas[:, :, 3].max()) == 255)

# 4) 旧行向上移出：vprog=0.5 时旧行在上半区、新行在下半区
obj._out, obj._cur, obj._vprog = "上一句歌词", short, 0.5
compose(120)
rows_with_ink = [y for y in range(H) if int(canvas[y, :, 3].max()) > 0]
check("换段同时覆盖上下两行", rows_with_ink and min(rows_with_ink) < H // 2 <= max(rows_with_ink))

# 5) 超长行跑马灯
long_text = "这是一句非常非常非常非常非常非常非常非常非常非常非常非常长的歌词用来测试横向滚动"
obj._out, obj._cur, obj._vprog = "", long_text, 1.0
obj._mq_reset(0)
twl = text_w(long_text)
check("超长文本确实超宽", twl > W - 2 * T.PAD_X)
x0 = obj._line_x(mdc, font, long_text, 0, True)
check("跑马灯开头停在左内边距", x0 == T.PAD_X)
# 进入 scroll 中段后应明显左移
mid = T.HOLD_HEAD_MS + 600
xmid = obj._line_x(mdc, font, long_text, mid, True)
check("滚动中段向左移动", xmid < T.PAD_X)
# 末尾停在右内边距对齐
dist = twl - (W - 2 * T.PAD_X)
scroll_ms = int(dist / T.SCROLL_PX_PER_SEC * 1000)
tail_t = T.HOLD_HEAD_MS + scroll_ms + 50
xtail = obj._line_x(mdc, font, long_text, tail_t, True)
check("滚动结束右对齐", xtail == T.PAD_X - dist)

# 6) 固定宽度常量符合“不吃满”
check("固定宽度=480", T.FIXED_W == 480)

# 清理
g32.SelectObject(mdc, mbmp)
T.TaskbarLyrics._destroy_gdi(screen, cdc, cbmp, mdc, mbmp, font)

print("\n结果:", "全部通过" if not fail else f"{len(fail)} 项失败 {fail}")
raise SystemExit(1 if fail else 0)
