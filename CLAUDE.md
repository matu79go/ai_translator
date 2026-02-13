# Project: Intent-First Translation MVP (PC Edition)

## 1. 開発目的

音声翻訳における「タイムラグによる会話の断絶」を、逐次翻訳（確定後の表示）ではなく、LLMによる「意図の先行表示（Intent-First）」によって解決できるか検証するためのPC向けプロトタイプ。

## 2. 技術スタック

- **Frontend**: React (Vite) + Tailwind CSS + Lucide React (Icons)
  
  - 役割: リアルタイムに更新される「意図」「スロット」「翻訳」の可視化。
- **Backend**: Python (FastAPI) + WebSockets
  
  - 役割: 音声ストリームの受信、ASR/LLMとの連携、ステート管理。
- **ASR (Speech-to-Text)**: Deepgram SDK (Streaming)
  
  - 理由: `is_final: false` (Partial結果) の返却速度が極めて速いため。
- **LLM**: Gemini 2.5 Flash (Google AI Studio API)
  
  - 理由: 推論速度が非常に速く、構造化出力（JSON）に優れているため。
- **Audio Capture**: Browser MediaDevices API (マイク入力)
  

## 3. システムアーキテクチャ

1. [Frontend] ブラウザマイクから音声をキャプチャし、WebSocketでBackendへ送信。
  
2. [Backend] 音声バイナリをDeepgramへ転送し、Partialなテキスト結果をリアルタイム受信。
  
3. [Backend] テキストが更新されるたびに、LLMへ「現在の意図」を推論させる。
  
4. [Backend] LLMの解析結果（JSON）をFrontendへWebSocketで返却。
  
5. [Frontend] 受信したJSONに基づき、UI（意図・要旨・翻訳）を即座に更新。
  

## 4. データ構造 (Meaning State JSON)

LLMから以下の形式でレスポンスを得ることを目標とする。

```
{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | OTHER",
  "intent_label": "短い日本語ラベル (例: 日程調整の打診)",
  "slots": {
    "when": "Tuesday afternoon",
    "who": "Project team",
    "where": "Zoom"
  },
  "key_terms": ["Next week", "Budget", "Urgent"],
  "confidence": 0.0 to 1.0,
  "is_meaning_stable": boolean,
  "full_translation": "最終的な日本語訳（文が確定した場合のみ）"
}
```

## 5. 実装フェーズ

### Phase 1: 基盤構築 (1-2日)

- FastAPIとReactのWebSocket疎通確認。
  
- Deepgramを導入し、ブラウザ経由の音声がリアルタイムにテキスト化（Partial出力）されることを確認。
  

### Phase 2: 意図推定ロジックの実装 (2-3日)

- Deepgramから届く断片的なテキストをGemini APIに投げ、上記JSONを生成するプロンプトを構築。
  
- **重要:** 500ms~1000ms間隔、またはテキストに変化があったタイミングでLLMを呼び出す最適化。
  
- 以前の文脈（Context）を保持し、会話の流れを考慮した意図抽出。
  

### Phase 3: UI/UX ブラッシュアップ (1-2日)

- 「確定していない情報」は薄く、またはアニメーションさせて表示する。
  
- 意図がひっくり返った際の視覚的なフィードバック（チラつきの抑制）。
  
- ログ出力機能（ASR速度、LLM速度、表示までの時間）の実装。
  

## 6. プロンプト定義 (System Instruction)

```
あなたはリアルタイム対話支援AIです。
入力される英語（進行中の断片的なテキスト）から、話者の「意図」と「重要な単語」を即座に抽出してください。
- 完璧な文章になるのを待たず、今ある情報から予測してください。
- 出力は必ず指定されたJSON形式で行ってください。
- intent_labelは日本語で10文字以内。
- 確信度が低い場合は confidence を低く設定してください。
- まだ情報が不十分な場合は slots は空にして構いません。
```

## 7. 実装メモ（重要）

### 翻訳速度最適化の仕組み
LLMはストリーミングでJSONを**上から順番に**生成する。そのため、`full_translation`をJSON内で早い位置に配置すると、翻訳が早く取得できる。

**現在のプロンプト（速度重視）:**
```json
{
  "intent_label": "...",      // 1番目 → 即送信
  "full_translation": "...",  // 2番目 → 即送信（速い！）
  "dialogue_act": "...",
  ...
}
```

**従来のプロンプト（品質重視）:**
```json
{
  "dialogue_act": "...",      // 文脈を先に分析
  "intent_label": "...",
  "slots": {...},
  "key_terms": [...],
  "confidence": ...,
  "is_meaning_stable": ...,
  "full_translation": "..."   // 最後に翻訳（文脈考慮済み）
}
```

### 次回実装タスク: is_finalフラグによるプロンプト切り替え
- **実装箇所**: `backend/main.py` の `IntentEstimator._call_llm_streaming()`
- **方針**:
  - `is_final=False`（進行中）: 現在のプロンプト（速度重視）
  - `is_final=True`（確定時）: 従来のプロンプト（品質重視）
- **目的**: 速度を維持しつつ、確定時は正確な翻訳を表示

## 8. 開発ログ

開発の経緯・意思決定・実験結果の詳細は **[docs/dev-log.md](docs/dev-log.md)** を参照。

## 9. 評価指標

- **Latency (T1)**: 発話開始から「意図ラベル」が画面に出るまでの秒数。

- **Stability**: 会話が進む中で、意図ラベルが不自然に激しく変化しないか。

- **Perceived Delay**: 逐次翻訳のみの場合と比較して、会話のテンポが改善されたか。

## 10. 起動方法

### ローカル開発（PC）

2つのターミナルで起動:

```bash
# ターミナル1: バックエンド
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# ターミナル2: フロントエンド（開発サーバー）
cd frontend
npm run dev
```

アクセス: http://localhost:5173

### 携帯テスト（ngrok経由）

ngrokを使って外部からアクセス可能にする。**HTTPSが自動提供されるためマイクが使える。**

```bash
# 1. フロントエンドをビルド（コード変更時のみ）
cd frontend
npm run build

# 2. バックエンド起動（ビルド済みフロントエンドも配信）
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 3. 別ターミナルでngrok起動
ngrok http 8000
```

表示されたURL（例: `https://xxxx.ngrok-free.app`）を携帯ブラウザで開く。

**注意:**
- ngrok無料版はURLが毎回変わる
- 使い終わったら `Ctrl+C` でngrokを停止（セキュリティのため）
- バックエンドとngrok両方が起動している必要がある

### 環境変数

`backend/.env` に設定:
```
DEEPGRAM_API_KEY=your_deepgram_key
OPENAI_API_KEY=your_openai_key
GOOGLE_API_KEY=your_google_api_key
```