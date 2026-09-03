"""验证内嵌元数据（标签/时间轴歌词/封面）解析与封面渲染。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from app import theme  # noqa: E402
from app.metadata import parse_lrc, read_metadata  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


tagged = ROOT / "samples" / "tone_tagged.flac"
if not tagged.is_file():
    print("缺少 samples/tone_tagged.flac，请先运行 python scripts/make_tagged_sample.py")
    sys.exit(1)

# ---- 带元数据 FLAC ----
m = read_metadata(str(tagged))
check("读取标题", m.title == "弦乐测试曲")
check("读取艺术家", m.artist == "测试艺术家")
check("读取专辑", m.album == "元数据自测专辑")
check("时间轴歌词 4 行", len(m.lyrics) == 4)
check("首行时间≈0ms", m.lyrics[0][0] == 0)
check("0.5s 行≈500ms", abs(m.lyrics[1][0] - 500) <= 1)
check("歌词文本正确", m.lyrics[2][1].startswith("第三行"))
check("纯文本歌词非空", bool(m.lyrics_plain))
check("含专辑封面", m.has_cover and len(m.cover) > 100)
check("封面为 PNG", m.cover_mime == "image/png")

# ---- 封面渲染 ----
pm = theme.cover_pixmap(m.cover, 46, radius=10)
check("封面渲染为非空 QPixmap", pm is not None and not pm.isNull())

# ---- 普通文件安全回退 ----
m2 = read_metadata(str(ROOT / "samples" / "tone.flac"))
check("无标签 FLAC 安全返回空", not m2.has_cover and not m2.has_any_lyrics)
m3 = read_metadata(str(ROOT / "samples" / "tone_16.wav"))
check("无标签 WAV 安全返回空", m3.title == "" and not m3.has_cover)

# ---- parse_lrc 单元 ----
timed, plain = parse_lrc("[01:02.30]行A\n纯文本行")
check("百分秒换算=62300ms", timed and timed[0][0] == 62300 and timed[0][1] == "行A")
multi, _ = parse_lrc("[00:01.00][00:05.00]重复行")
check("一行多时间标签展开为 2 条", len(multi) == 2)
t0, p0 = parse_lrc("没有时间轴的\n两行纯文本")
check("无时间轴回退纯文本", t0 == [] and "两行纯文本" in p0)
t1, _ = parse_lrc("")
check("空歌词安全", t1 == [])

# ---- 外部同名 .lrc 退化（内嵌无歌词时读取同目录 LRC）----
import shutil  # noqa: E402
import tempfile  # noqa: E402

src_wav = ROOT / "samples" / "tone_16.wav"
tmp = tempfile.mkdtemp(prefix="xianyue_lrc_")
try:
    # UTF-8 LRC，含 ti/ar/al 头部与时间轴
    w1 = os.path.join(tmp, "song.wav")
    shutil.copy(src_wav, w1)
    lrc1 = "[ti:标题][ar:歌手][al:专辑]\n[00:01.00]第一行\n[00:02.50]第二行"
    open(os.path.join(tmp, "song.lrc"), "wb").write(lrc1.encode("utf-8"))
    e1 = read_metadata(w1)
    check("外部LRC补全标题/艺术家/专辑",
          e1.title == "标题" and e1.artist == "歌手" and e1.album == "专辑")
    check("外部LRC解析为2行同步歌词", len(e1.lyrics) == 2 and e1.lyrics[1][0] == 2500)

    # GBK 编码 LRC（老歌词文件常见）
    w2 = os.path.join(tmp, "gbk.wav")
    shutil.copy(src_wav, w2)
    open(os.path.join(tmp, "gbk.lrc"), "wb").write(
        "[ar:毛阿敏]\n[00:01.00]幸福在哪里".encode("gbk"))
    e2 = read_metadata(w2)
    check("GBK 外部LRC解码无乱码", e2.artist == "毛阿敏"
          and e2.lyrics and e2.lyrics[0][1] == "幸福在哪里")

    # 大写 .LRC 扩展名也能匹配
    w3 = os.path.join(tmp, "upper.wav")
    shutil.copy(src_wav, w3)
    open(os.path.join(tmp, "upper.LRC"), "wb").write(
        "[00:03.00]大写扩展名".encode("utf-8"))
    e3 = read_metadata(w3)
    check("大写 .LRC 同样命中", e3.has_timed_lyrics and e3.lyrics[0][1] == "大写扩展名")

    # 没有任何 LRC -> 无歌词
    w4 = os.path.join(tmp, "none.wav")
    shutil.copy(src_wav, w4)
    e4 = read_metadata(w4)
    check("无内嵌且无LRC时无歌词", not e4.has_any_lyrics and e4.artist == "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
