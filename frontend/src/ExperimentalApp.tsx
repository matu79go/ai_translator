import { useCallback, useState, useRef, useEffect } from 'react';
import { AudioRecorder } from './components/AudioRecorder';
import { useWebSocket } from './hooks/useWebSocket';
import type { WebSocketMessage, IntentData } from './hooks/useWebSocket';
import { Link } from 'react-router-dom';

// 表示モード
type DisplayMode = 'separate' | 'unified';

// 推測翻訳エントリ
interface SpeculativeEntry {
  id: number;
  intentLabel: string;
  dialogueAct: string;
  speculativeTranslation: string | null;  // 推測訳（早い）
  finalTranslation: string | null;         // 確定訳（正確）
  isFinal: boolean;
  timestamp: Date;
}

const DIALOGUE_ACT_LABELS: Record<string, { label: string; color: string }> = {
  QUESTION: { label: '質問', color: 'bg-blue-500' },
  PROPOSAL: { label: '提案', color: 'bg-green-500' },
  AGREEMENT: { label: '同意', color: 'bg-emerald-500' },
  DISAGREEMENT: { label: '反対', color: 'bg-red-500' },
  STATEMENT: { label: '発言', color: 'bg-gray-500' },
  GREETING: { label: '挨拶', color: 'bg-purple-500' },
  OTHER: { label: 'その他', color: 'bg-gray-400' },
};

// LLMモデル型
type ModelType = 'gpt4' | 'gemini' | 'gemma' | 'groq' | 'lmstudio';
const MODEL_OPTIONS: { value: ModelType; label: string; color: string }[] = [
  { value: 'gpt4', label: 'GPT-4', color: 'bg-emerald-600' },
  { value: 'gemini', label: 'Gemini', color: 'bg-blue-600' },
  { value: 'gemma', label: 'Gemma 3 (Ollama)', color: 'bg-orange-600' },
  { value: 'lmstudio', label: 'LM Studio', color: 'bg-purple-600' },
  { value: 'groq', label: 'Groq', color: 'bg-pink-600' },
];

