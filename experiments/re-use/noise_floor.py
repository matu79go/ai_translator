"""雑音が消えたことを数字で示す（正解の音声がある物だけ）。

1. SNR：残った雑音（出力 − 正解）に対する声の大きさ（dB）。処理前は 雑音入り − 正解 = 雑音
   出力は音量が変わることがあるので、正解に最小二乗で合わせてから引く（SI-SDR と同じ考え方）
2. 話していない間の音量：正解の音声で一番静かな 20% の区間（20ms 単位）の音量（dBFS）
   処理前と処理後で比べる。処理後は音量を正解に合わせた後の値

  python noise_floor.py clean_dir noisy_dir enhanced_dir id1 id2 ...
"""
import json
import os
import sys

import numpy as np
import soundfile as sf


def load(d, i):
    x, sr = sf.read(os.path.join(d, i + ".wav"), dtype="float64")
    return x, sr


def fit(ref, est):
    a = np.dot(est, ref) / np.dot(est, est)  # est を ref の大きさに合わせる
    return a * est


def db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-20)


def main():
    cd, nd, ed = sys.argv[1:4]
    out = []
    for i in sys.argv[4:]:
        c, sr = load(cd, i)
        n, _ = load(nd, i)
        e, _ = load(ed, i)
        L = min(len(c), len(n), len(e))
        c, n, e = c[:L], n[:L], e[:L]
        n2, e2 = n, fit(c, e)  # 処理前は 正解＋雑音 そのもの（音量を合わせない）
        fl = int(0.02 * sr)
        frames = L // fl
        ce = np.array([db(c[k * fl:(k + 1) * fl]) for k in range(frames)])
        quiet = ce <= np.percentile(ce, 20)
        def floor(x):
            seg = np.concatenate([x[k * fl:(k + 1) * fl] for k in range(frames) if quiet[k]])
            return db(seg)
        out.append({
            "id": i,
            "snr_before_db": round(db(c) - db(n2 - c), 1),
            "snr_after_db": round(db(c) - db(e2 - c), 1),
            "quiet_gap_vs_peak_db": round(float(ce.max() - np.percentile(ce, 20)), 1),
            "quiet_level_before_dbfs": round(floor(n2), 1),
            "quiet_level_after_dbfs": round(floor(e2), 1),
            "quiet_level_clean_dbfs": round(floor(c), 1),
        })
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
