# 仕様書：AI Translator — オンライン会議アシスト（ローカルLLM版）

- 状態：**稼働中**（GX10 で常時起動。2026-11-05 時点の仕様。v2.0.0 として GitHub で公開、日→英は 2026-11-05 に追加）
- 日本語→英語の設計と確認の記録：[ja-to-en.md](ja-to-en.md)
- 開発の経緯・計測の詳細・不具合と修正の記録：[meeting-assist-local-history.md](meeting-assist-local-history.md)（以下「経緯」）
- 運用手順：[deploy/gx10/README.md](../../deploy/gx10/README.md)
- 画面の比較案（採用は案B）：[docs/mock/](../mock/index.html)

---

## 1. 目的と機能

英語のオンライン会議で、日本人ユーザーが次の2つを得る。

| 機能 | 中身 | 速さ |
|---|---|---|
| **リアルタイム翻訳** | 相手の発言を、話している途中から日本語で表示 | 最初の文字まで約0.1秒、1文の確定訳まで ASR 確定から約1秒 |
| **相手の意図と返事の案** | 相手が話し終えたとき、相手の立場・意図・返事の案2つを表示 | 話し終えてから約2秒で意図 |
| **あらすじ** | 会話を区切りごとに要約して積み重ね、流れを追えるようにする | 区切りが閉じてから約2秒 |

- 向きは **英→日（既定）と日→英** を画面で切り替える（§9）。画面・意図・あらすじの言語は訳の言語に合わせる（日→英なら英語）。Nemotron は多言語のモデルで、日本語にも対応している（2026-11-04 までは英語のみと誤解していた）
- LLM はすべてローカル（GX10 の Gemma 4）。ASR は Deepgram（クラウド）と Nemotron（GX10）を画面で切り替える。Nemotron を選べば**音声も外に出ない**
- 旧来の「意図の先行表示」（intent_label / dialogue_act）は、この画面では使わない（経緯 §2）

## 2. 全体構成

```
[PC ブラウザ]  会議アプリ（Zoom / Teams / Meet …）の音声 = 画面共有のシステム音声
[スマホ]       マイク
      │ 16kHz・16bit・モノラル PCM（WebSocket）
      ▼
https://<GX10>:10443  ── tailscale serve（tailnet 内のみ）
      ▼
ai-translator.service  uvicorn 127.0.0.1:8100（backend/main.py。ビルド済み画面も配信）
  ├─ ASR（どちらか）
  │    ├─ Deepgram nova-2（クラウド）
  │    └─ nemotron-asr.service  ws :8092（asr_server/asr_server.py、Deepgram と同じ形式の合図）
  ├─ 発話の組み立て（断片をつなぐ）
  └─ LLM：GX10 の vLLM 127.0.0.1:8001（Gemma 4 26B-A4B FP8、他のサービスと共用）
        ├ ① 翻訳（途中・確定のたび）
        ├ ② 意図と返事の案（相手の番の終わり）
        └ ③ あらすじ（区切りごと）
```

- ①②③は同じ Gemma に並行して送る。vLLM が同時に来たリクエストをまとめて処理するので、モデルを増やさずに済む（翻訳への影響は数十 ms。経緯 §7・§13.2）
- Gemma 4 の vLLM は他のサービスと共用。**このアプリからは起動・停止しない**

## 3. 音声入力（`frontend/src/components/AudioRecorder.tsx`）

| 端末 | 既定の入力 | 取り方 |
|---|---|---|
| PC | **会議の音声** | `getDisplayMedia`。共有画面で「画面全体」＋「システム音声を共有」（Windows の Chrome / Edge。デスクトップ版の Zoom・Teams でも可）、またはブラウザ会議のタブ＋「タブの音声を共有」。映像は取得直後に止める |
| PC | マイク（切り替え） | `getUserMedia`。マイクが複数あれば選択欄を出す |
| スマホ | **マイクのみ** | スマホのブラウザは画面共有の音声に対応していないため、「入力」の切り替え自体を出さない。ページを開いただけではマイクの許可を求めない（● を押したとき） |

