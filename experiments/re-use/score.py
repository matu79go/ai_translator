"""正解（雑音のない音声）と比べて PESQ・STOI・SI-SDR を出す。

  python score.py --clean clean/ --test noisy/ --test enhanced/ --out scores.json [--jobs 8]

すべて 16kHz に落としてから採点する（PESQ は広帯域モード）。長さは短い方に合わせる。
"""
import argparse
import json
import os
from multiprocessing import Pool

import librosa
import numpy as np
import soundfile as sf
from pesq import pesq
from pystoi import stoi

SR = 16000


def load16(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        x = librosa.resample(x, orig_sr=sr, target_sr=SR, res_type="soxr_hq")
    return x.astype(np.float64)


def si_sdr(ref, est):
    ref = ref - ref.mean()
    est = est - est.mean()
    a = np.dot(est, ref) / (np.dot(ref, ref) + 1e-12)
    t = a * ref
    return 10 * np.log10((np.dot(t, t) + 1e-12) / (np.dot(est - t, est - t) + 1e-12))


def find(d, stem):
    for ext in (".wav", ".flac"):
        p = os.path.join(d, stem + ext)
        if os.path.exists(p):
            return p
    return None


def score_one(job):
    stem, clean_path, tests = job
    ref = load16(clean_path)
    row = {"file": stem}
    for name, path in tests:
        est = load16(path)
        n = min(len(ref), len(est))
        r, e = ref[:n], est[:n]
        try:
            p = pesq(SR, r, e, "wb")
        except Exception:
            p = float("nan")
        row[name] = {"pesq": p, "stoi": stoi(r, e, SR, extended=False), "si_sdr": si_sdr(r, e)}
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", required=True)
    ap.add_argument("--test", action="append", required=True, help="名前=フォルダ（名前を省くとフォルダ名）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    tests = []
    for t in args.test:
        name, _, d = t.rpartition("=")
        tests.append((name or os.path.basename(os.path.normpath(d)), d))
    stems = sorted(os.path.splitext(f)[0] for f in os.listdir(args.clean) if f.endswith((".wav", ".flac")))
    if args.limit:
        stems = stems[: args.limit]
    jobs = []
    for s in stems:
        paths = [(n, find(d, s)) for n, d in tests]
        if all(p for _, p in paths):
            jobs.append((s, os.path.join(args.clean, s + ".wav"), paths))
    with Pool(args.jobs) as pool:
        rows = pool.map(score_one, jobs, chunksize=8)

    summary = {"n": len(rows)}
    for name, _ in tests:
        summary[name] = {}
        for m in ("pesq", "stoi", "si_sdr"):
            v = np.array([r[name][m] for r in rows], dtype=float)
            summary[name][m] = {"mean": float(np.nanmean(v)), "median": float(np.nanmedian(v)),
                                "p10": float(np.nanpercentile(v, 10)), "p90": float(np.nanpercentile(v, 90))}
    base = tests[0][0]
    for name, _ in tests[1:]:
        for m in ("pesq", "stoi", "si_sdr"):
            d = np.array([r[name][m] - r[base][m] for r in rows], dtype=float)
            summary[name][m]["delta_mean"] = float(np.nanmean(d))
            summary[name][m]["improved_ratio"] = float(np.mean(d > 0))
    with open(args.out, "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
