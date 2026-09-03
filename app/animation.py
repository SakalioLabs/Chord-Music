"""HarmonyOS 风格轻动效。

遵循 HarmonyOS 动效节奏（参考其设计指南）：
* 入场/转场 200~260ms，使用减速曲线（OutCubic，近似 cubic-bezier(0.2,0,0.1,1)）；
* 组件状态切换 ≤200ms；
* 一次动效只改变 1~2 个维度，位移克制；
* 按压有轻微收缩、释放带一点回弹（OutBack），反馈干脆不浮夸。
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QSize,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

# 统一节奏（毫秒）
DURATION_ENTER = 240       # 页面/元素入场
DURATION_STATE = 180       # 组件状态切换
DURATION_PRESS = 110       # 按下
DURATION_RELEASE = 240     # 释放回弹

EASE_DECELERATE = QEasingCurve.Type.OutCubic     # 减速曲线：起步快、收尾稳
EASE_SPRING = QEasingCurve.Type.OutBack          # 轻微回弹


def fade_in(widget: QWidget, duration: int = DURATION_ENTER, start: float = 0.0) -> None:
    """透明度淡入，用于页面切换。

    动画结束后主动移除 QGraphicsOpacityEffect：效果停留在 opacity=1 时仍会让整棵
    子树走离屏光栅化，使文字失去原生 ClearType/DirectWrite 渲染而发虚，移除后即恢复。
    """
    old = getattr(widget, "_fade_anim", None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:  # DeleteWhenStopped 已回收 C++ 对象
            pass
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(start)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setStartValue(start)
    anim.setEndValue(1.0)
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve(EASE_DECELERATE))

    def _cleanup() -> None:
        # 仅当挂着的仍是本次效果时才移除，避免误删后续新效果。
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    anim.finished.connect(_cleanup)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_effect = effect  # 保活引用
    widget._fade_anim = anim


class PressScaleFilter(QObject):
    """给图标按钮安装后：按下轻微缩小、释放回弹（HarmonyOS 按压反馈）。

    每个按钮以自身首次记录到的 iconSize 作为回弹基准，避免不同尺寸的按钮
    （18/19/22）被统一回弹到错误尺寸而“乱跳”。
    """

    def __init__(self, scale: float = 0.84, parent: QObject | None = None):
        super().__init__(parent)
        self._scale = scale
        self._bases: Dict[QPushButton, int] = {}
        self._anims: Dict[QPushButton, QPropertyAnimation] = {}

    def _base_of(self, btn: QPushButton) -> int:
        base = self._bases.get(btn)
        if base is None:
            base = max(10, btn.iconSize().width())
            self._bases[btn] = base
        return base

    def eventFilter(self, obj, event):  # noqa: N802
        if isinstance(obj, QPushButton):
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                target = max(8, int(self._base_of(obj) * self._scale))
                self._animate(obj, target, DURATION_PRESS, EASE_DECELERATE)
            elif t in (QEvent.Type.MouseButtonRelease, QEvent.Type.Leave):
                self._animate(obj, self._base_of(obj), DURATION_RELEASE, EASE_SPRING)
        return False  # 不吞事件

    def _drop_old(self, btn: QPushButton) -> None:
        old = self._anims.pop(btn, None)
        if old is None:
            return
        # DeleteWhenStopped 会在结束后回收 C++ 对象，再次访问会抛 RuntimeError，需兜底。
        try:
            old.stop()
        except RuntimeError:
            pass
        try:
            old.deleteLater()
        except RuntimeError:
            pass

    def _animate(self, btn: QPushButton, target: int, duration: int, curve) -> None:
        self._drop_old(btn)
        anim = QPropertyAnimation(btn, b"iconSize", btn)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve(curve))
        anim.setStartValue(btn.iconSize())
        anim.setEndValue(QSize(target, target))
        anim.finished.connect(lambda: self._anims.pop(btn, None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anims[btn] = anim


def pop_icon(btn: QPushButton, base_size: int = 22) -> None:
    """切换图标后来一次轻微回弹（如播放/暂停切换）。"""
    old = getattr(btn, "_pop_anim", None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    anim = QPropertyAnimation(btn, b"iconSize", btn)
    anim.setDuration(DURATION_RELEASE)
    anim.setEasingCurve(QEasingCurve(EASE_SPRING))
    anim.setStartValue(QSize(int(base_size * 0.7), int(base_size * 0.7)))
    anim.setEndValue(QSize(base_size, base_size))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    btn._pop_anim = anim