- システム音声には相手の声だけが入る（会議アプリは自分の声をスピーカーから鳴らさない）
- 会議の画面では TTS（読み上げ）を使わない（読み上げた音を拾って翻訳し続けるループになるため）
- 共有を「停止」されたら、録音停止と同じ処理をする

## 4. ASR と発話の組み立て

### 4.1 ASR エンジン（画面のトグルで切り替え、録音中でも可）
| | Deepgram nova-2 | Nemotron 3.5 ASR Streaming 0.6B |
|---|---|---|
| 場所 | クラウド | GX10（`nemotron-asr.service`） |
| 合図 | 途中結果 / `is_final` / `speech_final` / `UtteranceEnd`（`utterance_end_ms=1000`） | サーバーが同じ形式で返す（下記） |
| 長所 | なまりに強い。会議の雑談音声で誤り率が約5ポイント低い | 単語が出るまでの遅れが半分以下。音声が外に出ない。費用なし |
| 短所 | 音声を外部に送る。従量課金 | 英語らしくない発音で何も出力しないことがある（§12） |

選択は接続URL（`/ws/audio?asr=nemotron`）で最初から指定し、録音中は `set_asr` で張り直す。選択はブラウザに保存（`meetingAssist.asr`）。

### 4.2 Nemotron ASR サーバー（`asr_server/asr_server.py`）
- Transformers 版の `AutoModelForRNNT`。区切り **320ms**（`--lookahead 3`）、fp32
- 言語は接続ごとに URL で選ぶ（`/ws?language=ja-JP`。なければ `--language` の `en-US`）。対応していない言語なら接続を断る
- 日本語・中国語（単語の間に空白がない言語）：途中結果は全文を送る（トークンが1〜2文字ずつ伸びるだけなので）、空白を取り除く、句読点に `。、？！` を含める、長い区間は2文字を1語として数える
- 接続ごとに1本の `generate()` を別スレッドで動かし、届いた音声を 320ms ごとに渡す。文字へは `processor.decode` で戻す（`tokenizer.decode` は同じトークンの連続をまとめてしまう）
- **接続ごとにモデルを1つ割り当てる**（`--streams 2`、GPU 約5.1GB）。1つのモデルを同時に使うと壊れるため。上限を超えた接続には `{"type":"Error","message":"…同時に 2 接続までです"}` を返す
- Nemotron には確定の合図がないので、サーバーで判定する（Deepgram と同じ形式で返す）
  - 新しい単語が **0.7秒** 出なければ区間を確定（`is_final` と `speech_final` を true）。文字は 320ms ごとにまとめて届くので、これより短いと単語の途中で切れる
  - 区間が **25語** を超えたら、次の `. ? ! ,` で確定
  - 最後の単語から **1秒** で `UtteranceEnd` を1回
  - 途中結果は、書きかけの最後の単語を除いて送る。確定直後に届いた句読点は次の区間から外す。句読点の前の空白を詰める
- 起動時にモデル読み込み＋空回し（約35秒）。この間は接続できない

### 4.3 発話の組み立て（`TranscriptionManager`、`gx10` モードのみ）
ASR の確定結果は文の途中で切れることがある（"…and my task" / "was to source a supplier"）。断片ごとに訳すと崩れるので、**断片をつないで「発話」にしてから**確定訳を作る。
- 途中：つないだ断片＋今の途中結果を、途中の訳として訳す
- 日本語・中国語は空白なしでつなぎ、**2文字を1語として数える**（下の語数、§5 の5語、§9 のちらつき防止は最初の6文字）。ASR の結果の、日本語の文字の隣の空白は取り除く（`normalize_asr_text` / `join_text` / `text_units`）
- 区切る条件（どれか）
  - `UtteranceEnd`（単語が1秒途切れた）
  - `speech_final`（短い間）で、つないだ文が **12語以上**（`UTTERANCE_FLUSH_MIN_WORDS`）。短い断片（"so"、"and it was"）は次につなぐ
  - **30語** に達した（`UTTERANCE_MAX_WORDS`）
  - 確定結果のあと **1.5秒** 何も来ない（`UtteranceEnd` を取りこぼしたときの保険）
