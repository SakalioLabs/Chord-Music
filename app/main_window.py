"""主窗口：商用级布局与交互（HarmonyOS 风格资源与轻动效）。"""

from __future__ import annotations

import os
import random
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QSize,
    QThread,
    QThreadPool,
    Qt,
)
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import animation, frameless, metadata, store, theme
from .decoder import SUPPORTED_EXTS
from .dialogs import ConfirmDialog, InputDialog, NoticeDialog
from .engine import PlaybackEngine
from .metadata import TrackMeta
from .now_playing import NowPlayingView
from .workers import (
    DecodeSignalBridge,
    DecodeTask,
    ImportWorker,
    build_records,
    scan_folder,
)
from .widgets import ClickableFrame, ElidedLabel, TrackRow, VolumePopup

# 任务栏内嵌歌词依赖 Win32（ctypes.user32），非 Windows 环境优雅降级为不可用。
try:  # pragma: no cover - 平台相关
    from .taskbar_lyrics import TaskbarLyrics
except Exception:  # noqa: BLE001
    TaskbarLyrics = None

# 设计令牌（与 style.qss 对应）
BRAND = "#3569E0"
TEXT_1 = "#1B1E26"
TEXT_2 = "#5E6573"
TEXT_3 = "#9AA1AF"
ICON_DIM = "#C4C9D4"
APP_TITLE = "弦乐音乐播放器"

NAV_ITEMS = (("红心音乐", "heart"), ("最近播放", "clock"), ("本地管理", "folder"))
PLAY_MODES = (("loop", "repeat", "列表循环"), ("single", "repeat_one", "单曲循环"),
              ("shuffle", "shuffle", "随机播放"))


