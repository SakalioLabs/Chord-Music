"""验证播放引擎的暂停缓出 / 恢复淡入增益动画（离屏，不需要真实声卡）。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.engine import PlaybackEngine  # noqa: E402

app = QApplication([])
fail = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        fail.append(name)


def pump(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


eng = PlaybackEngine()

# 1) 缓出：增益 1 -> 0，结束触发回调
eng._gain = 1.0
finished = []
eng._start_fade(0.0, 90, lambda: finished.append(1))
pump(0.05)
mid = eng._gain
check("缓出中段增益下降", 0.1 < mid < 0.9)
pump(0.12)
check("缓出结束增益≈0", eng._gain < 0.02)
check("缓出结束回调触发", bool(finished))

# 2) 淡入：0 -> 1
eng._gain = 0.0
eng._start_fade(1.0, 90)
pump(0.12)
check("淡入结束增益≈1", eng._gain > 0.98)

# 3) 启动新缓出会打断旧动画，不残留旧回调
eng._gain = 1.0
hits = []
eng._start_fade(0.0, 200, lambda: hits.append("old"))
pump(0.04)
eng._start_fade(1.0, 40, lambda: hits.append("new"))  # 中途改向
pump(0.1)
check("中途改向后以新目标为准", eng._gain > 0.98)
check("被打断的旧回调不再触发", "old" not in hits and "new" in hits)

# 4) _finish_pause 在无 sink 时也能安全进入 paused（不抛异常）
eng2 = PlaybackEngine()
eng2._finish_pause()
check("无 sink 时缓出收尾安全", eng2.state == "paused")

print("\n结果:", "全部通过" if not fail else f"{len(fail)} 项失败 {fail}")
raise SystemExit(1 if fail else 0)