- 区切った発話 → ①の確定訳、②の会話履歴、③のあらすじに渡す

## 5. ① リアルタイム翻訳

- プロンプト：訳文だけ（JSON にしない）。直前の発話3つを文脈として付ける。`temperature 0.3`
- 途中の訳：ASR の途中結果ごと（debounce 300ms、5語未満は訳さない）
- **訳は出来上がってから送る**（1文字ずつ流すと、訳し直しのたびに行が1文字に戻って「出ては消え」になる）。1つの訳は0.3〜0.8秒で出来上がる
- リクエストには連番 `seq` を付ける。途中の訳は、すでに送った訳より新しいときだけ送る（新しいリクエストが来るたびに捨てると、話し続けている間は何も出なくなる）。確定訳は必ず送る
- 確定訳を作るときは、保留中（debounce 中）の途中の訳のリクエストを取り消す

## 6. ② 意図と返事の案

- 画面での呼び名は「相手の意図」（2026-11-04 に「本音」から変更）。LLM への指示文では、出力が変わらないよう「本音」の語をそのまま使っている

- 発火：相手の番の終わり ＝ `UtteranceEnd`（1秒の間）から **0.5秒**（合計約1.5秒）。話が再開したらタイマーを取り消す。短い間で区切った発話では発火しない
- 入力：会話の最初から直近 **30発話**（`INSIGHT_HISTORY_MAX`）
- 出力（JSON、この順で生成）
  ```json
  {"situation":"相手の立場（15字以内）","need":"相手の本音（30字以内）","ready":true,"replies":["返事1（40字以内）","返事2"]}
  ```
  - `situation` を先に書かせて、誰の視点かを固定する（質問している / 説明・講義している / 不満を伝えている など）。これがないと、話のテーマを相手の欲求として書き、それをユーザーの願望として返事にしてしまった（経緯 §14）
  - `need` はテーマの言い換えではなく相手の欲求。`replies` は need に応える内容で、ユーザーから相手への返事
  - `ready=false`：まだ前置きの段階（画面では薄く表示）
- `need` が出来た時点で `insight_partial` を送り、全部そろったら `insight` を送る。`temperature 0.5`

## 7. ③ あらすじ（`StorySummarizer`）

- 発話を区切りにまとめ、閉じた区切りごとに要約する
  - 区切る条件：**8発話**（`STORY_CHUNK_UTTERANCES`）、区切りの開始から **90秒**（`STORY_CHUNK_SECONDS`、2発話以上）、または相手の番の終わり（4発話以上）
- 出力：`title`（見出し15字以内）＋ `summary`（あらすじ60字以内、固有名詞・数字は残す）。直前の見出し5つを渡し、話が続いていることが分かる見出しにする。`temperature 0.3`
- たまっている途中は `story_progress`、要約が出来たら `story` を送る。要約は裏で作り、表示を急がない（1区切り約2秒）

## 8. 通信の形式

### 8.1 ブラウザ → バックエンド（`/ws/audio?asr=deepgram|nemotron&src=en|ja&tgt=ja|en`）
- `src` / `tgt`：翻訳の向き（既定は `en` / `ja`）。接続の最初から決める（ASR の言語もこれで決まる）
- バイナリ：16kHz・16bit・モノラル PCM（4096サンプルずつ）
- テキスト（JSON）：`{"type":"set_model","model":"gx10"}`（会議画面は接続直後に必ず送る）、`{"type":"set_asr","engine":"…"}`、`{"type":"stop"}` など

