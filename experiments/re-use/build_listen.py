"""聞き比べページ用の素材を作る。

各組について、同じ区間を切り出し、
  - 前・後・（あれば）正解：音量（RMS）をそろえた 16bit wav
  - 消された音（前 − 後）：前と同じ倍率で書き出す（大きさの比較ができるように）
  - スペクトログラム PNG（前・後・正解）。numpy だけで描く
  python build_listen.py pairs.json out_dir/
pairs.json: [{"id","label","note"?,"noisy","enhanced","clean"?,"start"?,"dur"?}]
"""
import json
import os
import struct
import sys
import zlib

import numpy as np
import soundfile as sf

TARGET_RMS = 0.05


def load(path, start, dur):
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    s = int(start * sr)
    e = s + int(dur * sr) if dur else len(x)
    return x[s:e], sr


def rms(x):
    return np.sqrt(np.mean(x ** 2) + 1e-12)


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


# 黒 → 紺 → 赤紫 → 橙 → 薄黄（magma 風）
STOPS = np.array([[0, 0, 4], [40, 11, 84], [140, 41, 129], [229, 80, 100], [253, 159, 108], [252, 253, 191]], float)


def colormap(v):
    v = np.clip(v, 0, 1) * (len(STOPS) - 1)
    i = np.minimum(v.astype(int), len(STOPS) - 2)
    f = (v - i)[..., None]
    return (STOPS[i] * (1 - f) + STOPS[i + 1] * f).astype(np.uint8)


def spectrogram_png(x, sr, path, fmax=8000, width=900, height=220, vmin=-80.0):
    n_fft = int(sr * 0.032)
    hop = max(1, len(x) // width)
    win = np.hanning(n_fft)
    pad = np.pad(x, (n_fft // 2, n_fft // 2))
    frames = np.stack([pad[i:i + n_fft] * win for i in range(0, len(pad) - n_fft, hop)][:width])
    spec = np.abs(np.fft.rfft(frames, axis=1)) + 1e-10
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    spec = spec[:, freqs <= fmax]
    db = 20 * np.log10(spec / (np.max(spec) + 1e-12))
    img = (db - vmin) / -vmin  # vmin..0 dB → 0..1
    img = img.T[::-1]  # 縦軸：上が高い周波数
    ys = np.linspace(0, img.shape[0] - 1, height).astype(int)
    write_png(path, colormap(img[ys]))


def main():
    pairs = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    meta = []
    for p in pairs:
        st, du = p.get("start", 0), p.get("dur")
        noisy, sr = load(p["noisy"], st, du)
        enh, sr2 = load(p["enhanced"], st, du)
        assert sr == sr2
        n = min(len(noisy), len(enh))
        noisy, enh = noisy[:n], enh[:n]
        g_noisy = TARGET_RMS / rms(noisy)
        removed = (noisy - enh) * g_noisy  # 前と同じ倍率
        tracks = {"noisy": noisy * g_noisy, "enhanced": enh * (TARGET_RMS / rms(enh)), "removed": removed}
        if "clean" in p:
            clean, _ = load(p["clean"], st, du)
            clean = clean[:n]
            tracks["clean"] = clean * (TARGET_RMS / rms(clean))
        for k, v in tracks.items():
            sf.write(os.path.join(out, f"{p['id']}-{k}.wav"), np.clip(v, -1, 1), sr, subtype="PCM_16")
            if k != "removed":
                spectrogram_png(v, sr, os.path.join(out, f"{p['id']}-{k}.png"))
        meta.append({
            "id": p["id"], "label": p["label"], "note": p.get("note", ""), "has_clean": "clean" in p,
            "removed_db": round(20 * np.log10(rms(noisy - enh) / rms(noisy)), 1),
        })
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
