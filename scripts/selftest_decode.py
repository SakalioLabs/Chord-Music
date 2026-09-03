"""解码层自测：生成多种位深 WAV 与 FLAC，验证统一解码为 16-bit PCM。

运行：python scripts/selftest_decode.py
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.decoder import decode  # noqa: E402

SAMPLE_RATE = 44100
DURATION = 2.0
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)


def make_signal():
    t = np.arange(int(SAMPLE_RATE * DURATION)) / SAMPLE_RATE
    left = 0.5 * np.sin(2 * np.pi * 440 * t)
    right = 0.4 * np.sin(2 * np.pi * 660 * t)
    stereo = np.stack([left, right], axis=1).astype(np.float32)
    return stereo


def main():
    sig = make_signal()

    cases = [
        ("tone_16.wav", "PCM_16"),
        ("tone_24.wav", "PCM_24"),
        ("tone_u8.wav", "PCM_U8"),
        ("tone_32.wav", "PCM_32"),
        ("tone.flac", "PCM_16"),
    ]
    for name, subtype in cases:
        sf.write(OUT / name, sig, SAMPLE_RATE, subtype=subtype)

    # 黄金基准：对每个文件用 libsndfile 以 int16 读回，检验本项目解码器是否一致
    ok = True
    for name, _ in cases:
        a = decode(str(OUT / name))
        pcm = np.frombuffer(a.pcm, dtype="<i2").reshape(-1, a.channels)
        gold, _ = sf.read(OUT / name, dtype="int16", always_2d=True)
        expect_bytes = a.n_frames * a.channels * 2
        checks = {
            "采样率=44100": a.sample_rate == SAMPLE_RATE,
            "立体声": a.channels == 2,
            "帧数=2s": a.n_frames == int(SAMPLE_RATE * DURATION),
            "PCM字节数匹配": len(a.pcm) == expect_bytes,
            "时长≈2000ms": abs(a.duration_ms - 2000) <= 20,
            "非静音": np.abs(pcm).max() > 1000,
            "无削顶": np.abs(pcm).max() <= 32767,
        }
        diff = int(np.max(np.abs(pcm.astype(int) - gold.astype(int))))
        # 16-bit 无损路径必须位级一致；位深下变换允许 1 个量化级舍入差
        if name in ("tone_16.wav", "tone.flac"):
            checks["与libsndfile位级一致"] = diff == 0
        else:
            checks[f"下变换误差<=1(实测{diff})"] = diff <= 1

        print(f"\n=== {name}  ({a.source_format}) ===")
        for k, v in checks.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")
            ok = ok and v

    print("\n总体结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