### 8.2 バックエンド → ブラウザ
| type | 中身 | いつ |
|---|---|---|
| `status` / `error` | `message` | 接続状態、Nemotron に接続できない、同時接続の上限 など |
| `asr_changed` | `engine`, `ok` | ASR の切り替え後 |
| `transcript` | `transcript`, `is_final`, `speech_final` | ASR の結果（画面は途中の英語の表示に使う） |
| `translation_partial` | `translation`, `source_text`, `seq`, `is_final:false` | 途中の訳が出来上がったとき |
| `translation_final` | `translation`, `source_text`, `seq` | 発話の確定訳が出来上がったとき |
| `insight_partial` | `need`, `situation` | 意図が出来た時点 |
| `insight` | `need`, `situation`, `ready`, `replies[]` | 意図と返事の案がそろったとき |
| `story_progress` | `index`, `start`（秒）, `n` | あらすじの区切りがたまっていく途中 |
| `story` | `index`, `start`, `n`, `first`, `title`, `summary` | 区切りの要約が出来たとき。`first` はその区切りの最初の発話（`source_text` と同じ文字列） |

### 8.3 バックエンド → Nemotron ASR サーバー（`ws://<GX10>:8092/ws`）
- 送る：PCM（上と同じ）、`{"type":"CloseStream"}`
- 受け取る：最初に `{"type":"Ready"}`（上限なら `{"type":"Error",…}`）、以後 `{"type":"Results","transcript","is_final","speech_final"}`、`{"type":"UtteranceEnd"}`

## 9. 画面（`#meeting`、`frontend/src/MeetingApp.tsx` + `meeting.css`）

```
┌ ● 録音 │ 入力: 会議の音声|マイク │ ASR: Deepgram|Nemotron │ ●録音中·Nemotron │ ? │ ☾ │ ⌫ ┐  ← 細い1行
├─────────────────────────────────────────────────────────────┤
│ ● 話し中                                                     │
│ では、なぜ当社のコンサルティングファームを志望されたのですか？   │  ← いま（大きく、いつも同じ場所）
│ So why exactly are you interested in …                      │
├─────────────────────────────────────────────────────────────┤
│ 💡 相手の意図 · 相手は志望理由を質問している │ 返事の案            │  ← 意図（PC は返事の案を右に並べて常に表示）
│ 他社ではなく当社を選ぶ本気度を確かめたい      │ ・… ・…            │
├──────────────────────────────────┬──────────────────────────┤
│ これまでの会話（新しい順）          │ 📖 あらすじ               │
│                                  │ 00:14 自己紹介と経歴 …     │
└──────────────────────────────────┴──────────────────────────┘
```

- **いま欄**：表示の優先順位は「途中の訳（話し中）」→「訳が出る前の英語（聞き取り中）」→「最新の確定訳（確定 / 相手が話し終えました）」
- **途中の訳のちらつき防止**：同じ発話（原文の最初の3語が同じ）で、訳が前の7割未満に短くなる更新は表示しない
- **履歴**：いま欄に出ていない確定訳を新しい順に並べる。相手の番の区切りに「── 相手が話し終えました」。下へ読み進めている間は自動で先頭に戻らず「↑ 最新へ」を出す
- **あらすじ**：時刻・見出し・あらすじ。進行中の区切りは「進行中（n文）…」。見出しをクリックすると、履歴のその位置へ移動して一瞬ハイライト
- **スマホ**（幅 860px 以下）：いま → 意図（返事の案は「返事の案を見る」で開く）→「会話 ／ あらすじ」タブ、の縦並び
- **色**：白（既定）と紺。☾ / ☀ で切り替え、ブラウザに保存（`meetingAssist.theme`）。アクセントは**緑**（ロゴと同じ。白 `#0f9d6e` / 紺 `#34d399`）、意図の欄は琥珀色
- **ブランド**：タイトル「AI Translator」、ロゴは吹き出しの「A → あ」（緑）。ファビコン、ホーム画面用アイコン、manifest（ホーム画面に追加すると `/#meeting` が開く）。`frontend/public/`
- **小窓**（⧉、PC の Chrome / Edge のみ。`frontend/src/hooks/useMiniWindow.ts`、`components/MiniWindow.tsx`）：Document Picture-in-Picture で、いつも最前面の小さなウィンドウ（約 520×170）を開く。会議アプリの上に重ねて使う
  - 中身：状態・いまの訳（長いときは新しい方を見せる）・💡 意図1行。データと録音は元の画面のまま（React の portal で描くだけ）
  - 高さ **300px 以上** にのばすと、「会話 ／ あらすじ」のタブが下に出る（会話は日本語だけ、新しい順。見出しを押すとその行へ移動）
  - 訳が小窓に収まらないときは新しい方を見せ、上の端で切れた行は薄くぼかす（`mask-image`）
  - 対応していないブラウザ（スマホなど）ではボタンを出さない
