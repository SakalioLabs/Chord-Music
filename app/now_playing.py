"""正在播放详情视图：专辑封面、曲目元信息与随播放进度高亮滚动的内嵌歌词。

桌面形态：左封面/元信息、右歌词的横向双栏。
移动形态（:meth:`set_compact`）：整体改为上下排布、封面缩小，底部出现一条移动控制条
（循环 / 上一首 / 播放暂停 / 下一首 / 音量），因为移动形态下主窗口底部播放栏已精简，
完整控制由详情页接管。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .metadata import TrackMeta

COVER_SIZE = 220
MOBILE_COVER_SIZE = 156


class NowPlayingView(QWidget):
    """覆盖在列表之上的详情页，通过 :meth:`set_track` 更新内容。"""

    backRequested = Signal()
    modeRequested = Signal()
    prevRequested = Signal()
    playRequested = Signal()
    nextRequested = Signal()
    volumeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._times: List[int] = []
        self._lines: List[QLabel] = []
        self._active_idx = -2
        self._compact = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 6)
        root.setSpacing(14)

        back = QPushButton("  返回列表")
        back.setObjectName("NpBack")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.setIcon(theme.icon("back", "#5E6573", 18))
        back.setIconSize(QSize(18, 18))
        back.clicked.connect(self.backRequested.emit)
        self.back_btn = back
        root.addWidget(back)

        self.body = QHBoxLayout()
        self.body.setSpacing(34)
        root.addLayout(self.body, 1)

        # 左：封面 + 元信息
        left = QVBoxLayout()
        left.setSpacing(14)
        left.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cover = QLabel()
        self.cover.setObjectName("NpCover")
        self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.title = QLabel("未在播放")
        self.title.setObjectName("NpTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        self.artist = QLabel("")
        self.artist.setObjectName("NpArtist")
        self.artist.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.album = QLabel("")
        self.album.setObjectName("NpAlbum")
        self.album.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        for w in (self.title, self.artist, self.album):
            left.addWidget(w)
        left.addStretch(1)

        self.left_wrap = QWidget()
        self.left_wrap.setLayout(left)
        self.left_wrap.setFixedWidth(280)
        self.body.addWidget(self.left_wrap)

        # 右：歌词滚动区
        self.scroll = QScrollArea()
        self.scroll.setObjectName("LyricScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.lyric_box_w = QWidget()
        self.lyric_box = QVBoxLayout(self.lyric_box_w)
        self.lyric_box.setContentsMargins(16, 0, 16, 0)
        self.lyric_box.setSpacing(16)
        self.lyric_box.addStretch(1)
        self._placeholder = QLabel("播放歌曲后，这里会逐行显示歌词")
        self._placeholder.setObjectName("LyricPlaceholder")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyric_box.addWidget(self._placeholder)
        self.lyric_box.addStretch(1)
        self.scroll.setWidget(self.lyric_box_w)
        self.body.addWidget(self.scroll, 1)

        # 移动形态底部控制条（桌面隐藏）
        root.addWidget(self._build_mobile_controls())

    def _build_mobile_controls(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("MobileCtrl")
        bar.hide()
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)

        def icon_btn(name: str, tip: str, signal_name: str, size: int = 18) -> QPushButton:
            b = QPushButton()
            b.setObjectName("IconBtn")
            b.setIconSize(QSize(size, size))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(theme.icon(name, "#5E6573", size))
            b.setToolTip(tip)
            b.clicked.connect(lambda: getattr(self, signal_name).emit())
            return b

        self.m_btn_mode = icon_btn("repeat", "播放模式", "modeRequested")
        self.m_btn_prev = icon_btn("prev", "上一首", "prevRequested", 22)
        self.m_btn_next = icon_btn("next", "下一首", "nextRequested", 22)
        self.m_btn_vol = icon_btn("volume", "音量", "volumeRequested")

        self.m_btn_play = QPushButton()
        self.m_btn_play.setObjectName("PlayBtn")
        self.m_btn_play.setFixedSize(48, 48)
        self.m_btn_play.setIconSize(QSize(24, 24))
        self.m_btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.m_btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.m_btn_play.setIcon(theme.icon("play", "#FFFFFF", 24))
        self.m_btn_play.clicked.connect(self.playRequested.emit)

        row.addStretch(1)
        row.addWidget(self.m_btn_mode)
        row.addStretch(2)
        row.addWidget(self.m_btn_prev)
        row.addWidget(self.m_btn_play)
        row.addWidget(self.m_btn_next)
        row.addStretch(2)
        row.addWidget(self.m_btn_vol)
        row.addStretch(1)
        self.m_ctrl = bar
        return bar

    # ------------------------------------------------------------------
    def set_compact(self, compact: bool) -> None:
        """桌面横向双栏 ↔ 移动上下排布（含底部控制条显隐）。"""
        if compact == self._compact:
            self.m_ctrl.setVisible(compact)
            return
        self._compact = compact
        if compact:
            self.body.setDirection(QHBoxLayout.Direction.TopToBottom)
            self.cover.setFixedSize(MOBILE_COVER_SIZE, MOBILE_COVER_SIZE)
            self.left_wrap.setMinimumWidth(0)
            self.left_wrap.setMaximumWidth(16777215)
            self.left_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            self.body.setSpacing(14)
        else:
            self.body.setDirection(QHBoxLayout.Direction.LeftToRight)
            self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
            self.left_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.left_wrap.setFixedWidth(280)
            self.body.setSpacing(34)
        self.m_ctrl.setVisible(compact)

    def set_playing(self, playing: bool) -> None:
        """同步播放/暂停图标（与主窗口播放键一致）。"""
        self.m_btn_play.setIcon(theme.icon("pause" if playing else "play", "#FFFFFF", 24))

    def set_mode_icon(self, icon: QIcon) -> None:
        self.m_btn_mode.setIcon(icon)

    def set_volume_icon(self, icon: QIcon) -> None:
        self.m_btn_vol.setIcon(icon)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.title.setText("未在播放")
        self.artist.setText("")
        self.album.setText("")
        self.cover.setPixmap(theme.render_pixmap("note", 88, "#C4C9D4"))
        self._set_lyrics([], "播放歌曲后，这里会逐行显示歌词")

    def set_track(self, meta: TrackMeta, fallback_title: str) -> None:
        self.title.setText(meta.title or fallback_title)
        # 作者信息：没有就留空（不显示“未知”占位，也不显示编码）
        self.artist.setText(meta.artist or "")
        self.album.setText(meta.album or "")

        cover_size = MOBILE_COVER_SIZE if self._compact else COVER_SIZE
        cover = theme.cover_pixmap(meta.cover, cover_size, radius=16)
        if cover is not None:
            self.cover.setPixmap(cover)
        else:
            self.cover.setPixmap(theme.render_pixmap("note", 88, "#C4C9D4"))

        if meta.has_timed_lyrics:
            self._set_lyrics(meta.lyrics, "")
        elif meta.lyrics_plain:
            lines = [(0, ln) for ln in meta.lyrics_plain.splitlines() if ln.strip()]
            self._set_lyrics(lines, "", timed=False)
        else:
            self._set_lyrics([], "暂无歌词")

    # ------------------------------------------------------------------
    def _clear_lines(self) -> None:
        while self.lyric_box.count() > 2:  # 保留首尾 stretch
            item = self.lyric_box.takeAt(1)
            w = item.widget()
            if w is not None:
                w.setParent(None)  # 立即摘除，避免 deleteLater 当帧与新占位同时可见
                w.deleteLater()
        self._placeholder = None
        self._lines.clear()
        self._times.clear()
        self._active_idx = -2

    def _set_lyrics(self, lines: List[Tuple[int, str]], placeholder: str, timed: bool = True) -> None:
        self._clear_lines()
        self._timed = timed and bool(lines)
        if not lines:
            self._placeholder = QLabel(placeholder)
            self._placeholder.setObjectName("LyricPlaceholder")
            self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lyric_box.insertWidget(1, self._placeholder)
            return

        for i, (_t, text) in enumerate(lines):
            label = QLabel(text)
            label.setObjectName("LyricLine")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            label.setProperty("active", False)
            self.lyric_box.insertWidget(i + 1, label)
            self._lines.append(label)
            self._times.append(_t if self._timed else -1)
        self._active_idx = -2
        if self._timed:
            self.set_position(0)

    def set_position(self, ms: int) -> None:
        """根据当前播放毫秒高亮对应歌词行并滚动到视口中部。"""
        if not getattr(self, "_timed", False) or not self._times:
            return
        idx = bisect_right(self._times, ms) - 1
        idx = max(0, idx)
        if idx == self._active_idx:
            return
        self._active_idx = idx
        for i, label in enumerate(self._lines):
            active = i == idx
            label.setProperty("active", active)
            label.style().unpolish(label)
            label.style().polish(label)
        target = self._lines[idx]
        self.scroll.ensureWidgetVisible(target, 50, self.scroll.viewport().height() // 3)
