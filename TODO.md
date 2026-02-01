# Intent-First Translation MVP - 進捗状況

## 未完了の作業（優先度順）

### 読み上げ機能（TTS）設計 ← NEW (2025-01-26)
- [ ] **音声読み上げ機能の実装**
  - 対象: `is_final=true` かつ `full_translation` が存在するとき
  - 言語: 日本語 (`ja-JP`)
  - API: Web Speech API (`SpeechSynthesis`) ← ブラウザ標準、追加ライブラリ不要

- **実装箇所**: `frontend/src/App.tsx`

- **状態管理**:
  ```typescript
  // 読み上げモードのON/OFF
  const [isSpeechEnabled, setIsSpeechEnabled] = useState(false);

  // 重複防止用（最後に読み上げたテキスト）
  const lastSpokenRef = useRef<string>('');
  ```

- **読み上げ関数**:
  ```typescript
  const speakText = useCallback((text: string) => {
    // 重複チェック
    if (text === lastSpokenRef.current) return;

    // 前の読み上げをキャンセル
    speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'ja-JP';
    speechSynthesis.speak(utterance);

    lastSpokenRef.current = text;
  }, []);
  ```

- **トリガー** (intentメッセージ処理内):
  ```typescript
  if (isSpeechEnabled && message.is_final && message.data.full_translation) {
    speakText(message.data.full_translation);
  }
  ```

- **UIトグル** (モデル選択の横):
  - ボタン形式でON/OFF切り替え
  - ON: 紫色 (`bg-purple-600`)、🔊 読み上げON
  - OFF: グレー (`bg-gray-200`)、🔇 読み上げOFF

### 翻訳品質の向上
- [ ] 案B: 1回で2つの翻訳を出力（quick_translation + full_translation）
- [ ] 案C: is_final時のみ別途高品質翻訳API呼び出し
- [ ] プロンプト改善（翻訳品質を重視する指示追加）

### 推測翻訳の改善
- [ ] 推測訳の精度向上（より多くの文脈を考慮）

### 追加改善案
- [ ] 日本語→英語の双方向対応
- [ ] 話者識別（複数人会話対応）
- [ ] ログ出力機能（ASR速度、LLM速度、表示までの時間）の可視化
- [ ] モバイル対応（レスポンシブデザイン）

### 断念した項目
- [x] ~~ngrok経由で外出先からローカルLLMを使う構成の検討~~ → ローカルLLMは断念（2025-02-01）

---

## 現在の機能

| 機能 | 説明 | レイテンシ |
|------|------|-----------|
| 統合タイムライン | 意図+翻訳を1カードで表示 | ~500-800ms |
| 推測翻訳 | dialogue_act+intent_labelから先行推測 | ~500ms早い |
| 会話サマリー | 全体の要約（画面下固定） | 沈黙1.5秒後 |
| トランスクリプト | 音声認識結果 | ~200ms |
| 予測ヒント | キーワードベースの即時予測 | ~0ms |
| モデル切替 | GPT-4 / Gemini / Gemma 3 (Ollama) / **LM Studio** / Groq | - |
| モード切替 | HYBRID / FINAL_ONLY | - |

### ルート構成

| URL | 説明 |
|-----|------|
| `/` | 完成版（統合タイムライン） |
| `/experimental` | 推測翻訳実験（別々/上書き表示切替） |

---

## 技術スタック

### Backend
- Python 3.x
- FastAPI + WebSockets
- Deepgram SDK (nova-2モデル)
- OpenAI API (gpt-4o-mini, Streaming)
- Google Generative AI (gemini-2.5-flash-lite)
- LM Studio + Gemma 3 4B (ローカルLLM)
- **Groq API (llama-4-maverick, llama-3.3-70b, llama-3.1-8b, gpt-oss-120b)**

### Frontend
- React 18 + TypeScript
- Vite 5
- Tailwind CSS 3
- Lucide React (Icons)

## 起動方法