- **向きの切り替え**（上の行の「言語 / Lang」：EN→JA | JA→EN）：録音中は切り替えられない。切り替えると履歴・意図・あらすじを消す（2つの言語が混ざらないように）。選んだ向きはブラウザに保存（`meetingAssist.langPair`）。画面の文言は `frontend/src/i18n.ts` にまとめ、訳の言語に合わせて日本語か英語にする
- 「?」に手順の説明と「従来の画面へ」。旧画面（`ExperimentalApp`）は `/` のまま残している

## 10. 設定値

### バックエンド（`backend/.env`）
| 変数 | 既定値 | 意味 |
|---|---|---|
| `GX10_HOST` | `localhost` | GX10 の場所（GX10 上で動かすときは `127.0.0.1`。git には入れない） |
| `GX10_BASE_URL` | `http://<GX10_HOST>:8001/v1` | Gemma 4 の vLLM |
| `GX10_MODEL` | `gemma4-26b-a4b` | vLLM のモデル名 |
| `NEMOTRON_ASR_URL` | `ws://<GX10_HOST>:8092/ws` | Nemotron ASR サーバー |
| `UTTERANCE_FLUSH_MIN_WORDS` | `12` | 短い間で発話を区切る最小語数 |
| `UTTERANCE_MAX_WORDS` | `30` | 発話の最大語数 |
| `STORY_CHUNK_UTTERANCES` | `8` | あらすじの区切りの発話数 |
| `STORY_CHUNK_SECONDS` | `90` | あらすじの区切りの最大秒数 |
| `DEEPGRAM_API_KEY` | — | Deepgram を使うとき |

コード内の固定値：意図の発火 `UtteranceEnd`＋0.5秒、意図の文脈30発話、翻訳の debounce 300ms・最小5語、保険の区切り1.5秒。

### Nemotron ASR サーバー（起動引数）
`--port 8092`、`--lookahead 3`（320ms）、`--final-silence 0.7`、`--utterance-end 1.0`、`--long-segment-words 25`、`--streams 2`、`--language en-US`（接続 URL で言語を指定しないときの既定）

## 11. 運用

- **常時起動**：GX10 の systemd ユーザーサービス `ai-translator.service`（`127.0.0.1:8100`）と `nemotron-asr.service`（`:8092`）。Linger 有効なので GX10 の再起動後も自動で立ち上がる。公開は `tailscale serve --https=10443`（tailnet 内のみ、Funnel なし）
- **URL**：`https://<GX10 の Tailscale 名>:10443/#meeting`
- **更新**：master に push → `bash deploy/gx10/deploy.sh`（GX10 で pull → WSL で画面をビルドしてコピー → 依存を入れ直し → サービス再起動 → 状態表示）。作業ツリーが push 済みの master と一致しないと中止する。再起動直後の約35秒は Nemotron が読み込み中
- **状態**：`bash deploy/gx10/status.sh`（Gemma 4、2つのサービス、Tailscale の設定）
- **開発時**（WSL）：`backend/.venv/bin/uvicorn main:app --reload --port 8000` と `npm run dev`、`http://localhost:5173/#meeting`。WSL の system Python は FastAPI と Starlette の版が合わないので `.venv` を使う。リポジトリが `/mnt/c` にあるため Vite はポーリングで変更を検知する

