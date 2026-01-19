# Intent-First Translation MVP - 進捗状況

## 完了した作業

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

## 現在の機能

| 機能 | 説明 | レイテンシ |
|------|------|-----------|
| 意図履歴 | 話者の意図をリアルタイム表示・蓄積 | ~500ms |
| 翻訳履歴 | リアルタイム翻訳を蓄積表示 | ~500-800ms |
| 会話サマリー | 全体の要約（画面下固定） | 沈黙1.5秒後 |
| トランスクリプト | 音声認識結果 | ~200ms |
| 予測ヒント | キーワードベースの即時予測 | ~0ms |

## 未完了の作業

### 翻訳品質の向上（次の課題）
- [ ] 速度と正確さのバランス調整
  - 案1: 2段階翻訳（速い翻訳 → 確定後に高品質翻訳で上書き）
  - 案2: プロンプト改善（翻訳品質を重視する指示追加）
  - 案3: Gemini 2.5 Flash への切り替え（速度維持で品質向上）
  - 案4: is_final時のみ品質重視の翻訳

### 追加改善案
- [ ] Gemini 2.5 Flash への切り替え検討
- [ ] 日本語→英語の双方向対応
- [ ] 話者識別（複数人会話対応）
- [ ] オフライン対応（ローカルLLM検討）
- [ ] ログ出力機能（ASR速度、LLM速度、表示までの時間）の可視化
- [ ] モバイル対応（レスポンシブデザイン）

## 技術スタック

### Backend
- Python 3.x
- FastAPI + WebSockets
- Deepgram SDK (nova-2モデル)
- OpenAI API (gpt-4o-mini, Streaming)

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
