"""验证桌面 ↔ 移动响应式形态切换（断点 720px）。

覆盖：
- 桌面：左侧栏可见、底部移动导航隐藏、进度/时间/胶囊内控件齐全；
- 移动列表：侧栏隐藏、底部横向导航出现、播放栏只剩封面/歌名作者/播放键；
- 移动详情：底部播放栏与导航隐藏，详情页移动控制条出现；
- 移动歌单中心 / 歌单内容页返回键；
- 移动 ↔ 桌面往返后控件可见性正确恢复。
"""
import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import store  # noqa: E402

store.app_data_dir = lambda: pathlib.Path(tempfile.mkdtemp(prefix="chord_test_"))
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main_window import MainWindow  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  [PASS] {name}")
    else:
        FAIL += 1; print(f"  [FAIL] {name}")


def pump(app, ms=120):
    import time
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(); time.sleep(0.01)


def main():
    app = QApplication([])
    w = MainWindow()
    w.show()
    root = pathlib.Path(__file__).resolve().parent.parent
    samples = list((root / "samples").glob("*.flac"))[:3]
    w._ingest([str(p) for p in samples])
    w.playlists = {"轻音乐": []}
    w._rebuild_playlist_buttons()

    # ---- 桌面 ----
    w.resize(1040, 660); pump(app)
    check("桌面：侧栏可见", w.sidebar.isVisible())
    check("桌面：移动导航隐藏", not w.mobile_nav.isVisible())
    for name in ("progress", "lbl_cur", "lbl_total", "btn_prev", "btn_next",
                 "btn_mode", "btn_mute", "btn_task_lyric"):
        check(f"桌面：{name} 可见", getattr(w, name).isVisible())
    check("桌面：详情移动控制条隐藏", not w.now_view.m_ctrl.isVisible())

    # ---- 移动列表 ----
    w.resize(560, 760); pump(app)
    check("移动：进入紧凑态", w._compact is True)
    check("移动：侧栏隐藏", not w.sidebar.isVisible())
    check("移动：底部导航可见", w.mobile_nav.isVisible())
    check("移动：播放栏可见", w.player_bar.isVisible())
    for name in ("progress", "lbl_cur", "lbl_total", "btn_prev", "btn_next",
                 "btn_mode", "btn_mute", "btn_task_lyric", "pill_sep"):
        check(f"移动：{name} 隐藏", not getattr(w, name).isVisible())
    check("移动：中心播放键保留", w.btn_play.isVisible())
    check("移动：歌名作者信息保留", w.song_info_w.isVisible())
    check("移动：详情为上下排布", w.now_view._compact is True)

    # ---- 移动歌单中心 ----
    w._open_playlist_hub(); pump(app)
    check("移动：歌单中心为 stack 第5页", w.stack.currentIndex() == 4)
    check("移动：歌单中心有1个歌单项", w.hub_lay.count() == 2)  # 1项 + stretch
    # 进入某歌单内容页 -> 出现返回键
    w.open_playlist("轻音乐"); pump(app)
    check("移动：歌单内容页显示返回键", w.btn_back_playlist.isVisible())
    w._back_to_playlist_hub(); pump(app)
    check("移动：返回歌单中心后返回键隐藏", not w.btn_back_playlist.isVisible())
    w.switch_page(2); pump(app)

    # ---- 移动详情 ----
    if w.local_order:
        w.current_path = w.local_order[0]
        w.open_now_playing(); pump(app)
        check("移动详情：底部播放栏隐藏", not w.player_bar.isVisible())
        check("移动详情：底部导航隐藏", not w.mobile_nav.isVisible())
        check("移动详情：移动控制条可见", w.now_view.m_ctrl.isVisible())
        for name in ("m_btn_mode", "m_btn_prev", "m_btn_play", "m_btn_next", "m_btn_vol"):
            check(f"移动详情：{name} 可见", getattr(w.now_view, name).isVisible())
        w._back_to_lists(); pump(app)
        check("移动详情返回：播放栏恢复", w.player_bar.isVisible())
        check("移动详情返回：导航恢复", w.mobile_nav.isVisible())

    # ---- 回到桌面：全部恢复 ----
    w.resize(1040, 660); pump(app)
    check("桌面恢复：侧栏可见", w.sidebar.isVisible())
    check("桌面恢复：移动导航隐藏", not w.mobile_nav.isVisible())
    check("桌面恢复：进度条可见", w.progress.isVisible())
    check("桌面恢复：上一首可见", w.btn_prev.isVisible())
    check("桌面恢复：详情控制条隐藏", not w.now_view.m_ctrl.isVisible())
    check("桌面恢复：详情横向排布", w.now_view._compact is False)
    check("桌面恢复：封面尺寸 220", w.now_view.cover.width() == 220)

    print(f"\n结果: 通过 {PASS}，失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
