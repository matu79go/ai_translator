# GX10 で常時起動（tailnet 内から PC・スマホで使う）

```
https://<GX10 の Tailscale 名>:10443/#meeting      ← tailnet 内のみ（Funnel は使わない）
  tailscale serve :10443 → 127.0.0.1:8100  ai-translator.service（uvicorn、ビルド済み画面も配信）
                                ├→ 127.0.0.1:8001  Gemma 4 vLLM（他のサービスと共用。ここからは起動・停止しない）
                                └→ 127.0.0.1:8092  nemotron-asr.service（Nemotron ASR）
```

## 初回だけ（GX10 で）
```bash
git clone https://github.com/matu79go/ai_translator.git ~/ai_translator
python3 -m venv ~/ai_translator/backend/.venv && ~/ai_translator/backend/.venv/bin/pip install -r ~/ai_translator/backend/requirements.txt
# backend/.env を用意（API キー + GX10_HOST=127.0.0.1）。chmod 600
# Nemotron の venv（~/venvs/nemotron-asr）は docs/designs/meeting-assist-local.md §12 の手順で作成済みであること
tailscale serve --bg --https=10443 http://127.0.0.1:8100
```

## 更新（WSL で、master に push した後）
```bash
bash deploy/gx10/deploy.sh     # GX10 で pull → 画面をビルドしてコピー → サービス再起動 → 状態表示
bash deploy/gx10/status.sh     # 状態だけ見る
```

## 止める・ログを見る（GX10 で）
```bash
systemctl --user stop ai-translator nemotron-asr       # 止める（Gemma 4 は止めない）
journalctl --user -u ai-translator -f                  # ログ
journalctl --user -u nemotron-asr -f
```

- 2つのサービスは systemd のユーザーサービス（Linger 有効）なので、GX10 を再起動しても自動で立ち上がる
- スマホは「マイク」入力のみ（スマホのブラウザは画面共有の音声に対応していない）
