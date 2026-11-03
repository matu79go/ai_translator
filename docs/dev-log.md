# 開発ログ (Development Log)

このファイルは開発の経緯・意思決定・実験結果を時系列で記録する。

---

## 2026-02-13: TTS（翻訳音声読み上げ）機能の追加

### 背景・動機
- デモ動画を撮影し、YouTubeに限定公開でアップロード済み
- 現状は翻訳結果が画面表示のみ → イヤフォンユーザーや画面を見られない状況では使えない
- 翻訳テキストを音声で読み上げることで、「聞くだけで翻訳がわかる」体験を実現したい

### 要件
- `full_translation`（日本語）が確定したタイミングで音声読み上げ
- 日本語の自然な読み上げ
- レイテンシをできるだけ小さく（リアルタイム翻訳の体験を損なわない）

### 技術選定の検討

| 方式 | メリット | デメリット |
|------|----------|------------|
| **Web Speech API (Browser TTS)** | 無料、実装が簡単、レイテンシ低い | 音声品質がOS/ブラウザ依存 |
| **Google Cloud TTS API** | 高品質、日本語対応良好 | API課金、ネットワークレイテンシ |
| **OpenAI TTS API** | 自然な音声 | API課金、日本語はやや弱い |
| **Edge TTS (edge-tts)** | 無料、高品質、日本語良好 | 非公式API（安定性リスク） |

### 方針
- **Web Speech API（ブラウザ内蔵TTS）で最速プロトタイプ** → 音質不足なら後から差し替え
- プロトタイプ段階ではAPI課金・外部依存を避ける

### 実装メモ
- **変更ファイル**: `frontend/src/App.tsx`, `frontend/src/ExperimentalApp.tsx`（両ページ）
- **実装内容**:
  1. `ttsEnabled` state + `ttsEnabledRef`（コールバック内から最新値を参照するため）
  2. `speakTranslation()`: `SpeechSynthesisUtterance` で日本語読み上げ
  3. `case 'intent'` 内で `is_final=true` かつ `full_translation` ありのとき発火
  4. UIにトグルボタン追加（amber色 ON / gray色 OFF）
- **パラメータ**: `lang='ja-JP'`, `rate=3.0`（最終値。1.1→1.8→3.0 と調整）
- **注意点**: `ttsEnabled` を直接コールバックで参照するとクロージャの問題で古い値を掴むため、`useRef` で最新値を保持
- **ExperimentalApp**: 表示モード切り替え（別々/上書き）の横にTTSボタン配置

### イテレーション経緯
1. **初版（rate=1.1）**: 動作確認OK。ただし `cancel()` で新しい翻訳が来るたびに前の読み上げが中断される問題
2. **修正1**: `cancel()` を削除 → Web Speech APIの自然なキューイングで順次読み上げに変更。TTS OFFボタン押下時のみ `cancel()` で即停止
3. **修正2（rate=1.8）**: 英語が早いと翻訳音声が追いつかない → rate を 1.8 に上げた。まだ遅い
4. **修正3（rate=3.0）**: さらに 3.0 に上げた。体感的にだいぶ追従できるようになった

### レイテンシ計測結果（GPT-4使用時）

#### 遅延の内訳（バックエンドログより）

| 区間 | 典型値 | 備考 |
|------|--------|------|
| 発話 → ASR確定 | ~500-1000ms | Deepgramのストリーミング処理 |
| ASR → LLM First chunk | ~460-690ms | GPT-4のレスポンス開始までの待ち |
| First chunk → FULL_TRANSLATION到達 | ~500-1500ms | JSONストリーミングで翻訳フィールドに到達 |
| **合計: 発話終了 → 翻訳テキスト表示** | **~1.5-3.0s** | 短い文で速い、長い文で遅い |
| TTS読み上げ追加遅延 | +数百ms～数秒 | 翻訳テキストの長さとキュー待ちに比例 |
| **合計: 発話終了 → 翻訳音声聞こえる** | **~2-4s** | 短文で2s、長文で6s以上も |

#### 実際のログ例
```
短い文 "i loved it":
  First chunk: 487ms → FULL_TRANSLATION: @2013ms → Complete: 2614ms

普通の文 "let me give you one example":
  First chunk: 413ms → SPECULATIVE: @1005ms → FULL_TRANSLATION: @1328ms → Complete: 2072ms

長い文 "calligraphy instruction in the...":
  First chunk: 2502ms → SPECULATIVE: @3570ms → FULL_TRANSLATION: @4133ms → Complete: 4561ms
```

#### リアルタイム翻訳としての実用性評価

| 遅延 | 体感 | 用途 |
|------|------|------|
| **< 2秒** | 会話のテンポを壊さない | 対面通訳の代替として実用的 |
| **2-4秒** | 遅いが追従できる | プレゼン・講演の同時通訳なら許容範囲 |
| **> 5秒** | 話題が先に進んでしまう | 実用困難 |

