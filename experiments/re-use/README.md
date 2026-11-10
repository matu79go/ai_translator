# Measuring NVIDIA NemotronLabs RE-USE noise reduction

Scripts and results for measuring how much noise [NVIDIA NemotronLabs RE-USE](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-RE-USE) removes. RE-USE is a speech enhancement model with only 9.6M parameters (about 40 MB). Design notes and full results (in Japanese): [docs/designs/re-use-denoise-experiment.md](../../docs/designs/re-use-denoise-experiment.md).

## Results (2026-11-10, one ASUS Ascent GX10)

| | Before | After RE-USE |
|---|---|---|
| Noise boosted 6×: speech-to-noise ratio (SNR) | −8.6 dB | +11.9 to +13.7 dB |
| Same: level during pauses in speech | −14 to −19 dBFS | −50 to −53 dBFS |
| VoiceBank-DEMAND test set (824 clips): PESQ / STOI / SI-SDR | 1.97 / 0.921 / 8.4 dB | 3.23 / 0.962 / 19.2 dB |

Even when the noise is louder than the voice, the voice ends up 12–14 dB above the remaining noise, and the pauses become quieter than in the original clean recording. Raw numbers are in `results/`.

## What you need (not included in this repository)
- Model: [nvidia/NVIDIA-NemotronLabs-RE-USE](https://huggingface.co/nvidia/NVIDIA-NemotronLabs-RE-USE) (OpenMDW-1.1). Put the whole repository in `model/`
- Test audio: [VoiceBank-DEMAND](https://datashare.ed.ac.uk/handle/10283/2791) (CC BY 4.0), `clean_testset_wav.zip` and `noisy_testset_wav.zip`
- `mamba-ssm` built with CUDA, PyTorch, soundfile, librosa, `pesq`, `pystoi`. An NVIDIA GPU is required

## Steps
```bash
# 1. Remove noise from every wav in a folder (also records processing time per file)
python enhance.py --model_dir model --in_dir data/vbd/noisy_testset_wav --out_dir out/vbd_48k

# 2. Score against the clean reference: PESQ, STOI, SI-SDR (scored at 16 kHz)
python score.py --clean data/vbd/clean_testset_wav --test noisy=data/vbd/noisy_testset_wav --test reuse=out/vbd_48k --out scores.json

# 3. Make louder-noise versions, remove the noise, and measure SNR and the level during pauses
python make_hard.py data/vbd data/hard p232_022 p257_003 --gains 1 3 6
python enhance.py --model_dir model --in_dir data/hard/noisy --out_dir out/hard
python noise_floor.py data/hard/clean data/hard/noisy out/hard p232_022_x6 p257_003_x6

# 4. Build listening material (loudness-matched wav files and spectrograms)
python build_listen.py pairs_listen.json out/listen
```

| File | What it does |
|---|---|
| `enhance.py` | Batch noise removal with RE-USE. Same processing as NVIDIA's `inference.py`, reading and writing audio with soundfile |
| `score.py` | PESQ, STOI, SI-SDR |
| `make_hard.py` | Adds the noise (noisy − clean) back k times louder |
| `noise_floor.py` | SNR and the level during pauses in speech |
| `build_listen.py` | Listening material |
| `ami_wer.py` | Word error rate on AMI meeting audio (used to compare Nemotron ASR with and without RE-USE in front) |

Notes: the comments in the scripts are in Japanese. The listening samples are derived from VoiceBank-DEMAND (CC BY 4.0).

## Listening samples
`samples/` holds the audio used in the blog post (loudness-matched, 128 kbps mono MP3) and a standalone listening page (`samples/demo/index.html`). Each set has the noisy input, the output of RE-USE, and the clean original. The noise was boosted 6× so the noise is louder than the voice; the "x1" files keep the original noise level. Derived from VoiceBank-DEMAND (CC BY 4.0).
