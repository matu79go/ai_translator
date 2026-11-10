# NVIDIA NemotronLabs RE-USE の雑音除去を測る

NVIDIA の雑音除去モデル [NVIDIA NemotronLabs RE-USE](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-RE-USE)(9.6M パラメータ、約 40MB)が、どれだけ雑音を消すかを測ったスクリプトと結果です。設計と結果の詳細は [docs/designs/re-use-denoise-experiment.md](../../docs/designs/re-use-denoise-experiment.md)。

## 結果(2026-11-10、ASUS Ascent GX10 1 台)

| | 処理する前 | RE-USE の後 |
|---|---|---|
| 雑音を 6 倍にした音声:声と雑音の差(SNR) | −8.6 dB | +11.9〜+13.7 dB |
| 同:話していない間の音量 | −14〜−19 dBFS | −50〜−53 dBFS |
| VoiceBank-DEMAND 824 本:PESQ / STOI / SI-SDR | 1.97 / 0.921 / 8.4 dB | 3.23 / 0.962 / 19.2 dB |

数字は `results/` にあります。

## 用意する物(このリポジトリには入れていません)
- モデル:[nvidia/NVIDIA-NemotronLabs-RE-USE](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-RE-USE)(OpenMDW-1.1)。リポジトリ一式を `model/` に置く
- 試験用の音声:[VoiceBank-DEMAND](https://datashare.ed.ac.uk/handle/10283/2791)(CC BY 4.0)の `clean_testset_wav.zip` と `noisy_testset_wav.zip`
- `mamba-ssm`(CUDA でビルド)、PyTorch、soundfile、librosa、`pesq`、`pystoi`。NVIDIA の GPU が必要

## 手順
```bash
# 1. 雑音を消す(フォルダ内の wav を一括処理。1 本ごとの処理時間も記録)
python enhance.py --model_dir model --in_dir data/vbd/noisy_testset_wav --out_dir out/vbd_48k

# 2. 正解と比べて PESQ・STOI・SI-SDR を出す(16kHz で採点)
python score.py --clean data/vbd/clean_testset_wav --test noisy=data/vbd/noisy_testset_wav --test reuse=out/vbd_48k --out scores.json

# 3. 雑音を大きくした音声を作り、雑音を消して、SNR と話していない間の音量を測る
python make_hard.py data/vbd data/hard p232_022 p257_003 --gains 1 3 6
python enhance.py --model_dir model --in_dir data/hard/noisy --out_dir out/hard
python noise_floor.py data/hard/clean data/hard/noisy out/hard p232_022_x6 p257_003_x6

# 4. 聞き比べ用の素材(音量をそろえた wav とスペクトログラム)を作る
python build_listen.py pairs_listen.json out/listen
```

| ファイル | 中身 |
|---|---|
| `enhance.py` | RE-USE で一括処理。NVIDIA の `inference.py` と同じ処理を、soundfile で読み書きする形にした |
| `score.py` | PESQ・STOI・SI-SDR |
| `make_hard.py` | 雑音(雑音入り − 元の音声)を k 倍して足した音声を作る |
| `noise_floor.py` | SNR と、話していない間の音量 |
| `build_listen.py` | 聞き比べ用の素材 |
| `ami_wer.py` | AMI 会議音声での音声認識の誤り率(Nemotron ASR の前に挟んだときの比較に使用) |
