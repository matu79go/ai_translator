"""雑音を大きくした音声を作る（聞き比べ用）。

VoiceBank-DEMAND の 雑音入り − 元の音声 = 雑音 を取り出し、k 倍して元の音声に足す。
  python make_hard.py vbd_dir out_dir id1 id2 ... [--gains 3 6]
out_dir/noisy/{id}_x{k}.wav と out_dir/clean/{id}_x{k}.wav（元の音声のコピー）を作る。
"""
import argparse
import os

import numpy as np
import soundfile as sf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vbd")
    ap.add_argument("out")
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--gains", type=float, nargs="+", default=[3, 6])
    a = ap.parse_args()
    for sub in ("noisy", "clean"):
        os.makedirs(os.path.join(a.out, sub), exist_ok=True)
    for i in a.ids:
        clean, sr = sf.read(os.path.join(a.vbd, "clean_testset_wav", i + ".wav"), dtype="float64")
        noisy, _ = sf.read(os.path.join(a.vbd, "noisy_testset_wav", i + ".wav"), dtype="float64")
        n = min(len(clean), len(noisy))
        clean, noise = clean[:n], noisy[:n] - clean[:n]
        for g in a.gains:
            mix = clean + g * noise
            peak = np.max(np.abs(mix))
            scale = 0.9 / peak if peak > 0.9 else 1.0  # 音割れを防ぐ（元の音声も同じ倍率）
            snr = 10 * np.log10(np.sum(clean ** 2) / np.sum((g * noise) ** 2))
            name = f"{i}_x{g:g}"
            sf.write(os.path.join(a.out, "noisy", name + ".wav"), mix * scale, sr, subtype="FLOAT")
            sf.write(os.path.join(a.out, "clean", name + ".wav"), clean * scale, sr, subtype="FLOAT")
            print(f"{name}: SNR {snr:.1f} dB")


if __name__ == "__main__":
    main()