**参考**: プロの同時通訳者は通常 **2-3秒遅れ** で訳出する。
現状は短い文（1.5〜2秒）なら実用圏内だが、長い文や早口では厳しい。

### PC vs スマホ（iOS）のTTS差異 — 要注意

Web Speech API はブラウザ内蔵だが、**実際のTTSエンジンはOS/プラットフォームごとに全く異なる**。
同じコードでもPC（Windows Chrome）とスマホ（iPhone Chrome）で挙動が大きく変わるため要注意。

#### 1. rate（読み上げ速度）の体感差
| rate値 | PC (Windows Chrome) | iPhone Chrome (iOS WebKit) |
|--------|--------------------|-----------------------------|
| 1.1 | 通常速度 | 通常速度 |
| 1.8 | やや速い | かなり速い |
| 3.0 | 追従できる速さ | **速すぎて聞き取り不可能** |

**原因**: iPhone Chrome は内部的にiOS Safari（WebKit）のTTSエンジンを使う。iOS版は全ブラウザがWebKitベースのため、Chrome/Safari/Firefox問わず同じ挙動。Apple独自のTTSエンジンは同じ rate 値でもWindowsより高速に読み上げる。

**対策**: `navigator.userAgent` でモバイル判定し、rateを分岐。
```
PC: rate = 3.0 / モバイル: rate = 1.3
```

#### 2. モバイルブラウザの自動再生制限
- モバイルブラウザでは `speechSynthesis.speak()` を**ユーザーのタップ操作から直接呼ばないとブロック**される
- WebSocketコールバックからの呼び出しは「ユーザー操作」と見なされない → **無音**
- **対策**: TTS ONボタンタップ時に無音の `SpeechSynthesisUtterance` (volume=0) を発話してロック解除
```typescript
const unlock = new SpeechSynthesisUtterance('');
unlock.volume = 0;
window.speechSynthesis.speak(unlock);
```

#### 3. 読み上げ漏れ問題（is_final依存）
- **症状**: 画面に翻訳が表示されているのに読み上げられないことがある
- **原因**: 元々 `is_final=true` の時のみTTSを発火していた。しかし実験モードでは partial (is_final=false) でも `full_translation` が返ることがある。partial で翻訳が表示された後、finalが来る前に次の発話で上書きされると、TTSが発火しないまま消える
- **対策**: `is_final` に関係なく `full_translation` があれば発火。`lastSpokenRef` で同一テキストの重複読み上げを防止

### イテレーション経緯（続き）
5. **修正4（モバイル対応）**: iOSでTTSが動かない → ユーザータップで無音発話してロック解除
6. **修正5（rate分岐）**: iOSでrate=3.0は速すぎ → モバイル判定でrate=1.5に。さらに1.3に調整
7. **修正6（読み上げ漏れ）**: `is_final` 限定 → `full_translation` があれば常に発火 + 重複防止

### Bluetoothイヤフォン実機テスト結果

#### 動作確認結果
- **自分の声（装着者）** → イヤフォンマイクで拾える → 翻訳OK → イヤフォンで聞こえる ✅
- **相手の声（周囲の音）** → イヤフォンマイクで拾えない ❌

#### 原因
Bluetoothイヤフォンのマイクは**装着者の声に最適化**されている：
- マイクが口元に近い位置
- ノイズキャンセリングが周囲の音を積極的にカット
- 通話用設計のため遠くの声は抑制

#### 市販の翻訳専用イヤホン
Timekettle M3、Vasco E1、OPPO Enco X3s などは**ビームフォーミング（指向性マイク技術）**で
周囲の声も拾えるよう設計されている。ただし専用アプリ+専用ハードウェアで実現しており、
ブラウザの `getUserMedia` では外音取り込みマイクの音声を取得できない。

#### 実用的な運用パターン
| パターン | 動作 |
|---------|------|
| 自分が英語を話す → 自分のイヤフォンで日本語訳 | ✅ |
| スマホ内蔵マイクで相手の声を拾う → イヤフォンで翻訳音声 | 🔧 マイク選択UIで対応可能 |
| イヤフォンマイクで相手の声を拾う | ❌ ハードウェア制約 |

#### 次のステップ
マイク入力デバイス選択UIを追加し、「入力=内蔵マイク / 出力=Bluetoothイヤフォン」を実現する。