### Backend
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm run dev
```

### アクセス
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- Health Check: http://localhost:8000/health

## 環境変数

`backend/.env` に以下を設定:
```
DEEPGRAM_API_KEY=your_deepgram_key
OPENAI_API_KEY=your_openai_key
GOOGLE_API_KEY=your_google_api_key
GROQ_API_KEY=your_groq_api_key
LMSTUDIO_BASE_URL=http://192.168.0.183:1234/v1
LMSTUDIO_MODEL=gemma-3-4b-it
```

## 評価指標（CLAUDE.mdより）

- **Latency (T1)**: 発話開始から「意図ラベル」が画面に出るまでの秒数
  - 現状: 約500ms（ストリーミング最適化済み）
  - 翻訳: 約500-800ms（ほぼリアルタイム）
- **Stability**: 意図ラベルが不自然に変化しないか
  - 現状: デバウンス処理で改善済み
- **Perceived Delay**: 逐次翻訳のみの場合との比較
  - 現状: ほぼリアルタイムの翻訳体験を実現

## 推奨使用方法

1. **意図（左）** - 話し始めの数百msでトピックを先行把握
2. **翻訳（右）** - 意図を軸に詳細をリアルタイムで補足
3. **サマリー（下）** - 話し終わった後に全体像を確認

---

## 完了した作業（セッションログ）

### Phase 1: 基盤構築 ✅
- [x] FastAPI + WebSocket バックエンド構築
- [x] React + Vite + TypeScript + Tailwind CSS フロントエンド構築
- [x] ブラウザマイク入力（MediaDevices API）
- [x] Deepgram SDK統合（リアルタイム音声認識、Partial結果対応）
- [x] WebSocket疎通確認

### Phase 2: 意図推定ロジック ✅
- [x] OpenAI GPT-4o-mini による意図推定
- [x] Meaning State JSON形式でのレスポンス
  - dialogue_act, intent_label, slots, key_terms, confidence, full_translation
- [x] デバウンス処理（300ms間隔でLLM呼び出し最適化）
- [x] 会話コンテキスト保持（過去3発話を参照）
- [x] 会話サマリー機能（1.5秒の沈黙後に自動生成）

### Phase 3: UI/UX ブラッシュアップ ✅
- [x] UIリニューアル（2カラムレイアウト: 意図 | 翻訳）
- [x] リアルタイム履歴表示（意図・翻訳・サマリーすべて履歴化）
- [x] サマリーを画面下部に固定表示（常に見える位置）
- [x] 自動スクロール機能（最新が常に表示）

### レイテンシ最適化 ✅
- [x] OpenAI Streaming API導入
- [x] intent_label先行表示（約500ms）
- [x] **翻訳のストリーミング先行表示**（JSON出力順序変更で実現）
  - intent_label → full_translation を最初に出力
  - 翻訳表示: ほぼリアルタイム（従来の約2秒 → 約500-800ms）
- [x] キーワード予測レイヤー（LLM不要の即時ヒント表示）
- [x] 非ブロッキングLLM呼び出し（Deepgram keepalive問題解決）

### 2025-01-20 セッション ✅
- [x] **is_final時のプロンプト切り替え（案D）**
  - partial（進行中）: SPEED プロンプト（dialogue_act → intent_label → slots → full_translation）
  - is_final（確定時）: QUALITY プロンプト（文脈分析重視）
- [x] **マルチモデル対応**
  - GPT-4o-mini / Gemini 2.5 Flash Lite 切り替えUI
  - サマリー生成も選択モデルを使用
- [x] **HYBRID / FINAL_ONLY モード切り替え**
  - HYBRID: partial + final 両方処理（速度重視）
  - FINAL_ONLY: is_final のみ処理（品質重視）
- [x] **統合タイムラインUI**
  - 意図ラベル + 翻訳を1つのカードにまとめて表示
  - partial → final の上書き表示
- [x] **短文スキップ機能**
  - min_words=5 で断片的なpartialをスキップ（ノイズ軽減）
- [x] **推測翻訳（Speculative Translation）実験機能**
  - `/experimental` ルート追加
  - dialogue_act + intent_label から推測訳を先行生成
  - 推測訳は確定訳より約500ms〜1秒早く表示
  - 「別々表示」「上書き表示」の切り替えUI

### 2025-01-24 セッション ✅
- [x] **ローカルLLM (Gemma 3 4B) 検証成功**
  - LM Studio (Windows) + GPU (RTX 3060 6GB) で動作確認
  - 応答速度: **230ms〜650ms**（API同等以上）
  - 翻訳品質: 高い（自然な日本語）
  - コスト: **無料**
  - 接続先: `http://192.168.0.183:1234` (OpenAI互換API)

