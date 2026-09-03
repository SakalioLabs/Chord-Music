"""音频内嵌元数据解析：标签、歌词（LRC 同步 / 纯文本）、专辑封面。

基于 ``mutagen``：
* FLAC 读取 Vorbis Comment（TITLE/ARTIST/ALBUM、LYRICS 等）与 PICTURE 块；
* WAV 读取其内嵌 ID3v2（TIT2/TPE1/TALB、USLT 歌词、APIC 封面）。

整个模块对损坏/无标签文件保持“静默回退为空元数据”，绝不影响解码与播放主链路。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# LRC 头部元信息标签，如 [ti:标题] [ar:艺术家] [al:专辑]
_LRC_HEADER = re.compile(r"\[\s*(ti|ar|al|by|offset)\s*:(.*?)\]", re.IGNORECASE)
LYRIC_KEYS = ("lyrics", "unsyncedlyrics", "unsync lyrics", "lyric", "歌词")


@dataclass
class TrackMeta:
    """一首曲目的内嵌元数据。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    # 同步歌词：[(时间毫秒, 文本行)]，按时间升序；为空表示没有时间轴歌词。
    lyrics: List[Tuple[int, str]] = field(default_factory=list)
    lyrics_plain: str = ""          # 纯文本歌词（同步歌词时为按序拼接）
    cover: Optional[bytes] = None   # 专辑封面原始字节（JPEG/PNG）
    cover_mime: str = ""

    @property
    def has_timed_lyrics(self) -> bool:
        return bool(self.lyrics)

    @property
    def has_any_lyrics(self) -> bool:
        return bool(self.lyrics_plain)

    @property
    def has_cover(self) -> bool:
        return bool(self.cover)


def parse_lrc(text: str) -> Tuple[List[Tuple[int, str]], str]:
    """解析 LRC 文本为 ``[(ms, 行文本)]``；无时间标签则返回空时间轴 + 原文。"""
    if not text:
        return [], ""
    timed: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        marks = list(_LRC_TIME.finditer(raw))
        if not marks:
            continue
        line = _LRC_TIME.sub("", raw).strip()
        for m in marks:
            mm, ss = int(m.group(1)), int(m.group(2))
            frac = m.group(3) or "0"
            if len(frac) <= 2:  # 百分秒，如 .50
                frac_ms = int(frac.ljust(2, "0") or "0") * 10
            else:              # 毫秒，如 .500
                frac_ms = int(frac[:3])
            timed.append(((mm * 60 + ss) * 1000 + frac_ms, line))
    timed.sort(key=lambda x: x[0])
    if timed:
        return timed, "\n".join(line for _, line in timed)
    return [], text.strip()