## 12. 性能（2026-11-03 の計測。詳細は経緯 §7・§11〜§13・§16・§17）

| 項目 | 値 |
|---|---|
| 翻訳：最初の文字 / 全文（途中の訳） | 約0.1秒 / 約0.4秒（JSON に入れていたときは約2.3秒） |
| 翻訳：ASR 確定 → 確定訳（面接動画190秒） | Deepgram 中央値0.80秒・上位10% 1.89秒 / Nemotron 0.92秒・1.42秒 |
| 音声開始 → 最初の訳（同上） | Deepgram 8.0秒 / Nemotron 6.5秒 |
| 意図：生成開始 → 意図 / 返事2つ | 約0.6秒 / 約1.6秒（相手が話し終えてから意図まで約2秒） |
| あらすじ：1区切りの要約 | 約2秒 |
| 単語が出るまでの遅れ（会議音声 AMI、中央値） | Deepgram 0.64秒 / Nemotron 0.29秒 |
| 誤り率（AMI、言いよどみ除く） | Deepgram 27.8% / Nemotron 32.5% |
| GX10 の資源 | Nemotron GPU 約5.1GB（2接続分）。Nemotron 使用中も Gemma の翻訳は約40ms 遅くなるだけ |
| 表示の崩れ（面接動画190秒、表示シミュレーター） | 縮む・消える・重複・抜け落ち：すべて 0 |

## 13. 既知のリスク・制約

| 項目 | 内容 | 対処 |
|---|---|---|
| Nemotron となまり | 英語らしくない発音を英語として書き起こせず、何も出ないことがある（合成音声で確認。言語の自動判定では日本語と判定した） | 何も出なければ Deepgram に切り替える。実際の会議での確認が必要 |
| GX10 の共用 | Gemma 4 を他のサービス（夜間バッチなど）と共用。重なると訳が遅れることがある（最初の文字まで約1〜5秒になった例あり） | 状況を見て判断。このアプリからは Gemma を操作しない |
| 同時接続 | Nemotron は同時2接続まで | 3つ目は画面にエラー。Deepgram なら制限なし |
| 2人の声が両方入る音声 | 間がないと「相手の番の終わり」を判定できず、意図が出にくい | 実際の会議では相手の声だけが入るので問題になりにくい |
| スマホ | 会議の音声は取れない（マイクのみ） | — |
| GX10 の Wi-Fi | 切れると全体が止まる | GX10 側の watchdog で自動復帰 |
| 再起動直後 | Nemotron は約35秒使えない | 待つか Deepgram に切り替え |

## 14. ファイル

| ファイル | 役割 |
|---|---|
| `backend/main.py` | `gx10` モード：`IntentEstimator._call_gx10`（翻訳）、`SummaryGenerator._generate_insight_gx10`（意図）、`StorySummarizer`（あらすじ）、`TranscriptionManager`（ASR 切り替え・発話の組み立て）、プロンプト `build_translation_only_prompt` / `build_insight_prompt` / `build_story_prompt` |
| `asr_server/asr_server.py` | Nemotron ASR サーバー |
| `frontend/src/MeetingApp.tsx`, `meeting.css` | 会議画面 |
| `frontend/src/i18n.ts` | 画面の文言（日本語・英語）と翻訳の向き |
| `frontend/src/components/AudioRecorder.tsx` | 音声入力（`variant="compact"`） |
| `frontend/src/hooks/useMiniWindow.ts`, `components/MiniWindow.tsx` | 小窓（Document Picture-in-Picture） |
| `frontend/src/hooks/useWebSocket.ts` | メッセージの型 |
| `frontend/index.html`, `frontend/public/` | タイトル・アイコン・manifest |
| `deploy/gx10/` | systemd ユニット、`deploy.sh`、`status.sh`、README |
| `docs/mock/` | 画面の比較案 A / B / C |