### 2025-01-25 セッション ✅
- [x] **ローカルLLM対応を試行**
  - LM Studio: 並列リクエストでクラッシュ多発（セマフォ/ロックでも解決せず）
  - Ollama: 同様にGPUリソース枯渇でWindows強制シャットダウン
  - **結論**: RTX 3060 6GBでは並列リアルタイム翻訳に不向き
- [x] **Groq API対応を追加**
  - 無料枠: 30 req/min, 最大500k tokens/day
  - 超高速推論（800+ tokens/sec）
  - OpenAI互換APIで実装
- [x] **5モデル選択UI**
  - GPT-4o-mini / Gemini 2.5 Flash / Gemma 3 (Ollama) / LM Studio / Groq
  - 通常版・実験版の両方で切り替え可能
- [x] **Groqサブモデル選択（4モデル）**
  - Llama 4 Maverick 17B (最速: 413ms)
  - Llama 3.3 70B (推奨: 480ms)
  - Llama 3.1 8B (軽量: 377ms)
  - GPT-OSS 120B (高品質: 662ms)
- [x] **全モデル速度・品質比較検証**
  - Groq全11モデルをテスト
  - **最速**: Llama 4 Maverick (413ms)
  - **最高品質**: GPT-OSS 120B, Llama 3.3 70B
- [x] **API料金比較**
  | プロバイダー | 5時間コスト | 速度 |
  |-------------|------------|------|
  | Gemini 2.0 Flash | $1.17 (175円) | 954ms |
  | OpenAI GPT-4o-mini | $1.74 (261円) | 1976ms |
  | Groq Llama 4 Maverick | $3.43 (515円) | **463ms** |
  - **結論**: Groqは高いが最速。リアルタイム向き

### 2025-02-01 セッション ✅
- [x] **LM Studio 0.4.0 新機能検証**
  - 新コア「llmster」（ヘッドレスモード対応）
  - 新CLI「lms」コマンド（`lms daemon up`, `lms server start`）
  - 並列リクエスト対応（`Max Concurrent Predictions`設定）
  - OpenAI/Anthropic互換API、MCP対応

- [x] **LM Studioオプションをバックエンドに追加**
  - `lmstudio` モデルオプション追加（UIに紫色ボタン）
  - `_call_lmstudio()`, `_generate_summary_lmstudio()` 実装
  - 環境変数: `LMSTUDIO_BASE_URL`, `LMSTUDIO_MODEL`

- [x] **並列リクエスト問題への対策**
  - LM Studio側: `Max Concurrent Predictions = 1` → クラッシュ後リセットされる問題
  - バックエンド側: `lmstudio_lock` でリクエストを直列化
  - `FINAL_ONLY`モードを強制（partial時はスキップ、is_final時はロック待ち）

- [x] **ヘッドレスモード検証**
  - GUIを閉じてCLIのみで動作確認
  - `lms server start --port 1234 --bind 0.0.0.0 --cors`
  - ネットワーク経由でアクセス可能に

- [x] **モバイルでのローカルLLM調査**
  - MLC Chat, Google AI Edge Gallery, SmolChat などが存在
  - Snapdragon 8 Gen 2 で 1Bモデル: 12-16 tokens/sec
  - **結論**: リアルタイム翻訳には不向き（500ms以内は無理）

- **最終結論: LM Studio + RTX 3060 6GB**
  | 項目 | 結果 |
  |------|------|
  | 翻訳 | △ 動作するが3-4秒遅延 |
  | サマリー | ✗ ロック競合でほぼ実行されない |
  | リアルタイム性 | ✗ 程遠い |
  | ヘッドレスモード | GUIとほぼ同等の速度 |

  **根本的な制約**:
  - VRAM 6GB では並列処理不可
  - ロックで直列化すると翻訳だけで手一杯
  - リアルタイム翻訳には **Groq** か **Gemini** が現実的
