"""回归：DTS-WAV（伪装成 PCM 的 DTS 6.1 压缩码流）能被正确解码为真立体声音乐；
普通 PCM WAV / FLAC 仍走原路径，不被误伤。"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ffmpeg_decoder  # noqa: E402
from app.decoder import decode  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def autocorr(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if x.size < 4:
        return 0.0
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


DTS_DIR = Path(r"E:\SD卡备份\Music\毛阿敏")
dts_files = ["呼唤.wav", "天之大.wav", "幸福.wav", "相思.wav"]

check("PyAV 可用", ffmpeg_decoder.available())

for name in dts_files:
    fp = DTS_DIR / name
    if not fp.exists():
        print(f"（跳过，文件不存在: {fp}）")
        continue
    info = ffmpeg_decoder.probe_codec(str(fp))
    audio = decode(str(fp))
    x = np.frombuffer(audio.pcm, dtype="<i2")
    corr = autocorr(audio.pcm)
    peak = int(max(abs(x.min()), abs(x.max())))
    print(f"\n{name}: 真实编码={info.codec} {info.channels}ch({info.layout}) -> "
          f"{audio.source_format} | {audio.sample_rate}Hz frames={audio.n_frames} "
          f"时长={audio.duration_ms/1000:.1f}s 自相关={corr:.3f} 峰值={peak}")
    check(f"{name} 识别为 DTS 压缩码流", info.codec in ("dca", "dts"))
    check(f"{name} 输出立体声", audio.channels == 2 and x.size == audio.n_frames * 2)
    check(f"{name} 解码为真音乐(自相关>0.4)", corr > 0.4)
    check(f"{name} 突破14bit上限(不再卡±8192)", peak > 8192)
    check(f"{name} 标注含下混信息", "→立体声" in audio.source_format)

# 对照：自制普通 PCM/FLAC 不应被当成压缩码流，且仍走原解码路径（非空、立体声）
print()
for rel, tag, prefix in [
    ("samples/tone_16.wav", "PCM WAV", "WAV"),
    ("samples/tone_tagged.flac", "FLAC", "FLAC"),
]:
    fp = ROOT / rel
    if fp.exists():
        a = decode(str(fp))
        n = len(a.pcm)
        print(f"{tag}: {a.source_format} 立体声={a.channels == 2} PCM字节={n} 时长={a.duration_ms}ms")
        check(f"{tag} 解码非空且为立体声", n > 0 and a.channels == 2 and a.n_frames > 0)
        check(f"{tag} 走原路径(标注以{prefix}开头)", a.source_format.startswith(prefix))
        check(f"{tag} 未被误标为 DTS", "DTS" not in a.source_format)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
