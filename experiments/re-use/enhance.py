"""RE-USE で wav のフォルダを一括処理する（GX10 用）。

NVIDIA の inference.py / inference_chunk.py と同じ処理を、torchaudio の代わりに
soundfile で読み書きし、1本ごとの処理時間を記録する。

  ~/venvs/re-use/bin/python enhance.py --model_dir ~/work/re-use/model \
      --in_dir noisy/ --out_dir enhanced/ [--chunk_sec 5 --hop 0.5] [--sr 16000]

--chunk_sec 0（既定）は1本をまるごと処理する（30秒以下の音声向け）。
"""
import argparse
import json
import math
import os
import sys
import time

import librosa
import numpy as np
import soundfile as sf
import torch
import torch.nn as nn


def make_even(v):
    v = int(round(v))
    return v if v % 2 == 0 else v + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model_dir", required=True)
    ap.add_argument("--in_dir", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--chunk_sec", type=float, default=0.0)
    ap.add_argument("--hop", type=float, default=0.5)
    ap.add_argument("--sr", type=int, default=0, help="入力をこの周波数に変えてから処理する（0 = そのまま）")
    args = ap.parse_args()

    sys.path.insert(0, args.model_dir)
    from models.generator_SEMamba_time_d4 import SEMamba
    from models.stfts import mag_phase_stft, mag_phase_istft
    from utils.util import load_config, pad_or_trim_to_match

    relu = nn.ReLU()
    cfg = load_config(os.path.join(args.model_dir, "recipes",
        "USEMamba_30x1_lr_00002_norm_05_vq_065_nfft_320_hop_40_NRIR_012_pha_0005_com_04_early_001.yaml"))
    n_fft, hop_size, win_size = (cfg["stft_cfg"][k] for k in ("n_fft", "hop_size", "win_size"))
    compress = cfg["model_cfg"]["compress_factor"]
    base_sr = cfg["stft_cfg"]["sampling_rate"]

    dev = torch.device("cuda")
    t0 = time.perf_counter()
    model = SEMamba.from_pretrained(args.model_dir, cfg=cfg).to(dev).eval()
    load_sec = time.perf_counter() - t0

    def enhance_segment(x, sr):
        nf, hs, ws = (make_even(v * sr // base_sr) for v in (n_fft, hop_size, win_size))
        mag, pha, _ = mag_phase_stft(x, n_fft=nf, hop_size=hs, win_size=ws,
                                     compress_factor=compress, center=True, addeps=False)
        amp_g, pha_g, _ = model(mag, pha)
        m = torch.expm1(relu(amp_g))
        zero = torch.sum(m == 0, 1) / m.shape[1]
        amp_g[:, :, (zero > 0.5)[0]] = 0  # NVIDIA のコードと同じ（掃引音の除去）
        y = mag_phase_istft(amp_g, pha_g, nf, hs, ws, compress)
        return pad_or_trim_to_match(x, y, pad_value=1e-8)

    os.makedirs(args.out_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(args.in_dir) if f.lower().endswith((".wav", ".flac")))
    log = []
    with torch.no_grad():
        for i, name in enumerate(files):
            wav, sr = sf.read(os.path.join(args.in_dir, name), dtype="float32", always_2d=True)
            wav = wav.mean(axis=1)  # モノラル
            if args.sr and sr != args.sr:
                wav = librosa.resample(wav, orig_sr=sr, target_sr=args.sr, res_type="soxr_hq")
                sr = args.sr
            x = torch.from_numpy(np.ascontiguousarray(wav))[None].to(dev)
            torch.cuda.synchronize()
            t = time.perf_counter()
            if args.chunk_sec <= 0:
                y = enhance_segment(x, sr)
            else:
                cs = int(args.chunk_sec * sr)
                hl = int(args.hop * cs)
                win = torch.clamp(torch.hann_window(cs, device=dev), min=1e-7)
                y = torch.zeros_like(x)
                wsum = torch.zeros_like(x)
                n = max(1, math.ceil((x.shape[1] - cs) / hl) + 1)
                for k in range(n):
                    sl = slice(k * hl, k * hl + cs) if k < n - 1 else slice(max(0, x.shape[1] - cs), x.shape[1])
                    seg = enhance_segment(x[:, sl], sr)
                    y[:, sl] += seg * win[: seg.shape[1]]
                    wsum[:, sl] += win[: seg.shape[1]]
                y = y / wsum
            torch.cuda.synchronize()
            proc = time.perf_counter() - t
            out = y[0].cpu().numpy()
            sf.write(os.path.join(args.out_dir, os.path.splitext(name)[0] + ".wav"), out, sr, subtype="FLOAT")
            dur = len(wav) / sr
            log.append({"file": name, "sr": sr, "dur_sec": round(dur, 3), "proc_sec": round(proc, 4)})
            if i % 50 == 0:
                print(f"[{i + 1}/{len(files)}] {name} {dur:.2f}s -> {proc * 1000:.0f}ms", flush=True)

    total_dur = sum(r["dur_sec"] for r in log)
    total_proc = sum(r["proc_sec"] for r in log[1:]) if len(log) > 1 else sum(r["proc_sec"] for r in log)
    summary = {
        "n_files": len(log), "load_sec": round(load_sec, 2),
        "audio_sec": round(total_dur, 1),
        "proc_sec_excl_first": round(total_proc, 2),
        "rtf_excl_first": round(total_proc / max(1e-9, total_dur - (log[0]["dur_sec"] if len(log) > 1 else 0)), 4),
        "max_mem_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "chunk_sec": args.chunk_sec, "hop": args.hop, "sr_override": args.sr,
        "files": log,
    }
    with open(os.path.join(args.out_dir, "_timing.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "files"}))


if __name__ == "__main__":
    main()