def _decode_text_bytes(raw: bytes) -> str:
    """按常见中文编码顺序解码 LRC/文本：UTF-8（含 BOM）→ GB18030 → Big5 → 忽略错误。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def parse_lrc_header(text: str) -> dict:
    """提取 LRC 头部的 ti/ar/al（标题/艺术家/专辑），各取首个非空值。"""
    head = {}
    keymap = {"ti": "title", "ar": "artist", "al": "album"}
    for m in _LRC_HEADER.finditer(text):
        key = keymap.get(m.group(1).lower())
        value = m.group(2).strip()
        if key and value and key not in head:
            head[key] = value
    return head


def find_external_lrc(audio_path: str) -> str:
    """查找与音频同目录、同名的 .lrc 文件（扩展名大小写不敏感），返回路径或空串。"""
    stem = os.path.splitext(audio_path)[0]
    for cand in (stem + ".lrc", stem + ".LRC"):
        if os.path.isfile(cand):
            return cand
    folder = os.path.dirname(audio_path)
    try:
        target = os.path.basename(stem).lower()
        for name in os.listdir(folder or "."):
            noext, ext = os.path.splitext(name)
            if ext.lower() == ".lrc" and noext.lower() == target:
                return os.path.join(folder, name)
    except OSError:
        pass
    return ""


def merge_external_lrc(audio_path: str, meta: "TrackMeta") -> None:
    """内嵌歌词缺失时，退化读取同目录同名 .lrc；其头部 ti/ar/al 补全空缺元信息。"""
    lrc_path = find_external_lrc(audio_path)
    if not lrc_path:
        return
    try:
        with open(lrc_path, "rb") as fh:
            text = _decode_text_bytes(fh.read())
    except OSError:
        return
    # 头部标签仅在内嵌没有对应信息时补全（内嵌优先）。
    head = parse_lrc_header(text)
    if not meta.title and head.get("title"):
        meta.title = head["title"]
    if not meta.artist and head.get("artist"):
        meta.artist = head["artist"]
    if not meta.album and head.get("album"):
        meta.album = head["album"]
    # 歌词退化策略：
    # 1) 外部 LRC 带同步时间轴、而内嵌没有同步歌词时，用外部同步歌词（体验更好）；
    # 2) 内嵌完全没有任何歌词时，用外部 LRC（哪怕只是纯文本）兜底；
    # 3) 内嵌已有同步歌词则保持内嵌优先。
    ext_lyrics, ext_plain = parse_lrc(text)
    if ext_lyrics and not meta.has_timed_lyrics:
        meta.lyrics, meta.lyrics_plain = ext_lyrics, ext_plain
    elif not meta.has_any_lyrics and ext_plain:
        meta.lyrics, meta.lyrics_plain = ext_lyrics, ext_plain


def _tag_value(tags, key: str, default: str = "") -> str:
    """大小写不敏感地取一个标签值，兼容 Vorbis comment 与 ID3 TextFrame。"""
    if tags is None:
        return default
    real_key = next((k for k in tags.keys() if k.lower() == key.lower()), None)
    if real_key is None:
        return default
    value = tags[real_key]
    if hasattr(value, "text"):  # ID3 TextFrame
        value = list(value.text)
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else default
    return str(value)


def _join_id3_text(parts) -> str:
    """拼接 ID3 文本帧。

    部分 WAV 内嵌的 UTF-16 USLT 会被底层拆成“每个字符一个元素”，此时需用空串
    拼接还原；正常的多段文本则按换行连接。同时清除可能残留的空字节。
    """
    items = [str(x).replace("\x00", "") for x in (parts or [])]
    if not items:
        return ""
    if len(items) > 1 and all(len(x) <= 1 for x in items):
        return "".join(items)
    return "\n".join(items)


def _tag_lyrics(tags) -> str:
    if tags is None:
        return ""
    for k in tags.keys():
        if k.lower() in LYRIC_KEYS:
            value = tags[k]
            if hasattr(value, "text"):
                value = list(value.text)
            if isinstance(value, (list, tuple)):
                return "\n".join(str(x) for x in value)
            return str(value)
    return ""


def _from_flac(path: str) -> TrackMeta:
    from mutagen.flac import FLAC

    audio = FLAC(path)
    meta = TrackMeta(
        title=_tag_value(audio.tags, "title"),
        artist=_tag_value(audio.tags, "artist") or _tag_value(audio.tags, "albumartist"),
        album=_tag_value(audio.tags, "album"),
    )
    meta.lyrics, meta.lyrics_plain = parse_lrc(_tag_lyrics(audio.tags))
    pictures = list(getattr(audio, "pictures", []) or [])
    # 优先 FrontCover(type=3)，否则取第一张图。
    pictures.sort(key=lambda p: 0 if getattr(p, "type", 3) == 3 else 1)
    if pictures:
        meta.cover = pictures[0].data
        meta.cover_mime = pictures[0].mime
    return meta


def _from_wav(path: str) -> TrackMeta:
    from mutagen.wave import WAVE

    audio = WAVE(path)
    meta = TrackMeta()
    tags = getattr(audio, "tags", None)
    if tags is None:
        return meta
    meta.title = _tag_value(tags, "tit2")
    meta.artist = _tag_value(tags, "tpe1")
    meta.album = _tag_value(tags, "talb")
    for key in tags.keys():
        if key.startswith("USLT"):  # 非同步/内嵌 LRC 歌词
            frame = tags[key]
            meta.lyrics, meta.lyrics_plain = parse_lrc(_join_id3_text(frame.text))
            break
    for key in tags.keys():
        if key.startswith("APIC"):  # 附加图片
            frame = tags[key]
            meta.cover = bytes(frame.data)
            meta.cover_mime = frame.mime
            break
    return meta


def read_metadata(path: str) -> TrackMeta:
    """读取元数据；任何异常都回退为空 :class:`TrackMeta`，不抛给上层。

    顺序：先读音频内嵌标签/歌词/封面；内嵌没有歌词时，退化读取同目录同名 .lrc
    （并可用其 [ti]/[ar]/[al] 补全标题/艺术家/专辑）；都没有则保持空歌词。
    """
    meta = TrackMeta()
    try:
        low = path.lower()
        if low.endswith(".flac"):
            meta = _from_flac(path)
        elif low.endswith(".wav"):
            meta = _from_wav(path)
    except Exception:  # noqa: BLE001  元数据是增强信息，失败不影响播放
        meta = TrackMeta()
    try:
        merge_external_lrc(path, meta)
    except Exception:  # noqa: BLE001  外部 LRC 失败同样不影响主链路
        pass
    return meta
