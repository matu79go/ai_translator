"""AMI の誤り率（WER）を、置換・脱落・挿入に分けて出す。

  python ami_wer.py manifest.json nemo_result.json [--key 3] [--fillers um uh ...]
文の整え方は ~/work/nemotron-asr/analyze.py の norm() と同じ。言いよどみは正解・認識結果の両方から除く。
"""
import argparse
import json
import re

import jiwer

DEFAULT_FILLERS = ["um", "uh", "hmm", "mm", "mhm", "uh-huh", "mm-hmm", "er", "erm", "ah", "eh"]


def norm(s, fillers):
    s = s.lower().replace("’", "'")
    s = re.sub(r"\bmr\b\.?", "mister", s)
    s = re.sub(r"\bmrs\b\.?", "missus", s)
    words = []
    for w in s.split():
        if w.strip(".,?!") in fillers:  # ハイフン入り（uh-huh）を先に除く
            continue
        w = re.sub(r"[^a-z0-9' ]+", " ", w.replace("-", " "))
        words += [x.strip("'") for x in w.split() if x.strip("'") and x.strip("'") not in fillers]
    return " ".join(words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("result")
    ap.add_argument("--key", default="3")
    ap.add_argument("--fillers", nargs="*", default=DEFAULT_FILLERS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    fillers = set(a.fillers)
    ref = {m["id"]: m["text"] for m in json.load(open(a.manifest))}
    res = json.load(open(a.result))
    clips = res[a.key]["clips"] if a.key in res else res
    out = {}
    R, H = [], []
    for cid in sorted(clips):
        r, h = norm(ref[cid], fillers), norm(clips[cid]["final"], fillers)
        o = jiwer.process_words(r, h)
        out[cid] = dict(wer=o.wer, S=o.substitutions, D=o.deletions, I=o.insertions, N=len(r.split()), hyp_words=len(h.split()))
        R.append(r)
        H.append(h)
    o = jiwer.process_words(R, H)
    out["ALL"] = dict(wer=o.wer, S=o.substitutions, D=o.deletions, I=o.insertions, N=sum(len(r.split()) for r in R),
                      hyp_words=sum(len(h.split()) for h in H))
    if a.json:
        print(json.dumps(out))
    else:
        for k, v in out.items():
            print(f"{k:12s} WER {v['wer'] * 100:5.1f}%  S {v['S']:4d}  D {v['D']:4d}  I {v['I']:4d}  N {v['N']}  hyp {v['hyp_words']}")


if __name__ == "__main__":
    main()
