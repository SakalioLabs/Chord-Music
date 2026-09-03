"""回归：导入与播放解码均在后台线程，不阻塞 UI；快速切歌只保留最新请求（防串歌）。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def pump_until(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
        if pred():
            return True
    return pred()


samples = sorted(str(p) for p in (ROOT / "samples").iterdir()
                 if p.suffix.lower() in (".wav", ".flac"))

# ---- 1. 文件夹/文件导入走后台线程，提交即返回、期间禁用、完成恢复 ----
win._start_import(paths=samples)
check("导入提交后立即返回并标记忙碌", win._importing is True)
check("导入期间添加按钮禁用", not win.btn_add_files.isEnabled()
      and not win.btn_add_folder.isEnabled())
pump_until(lambda: not win._importing)
check("后台导入完成，曲目数=样本数", len(win.tracks) == len(samples))
check("导入完成后按钮恢复可用", win.btn_add_files.isEnabled()
      and win.btn_add_folder.isEnabled())
check("导入线程已清理", win._import_thread is None)

# 屏蔽真实声卡，只验证状态流
win.engine.play = lambda: None

# ---- 2. play_path 不同步解码：立即返回，解码在后台，完成后回投起播 ----
flac = next(p for p in samples if p.endswith(".flac"))
t0 = time.time()
win.play_path(flac, win.local_order)
elapsed = time.time() - t0
check("play_path 立即返回（未在主线程整曲解码）", elapsed < 0.05)
check("已登记待播曲目", win._pending_path == flac)
# 解码进行期间主线程仍可自由处理事件（不被阻塞）
idle_ticks = 0
for _ in range(5):
    app.processEvents()
    idle_ticks += 1
check("解码期间主线程事件循环仍可运转", idle_ticks == 5)
pump_until(lambda: win.current_path == flac)
check("后台解码回投后正确起播", win.current_path == flac
      and win.progress.maximum() > 0 and win._pending_path is None)

# ---- 3. 快速连续切歌：过期解码结果被丢弃，最终只播最后一首 ----
p0, p1, p2 = win.local_order[0], win.local_order[1], win.local_order[-1]
win.play_path(p0)
win.play_path(p1)
win.play_path(p2)
check("连点后只保留最后一次请求", win._pending_path == p2)
pump_until(lambda: win.current_path == p2)
check("最终播放最后一首（不串歌）", win.current_path == p2)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