def format_time(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def format_total(ms: int) -> str:
    s = ms // 1000
    if s >= 3600:
        return f"{s // 3600} 小时 {(s % 3600) // 60:02d} 分"
    if s >= 60:
        return f"{s // 60} 分 {s % 60:02d} 秒"
    return f"{s} 秒"


@dataclass
class Track:
    path: str
    title: str
    ext: str
    duration_ms: int = 0
    meta: TrackMeta | None = None


class MainWindow(QWidget):
    """弦乐播放器主窗口（无边框浅色圆角窗口）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1040, 660)
        # 允许收窄到移动端断点以下以触发紧凑布局
        self.setMinimumSize(380, 520)
        self.setWindowOpacity(0.0)

        # ---- 数据状态 ----
        # 载入本地持久化设置（曲库路径 / 最近 / 红心 / 音量 / 模式 / 上次会话 / 窗口几何）
        self.settings = store.load_settings()
        self.tracks: Dict[str, Track] = {}
        self.local_order: List[str] = []
        self.recent: List[str] = list(self.settings.get("recent", []))
        self.liked: set[str] = set(self.settings.get("liked", []))
        # 歌单：名称 -> 有序曲目路径（去重保序），持久化到本地数据目录
        self.playlists: Dict[str, List[str]] = store.load_playlists()
        self._playlist_buttons: Dict[str, QToolButton] = {}
        self._current_playlist: Optional[str] = None  # 当前正在查看的歌单名（None=主页面）
        self.queue: List[str] = []
        self.current_path: Optional[str] = None
        self.play_mode = self.settings.get("play_mode", "loop")
        self._last_volume = int(self.settings.get("volume", 80))
        # 恢复上次会话：曲库重建后加载该曲并定位，但保持暂停（不自动出声）
        self._restore_session = dict(self.settings.get("last_session", {}))
        self._restore_seek_ms = 0
        self._pending_restore_seek: Optional[int] = None
        self._restoring_library = bool(self.settings.get("library_paths"))
        self._seeking = False
        self._drag_offset: Optional[QPoint] = None
        self._normal_geom = None
        self._first_show = True
        self._compact = False  # 窄屏（移动端）紧凑布局，由 resizeEvent 切换

        # ---- 播放引擎 ----
        self.engine = PlaybackEngine(self)
        self.engine.set_volume(int(self.settings.get("volume", 80)) / 100.0)
        self.engine.positionChanged.connect(self._on_position)
        self.engine.stateChanged.connect(self._on_state)
        self.engine.ended.connect(self._on_ended)

        # ---- 后台线程设施：导入线程 + 解码线程池（避免阻塞 UI/音频） ----
        self._importing = False
        self._import_thread = None
        self._import_worker = None
        self._play_token = 0
        self._pending_path: Optional[str] = None
        self._pending_queue: List[str] = []
        self._decode_bridge = DecodeSignalBridge()
        self._decode_bridge.decoded.connect(self._on_decoded)
        self._decode_bridge.failed.connect(self._on_decode_failed)
        self._decode_pool = QThreadPool(self)
        self._decode_pool.setMaxThreadCount(2)

        # ---- 任务栏内嵌歌词（Win32 重父化到 Shell_TrayWnd），窗口懒创建 ----
        self.taskbar_lyrics = None
        self._task_on = False
        self._task_times: List[int] = []
        self._task_lines: List[str] = []
        self._task_idx = -2
        self._task_placeholder = ""
        self._task_tick = 0  # reassert 节流计数（positionChanged 约 50ms 一次）

        # ---- 动效（按各按钮自身图标尺寸回弹） ----
        self.press_ctrl = animation.PressScaleFilter()
        self.press_icon = animation.PressScaleFilter()

        self._build_ui()
        self._wire()
        self._setup_volume_popup()
        self._bind_shortcuts()
        self._update_mode_button()
        self.refresh_all()  # 初始化三个页面的空状态与计数
        self.now_view.reset()
        self._restore_geometry()
        # 若恢复的窗口宽度落在移动断点内，先切到移动布局（否则首帧是桌面态再跳变）
        self._compact = self.width() < self.COMPACT_BREAKPOINT
        if self._compact:
            self._apply_responsive()
        # 后台重建上次导入的曲库（读元数据在子线程，不卡启动）
        self._restore_library_if_any()

    # ==================================================================
    # 界面构建
    # ==================================================================
    def _build_ui(self) -> None:
        # Chord 是左侧栏独立 layout 内的文本控件；右侧最小化/最大化/关闭所在的窗口控制手柄
        # 是另一个独立 layout。两者解耦：调整 Chord 头部高度不影响右侧控制区。
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("Card")
        root.addWidget(self.card)

        outer = QVBoxLayout(self.card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._build_sidebar())
        body.addLayout(self._build_right(), 1)

        # 两个相互独立的顶部拖拽区：侧栏内的 Chord 头部、右侧的窗口控制手柄
        self._drag_widgets = {self.chord_head, self.win_handle}

    # ---------- 右侧窗口控制手柄（独立 layout：最小化/最大化/关闭 + 空白处拖拽） ----------
    def _build_window_handle(self) -> QWidget:
        self.win_handle = QWidget()
        self.win_handle.setObjectName("WinHandle")
        self.win_handle.setFixedHeight(38)

        lay = QHBoxLayout(self.win_handle)
        lay.setContentsMargins(16, 0, 8, 0)
        lay.setSpacing(4)
        lay.addStretch(1)

        self.btn_min = self._win_btn("minimize", "btnMin", "最小化")
        self.btn_max = self._win_btn("maximize", "btnMax", "最大化/还原")
        self.btn_close = self._win_btn("close", "btnClose", "关闭")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)
        # 手柄空白处承担窗口拖拽（容器自身安装事件过滤器，真实鼠标事件才会进入 eventFilter）
        self.win_handle.installEventFilter(self)
        return self.win_handle

    def _win_btn(self, icon_name: str, obj: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setObjectName(obj)
        b.setFixedSize(30, 30)
        b.setIconSize(QSize(15, 15))
        b.setIcon(theme.icon(icon_name, "#5E6573", 15))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.installEventFilter(self)
        b._icon_name = icon_name
        return b

    # ---------- 侧边栏 ----------
    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(208)
        self.sidebar = bar
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(8, 8, 8, 12)
        lay.setSpacing(9)

        # Chord：侧栏独立 layout 内的文本控件（与右侧窗口控制手柄解耦，高度独立可调）
        self.chord_head = QWidget()
        self.chord_head.setObjectName("ChordHead")
        self.chord_head.setFixedHeight(80)
        self.chord_head.installEventFilter(self)
        chl = QHBoxLayout(self.chord_head)
        chl.setContentsMargins(16, 0, 8, 0)
        self.chord_logo = QLabel("Chord")
        self.chord_logo.setObjectName("ChordLogo")
        # 文字不拦截鼠标，按下任意位置都落到拖拽容器
        self.chord_logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        chl.addWidget(self.chord_logo)
        chl.addStretch(1)
        lay.addWidget(self.chord_head)

        self.nav_buttons: List[QToolButton] = []
        for i, (name, icon_name) in enumerate(NAV_ITEMS):
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setText(name)
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setFixedHeight(40)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.switch_page(idx))
            self.nav_buttons.append(b)
            lay.addWidget(b)

        # 歌单分区：小标题行（左“歌 单”，右侧一个加号图标按钮用于新建），其下为动态歌单列表
        lay.addSpacing(8)
        pl_header = QWidget()
        phl = QHBoxLayout(pl_header)
        phl.setContentsMargins(12, 0, 8, 2)
        phl.setSpacing(0)
        # 左侧弹簧仅在窄屏（隐藏“歌 单”文字）时启用，使加号水平居中
        self._pl_left_spring = QWidget()
        self._pl_left_spring.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pl_left_spring.setFixedHeight(1)
        self._pl_left_spring.hide()
        phl.addWidget(self._pl_left_spring)
        self.pl_section = QLabel("歌 单")
        self.pl_section.setObjectName("PlaylistSection")
        phl.addWidget(self.pl_section)
        phl.addStretch(1)

        self.btn_new_playlist = QToolButton()
        self.btn_new_playlist.setObjectName("PlaylistAddBtn")
        self.btn_new_playlist.setFixedSize(22, 22)
        self.btn_new_playlist.setIconSize(QSize(16, 16))
        self.btn_new_playlist.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_new_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new_playlist.setToolTip("新建歌单")
        self.btn_new_playlist.setIcon(theme.icon("plus", TEXT_2, 16))
        self.btn_new_playlist.clicked.connect(self.create_playlist)
        phl.addWidget(self.btn_new_playlist)
        lay.addWidget(pl_header)

        self.playlist_holder = QWidget()
        self.playlist_lay = QVBoxLayout(self.playlist_holder)
        self.playlist_lay.setContentsMargins(0, 0, 0, 0)
        self.playlist_lay.setSpacing(9)
        lay.addWidget(self.playlist_holder)

        lay.addStretch(1)
        self._rebuild_playlist_buttons()
        return bar

    # ---------- 右侧 ----------
    def _build_right(self) -> QVBoxLayout:
        right = QVBoxLayout()
        right.setContentsMargins(0, 8, 0, 0)
        right.setSpacing(0)

        # 顶部独立窗口控制手柄（列表页/详情页都常驻，保证最小化/最大化/关闭始终可用）
        right.addWidget(self._build_window_handle())

        self.content = QFrame()
        self.content.setObjectName("ContentArea")
        content_lay = QVBoxLayout(self.content)
        content_lay.setContentsMargins(16, 2, 16, 12)
        content_lay.setSpacing(10)
        right.addWidget(self.content, 1)

        # 头部：标题 + 计数 + 工具（详情页时隐藏）
        self.stack = QStackedWidget()
        self.page_titles = ("红心音乐", "最近播放", "本地管理")
        self.header_w = QWidget()
        self.header = QHBoxLayout(self.header_w)
        self.header.setContentsMargins(0, 0, 0, 0)
        # 移动形态：从歌单内容页返回“歌单中心”
        self.btn_back_playlist = QPushButton()
        self.btn_back_playlist.setObjectName("PlaylistBackBtn")
        self.btn_back_playlist.setFixedSize(30, 30)
        self.btn_back_playlist.setIconSize(QSize(18, 18))
        self.btn_back_playlist.setIcon(theme.icon("back", TEXT_2, 18))
        self.btn_back_playlist.setToolTip("返回歌单")
        self.btn_back_playlist.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_back_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back_playlist.clicked.connect(self._back_to_playlist_hub)
        self.btn_back_playlist.hide()
        self.header.addWidget(self.btn_back_playlist)
        self.title_label = QLabel("红心音乐")
        self.title_label.setObjectName("PageTitle")
        self.count_label = QLabel("")
        self.count_label.setObjectName("PageCount")
        self.header.addWidget(self.title_label)
        self.header.addWidget(self.count_label)
        self.header.addStretch(1)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.btn_add_files = self._tool_btn("添加文件")
        self.btn_add_folder = self._tool_btn("添加文件夹")
        tools.addWidget(self.btn_add_files)
        tools.addWidget(self.btn_add_folder)
        self.header.addLayout(tools)
        content_lay.addWidget(self.header_w)

        self.liked_page, self.liked_list, _ = self._build_list_page(
            "heart", "还没有收藏的音乐", "在歌曲列表点击心形按钮，即可收藏喜爱的音乐")
        self.recent_page, self.recent_list, _ = self._build_list_page(
            "clock", "还没有播放记录", "双击本地音乐开始你的第一次播放")
        self.local_page, self.local_list, _ = self._build_list_page(
            "folder", "本地音乐库为空", "导入 WAV / FLAC 文件，建立你的本地音乐库",
            with_add_action=True)
        # 歌单内容页（所有歌单共用这一页，按当前选中歌单渲染）
        self.playlist_page, self.playlist_list, _ = self._build_list_page(
            "playlist", "这个歌单还是空的", "在任意歌曲行尾点击“+”，即可把它添加进歌单")
        for p in (self.liked_page, self.recent_page, self.local_page, self.playlist_page):
            self.stack.addWidget(p)
        # 移动形态的“歌单中心”页：列出现有歌单 + 新建入口（桌面形态用侧栏，此页不可达）
        self.playlist_hub = self._build_playlist_hub()
        self.stack.addWidget(self.playlist_hub)
        # 歌单内容页：右键单曲可从该歌单移除
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlist_list.customContextMenuRequested.connect(self._playlist_song_menu)

        # 外层路由：列表三页 / 正在播放详情
        self.now_view = NowPlayingView()
        self.now_view.backRequested.connect(self._back_to_lists)
        self.now_view.modeRequested.connect(self.cycle_play_mode)
        self.now_view.prevRequested.connect(lambda: self._step(-1))
        self.now_view.playRequested.connect(self._toggle_play)
        self.now_view.nextRequested.connect(lambda: self._step(1))
        self.now_view.volumeRequested.connect(self._open_mobile_volume)
        self.content_router = QStackedWidget()
        self.content_router.addWidget(self.stack)
        self.content_router.addWidget(self.now_view)
        content_lay.addWidget(self.content_router, 1)

        right.addWidget(self._build_player_bar())
        right.addWidget(self._build_mobile_nav())
        self._highlight_nav(0)
        return right

    # ---------- 移动形态底部横向导航（喜欢/最近/本地/歌单） ----------
    def _build_mobile_nav(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("MobileNav")
        bar.setFixedHeight(60)
        bar.hide()
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 4, 6, 6)
        row.setSpacing(4)
        self.mobile_nav_buttons: List[QToolButton] = []
        items = (
            ("喜欢", "heart", lambda: self.switch_page(0)),
            ("最近", "clock", lambda: self.switch_page(1)),
            ("本地", "folder", lambda: self.switch_page(2)),
            ("歌单", "playlist", self._open_playlist_hub),
        )
        for text, icon_name, slot in items:
            b = QToolButton()
            b.setObjectName("MobileNavBtn")
            b.setText(text)
            b.setIcon(theme.icon(icon_name, TEXT_2, 20))
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            b._nav_icon = icon_name
            self.mobile_nav_buttons.append(b)
            row.addWidget(b)
        self.mobile_nav = bar
        return bar

    # ---------- 移动形态“歌单中心”页 ----------
    def _build_playlist_hub(self) -> QWidget:
        page = QFrame()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(10)

        head = QHBoxLayout()
        hint = QLabel("我的歌单")
        hint.setObjectName("HubTitle")
        head.addWidget(hint)
        head.addStretch(1)
        btn_new = QPushButton("  新建歌单")
        btn_new.setObjectName("ToolBtn")
        btn_new.setIcon(theme.icon("plus", "#FFFFFF", 15))
        btn_new.setIconSize(QSize(15, 15))
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_new.clicked.connect(self.create_playlist)
        self.hub_new_btn = btn_new
        head.addWidget(btn_new)
        lay.addLayout(head)

        self.hub_holder = QWidget()
        self.hub_lay = QVBoxLayout(self.hub_holder)
        self.hub_lay.setContentsMargins(0, 0, 0, 0)
        self.hub_lay.setSpacing(8)
        self.hub_lay.addStretch(1)
        lay.addWidget(self.hub_holder, 1)
        return page

    def _open_playlist_hub(self) -> None:
        """移动形态：进入歌单中心（现有歌单列表 + 新建）。"""
        self._current_playlist = None
        self.stack.setCurrentWidget(self.playlist_hub)
        self._highlight_nav(-2)  # -2 表示仅高亮移动导航的“歌单”
        self._update_playlist_back()
        animation.fade_in(self.playlist_hub)

    def _back_to_playlist_hub(self) -> None:
        """移动形态：从某个歌单内容页返回歌单中心。"""
        self._open_playlist_hub()

    def _back_to_lists(self) -> None:
        self.content_router.setCurrentWidget(self.stack)
        self.header_w.show()
        self._update_mobile_chrome()
        self._update_playlist_back()

    def _tool_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("ToolBtn")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _build_list_page(self, empty_icon, empty_title, empty_sub, with_add_action=False):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        inner = QStackedWidget()
        lst = QListWidget()
        lst.setObjectName("TrackList")
        lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lst.setFrameShape(QListWidget.Shape.NoFrame)
        lst.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        inner.addWidget(lst)
        inner.addWidget(self._build_empty(empty_icon, empty_title, empty_sub, with_add_action))
        lay.addWidget(inner)
        page._inner_stack = inner
        return page, lst, inner

    def _build_empty(self, icon_name, title, sub, with_add_action) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.addStretch(1)
        ic = QLabel()
        ic.setObjectName("EmptyIcon")
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(theme.render_pixmap(icon_name, 56, ICON_DIM))
        t1 = QLabel(title)
        t1.setObjectName("EmptyTitle")
        t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t2 = QLabel(sub)
        t2.setObjectName("EmptySub")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ic)
        v.addSpacing(12)
        v.addWidget(t1)
        v.addSpacing(4)
        v.addWidget(t2)
        if with_add_action:
            v.addSpacing(16)
            row = QHBoxLayout()
            row.addStretch(1)
            self.btn_empty_add = QPushButton("添加音乐")
            self.btn_empty_add.setObjectName("GhostBtn")
            self.btn_empty_add.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_empty_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.btn_empty_add.clicked.connect(self.add_files)
            row.addWidget(self.btn_empty_add)
            row.addStretch(1)
            v.addLayout(row)
        v.addStretch(1)
        return box

    # ---------- 底部播放栏 ----------
    def _build_player_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PlayerBar")
        bar.setFixedHeight(78)
        self.player_bar = bar
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # 封面 + 歌名整块可点击，进入“正在播放”详情（封面/歌词）
        self.now_zone = ClickableFrame()
        self.now_zone.setObjectName("NowZone")
        self.now_zone.setToolTip("查看歌词与专辑")
        nz = QHBoxLayout(self.now_zone)
        nz.setContentsMargins(6, 4, 8, 4)
        nz.setSpacing(8)

        # 默认封面（浅灰圆角 + 音符），有内嵌专辑封面时替换为封面
        self.cover_frame = QFrame()
        self.cover_frame.setObjectName("Cover")
        cl = QHBoxLayout(self.cover_frame)
        cl.setContentsMargins(0, 0, 0, 0)
        self.cover_icon = QLabel()
        self.cover_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_icon.setPixmap(theme.render_pixmap("note", 20, "#8A91A0"))
        cl.addWidget(self.cover_icon)
        nz.addWidget(self.cover_frame)

        self.song_info_w = QWidget()
        self.song_info_w.setFixedWidth(120)
        sb = QVBoxLayout(self.song_info_w)
        sb.setContentsMargins(0, 2, 0, 2)
        sb.setSpacing(2)
        self.song_title = ElidedLabel("未在播放")
        self.song_title.setObjectName("SongTitle")
        self.song_fmt = ElidedLabel("准备就绪")
        self.song_fmt.setObjectName("SongFmt")
        sb.addWidget(self.song_title)
        sb.addWidget(self.song_fmt)
        nz.addWidget(self.song_info_w)
        self.now_zone.clicked.connect(self.open_now_playing)
        lay.addWidget(self.now_zone)

        self.lbl_cur = QLabel("00:00")
        self.lbl_cur.setObjectName("TimeLabel")
        self.lbl_cur.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_cur)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setObjectName("Progress")
        self.progress.setRange(0, 0)
        lay.addWidget(self.progress, 1)

        self.lbl_total = QLabel("00:00")
        self.lbl_total.setObjectName("TimeLabel")
        self.lbl_total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_total)

        # 播放控制区：整体收进一个白色圆角胶囊（辅助功能 | 切歌/播放）
        pill = QFrame()
        pill.setObjectName("CtrlPill")
        pill.setFixedHeight(54)
        self.ctrl_pill = pill
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(12, 0, 12, 0)
        pl.setSpacing(6)

        # 播放模式
        self.btn_mode = self._icon_btn(18)
        self.btn_mode.clicked.connect(self.cycle_play_mode)
        pl.addWidget(self.btn_mode)

        # 任务栏歌词开关：把当前歌词行嵌入显示在 Windows 任务栏内部
        self.btn_task_lyric = self._icon_btn(18, "lyrics")
        self.btn_task_lyric.setToolTip("任务栏歌词（嵌入 Windows 任务栏显示）")
        self.btn_task_lyric.setCheckable(True)
        self.btn_task_lyric.clicked.connect(self.toggle_taskbar_lyrics)
        pl.addWidget(self.btn_task_lyric)

        # 音量：默认只显示喇叭，点击浮出竖向音量条（self.volume 隐藏，仅持有逻辑值）
        self.btn_mute = self._icon_btn(18, "volume")
        self.btn_mute.setToolTip("音量")
        self.btn_mute.clicked.connect(self._open_volume_popup)
        pl.addWidget(self.btn_mute)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setObjectName("Volume")
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.settings.get("volume", 80)))
        self.volume.valueChanged.connect(self._on_volume_changed)

        # 胶囊内的竖分隔线：左侧辅助功能 / 右侧切歌播放
        sep = QFrame()
        sep.setObjectName("PillSep")
        sep.setFixedSize(1, 24)
        self.pill_sep = sep
        pl.addSpacing(4)
        pl.addWidget(sep)
        pl.addSpacing(4)

        # 播放控制
        self.btn_prev = self._ctrl_btn("prev", "#454B57", TEXT_1)
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("PlayBtn")
        self.btn_play.setFixedSize(42, 42)
        self.btn_play.setIconSize(QSize(22, 22))
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setIcon(theme.icon("play", "#FFFFFF", 22))
        self.btn_next = self._ctrl_btn("next", "#454B57", TEXT_1)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.installEventFilter(self.press_ctrl)
            pl.addWidget(b)
        lay.addWidget(pill)
        return bar

    def _icon_btn(self, size: int, name: str = "") -> QPushButton:
        b = QPushButton()
        b.setObjectName("IconBtn")
        b.setIconSize(QSize(size, size))
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.installEventFilter(self.press_icon)
        if name:
            b.setIcon(theme.icon(name, TEXT_2, size))
            b._icon_name = name
        return b

    def _ctrl_btn(self, name, normal, active) -> QPushButton:
        b = QPushButton()
        b.setObjectName("CtrlBtn")
        b.setFixedSize(38, 36)
        b.setIconSize(QSize(22, 22))
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(theme.icon_states(name, normal, active, 22))
        return b

    # ==================================================================
    # 信号 / 快捷键
    # ==================================================================
    def _wire(self) -> None:
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self.toggle_maximize)
        self.btn_close.clicked.connect(self.close)
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_add_folder.clicked.connect(self.add_folder)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.progress.sliderPressed.connect(self._begin_seek)
        self.progress.sliderMoved.connect(self._seek_preview)
        self.progress.sliderReleased.connect(self._end_seek)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._space_toggle)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self._step(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self._step(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._nudge(5000))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._nudge(-5000))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, activated=lambda: self._change_volume(5))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, activated=lambda: self._change_volume(-5))

    def _space_toggle(self) -> None:
        fw = QGuiApplication.focusWidget()
        if isinstance(fw, (QPushButton, QToolButton)):
            return  # 焦点在按钮上时交由按钮处理，避免重复触发
        self._toggle_play()

    def closeEvent(self, event):  # noqa: N802
        """关闭前优雅回收后台线程与线程池，避免任务访问已销毁界面。"""
        try:
            self._persist_settings()  # 保存窗口几何 / 音量 / 曲库 / 最近 / 上次会话
            thread = self._import_thread
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                thread.wait(1000)
            self._decode_pool.clear()
            self._decode_pool.waitForDone(800)
            if self.taskbar_lyrics is not None:
                self.taskbar_lyrics.close()
        finally:
            super().closeEvent(event)

    # ==================================================================
    # 事件过滤器：标题栏拖动 / 关闭键悬停反色
    # ==================================================================
    def nativeEvent(self, eventType, message):  # noqa: N802
        # 先打磨非客户区/背景擦除消息，消除无边框窗口缩放闪烁
        polished = frameless.polish_native(self, message)
        if polished is not None:
            return polished
        # Windows：把窗口边缘/角落回报为缩放热区，实现无边框窗口原生拖拽缩放
        ht = frameless.hit_test(self, message)
        if ht is not None and ht >= frameless.HTLEFT:
            return True, ht
        return super().nativeEvent(eventType, message)

    # 窄于该宽度（逻辑像素）自动切换为移动端紧凑布局
    COMPACT_BREAKPOINT = 720

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        compact = self.width() < self.COMPACT_BREAKPOINT
        if compact != getattr(self, "_compact", False):
            self._compact = compact
            # 跨断点一次性切换大量控件显隐：先冻结整窗重绘，避免逐控件闪烁
            self.setUpdatesEnabled(False)
            try:
                self._apply_responsive()
            finally:
                self.setUpdatesEnabled(True)
            self.update()

    def _apply_responsive(self) -> None:
        """桌面布局 ↔ 移动端布局。

        移动（窄屏）：隐藏整条左侧栏，改由底部横向导航（喜欢/最近/本地/歌单）；
        底部播放栏只保留封面、歌名、作者与一个播放键（进度/时间/切歌/音量/循环全部隐藏，
        这些控制在“正在播放”详情页内提供）；详情页改为上下排布并显示移动控制条。
        """
        c = self._compact
        # 侧栏仅桌面可见，移动由底部横向导航替代
        self.sidebar.setVisible(not c)
        # 底部播放栏高度
        self.player_bar.setFixedHeight(64 if c else 78)
        # 时间 / 进度条：移动隐藏
        for w in (self.lbl_cur, self.progress, self.lbl_total):
            w.setVisible(not c)
        # 胶囊内除中心播放键外全部隐藏（循环/任务栏歌词/音量/分隔线/上下首）
        for w in (self.btn_mode, self.btn_task_lyric, self.btn_mute, self.pill_sep,
                  self.btn_prev, self.btn_next):
            w.setVisible(not c)
        self.ctrl_pill.setProperty("mobile", c)
        self._repolish(self.ctrl_pill)
        # 信息区：移动时歌名/作者列拉伸占满；桌面恢复固定 120
        self.now_zone.setSizePolicy(
            QSizePolicy.Policy.Expanding if c else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed)
        if c:
            self.song_info_w.setFixedWidth(16777215)
            self.song_info_w.setMinimumWidth(0)
            self.song_info_w.setMaximumWidth(16777215)
            self.song_info_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.song_info_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            self.song_info_w.setFixedWidth(120)
        # 详情页形态切换（上下排布 + 移动控制条）
        self.now_view.set_compact(c)
        # 歌单内容页的“返回歌单”键、底部栏可见性
        self._update_playlist_back()
        self._update_mobile_chrome()

    def _update_mobile_chrome(self) -> None:
        """移动形态下，进入正在播放详情时隐藏底部播放栏与底部导航（控制交给详情页）。"""
        c = getattr(self, "_compact", False)
        in_detail = self.content_router.currentWidget() is self.now_view
        self.player_bar.setVisible(not (c and in_detail))
        if hasattr(self, "mobile_nav"):
            self.mobile_nav.setVisible(c and not in_detail)

    def _update_playlist_back(self) -> None:
        """移动形态且停留在某个歌单内容页时，显示头部的“返回歌单中心”。"""
        in_playlist_page = (self._current_playlist is not None
                            and self.content_router.currentWidget() is self.stack
                            and self.stack.currentWidget() is self.playlist_page)
        self.btn_back_playlist.setVisible(self._compact and in_playlist_page)

    def eventFilter(self, obj, event):  # noqa: N802
        if isinstance(obj, QPushButton) and getattr(obj, "_icon_name", "") == "close":
            if event.type() == QEvent.Type.Enter:
                obj.setIcon(theme.icon("close", "#FFFFFF", 15))
            elif event.type() == QEvent.Type.Leave:
                obj.setIcon(theme.icon("close", "#5E6573", 15))
            return False

        if obj in getattr(self, "_drag_widgets", ()):
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if t == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset:
                if self._normal_geom is not None:
                    self.setGeometry(self._normal_geom)
                    self._normal_geom = None
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if t == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                return True
            if t == QEvent.Type.MouseButtonDblClick:
                self.toggle_maximize()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(animation.DURATION_ENTER)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve(animation.EASE_DECELERATE))
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._show_anim = anim
            animation.fade_in(self.stack.currentWidget())
            if getattr(self, "_restore_maximized", False):
                self.showMaximized()
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, _screen) -> None:
        """窗口拖到不同缩放比的屏幕：清空位图缓存，按新 dpr 重渲染所有图标。"""
        theme.clear_icon_cache()
        self._restyle_icons()
        self.refresh_all()

    def _restyle_icons(self) -> None:
        self.btn_min.setIcon(theme.icon("minimize", "#5E6573", 15))
        self.btn_max.setIcon(theme.icon(
            "restore" if self._normal_geom is not None else "maximize", "#5E6573", 15))
        self.btn_close.setIcon(theme.icon("close", "#5E6573", 15))
        self._highlight_nav(-1 if self._current_playlist is not None else self.stack.currentIndex())
        self._update_mode_button()
        task_color = BRAND if getattr(self, "_task_on", False) else TEXT_2
        self.btn_task_lyric.setIcon(theme.icon("lyrics", task_color, 18))
        self._on_volume_changed(self.volume.value())
        self.btn_prev.setIcon(theme.icon_states("prev", "#454B57", TEXT_1, 22))
        self.btn_next.setIcon(theme.icon_states("next", "#454B57", TEXT_1, 22))
        self._on_state(self.engine.state)
        self._apply_cover()
        track = self.tracks.get(self.current_path) if self.current_path else None
        if track is not None:
            self.now_view.set_track(track.meta or TrackMeta(), track.title)

    def _apply_cover(self) -> None:
        """渲染当前曲目内嵌封面；无封面或解码失败时回退默认音符。"""
        track = self.tracks.get(self.current_path) if self.current_path else None
        if track is not None and track.meta is not None and track.meta.has_cover:
            pix = theme.cover_pixmap(track.meta.cover, 42, radius=10)
            if pix is not None:
                self.cover_icon.setPixmap(pix)
                return
        self.cover_icon.setPixmap(theme.render_pixmap("note", 20, "#8A91A0"))

    def open_now_playing(self) -> None:
        """打开正在播放详情（封面 + 歌词）；尚未播放且本地有歌时先播放第一首。"""
        if self.current_path is None:
            if self.local_order:
                self.play_path(self.local_order[0], self.local_order)
            else:
                return
        self.header_w.hide()
        self.content_router.setCurrentWidget(self.now_view)
        animation.fade_in(self.now_view)
        self._update_mobile_chrome()

    # ==================================================================
    # 导航 / 列表
    # ==================================================================
    def switch_page(self, idx: int) -> None:
        """切换到三个主导航页之一（红心/最近/本地）。"""
        if self._current_playlist is None and self.stack.currentIndex() == idx:
            return
        self._current_playlist = None
        self.stack.setCurrentIndex(idx)
        self._highlight_nav(idx)
        animation.fade_in(self.stack.currentWidget())
        self._update_playlist_back()

    def open_playlist(self, name: str) -> None:
        """打开某个歌单内容页。"""
        if name not in self.playlists:
            return
        self._current_playlist = name
        self.stack.setCurrentIndex(3)
        self._highlight_nav(-1)
        self._render_current_playlist()
        animation.fade_in(self.stack.currentWidget())
        self._update_playlist_back()

    @staticmethod
    def _repolish(b) -> None:
        b.style().unpolish(b)
        b.style().polish(b)

    def _highlight_nav(self, active_main: int) -> None:
        """active_main：0-2 对应主导航页；-1 某歌单内容页；-2 移动歌单中心。"""
        # 桌面侧栏主导航
        for i, b in enumerate(self.nav_buttons):
            on = i == active_main
            b.setProperty("active", on)
            color = BRAND if on else TEXT_2
            b.setIcon(theme.icon(NAV_ITEMS[i][1], color, 20))
            self._repolish(b)
        # 移动底部导航（第 4 项“歌单”在歌单中心/歌单内容页都高亮）
        for i, b in enumerate(getattr(self, "mobile_nav_buttons", ())):
            on = (i == active_main) if i < 3 else (active_main in (-1, -2))
            b.setProperty("active", on)
            color = BRAND if on else TEXT_2
            b.setIcon(theme.icon(b._nav_icon, color, 20))
            self._repolish(b)
        for name, b in self._playlist_buttons.items():
            on = active_main == -1 and name == self._current_playlist
            b.setProperty("active", on)
            b.setIcon(theme.icon("playlist", BRAND if on else TEXT_2, 20))
            self._repolish(b)
        self._sync_header()

    def _playlist_paths(self, name: str) -> List[str]:
        """歌单内、且当前曲库中存在的曲目（未导入的路径自动隐藏）。"""
        return [p for p in self.playlists.get(name, []) if p in self.tracks]

    def _sync_header(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 3 and self._current_playlist is not None:
            paths = self._playlist_paths(self._current_playlist)
            self.title_label.setText(self._current_playlist)
            total = sum(self.tracks[p].duration_ms for p in paths)
            self.count_label.setText(f"{len(paths)} 首 · 共 {format_total(total)}" if paths else "")
            self.btn_add_files.setVisible(False)
            self.btn_add_folder.setVisible(False)
            return
        if idx == 4:  # 移动“歌单中心”：页内自带标题与新建按钮，顶部只留“歌单”
            self.title_label.setText("歌单")
            self.count_label.setText("")
            self.btn_add_files.setVisible(False)
            self.btn_add_folder.setVisible(False)
            return
        self.title_label.setText(self.page_titles[idx])
        if idx == 0:
            paths = [p for p in self.local_order if p in self.liked]
            label = "收藏"
        elif idx == 1:
            paths = [p for p in self.recent if p in self.tracks]
            label = "最近播放"
        else:
            paths = self.local_order
            label = "本地"
        total = sum(self.tracks[p].duration_ms for p in paths if p in self.tracks)
        self.count_label.setText(
            f"{label} {len(paths)} 首 · 共 {format_total(total)}" if paths else "")
        self.btn_add_files.setVisible(idx == 2)
        self.btn_add_folder.setVisible(idx == 2)

    def _set_empty(self, page, is_empty: bool) -> None:
        page._inner_stack.setCurrentIndex(1 if is_empty else 0)

    def refresh_all(self) -> None:
        self._render(self.local_list, self.local_order, self.local_page)
        self._render(self.recent_list, [p for p in self.recent if p in self.tracks], self.recent_page)
        liked = [p for p in self.local_order if p in self.liked]
        liked += [p for p in self.recent if p in self.liked and p not in liked]
        self._render(self.liked_list, liked, self.liked_page)
        if self._current_playlist is not None:
            self._render_current_playlist()
        if hasattr(self, "hub_lay"):
            self._rebuild_hub()
        self._sync_header()

    def _render_current_playlist(self) -> None:
        if self._current_playlist is None:
            return
        self._render(self.playlist_list,
                     self._playlist_paths(self._current_playlist), self.playlist_page)
        self._sync_header()

    def _render(self, lst: QListWidget, paths: List[str], page) -> None:
        lst.clear()
        for i, p in enumerate(paths, start=1):
            t = self.tracks.get(p)
            if t is None:
                continue
            item = QListWidgetItem(lst)
            item.setSizeHint(QSize(0, 44))
            item.setData(Qt.ItemDataRole.UserRole, p)
            row = TrackRow(p, t.title, t.ext, format_time(t.duration_ms),
                           p in self.liked, index=i, playing=(p == self.current_path))
            row.doubleActivated.connect(lambda pp, src=paths: self.play_path(pp, src))
            row.likedClicked.connect(self.toggle_liked)
            row.addRequested.connect(self._show_add_to_playlist_menu)
            row.heart.installEventFilter(self.press_icon)
            row.add_list_btn.installEventFilter(self.press_icon)
            lst.addItem(item)
            lst.setItemWidget(item, row)
        self._set_empty(page, len(paths) == 0)

    # ==================================================================
    # 导入
    # ==================================================================
    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            f"音频文件 ({' '.join('*' + e for e in SUPPORTED_EXTS)});;所有文件 (*.*)")
        if files:
            self._start_import(paths=files)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if folder:
            # 扫描与解析都放到后台线程，避免大文件夹卡住界面与正在播放的声音
            self._start_import(folder=folder)

    # ---------- 后台导入 ----------
    def _set_import_busy(self, busy: bool, done: int = 0, total: int = 0) -> None:
        self._importing = busy
        self.btn_add_files.setEnabled(not busy)
        self.btn_add_folder.setEnabled(not busy)
        if busy:
            self.btn_add_files.setText("导入中…")
            self.btn_add_folder.setText(f"导入中 {done}/{total}" if total else "导入中…")
        else:
            self.btn_add_files.setText("添加文件")
            self.btn_add_folder.setText("添加文件夹")

    def _start_import(self, paths: Optional[List[str]] = None,
                      folder: Optional[str] = None) -> None:
        if self._importing:
            return
        self._import_is_folder = folder is not None
        self._set_import_busy(True)
        thread = QThread(self)
        worker = ImportWorker(set(self.tracks.keys()), paths=paths, folder=folder)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_import_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._import_thread = thread
        self._import_worker = worker
        thread.start()

    def _on_import_progress(self, done: int, total: int) -> None:
        self._set_import_busy(True, done, total)

    def _on_import_finished(self, records) -> None:
        added = self._apply_records(records)
        is_folder = getattr(self, "_import_is_folder", False)
        restoring = getattr(self, "_restoring_library", False)
        self._restoring_library = False
        self._set_import_busy(False)
        self._import_thread = None
        self._import_worker = None
        self.refresh_all()
        if restoring:
            # 启动恢复：不跳页、不弹“空文件夹”，随后定位上次曲目
            self._after_library_restored()
            return
        self._persist_settings()
        if added:
            self.switch_to(2)
        elif is_folder:
            NoticeDialog.show(self, "提示", "该文件夹下没有找到新的 WAV / FLAC 文件。")

    def _apply_records(self, records) -> int:
        added = 0
        for path, title, ext, duration_ms, meta in records:
            if path in self.tracks:
                continue
            self.tracks[path] = Track(
                path=path, title=title, ext=ext, duration_ms=duration_ms, meta=meta)
            self.local_order.append(path)
            added += 1
        return added

    def _ingest(self, paths: List[str]) -> int:
        """同步导入（供测试/脚本使用）；UI 入口走 :meth:`_start_import` 后台线程。"""
        records = build_records(paths, set(self.tracks.keys()))
        added = self._apply_records(records)
        self.refresh_all()
        self._persist_settings()
        if added:
            self.switch_to(2)
        return added

    def switch_to(self, idx: int) -> None:
        if self.stack.currentIndex() != idx:
            self.switch_page(idx)

    # ==================================================================
    # 歌单
    # ==================================================================
    def _rebuild_playlist_buttons(self) -> None:
        """按 self.playlists 重建侧栏歌单按钮（保序）。"""
        while self.playlist_lay.count():
            item = self.playlist_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._playlist_buttons = {}
        for name in self.playlists:
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setText(name)
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setFixedHeight(36)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(theme.icon("playlist", TEXT_2, 20))
            b.clicked.connect(lambda _=False, n=name: self.open_playlist(n))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, n=name, bb=b: self._playlist_context_menu(n, bb.mapToGlobal(pos)))
            self.playlist_lay.addWidget(b)
            self._playlist_buttons[name] = b
        # 同步移动形态的“歌单中心”页（构建顺序上侧栏更早，此时 hub 可能尚未创建）
        if hasattr(self, "hub_lay"):
            self._rebuild_hub()

    def _rebuild_hub(self) -> None:
        """重建移动“歌单中心”里的歌单条目（保序，与侧栏同步）。"""
        while self.hub_lay.count() > 1:  # 保留末尾 stretch
            item = self.hub_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)  # 立即从界面摘除，避免 deleteLater 当帧残留
                w.deleteLater()
        if not self.playlists:
            empty = QLabel("还没有歌单，点击右上角“新建歌单”即可创建")
            empty.setObjectName("HubEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hub_lay.insertWidget(0, empty)
            return
        for idx, name in enumerate(self.playlists):
            n = len(self._playlist_paths(name))
            b = QToolButton()
            b.setObjectName("HubItem")
            b.setText(f"{name}    {n} 首")
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setFixedHeight(52)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(theme.icon("playlist", TEXT_2, 20))
            b.clicked.connect(lambda _=False, n=name: self.open_playlist(n))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, n=name, bb=b: self._playlist_context_menu(n, bb.mapToGlobal(pos)))
            self.hub_lay.insertWidget(idx, b)

    def _unique_playlist_name(self, base: str) -> str:
        if base not in self.playlists:
            return base
        i = 2
        while f"{base} {i}" in self.playlists:
            i += 1
        return f"{base} {i}"

    def create_playlist(self, prefill_path: Optional[str] = None) -> None:
        """新建歌单；prefill_path 非空时把该曲直接加入新歌单。"""
        suggested = self._unique_playlist_name("新建歌单")
        name, ok = InputDialog.get_text(self, "新建歌单", "歌单名称：", text=suggested)
        if not ok:
            return
        if not name:
            NoticeDialog.show(self, "提示", "歌单名称不能为空。")
            return
        if name in self.playlists:
            NoticeDialog.show(self, "新建歌单", "已存在同名歌单，请换一个名称。")
            return
        self.playlists[name] = [prefill_path] if prefill_path else []
        store.save_playlists(self.playlists)
        self._rebuild_playlist_buttons()
        self.open_playlist(name)

    def _show_add_to_playlist_menu(self, path: str, global_pos) -> None:
        """歌曲行尾“+”：弹出原生菜单，选择目标歌单或新建后加入。"""
        menu = QMenu(self)
        for name in self.playlists:
            act = menu.addAction(theme.icon("playlist", TEXT_2, 18), name)
            act.triggered.connect(lambda _=False, n=name: self.add_to_playlist(n, path))
        if self.playlists:
            menu.addSeparator()
        act_new = menu.addAction(theme.icon("plus", TEXT_2, 18), "新建歌单并添加…")
        act_new.triggered.connect(lambda: self.create_playlist(prefill_path=path))
        menu.exec(global_pos)

    def add_to_playlist(self, name: str, path: str) -> None:
        songs = self.playlists.setdefault(name, [])
        if path not in songs:
            songs.append(path)
            store.save_playlists(self.playlists)
        if self._current_playlist == name:
            self._render_current_playlist()

    def remove_from_playlist(self, name: str, path: str) -> None:
        songs = self.playlists.get(name)
        if songs and path in songs:
            songs.remove(path)
            store.save_playlists(self.playlists)
            self._render_current_playlist()

    def _playlist_song_menu(self, pos) -> None:
        """在歌单内容页右键某首歌：从当前歌单移除。"""
        if self._current_playlist is None:
            return
        item = self.playlist_list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act = menu.addAction(f"从「{self._current_playlist}」移除")
        chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))
        if chosen is act:
            self.remove_from_playlist(self._current_playlist, path)

    def _playlist_context_menu(self, name: str, global_pos) -> None:
        """侧栏歌单项右键：播放全部 / 重命名 / 删除。"""
        menu = QMenu(self)
        act_play = menu.addAction("播放全部")
        act_rename = menu.addAction("重命名")
        act_del = menu.addAction("删除歌单")
        chosen = menu.exec(global_pos)
        if chosen is act_play:
            self.open_playlist(name)
            paths = self._playlist_paths(name)
            if paths:
                self.play_path(paths[0], paths)
        elif chosen is act_rename:
            self._rename_playlist(name)
        elif chosen is act_del:
            self._delete_playlist(name)

    def _rename_playlist(self, name: str) -> None:
        new_name, ok = InputDialog.get_text(self, "重命名歌单", "新的名称：", text=name)
        if not ok:
            return
        if not new_name or new_name == name:
            return
        if new_name in self.playlists:
            NoticeDialog.show(self, "重命名歌单", "已存在同名歌单。")
            return
        # dict 改键并保持原顺序
        rebuilt = {new_name if k == name else k: v for k, v in self.playlists.items()}
        self.playlists = rebuilt
        if self._current_playlist == name:
            self._current_playlist = new_name
        store.save_playlists(self.playlists)
        self._rebuild_playlist_buttons()
        self._highlight_nav(-1 if self._current_playlist is not None else self.stack.currentIndex())

    def _delete_playlist(self, name: str) -> None:
        ok = ConfirmDialog.get_confirm(
            self, "删除歌单", f"确定删除歌单「{name}」吗？（不会删除本地音乐文件）",
            ok_text="删除", danger=True)
        if not ok:
            return
        self.playlists.pop(name, None)
        store.save_playlists(self.playlists)
        if self._current_playlist == name:
            self._current_playlist = None
            self.switch_page(2)
        self._rebuild_playlist_buttons()
        self._highlight_nav(-1 if self._current_playlist is not None else self.stack.currentIndex())

    # ==================================================================
    # 收藏 / 播放
    # ==================================================================
    def toggle_liked(self, path: str) -> None:
        (self.liked.discard if path in self.liked else self.liked.add)(path)
        self.refresh_all()
        self._persist_settings()

    def play_path(self, path: str, source_queue: Optional[List[str]] = None) -> None:
        """请求播放：整曲解码放到后台线程，解码完成回主线程再真正起播，避免卡顿。"""
        if path not in self.tracks:
            return
        queue = [p for p in (source_queue or self.local_order) if p in self.tracks]
        self._pending_queue = queue if path in queue else list(self.local_order)
        self._play_token += 1
        token = self._play_token
        self._pending_path = path
        # 即时反馈但不阻塞；旧歌继续播放直到新曲解码完成，切换几乎无间隙
        t0 = self.tracks[path]
        self.song_title.setText(t0.title)
        self.song_fmt.setText(self._song_sub(
            t0.meta.artist if t0.meta else "", "加载中…"))
        self._decode_pool.clear()  # 丢弃仍在排队的旧解码任务
        self._decode_pool.start(
            DecodeTask(path, token, self._decode_bridge, self.engine.output_format))

    def _on_decoded(self, path: str, payload, token: int) -> None:
        if token != self._play_token or path != self._pending_path:
            return  # 已被更新的播放请求取代，丢弃过期解码结果（防串歌）
        self._pending_path = None
        audio, device_pcm = payload
        self._start_playback(path, audio, self._pending_queue, device_pcm)

    def _on_decode_failed(self, path: str, message: str, token: int) -> None:
        if token != self._play_token or path != self._pending_path:
            return
        self._pending_path = None
        if "加载中" in self.song_fmt.text():
            tr = self.tracks.get(path)
            self.song_fmt.setText(self._song_sub(
                tr.meta.artist if tr is not None and tr.meta else "", ""))
        NoticeDialog.show(
            self, "解码失败", f"无法播放该文件：\n{path}\n\n{message}",
            ok_text="知道了", danger=True)

    @staticmethod
    def _song_sub(artist: str, fmt: str) -> str:
        """播放栏副信息行：作者为主，编码格式以中点缀在其后；缺省回退占位。"""
        artist = (artist or "").strip()
        fmt = (fmt or "").strip()
        if artist and fmt:
            return f"{artist} · {fmt}"
        return artist or fmt or "准备就绪"

    def _start_playback(self, path: str, audio, queue: List[str], device_pcm=None) -> None:
        self.engine.load(audio, device_pcm)
        self.engine.set_volume(self.volume.value() / 100.0)
        self.engine.play()
        self.current_path = path
        self.queue = queue

        if path in self.recent:
            self.recent.remove(path)
        self.recent.insert(0, path)
        self.recent = self.recent[:50]

        t = self.tracks[path]
        self._source_format = audio.source_format
        self.song_title.setText(t.title)
        self.song_fmt.setText(self._song_sub(
            t.meta.artist if t.meta else "", audio.source_format))
        self.progress.setRange(0, audio.duration_ms)
        self.lbl_total.setText(format_time(audio.duration_ms))
        self.now_view.set_track(t.meta or TrackMeta(), t.title)
        if self._task_on:
            self._bind_task_lyrics(path)
        self._apply_cover()
        self.refresh_all()

        # 启动恢复：加载上次曲目后定位到退出时进度并保持暂停（不自动出声）
        if self._pending_restore_seek is not None:
            seek_ms = self._pending_restore_seek
            self._pending_restore_seek = None
            if 0 <= seek_ms <= audio.duration_ms:
                self.engine.seek_ms(seek_ms)
                self.progress.setValue(seek_ms)
            self.engine.pause()

        self._persist_settings()  # 最近播放变化，落盘一次

    def _toggle_play(self) -> None:
        if self.current_path is None:
            if self.local_order:
                self.play_path(self.local_order[0], self.local_order)
            return
        self.engine.toggle()

    def _nudge(self, delta_ms: int) -> None:
        if QGuiApplication.focusWidget() is self.progress:
            return
        if self.current_path:
            self.engine.seek_ms(self.engine.current_ms() + delta_ms)

    def _step(self, delta: int, auto: bool = False) -> None:
        if not self.queue:
            if self.local_order:
                self.queue = list(self.local_order)
            else:
                return
        if self.current_path in self.queue:
            idx = self.queue.index(self.current_path)
            nxt = (idx + delta) % len(self.queue)
        else:
            nxt = 0
        if delta == -1 and not auto and self.engine.current_ms() > 3000:
            self.engine.seek_ms(0)
            return
        target = self.queue[nxt]
        if target != self.current_path or auto:
            self.play_path(target, self.queue)

    def _on_ended(self) -> None:
        """自然播完：按播放模式决定下一步。"""
        if self.play_mode == "single" and self.current_path is not None:
            self.play_path(self.current_path, self.queue)
        elif self.play_mode == "shuffle" and len(self.queue) > 1:
            candidates = [p for p in self.queue if p != self.current_path]
            self.play_path(random.choice(candidates), self.queue)
        else:
            self._step(1, auto=True)

    # -------- 播放模式 / 音量 --------
    def cycle_play_mode(self) -> None:
        order = [m[0] for m in PLAY_MODES]
        self.play_mode = order[(order.index(self.play_mode) + 1) % len(order)]
        self._update_mode_button()
        self._persist_settings()
        animation.pop_icon(self.btn_mode, 18)

    def _update_mode_button(self) -> None:
        mode = next(m for m in PLAY_MODES if m[0] == self.play_mode)
        icon = theme.icon(mode[1], BRAND, 18)
        self.btn_mode.setIcon(icon)
        self.btn_mode.setToolTip(f"播放模式：{mode[2]}（点击切换）")
        self.btn_mode.setProperty("on", True)
        self.btn_mode.style().unpolish(self.btn_mode)
        self.btn_mode.style().polish(self.btn_mode)
        if hasattr(self, "now_view"):
            self.now_view.set_mode_icon(icon)

    def toggle_taskbar_lyrics(self) -> None:
        """开关“任务栏歌词”：在 Windows 任务栏内部嵌入一条当前歌词。"""
        want = self.btn_task_lyric.isChecked()
        if want:
            if not self._ensure_taskbar_window():
                # 当前平台/环境不支持嵌入：回弹开关并说明
                self.btn_task_lyric.blockSignals(True)
                self.btn_task_lyric.setChecked(False)
                self.btn_task_lyric.blockSignals(False)
                NoticeDialog.show(
                    self, "任务栏歌词",
                    "当前系统环境不支持把歌词嵌入 Windows 任务栏。")
                return
            self._task_on = True
            self._bind_task_lyrics(self.current_path)
            self.btn_task_lyric.setIcon(theme.icon("lyrics", BRAND, 18))
            self._update_taskbar_line(self.engine.current_ms(), force=True)
        else:
            self._task_on = False
            if self.taskbar_lyrics is not None:
                self.taskbar_lyrics.hide()
            self.btn_task_lyric.setIcon(theme.icon("lyrics", TEXT_2, 18))
        animation.pop_icon(self.btn_task_lyric, 18)

    def _ensure_taskbar_window(self) -> bool:
        """懒创建并显示任务栏内嵌歌词窗；平台不支持或创建失败时返回 False。"""
        if TaskbarLyrics is None or self.taskbar_lyrics is None:
            if TaskbarLyrics is None:
                return False
            self.taskbar_lyrics = TaskbarLyrics()
        if not self.taskbar_lyrics.is_supported():
            return False
        return self.taskbar_lyrics.show()

    def _bind_task_lyrics(self, path: Optional[str]) -> None:
        """载入当前曲目同步歌词时间轴供任务栏歌词条使用；无同步歌词则退化为曲名。"""
        track = self.tracks.get(path) if path else None
        meta = track.meta if track else None
        timed = list(meta.lyrics) if (meta and meta.lyrics) else []
        self._task_times = [t for t, _ in timed]
        self._task_lines = [txt for _, txt in timed]
        self._task_idx = -2
        self._task_placeholder = track.title if track else ""

    def _update_taskbar_line(self, ms: int, force: bool = False) -> None:
        """按播放位置选当前歌词行并刷新任务栏歌词条（仅在换行时重绘）。"""
        if not self._task_on or self.taskbar_lyrics is None:
            return
        if self._task_times:
            idx = max(0, bisect_right(self._task_times, ms) - 1)
            if force or idx != self._task_idx:
                self._task_idx = idx
                self.taskbar_lyrics.set_text(self._task_lines[idx])
        else:
            # 无同步歌词：整曲显示曲名（占位）
            if force or self._task_idx != -1:
                self._task_idx = -1
                self.taskbar_lyrics.set_text(self._task_placeholder)

    def _on_volume_changed(self, value: int) -> None:
        self.engine.set_volume(value / 100.0)
        name = "mute" if value == 0 else "volume"
        if getattr(self.btn_mute, "_icon_name", "") != name:
            self.btn_mute.setIcon(theme.icon(name, TEXT_2, 18))
            self.btn_mute._icon_name = name
        if hasattr(self, "now_view"):
            self.now_view.set_volume_icon(theme.icon(name, TEXT_2, 18))
        if value > 0:
            self._last_volume = value
        if hasattr(self, "volume_popup"):
            self.volume_popup.sync(value, name, TEXT_2)

    def _change_volume(self, delta: int) -> None:
        self.volume.setValue(max(0, min(100, self.volume.value() + delta)))

    def _setup_volume_popup(self) -> None:
        self.volume_popup = VolumePopup()
        self.volume_popup.valueChanged.connect(self.volume.setValue)
        self.volume_popup.muteRequested.connect(self.toggle_mute)

    def _open_volume_popup(self) -> None:
        value = self.volume.value()
        name = "mute" if value == 0 else "volume"
        self.volume_popup.sync(value, name, TEXT_2)
        self.volume_popup.popup_at(self.btn_mute)

    def _open_mobile_volume(self) -> None:
        """移动详情页控制条上的音量键：复用同一浮层，锚定到移动音量键。"""
        value = self.volume.value()
        name = "mute" if value == 0 else "volume"
        self.volume_popup.sync(value, name, TEXT_2)
        self.volume_popup.popup_at(self.now_view.m_btn_vol)

    def toggle_mute(self) -> None:
        self.volume.setValue(0 if self.volume.value() > 0 else self._last_volume)

    # -------- 进度 --------
    def _begin_seek(self) -> None:
        self._seeking = True

    def _seek_preview(self, value: int) -> None:
        self.lbl_cur.setText(format_time(value))

    def _end_seek(self) -> None:
        self.engine.seek_ms(self.progress.value())
        self._seeking = False

    def _on_position(self, ms: int) -> None:
        if not self._seeking:
            self.progress.blockSignals(True)
            self.progress.setValue(ms)
            self.progress.blockSignals(False)
            self.lbl_cur.setText(format_time(ms))
        if hasattr(self, "now_view"):
            self.now_view.set_position(ms)
        if getattr(self, "_task_on", False) and self.taskbar_lyrics is not None:
            self._update_taskbar_line(ms)
            # 每 ~500ms 复核任务栏句柄/几何，应对 Explorer 重启、DPI/分辨率变化
            self._task_tick += 1
            if self._task_tick >= 10:
                self._task_tick = 0
                self.taskbar_lyrics.reassert()

    def _on_state(self, state: str) -> None:
        name = "pause" if state == "playing" else "play"
        self.btn_play.setIcon(theme.icon(name, "#FFFFFF", 22))
        animation.pop_icon(self.btn_play, 22)
        if hasattr(self, "now_view"):
            self.now_view.set_playing(state == "playing")

    # ==================================================================
    # 窗口
    # ==================================================================
    def toggle_maximize(self) -> None:
        if self._normal_geom is None:
            self._normal_geom = self.geometry()
            self.setGeometry(self.screen().availableGeometry())
            self.btn_max.setIcon(theme.icon("restore", "#5E6573", 15))
        else:
            self.setGeometry(self._normal_geom)
            self._normal_geom = None
            self.btn_max.setIcon(theme.icon("maximize", "#5E6573", 15))

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2,
                  screen.center().y() - self.height() // 2)

    # ------------------------------------------------------------------
    # 配置记忆：窗口几何 / 曲库 / 最近 / 红心 / 音量 / 模式 / 上次会话
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        """启动时恢复上次窗口大小与位置；记录无效则回退居中。"""
        win = self.settings.get("window", {})
        w, h = win.get("width"), win.get("height")
        x, y = win.get("x"), win.get("y")
        if isinstance(w, int) and isinstance(h, int) and w >= self.minimumWidth() and h >= self.minimumHeight():
            self.resize(w, h)
        pos_ok = False
        if isinstance(x, int) and isinstance(y, int):
            # 窗口左上角偏移一点后仍落在某个屏幕内才认为位置有效，避免恢复到已断开的屏幕外
            for scr in QGuiApplication.screens():
                if scr.geometry().contains(x + 60, y + 40):
                    pos_ok = True
                    break
        self.move(x, y) if pos_ok else self._center_on_screen()
        self._restore_maximized = bool(win.get("maximized", False))

    def _restore_library_if_any(self) -> None:
        """有上次曲库记录时，后台线程重建曲目（重新读取元数据，不阻塞启动）。"""
        paths = list(self.settings.get("library_paths", []))
        if not paths:
            self._restoring_library = False
            return
        # _restoring_library 已在 __init__ 置 True；_on_import_finished 中据此不跳页/不弹空提示
        self._start_import(paths=paths)

    def _after_library_restored(self) -> None:
        """曲库重建完成：恢复最近/红心显示（refresh_all 已处理），并定位到上次曲目（暂停）。"""
        sess = self._restore_session or {}
        path = sess.get("path")
        if path and path in self.tracks:
            self._pending_restore_seek = int(sess.get("position_ms", 0) or 0)
            self.play_path(path, list(self.local_order))

    def _collect_settings(self) -> None:
        """把当前运行状态汇总进 self.settings（不落盘）。"""
        geom = (self._normal_geom if self.isMaximized() and self._normal_geom
                else self.geometry())
        self.settings["window"] = {
            "width": geom.width(), "height": geom.height(),
            "x": geom.x(), "y": geom.y(), "maximized": self.isMaximized()}
        self.settings["volume"] = int(self.volume.value())
        self.settings["play_mode"] = self.play_mode
        # 红心按曲库/最近顺序落盘，得到稳定有序列表
        liked = [p for p in self.local_order if p in self.liked]
        liked += [p for p in self.recent if p in self.liked and p not in liked]
        self.settings["liked"] = liked
        self.settings["recent"] = list(self.recent[:50])
        self.settings["library_paths"] = list(self.local_order)
        self.settings["last_session"] = {
            "path": self.current_path,
            "position_ms": self.engine.current_ms() if self.current_path else 0,
            "playing": self._engine_is_playing(),
        }

    def _persist_settings(self) -> None:
        self._collect_settings()
        store.save_settings(self.settings)

    def _engine_is_playing(self) -> bool:
        try:
            from PySide6.QtMultimedia import QAudio
            return self.engine.state == QAudio.State.ActiveState
        except Exception:  # noqa: BLE001
            return False
