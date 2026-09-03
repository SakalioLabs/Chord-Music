"""本地持久化（当前仅歌单）。

歌单属于用户资产，关闭重开后必须保留，因此写入用户本地数据目录而不是内存：
- Windows: %LOCALAPPDATA%/Chord/playlists.json
- macOS:   ~/Library/Application Support/Chord/playlists.json
- Linux:   $XDG_DATA_HOME/Chord 或 ~/.local/share/Chord/playlists.json

写入采用“临时文件 + replace”原子替换，避免写到一半进程退出导致 JSON 损坏；
读取时做结构校验，任何异常都回退为空歌单，绝不因脏数据让应用起不来。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List

APP_DIR_NAME = "Chord"
PLAYLISTS_FILE = "playlists.json"


def app_data_dir() -> Path:
    """返回（并确保存在）应用本地数据目录。"""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    target = base / APP_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _playlists_path() -> Path:
    return app_data_dir() / PLAYLISTS_FILE


def load_playlists() -> Dict[str, List[str]]:
    """读取歌单 {名称: [曲目绝对路径...]}；文件缺失或损坏时返回空字典。"""
    path = _playlists_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: Dict[str, List[str]] = {}
    if not isinstance(data, dict):
        return {}
    for name, paths in data.items():
        if not isinstance(name, str) or not isinstance(paths, list):
            continue
        # 去重保序，只保留字符串路径
        seen, ordered = set(), []
        for p in paths:
            if isinstance(p, str) and p not in seen:
                seen.add(p)
                ordered.append(p)
        result[name] = ordered
    return result


def save_playlists(playlists: Dict[str, List[str]]) -> bool:
    """原子写入歌单；成功返回 True。"""
    path = _playlists_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(playlists, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


# ======================================================================
# 统一应用设置（窗口几何 / 音量 / 播放模式 / 红心 / 最近 / 曲库 / 上次会话）
# ======================================================================
SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 1


def default_settings() -> dict:
    return {
        "version": SETTINGS_VERSION,
        "window": {"width": 1040, "height": 660, "x": None, "y": None, "maximized": False},
        "volume": 80,
        "muted": False,
        "play_mode": "loop",
        "liked": [],
        "recent": [],
        "library_paths": [],
        "last_session": {"path": None, "position_ms": 0, "playing": False},
    }


def _settings_path() -> Path:
    return app_data_dir() / SETTINGS_FILE


def _as_str_list(value, limit: int = 0) -> List[str]:
    """容错地把任意值清洗为去重保序的字符串列表；limit>0 时截断到最近 N 条。"""
    if not isinstance(value, list):
        return []
    seen, out = set(), []
    for item in value:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            out.append(item)
    if limit > 0:
        out = out[:limit]
    return out


def load_settings() -> dict:
    """读取设置并做字段级校验；文件缺失/损坏时回退默认值，绝不阻断启动。"""
    base = default_settings()
    path = _settings_path()
    if not path.exists():
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(data, dict):
        return base

    win = data.get("window")
    if isinstance(win, dict):
        for key in ("width", "height", "x", "y"):
            v = win.get(key)
            if isinstance(v, (int, float)) or v is None:
                base["window"][key] = int(v) if v is not None else None
        base["window"]["maximized"] = bool(win.get("maximized", False))

    vol = data.get("volume", base["volume"])
    if isinstance(vol, (int, float)):
        base["volume"] = max(0, min(100, int(vol)))
    base["muted"] = bool(data.get("muted", False))
    if data.get("play_mode") in ("loop", "single", "shuffle"):
        base["play_mode"] = data["play_mode"]

    base["liked"] = _as_str_list(data.get("liked"))
    base["recent"] = _as_str_list(data.get("recent"), limit=50)
    base["library_paths"] = _as_str_list(data.get("library_paths"))

    sess = data.get("last_session")
    if isinstance(sess, dict):
        p = sess.get("path")
        if isinstance(p, str):
            base["last_session"]["path"] = p
        pos = sess.get("position_ms", 0)
        if isinstance(pos, (int, float)):
            base["last_session"]["position_ms"] = max(0, int(pos))
        base["last_session"]["playing"] = bool(sess.get("playing", False))
    return base


def save_settings(settings: dict) -> bool:
    """原子写入设置；成功返回 True。"""
    path = _settings_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        payload = dict(settings)
        payload["version"] = SETTINGS_VERSION
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False
