"""歌单功能离屏回归：创建 / 添加去重保序 / 持久化 / 打开页 / 移除 / 重命名 / 删除 /
未导入曲目过滤 / 主导航与歌单页互斥高亮。存储重定向到临时目录，不触碰真实歌单。"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import main_window as mw  # noqa: E402
from app import store  # noqa: E402
from app.main_window import MainWindow  # noqa: E402

# --- 把持久化目录重定向到临时文件夹，避免污染真实歌单/设置 ---
_tmp = Path(tempfile.mkdtemp(prefix="chord_pl_"))
store.app_data_dir = lambda: _tmp

app = QApplication(sys.argv)
win = MainWindow()
win.show()

# --- 屏蔽应用自绘对话框，用脚本变量驱动（替代旧的 QInputDialog/QMessageBox） ---
_dlg = {"text": "摇滚"}
mw.InputDialog.get_text = classmethod(lambda cls, *a, **k: (_dlg["text"], True))
mw.ConfirmDialog.get_confirm = classmethod(lambda cls, *a, **k: True)
mw.NoticeDialog.show = classmethod(lambda cls, *a, **k: None)

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


samples = sorted(str(p) for p in (ROOT / "samples").iterdir()
                 if p.suffix.lower() in (".wav", ".flac"))
win._ingest(samples)
p0, p1, p2 = samples[0], samples[1], samples[2]

# 1) 初始无歌单按钮
check("初始侧栏无歌单", len(win._playlist_buttons) == 0)

# 2) 新建歌单（空）→ 自动打开该歌单页
win.switch_page(2)
win.create_playlist()
check("创建后歌单存在", "摇滚" in win.playlists)
check("侧栏出现歌单按钮", "摇滚" in win._playlist_buttons)
check("自动进入歌单页(idx=3)", win.stack.currentIndex() == 3)
check("记录当前歌单", win._current_playlist == "摇滚")
check("标题为歌单名", win.title_label.text() == "摇滚")
check("空歌单为空态", win.playlist_page._inner_stack.currentIndex() == 1)

# 3) 加入两首，去重保序
win.add_to_playlist("摇滚", p1)
win.add_to_playlist("摇滚", p0)
win.add_to_playlist("摇滚", p1)  # 重复
check("去重保序后=2 首", win._playlist_paths("摇滚") == [p1, p0])
check("歌单列表渲染 2 行", win.playlist_list.count() == 2)
check("计数显示 2 首", "2 首" in win.count_label.text())

# 4) 未导入的路径自动隐藏
win.playlists["摇滚"].insert(0, "Z:/not_imported.wav")
check("未导入曲目被过滤", win._playlist_paths("摇滚") == [p1, p0])
win.playlists["摇滚"].remove("Z:/not_imported.wav")

# 5) 持久化：重新从磁盘读取一致
reloaded = store.load_playlists()
check("持久化到磁盘且内容一致", reloaded == win.playlists)

# 6) 切回主导航会清空当前歌单标记
win.switch_page(2)
check("切主导航后当前歌单清空", win._current_playlist is None)
check("主导航本地管理高亮", win.nav_buttons[2].property("active") is True)
check("歌单按钮取消高亮", win._playlist_buttons["摇滚"].property("active") is False)
win.open_playlist("摇滚")
check("重新打开歌单页", win.stack.currentIndex() == 3 and win._current_playlist == "摇滚")
check("歌单按钮高亮", win._playlist_buttons["摇滚"].property("active") is True)

# 7) 从歌单移除一首
win.remove_from_playlist("摇滚", p1)
check("移除后剩 1 首", win._playlist_paths("摇滚") == [p0])

# 8) 重命名（保数据、保顺序、改键）
_dlg["text"] = "民谣"
win._rename_playlist("摇滚")
check("旧名消失", "摇滚" not in win.playlists)
check("新名存在且数据保留", win.playlists.get("民谣") == [p0])
check("当前查看跟随改名", win._current_playlist == "民谣")

# 9) 带曲新建
_dlg["text"] = "深夜"
win.create_playlist(prefill_path=p2)
check("带曲新建即含该曲", win.playlists["深夜"] == [p2])

# 10) 自动去重命名
check("唯一名：不冲突原样", win._unique_playlist_name("深夜") == "深夜 2")
win.playlists["深夜 2"] = []
check("唯一名：继续递增", win._unique_playlist_name("深夜") == "深夜 3")

# 11) 删除歌单 → 回到本地页
win.open_playlist("民谣")
win._delete_playlist("民谣")
check("删除后不存在", "民谣" not in win.playlists and "民谣" not in win._playlist_buttons)
check("删除后回到本地管理页", win.stack.currentIndex() == 2 and win._current_playlist is None)

# 12) 行内“加入歌单”按钮存在
win.add_to_playlist("深夜", p0)
win.open_playlist("深夜")
item = win.playlist_list.item(0)
row = win.playlist_list.itemWidget(item)
check("歌曲行含加入歌单按钮", hasattr(row, "add_list_btn") and not row.add_list_btn.icon().isNull())

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