### 結果
- Web Speech API による TTS は動作する。PC では rate=3.0 で英語の早口にもある程度追従可能
- ただし根本的にはLLMの応答速度がボトルネック。TTS自体の遅延は小さい
- 音質はOS/ブラウザ依存だが、プロトタイプとしては十分
- **PC と iOS で同じ rate 値でも体感速度が全く違う** — プラットフォーム分岐が必須
- **モバイルは自動再生制限がある** — ユーザー操作でのロック解除が必要
- **翻訳の読み上げ漏れ** — `is_final` に頼らず `full_translation` の有無で発火すべき
- **Bluetoothイヤフォンのマイクは装着者の声専用** — 相手の声を拾うには内蔵マイク選択が必要

### iOSブラウザでのマイクデバイス選択の試み

#### 実装
- `navigator.mediaDevices.enumerateDevices()` で利用可能なマイク一覧を取得
- UIにドロップダウンで選択可能に
- `getUserMedia` の `deviceId` 制約で指定

#### 結果: ❌ 失敗
- `deviceId: { exact: id }` → iOSが拒否、接続即切断
- `deviceId: id`（緩い指定）→ こちらも動作せず
- iOSのWebKit はオーディオ入力デバイスの明示的な選択をサポートしていない模様
- **結論**: ブラウザでは入力/出力デバイスの分離制御は不可能

### 最終結論

#### ブラウザの限界とネイティブアプリの可能性
- **ブラウザ（現状）**: 入力=内蔵マイク / 出力=Bluetoothイヤフォン の分離は不可能
- **ネイティブアプリ**: `AVAudioSession` で `setPreferredInput(.builtInMic)` + `allowBluetooth` 出力 → **実現可能**
- React Native + Expo ならWindows環境でも開発可能（ビルドはクラウド）
- ただし Apple Developer Account ($99/年) が必要

#### TTS遅延の最終評価
直近の計測（rate=1.3、iPhone Chrome）:
- FULL_TRANSLATION到達: 平均 **2,115ms**
- TTS読み上げ: 短文で約1秒
- **翻訳テキスト表示と音声読み上げの遅延差は約1秒以内**
- プロ同時通訳者（2-3秒遅れ）と同等のペースで実用的

#### 成果物
- ブログ記事: `articles/linkedin_part4.md`
- ポートフォリオ: `articles/portfolio_tts_earbuds.md`

---

## 2026-11-03: ローカル（GX10）会議アシスト — Phase 1・Phase 2

現在の仕様は [docs/designs/meeting-assist-local.md](designs/meeting-assist-local.md)、設計・計測・不具合の経緯は [meeting-assist-local-history.md](designs/meeting-assist-local-history.md)。

### やったこと
- LLM を API から **GX10 のローカル vLLM（Gemma 4 26B-A4B FP8）** に切り替えた会議アシストを新設（`#meeting` 画面、モデル `gx10`）
- 「意図の先行表示」をやめ、**① 訳文だけのリアルタイム翻訳** と **② 相手が話し終えた時の「本音」と返事の案** の2本立てにした
- **会議の音声**を `getDisplayMedia`（画面全体＋システム音声）で取得。Zoom / Teams のデスクトップ版でも使え、相手の声だけが入る
- **ASR を Deepgram / Nemotron 3.5 ASR Streaming 0.6B（GX10）でトグル切り替え**。Nemotron は Deepgram と同じ形式の合図を返す自作サーバー（`asr_server/`）

### 主な結果
- 翻訳を JSON の中に入れると、翻訳が出るまで約2.3秒かかった。訳文だけにすると、**最初の文字まで約0.1秒、全文で約0.4秒**
- 本音は短い出力（本音→返事2つ）にして、生成開始から**約0.55秒**
- Nemotron は単語が出るまでの遅れが Deepgram の半分以下。会議の雑談音声（AMI）では誤り率が約5ポイント劣るが、ユーザーの実地テストでは「Nemotron の方が速く、訳はほぼ同程度」
- GX10 の負荷：Nemotron は GPU 約2.6GB。Gemma の翻訳は約40ms遅くなるだけ

### 学び
- **JSON の中の翻訳は遅い**：前にあるフィールドの生成を待つ。速さが要る部分は、構造なしで流す
- **表示は「完成した訳で置き換える」**：1文字ずつ流すと、訳し直しのたびに行が1文字に戻り、「出ては消え」になる
- **ASR の断片は文単位でつなぐ**：Deepgram は文の途中でも確定させる。断片ごとに訳すと崩れる。逆に長くつなぎすぎると、同じ訳が何度も出直して見える（12語・短い間で区切る）
- **Nemotron の区切り判定は、文字が届く間隔（320ms）より長い無音で**。短いと単語の途中で切れる
- **Nemotron は発音が英語らしくないと何も出力しないことがある**（合成音声で確認。言語の自動判定では日本語と判定してカタカナで書き起こした）。なまりの強い話者では Deepgram に切り替えて使う
- 検証は、実際の音声を実時間で流して、画面の表示をシミュレーターで再現し、数で確かめてから渡す（ユーザーに何度も試してもらわない）
