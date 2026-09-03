"""UI / 播放逻辑离屏冒烟测试（不依赖真实声卡与显示器）。

运行：python scripts/smoke_ui.py
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.decoder import decode  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
(ROOT / "app" / "style.qss").read_text(encoding="utf-8")
win = MainWindow()
win.show()

results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def pump_until(pred, timeout=5.0):
    """驱动事件循环，直到后台线程解码结果回投主线程（异步播放）。"""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
        if pred():
            return True
    return pred()


samples = sorted(str(p) for p in (ROOT / "samples").iterdir()
                 if p.suffix.lower() in (".wav", ".flac"))
win._ingest(samples)
check(f"导入{len(samples)}个样本", win.local_list.count() == len(samples))
check("红心初始为空态", win.liked_page._inner_stack.currentIndex() == 1)
check("最近初始为空态", win.recent_page._inner_stack.currentIndex() == 1)

# 页面切换
win.switch_page(2)
check("切到本地管理", win.stack.currentIndex() == 2 and win.nav_buttons[2].property("active"))
win.switch_page(0)

# 收藏
first_path = win.local_order[0]
win.toggle_liked(first_path)
check("收藏后红心列表=1", win.liked_list.count() == 1)
win.toggle_liked(first_path)
check("取消收藏后为空", win.liked_list.count() == 0)
win.toggle_liked(first_path)

# 屏蔽真实声卡播放，仅验证状态机/UI 更新逻辑
win.engine.play = lambda: None

flac = next(p for p in samples if p.endswith(".flac"))
win.play_path(flac, win.local_order)
pump_until(lambda: win.current_path == flac)
check("播放后曲名更新", win.song_title.text() != "DEEP")
check("进度上限≈2000ms", abs(win.progress.maximum() - 2000) <= 20)
check("计入最近播放", win.recent_list.count() == 1)
check("播放队列长度=样本数", len(win.queue) == len(samples))
check("当前曲目被记录", win.current_path == flac)

# seek 换算
win.engine.seek_ms(1000)
check("seek 到1000ms", abs(win.engine.current_ms() - 1000) <= 60)

# 下一首：队列切换
cur = win.current_path
win._step(1)
pump_until(lambda: win.current_path == win.local_order[1])
check("下一首切换", win.current_path != cur and win.current_path == win.local_order[1])
# 上一首（播放不足3s -> 直接切到上一曲）
win.engine.seek_ms(500)
cur = win.current_path
idx = win.queue.index(cur)
expect_prev = win.queue[(idx - 1) % len(win.queue)]
win._step(-1)
pump_until(lambda: win.current_path == expect_prev)
check("不足3s上一首=切到上一曲", win.current_path == expect_prev)

# 解码引擎层：load 后 total/格式
audio = decode(flac)
check("FLAC 时长≈2s", abs(audio.duration_ms - 2000) <= 20)
check("PCM 非空", len(audio.pcm) > 0)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