// Groqサブモデル
type GroqModelType = 'llama-3.1-8b-instant' | 'llama-3.3-70b-versatile' | 'meta-llama/llama-4-maverick-17b-128e-instruct' | 'openai/gpt-oss-120b';
const GROQ_MODEL_OPTIONS: { value: GroqModelType; label: string }[] = [
  { value: 'meta-llama/llama-4-maverick-17b-128e-instruct', label: 'Llama 4 Maverick (最速)' },
  { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B (推奨)' },
  { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B' },
  { value: 'openai/gpt-oss-120b', label: 'GPT-OSS 120B (高品質)' },
];

function ExperimentalApp() {
  const [currentTranscript, setCurrentTranscript] = useState<string>('');
  const [timeline, setTimeline] = useState<SpeculativeEntry[]>([]);
  const timelineEndRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string>('未接続');
  const [displayMode, setDisplayMode] = useState<DisplayMode>('separate');
  // LLMモデル切り替え（5モデル対応）
  const [model, setModel] = useState<ModelType>('gpt4');
  // Groqサブモデル
  const [groqModel, setGroqModel] = useState<GroqModelType>('meta-llama/llama-4-maverick-17b-128e-instruct');
  // サマリー関連
  const [summary, setSummary] = useState<string>('');
  const [summaryHistory, setSummaryHistory] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const summaryEndRef = useRef<HTMLDivElement>(null);
  // TTS（翻訳読み上げ）
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const ttsEnabledRef = useRef(ttsEnabled);
  useEffect(() => { ttsEnabledRef.current = ttsEnabled; }, [ttsEnabled]);

  const isMobile = /iPhone|iPad|Android/i.test(navigator.userAgent);
  const ttsRate = isMobile ? 1.3 : 3.0;
  const lastSpokenRef = useRef<string>('');

  const speakTranslation = useCallback((text: string) => {
    if (!ttsEnabledRef.current) return;
    // 同じ翻訳の重複読み上げを防止
    if (lastSpokenRef.current === text) return;
    lastSpokenRef.current = text;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'ja-JP';
    utterance.rate = ttsRate;
    window.speechSynthesis.speak(utterance);
  }, [ttsRate]);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'transcript': {
        if (message.is_final) {
          setCurrentTranscript('');
        } else {
          setCurrentTranscript(message.transcript);
        }
        break;
      }
      case 'intent_partial': {
        // 意図ラベルを先に表示
        setTimeline((prev) => {
          const lastPartialIndex = prev.findLastIndex((e) => !e.isFinal);
          if (lastPartialIndex !== -1) {
            const newList = [...prev];
            newList[lastPartialIndex] = {
              ...newList[lastPartialIndex],
              intentLabel: message.intent_label,
            };
            return newList;
          }
          return [...prev, {
            id: Date.now(),
            intentLabel: message.intent_label,
            dialogueAct: 'OTHER',
            speculativeTranslation: null,
            finalTranslation: null,
            isFinal: false,
            timestamp: new Date(),
          }];
        });
        break;
      }
      case 'speculative_translation': {
        // 推測訳が来た！（早い段階で）
        setTimeline((prev) => {
          const lastPartialIndex = prev.findLastIndex((e: SpeculativeEntry) => !e.isFinal);
          if (lastPartialIndex !== -1) {
            const newList = [...prev];
            newList[lastPartialIndex] = {
              ...newList[lastPartialIndex],
              speculativeTranslation: message.translation,
            };
            return newList;
          }
          return [...prev, {
            id: Date.now(),
            intentLabel: '',
            dialogueAct: 'OTHER',
            speculativeTranslation: message.translation,
            finalTranslation: null,
            isFinal: false,
            timestamp: new Date(),
          }];
        });
        break;
      }
      case 'intent': {
        // 完全な結果
        const data = message.data as IntentData & { speculative_translation?: string };
        // TTS: 翻訳を読み上げ（重複はspeakTranslation内で防止）
        if (data.full_translation) {
          speakTranslation(data.full_translation);
        }
        setTimeline((prev) => {
          const lastPartialIndex = prev.findLastIndex((e) => !e.isFinal);
          // 既存の推測訳を保持（上書きしない）
          const existingSpeculative = lastPartialIndex !== -1
            ? prev[lastPartialIndex].speculativeTranslation
            : null;
          const newEntry: SpeculativeEntry = {
            id: Date.now(),
            intentLabel: data.intent_label || '',
            dialogueAct: data.dialogue_act || 'OTHER',
            // 優先順位: 既存の推測訳 > JSONの推測訳 > null
            speculativeTranslation: existingSpeculative || data.speculative_translation || null,
            finalTranslation: data.full_translation || null,
            isFinal: message.is_final,
            timestamp: new Date(),
          };

          if (lastPartialIndex !== -1) {
            const newList = [...prev];
            newList[lastPartialIndex] = newEntry;
            return newList;
          }
          return [...prev, newEntry];
        });
        break;
      }
      case 'summary': {
        setSummary(message.summary);
        setTopics(message.topics);
        setIsStreaming(message.streaming || false);
        if (!message.streaming && message.summary) {
          setSummaryHistory((prev) => {
            const lastSummary = prev[prev.length - 1];
            if (lastSummary !== message.summary) {
              return [...prev, message.summary];
            }
            return prev;
          });
        }
        break;
      }
      case 'status':
        setStatus(message.message);
        break;
      case 'error':
        setStatus(`エラー: ${message.message}`);
        break;
    }
  }, [speakTranslation]);

  const handleOpen = useCallback(() => {
    setStatus('接続中...');
  }, []);

  const handleClose = useCallback(() => {
    setStatus('切断されました');
    setCurrentTranscript('');
  }, []);

  const handleError = useCallback(() => {
    setStatus('接続エラー');
  }, []);

  // WebSocket URL: httpsならwss、httpならws
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsHost = import.meta.env.MODE === 'development'
    ? `${window.location.hostname}:8000`
    : window.location.host;
  const wsUrl = `${wsProtocol}//${wsHost}/ws/audio`;

  const { isConnected, connect, disconnect, sendAudio, sendControl } = useWebSocket({
    url: wsUrl,
    onMessage: handleMessage,
    onOpen: handleOpen,
    onClose: handleClose,
    onError: handleError,
  });

  // モデル選択
  const selectModel = useCallback((newModel: ModelType) => {
    setModel(newModel);
    sendControl({ type: 'set_model', model: newModel });
  }, [sendControl]);

  // Groqサブモデル選択
  const selectGroqModel = useCallback((newGroqModel: GroqModelType) => {
    setGroqModel(newGroqModel);
    sendControl({ type: 'set_groq_model', groq_model: newGroqModel });
  }, [sendControl]);

  // 接続時に実験モードを有効化 + モデル設定を同期
  useEffect(() => {
    if (isConnected) {
      sendControl({ type: 'set_experimental', enabled: true });
      if (model !== 'gpt4') {
        sendControl({ type: 'set_model', model });
      }
      if (model === 'groq') {
        sendControl({ type: 'set_groq_model', groq_model: groqModel });
      }
    }
  }, [isConnected, sendControl, model, groqModel]);

  const handleAudioData = useCallback(
    (data: ArrayBuffer) => {
      sendAudio(data);
    },
    [sendAudio]
  );

  const handleStart = useCallback(() => {
    connect();
  }, [connect]);

  const handleStop = useCallback(() => {
    disconnect();
  }, [disconnect]);

  const clearAll = useCallback(() => {
    setTimeline([]);
    setCurrentTranscript('');
    setSummary('');
    setSummaryHistory([]);
    setTopics([]);
  }, []);

  // タイムラインの自動スクロール
  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [timeline]);

  // サマリー履歴の自動スクロール
  useEffect(() => {
    summaryEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [summaryHistory]);

  // 上書きモード用: 表示する翻訳を決定
  const getDisplayTranslation = (entry: SpeculativeEntry) => {
    // 確定訳があればそれを使用、なければ推測訳
    return entry.finalTranslation || entry.speculativeTranslation;
  };

  // 上書きモード用: 翻訳の状態を判定
  const getTranslationStatus = (entry: SpeculativeEntry): 'speculative' | 'final' | 'none' => {
    if (entry.finalTranslation) return 'final';
    if (entry.speculativeTranslation) return 'speculative';
    return 'none';
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 to-red-50 py-8 px-4">
      <div className="max-w-5xl mx-auto">
        <header className="text-center mb-6">
          <h1 className="text-3xl font-bold text-orange-800 mb-2">
            Intent-First Translation
          </h1>
          {/* ページ切り替えタブ */}
          <div className="flex justify-center gap-1 mb-4">
            <Link
              to="/"
              className="px-4 py-2 rounded-t-lg text-sm font-medium bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors"
            >
              通常版
            </Link>
            <Link
              to="/experimental"
              className="px-4 py-2 rounded-t-lg text-sm font-medium bg-white text-orange-800 border-b-2 border-orange-500"
            >
              実験版（推測翻訳）
            </Link>
          </div>
          <p className="text-orange-600 text-sm mb-2">
            意図(dialogue_act + intent_label)から翻訳を先行推測する実験
          </p>

          {/* モデル選択（4択） */}
          <div className="flex justify-center gap-1 mt-3 mb-2">
            {MODEL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => selectModel(opt.value)}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  model === opt.value
                    ? `${opt.color} text-white`
                    : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Groqサブモデル選択（Groq選択時のみ表示） */}
          {model === 'groq' && (
            <div className="flex justify-center items-center gap-2 mt-2">
              <span className="text-sm text-gray-600">Groqモデル:</span>
              <select
                value={groqModel}
                onChange={(e) => selectGroqModel(e.target.value as GroqModelType)}
                className="px-3 py-1.5 rounded-lg text-sm border border-pink-300 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-pink-500"
              >
                {GROQ_MODEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* 表示モード切り替え + TTS */}
          <div className="flex justify-center gap-2 mt-2 mb-2">
            <button
              onClick={() => setDisplayMode('separate')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                displayMode === 'separate'
                  ? 'bg-orange-600 text-white'
                  : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
              }`}
            >
              別々表示
            </button>
            <button
              onClick={() => setDisplayMode('unified')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                displayMode === 'unified'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
              }`}
            >
              上書き表示
            </button>
            <button
              onClick={() => {
                if (ttsEnabled) {
                  window.speechSynthesis.cancel();
                } else {
                  // モバイルブラウザ対策: ユーザータップで無音発話してロック解除
                  const unlock = new SpeechSynthesisUtterance('');
                  unlock.volume = 0;
                  window.speechSynthesis.speak(unlock);
                }
                setTtsEnabled(!ttsEnabled);
              }}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-90 ${
                ttsEnabled
                  ? 'bg-amber-500 text-white'
                  : 'bg-gray-300 text-gray-600'
              }`}
            >
              {ttsEnabled ? '🔊 TTS ON' : '🔇 TTS OFF'}
            </button>
          </div>
          <div className="mt-2 text-sm text-gray-500">{status}</div>
        </header>

        <div className="bg-white rounded-xl shadow-lg p-6 mb-6">
          <AudioRecorder
            isConnected={isConnected}
            onAudioData={handleAudioData}
            onStart={handleStart}
            onStop={handleStop}
          />
        </div>

        {/* 現在の認識テキスト */}
        {currentTranscript && (
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-4">
            <p className="text-blue-800 italic">{currentTranscript}</p>
          </div>
        )}

        {/* 推測翻訳タイムライン */}
        <div className="bg-white rounded-xl shadow-lg p-6 mb-6">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-gray-700">
              {displayMode === 'separate' ? '推測 vs 確定' : 'リアルタイム翻訳（推測→確定）'}
            </h2>
            {timeline.length > 0 && (
              <button
                onClick={clearAll}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                クリア
              </button>
            )}
          </div>
          <div className="max-h-[500px] overflow-y-auto space-y-4">
            {timeline.length === 0 ? (
              <p className="text-gray-400 text-center py-8">
                {displayMode === 'separate'
                  ? '話し始めると、推測翻訳と確定翻訳が比較表示されます'
                  : '話し始めると、推測訳が先に表示され、確定訳で上書きされます'
                }
              </p>
            ) : (
              <>
                {timeline.map((entry, i) => {
                  const actInfo = DIALOGUE_ACT_LABELS[entry.dialogueAct] || DIALOGUE_ACT_LABELS.OTHER;
                  const isLatest = i === timeline.length - 1;

                  if (displayMode === 'unified') {
                    // 上書き表示モード
                    const translation = getDisplayTranslation(entry);
                    const translationStatus = getTranslationStatus(entry);

                    return (
                      <div
                        key={entry.id}
                        className={`p-4 rounded-lg border-2 ${
                          isLatest
                            ? translationStatus === 'final'
                              ? 'border-green-400 bg-green-50'
                              : 'border-orange-400 bg-orange-50'
                            : 'border-gray-200 bg-gray-50'
                        }`}
                      >
                        {/* 意図ラベル行 */}
                        <div className="flex items-center gap-2 mb-2">
                          <span className={`px-2 py-0.5 rounded text-white text-xs ${actInfo.color}`}>
                            {actInfo.label}
                          </span>
                          <span className="text-gray-700 font-medium">{entry.intentLabel}</span>
                          {translationStatus === 'speculative' && (
                            <span className="text-xs text-orange-500 bg-orange-100 px-2 py-0.5 rounded animate-pulse">
                              推測中
                            </span>
                          )}
                          {translationStatus === 'final' && (
                            <span className="text-xs text-green-600 bg-green-100 px-2 py-0.5 rounded">
                              確定
                            </span>
                          )}
                        </div>

                        {/* 翻訳（1つだけ表示） */}
                        {translation ? (
                          <p className={`text-lg pl-1 ${
                            translationStatus === 'final' ? 'text-green-800' : 'text-orange-800'
                          }`}>
                            → {translation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-1 animate-pulse">
                            → 翻訳中...
                          </p>
                        )}
                      </div>
                    );
                  }

                  // 別々表示モード（従来の表示）
                  return (
                    <div
                      key={entry.id}
                      className={`p-4 rounded-lg border-2 ${
                        isLatest
                          ? entry.isFinal
                            ? 'border-green-400 bg-green-50'
                            : 'border-orange-400 bg-orange-50'
                          : 'border-gray-200 bg-gray-50'
                      }`}
                    >
                      {/* 意図ラベル行 */}
                      <div className="flex items-center gap-2 mb-3">
                        <span className={`px-2 py-0.5 rounded text-white text-xs ${actInfo.color}`}>
                          {actInfo.label}
                        </span>
                        <span className="text-gray-700 font-medium">{entry.intentLabel}</span>
                        {!entry.isFinal && <span className="text-xs text-orange-500 animate-pulse">推測中...</span>}
                      </div>

                      {/* 推測翻訳（早い） */}
                      <div className="mb-2">
                        <span className="text-xs text-orange-600 font-medium">推測訳:</span>
                        {entry.speculativeTranslation ? (
                          <p className="text-orange-800 text-lg pl-2">
                            {entry.speculativeTranslation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-2 animate-pulse">
                            推測中...
                          </p>
                        )}
                      </div>

                      {/* 確定翻訳（正確） */}
                      <div>
                        <span className="text-xs text-green-600 font-medium">確定訳:</span>
                        {entry.finalTranslation ? (
                          <p className="text-green-800 text-lg pl-2">
                            {entry.finalTranslation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-2">
                            {entry.isFinal ? '(なし)' : '確定待ち...'}
                          </p>
                        )}
                      </div>

                      {/* 比較: 推測と確定が違う場合にハイライト */}
                      {entry.speculativeTranslation && entry.finalTranslation &&
                       entry.speculativeTranslation !== entry.finalTranslation && (
                        <div className="mt-2 text-xs text-red-500 bg-red-50 p-2 rounded">
                          ⚠ 推測と確定が異なります
                        </div>
                      )}
                    </div>
                  );
                })}
                <div ref={timelineEndRef} />
              </>
            )}
          </div>
        </div>

        {/* モード説明 */}
        <div className="bg-white/50 rounded-lg p-4 mb-6">
          <p className="text-sm text-gray-600">
            {displayMode === 'separate' ? (
              <>
                <strong>別々表示:</strong> 推測訳と確定訳を並べて表示。精度比較に最適。
              </>
            ) : (
              <>
                <strong>上書き表示:</strong> 推測訳を先に表示し、確定訳で上書き。最速体感用。
                約500ms〜1秒早く翻訳が表示されます。
              </>
            )}
          </p>
        </div>

        <footer className="mt-8 text-center text-sm text-gray-500 pb-24">
          <p>Experimental: Speculative Translation</p>
        </footer>
      </div>

      {/* 会話サマリー - 画面下部に固定 */}
      {(summaryHistory.length > 0 || summary) && (
        <div className="fixed bottom-0 left-0 right-0 bg-gradient-to-r from-indigo-500 to-purple-600 shadow-lg text-white z-50">
          <div className="max-w-5xl mx-auto p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-semibold">サマリー</span>
              {(model === 'gemma' || model === 'groq') && <span className="text-xs bg-white/30 px-2 py-0.5 rounded">GPT-4で生成</span>}
              {isStreaming && <span className="text-xs bg-white/20 px-2 py-0.5 rounded animate-pulse">更新中...</span>}
              {topics.length > 0 && (
                <div className="flex flex-wrap gap-1 ml-auto">
                  {topics.map((topic, i) => (
                    <span key={i} className="px-2 py-0.5 bg-white/20 rounded text-xs">
                      {topic}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="max-h-[120px] overflow-y-auto space-y-2">
              {summaryHistory.map((s, i) => (
                <p
                  key={i}
                  className={`text-sm p-2 rounded ${
                    i === summaryHistory.length - 1
                      ? 'bg-white/20 text-white'
                      : 'bg-white/10 text-white/70'
                  }`}
                >
                  {s}
                </p>
              ))}
              <div ref={summaryEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ExperimentalApp;
