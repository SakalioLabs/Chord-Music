"""回归：解码层把 1/2/5.1/7.1 等任意声道统一为立体声 16-bit PCM 输出。"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import decoder  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


# ---- 1. 直接下混：形状 / 单声道复制 / 限幅 ----
for n in (1, 2, 3, 6, 8):
    x = (np.arange(4 * n, dtype=np.int32) % 7000 - 3500).astype(np.int16).reshape(4, n)
    out = decoder._downmix_to_stereo(x)
    check(f"{n}声道下混输出固定立体声(4帧->8样本)", out.shape == (8,) and out.dtype == np.int16)

mono = np.full((4, 1), 1234, dtype=np.int16)
mo = decoder._downmix_to_stereo(mono)
check("单声道左右声道相同", mo[0] == mo[1] == 1234)

loud = np.full((8, 6), 30000, dtype=np.int16)
lo = decoder._downmix_to_stereo(loud)
check("多声道叠加限幅不溢出", int(lo.max()) <= 32767 and int(lo.min()) >= -32768)

# ---- 2. 落盘多声道 WAV，走完整 decode（标准库 wave 路径）----
tmp = tempfile.mkdtemp(prefix="xianyue_ch_")
try:
    sr = 8000
    frames = 1000
    data6 = (np.random.RandomState(0).rand(frames, 6) * 2000 - 1000).astype(np.int16)
    p6 = os.path.join(tmp, "surround51.wav")
    sf.write(p6, data6, sr, subtype="PCM_16")
    a6 = decoder.decode(p6)
    check("5.1 WAV 输出为 2 声道", a6.channels == 2)
    check("5.1 WAV PCM 字节=帧×2声道×2字节", len(a6.pcm) == frames * 2 * 2
          and isinstance(a6.pcm, bytes))
    check("source_format 标注下混", "5.1" in a6.source_format and "立体声" in a6.source_format)
    check("5.1 时长正确", abs(a6.duration_ms - round(frames * 1000 / sr)) <= 1)

    # 单声道 WAV -> 立体声，左右相等
    data1 = np.full(frames, 2000, dtype=np.int16)
    p1 = os.path.join(tmp, "mono.wav")
    sf.write(p1, data1, sr, subtype="PCM_16")
    a1 = decoder.decode(p1)
    raw = np.frombuffer(a1.pcm, dtype="<i2").reshape(-1, 2)
    check("单声道 WAV 展开为立体声且 L=R", a1.channels == 2
          and np.all(raw[:, 0] == raw[:, 1]))

    # 立体声样本保持不变
    stereo_sample = ROOT / "samples" / "tone_16.wav"
    if stereo_sample.is_file():
        a2 = decoder.decode(str(stereo_sample))
        check("立体声样本仍是 2 声道且 PCM 完整",
              a2.channels == 2 and len(a2.pcm) == a2.n_frames * 2 * 2)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
