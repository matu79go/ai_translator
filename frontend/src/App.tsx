import { useCallback, useState, useRef, useEffect } from 'react';
import { AudioRecorder } from './components/AudioRecorder';
import { useWebSocket } from './hooks/useWebSocket';
import type { WebSocketMessage, IntentData } from './hooks/useWebSocket';

interface TranscriptEntry {
  id: number;
  text: string;
  isFinal: boolean;
  confidence: number;
  timestamp: Date;
}

interface IntentHistoryEntry {
  id: number;
  intent: IntentData;
  sourceText: string;
  timestamp: Date;
}

interface TranslationHistoryEntry {
  id: number;
  sourceText: string;
  translation: string;
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

// 統合タイムラインエントリ
interface TimelineEntry {
  id: number;
  intentLabel: string;
  dialogueAct: string;
  translation: string | null;  // 翻訳が来るまでnull
  isFinal: boolean;
  timestamp: Date;
}

function App() {
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState<string>('');
  const [currentIntent, setCurrentIntent] = useState<IntentData | null>(null);
  const [partialIntentLabel, setPartialIntentLabel] = useState<string>('');
  const [intentHistory, setIntentHistory] = useState<IntentHistoryEntry[]>([]);
  const [translationHistory, setTranslationHistory] = useState<TranslationHistoryEntry[]>([]);
  const [realtimeTranslations, setRealtimeTranslations] = useState<{sourceText: string; translation: string; isFinal: boolean}[]>([]);
  const [realtimeIntents, setRealtimeIntents] = useState<{label: string; act: string}[]>([]);
  // 統合タイムライン
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const timelineEndRef = useRef<HTMLDivElement>(null);
  const translationsEndRef = useRef<HTMLDivElement>(null);
  const intentsEndRef = useRef<HTMLDivElement>(null);
  const [summary, setSummary] = useState<string>('');
  const [summaryHistory, setSummaryHistory] = useState<string[]>([]);
  const summaryEndRef = useRef<HTMLDivElement>(null);
  const [topics, setTopics] = useState<string[]>([]);
  const [hints, setHints] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [status, setStatus] = useState<string>('未接続');

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'transcript': {
        if (message.is_final) {
          setTranscripts((prev) => [
            ...prev,
            {
              id: Date.now(),
              text: message.transcript,
              isFinal: true,
              confidence: message.confidence,
              timestamp: new Date(),
            },
          ]);
          setCurrentTranscript('');
        } else {
          setCurrentTranscript(message.transcript);
        }
        break;
      }
      case 'hints': {
        // 即座にヒントを表示
        setHints(message.hints);
        break;
      }
      case 'intent_partial': {
        // 部分的な意図ラベルを即座に表示
        setPartialIntentLabel(message.intent_label);
        // タイムラインに意図を追加（翻訳はまだnull）
        setTimeline((prev) => {
          const lastEntry = prev[prev.length - 1];
          // 同じ意図ラベルなら更新しない
          if (lastEntry?.intentLabel === message.intent_label && !lastEntry?.isFinal) {
            return prev;
          }
          // 最新のpartialエントリを上書き、または新規追加
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
            translation: null,
            isFinal: false,
            timestamp: new Date(),
          }];
        });
        break;
      }
      case 'translation_partial': {
        // 部分的な翻訳: 最新のpartialを上書き（1つだけ保持）
        setRealtimeTranslations((prev) => {
          const lastPartialIndex = prev.findLastIndex((entry) => !entry.isFinal);
          if (lastPartialIndex !== -1) {
            // 既存のpartialを上書き
            const newList = [...prev];
            newList[lastPartialIndex] = { sourceText: message.source_text, translation: message.translation, isFinal: false };
            return newList;
          }
          // partialがなければ追加
          return [...prev, { sourceText: message.source_text, translation: message.translation, isFinal: false }];
        });
        break;
      }
      case 'intent': {
        setCurrentIntent(message.data);
        setPartialIntentLabel(''); // 完全な結果が来たらpartialをクリア
        // リアルタイム意図履歴に追加（重複チェック）
        if (message.data.intent_label) {
          setRealtimeIntents((prev) => {
            const lastIntent = prev[prev.length - 1];
            if (lastIntent?.label !== message.data.intent_label) {
              return [...prev, { label: message.data.intent_label, act: message.data.dialogue_act }];
            }
            return prev;
          });
        }
        // タイムラインを更新（意図+翻訳）
        setTimeline((prev) => {
          const lastPartialIndex = prev.findLastIndex((e) => !e.isFinal);
          const newEntry: TimelineEntry = {
            id: Date.now(),
            intentLabel: message.data.intent_label || '',
            dialogueAct: message.data.dialogue_act || 'OTHER',
            translation: message.data.full_translation || null,
            isFinal: message.is_final,
            timestamp: new Date(),
          };

          if (lastPartialIndex !== -1) {
            // 既存のpartialを更新
            const newList = [...prev];
            newList[lastPartialIndex] = newEntry;
            return newList;
          }
          // 新規追加
          return [...prev, newEntry];
        });
        // リアルタイム翻訳履歴に追加/上書き
        if (message.data.full_translation) {
          setRealtimeTranslations((prev) => {
            const lastPartialIndex = prev.findLastIndex((entry) => !entry.isFinal);

            if (message.is_final) {
              // is_final=true: partialを確定翻訳に変換
              if (lastPartialIndex !== -1) {
                const newList = [...prev];
                newList[lastPartialIndex] = {
                  sourceText: message.source_text,
                  translation: message.data.full_translation!,
                  isFinal: true
                };
                return newList;
              }
              // partialがなければ新規追加
              return [...prev, {
                sourceText: message.source_text,
                translation: message.data.full_translation!,
                isFinal: true
              }];
            } else {
              // is_final=false (partial): 既存のpartialを上書き
              if (lastPartialIndex !== -1) {
                const newList = [...prev];
                newList[lastPartialIndex] = {
                  sourceText: message.source_text,
                  translation: message.data.full_translation!,
                  isFinal: false
                };
                return newList;
              }
              // partialがなければ新規追加
              return [...prev, {
                sourceText: message.source_text,
                translation: message.data.full_translation!,
                isFinal: false
              }];
            }
          });
        }
        break;
      }
      case 'summary': {
        setSummary(message.summary);
        setTopics(message.topics);
        setIsStreaming(message.streaming || false);
        // サマリー履歴に追加（重複チェック、streaming完了時のみ）
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
  }, []);

  const handleOpen = useCallback(() => {
    setStatus('接続中...');
  }, []);

  const handleClose = useCallback(() => {
    setStatus('切断されました');
    setCurrentTranscript('');
    setCurrentIntent(null);
    setPartialIntentLabel('');
    setHints([]);
  }, []);

  const handleError = useCallback(() => {
    setStatus('接続エラー');
  }, []);

  // モード切り替え
  const [isFinalOnly, setIsFinalOnly] = useState(false);
  // LLMモデル切り替え
  const [model, setModel] = useState<'gpt4' | 'gemini'>('gpt4');

  const wsUrl =
    import.meta.env.MODE === 'development'
      ? `ws://${window.location.hostname}:8000/ws/audio`
      : `ws://${window.location.host}/ws/audio`;

  const { isConnected, connect, disconnect, sendAudio, sendControl } = useWebSocket({
    url: wsUrl,
    onMessage: handleMessage,
    onOpen: handleOpen,
    onClose: handleClose,
    onError: handleError,
  });

  const toggleMode = useCallback(() => {
    const newMode = !isFinalOnly;
    setIsFinalOnly(newMode);
    sendControl({ type: 'set_mode', is_final_only: newMode });
  }, [isFinalOnly, sendControl]);

  const toggleModel = useCallback(() => {
    const newModel = model === 'gpt4' ? 'gemini' : 'gpt4';
    setModel(newModel);
    sendControl({ type: 'set_model', model: newModel });
  }, [model, sendControl]);

  // 接続時に設定を同期
  useEffect(() => {
    if (isConnected) {
      if (isFinalOnly) {
        sendControl({ type: 'set_mode', is_final_only: true });
      }
      if (model !== 'gpt4') {
        sendControl({ type: 'set_model', model });
      }
    }
  }, [isConnected]);

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
    setTranscripts([]);
    setCurrentTranscript('');
    setCurrentIntent(null);
    setPartialIntentLabel('');
    setIntentHistory([]);
    setTranslationHistory([]);
    setRealtimeTranslations([]);
    setRealtimeIntents([]);
    setTimeline([]);
    setSummary('');
    setSummaryHistory([]);
    setTopics([]);
    setHints([]);
  }, []);

  // タイムラインの自動スクロール
  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [timeline]);

  // 翻訳履歴の自動スクロール
  useEffect(() => {
    translationsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [realtimeTranslations]);

  // 意図履歴の自動スクロール
  useEffect(() => {
    intentsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [realtimeIntents]);

  // サマリー履歴の自動スクロール
  useEffect(() => {
    summaryEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [summaryHistory]);

  const dialogueActInfo = currentIntent
    ? DIALOGUE_ACT_LABELS[currentIntent.dialogue_act] || DIALOGUE_ACT_LABELS.OTHER
    : null;

  // 表示する意図ラベル（partialがあればpartial、なければ完全版）
  const displayIntentLabel = partialIntentLabel || currentIntent?.intent_label || '';

  return (
    <div className="min-h-screen bg-gray-100 py-8 px-4">
      <div className="max-w-5xl mx-auto">
        <header className="text-center mb-6">
          <h1 className="text-3xl font-bold text-gray-800 mb-2">
            Intent-First Translation
          </h1>
          <div className="flex justify-center items-center gap-2 mt-2">
            <button
              onClick={toggleModel}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-90 ${
                model === 'gpt4'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-blue-600 text-white'
              }`}
            >
              {model === 'gpt4' ? 'GPT-4' : 'Gemini'}
            </button>
            <button
              onClick={toggleMode}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-90 ${
                isFinalOnly
                  ? 'bg-purple-600 text-white'
                  : 'bg-green-600 text-white'
              }`}
            >
              {isFinalOnly ? 'FINAL_ONLY' : 'HYBRID'}
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

        {/* 即時ヒント表示 */}
        {hints.length > 0 && (
          <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 mb-4">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-yellow-700 text-sm font-medium">予測:</span>
              {hints.map((hint, i) => (
                <span
                  key={i}
                  className="px-3 py-1 bg-yellow-200 text-yellow-800 rounded-full text-sm font-medium animate-pulse"
                >
                  {hint}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 統合タイムライン */}
        <div className="bg-white rounded-xl shadow-lg p-6 mb-6">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-gray-700">リアルタイム翻訳</h2>
            {timeline.length > 0 && (
              <button
                onClick={clearAll}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                クリア
              </button>
            )}
          </div>
          <div className="max-h-[400px] overflow-y-auto space-y-3">
            {timeline.length === 0 ? (
              <p className="text-gray-400 text-center py-8">
                話し始めると、意図と翻訳がここに表示されます
              </p>
            ) : (
              <>
                {timeline.map((entry, i) => {
                  const actInfo = DIALOGUE_ACT_LABELS[entry.dialogueAct] || DIALOGUE_ACT_LABELS.OTHER;
                  const isLatest = i === timeline.length - 1;
                  return (
                    <div
                      key={entry.id}
                      className={`p-3 rounded-lg ${
                        isLatest
                          ? entry.isFinal
                            ? 'bg-green-50 border-l-4 border-green-500'
                            : 'bg-blue-50 border-l-4 border-blue-400'
                          : 'bg-gray-50'
                      }`}
                    >
                      {/* 意図ラベル行 */}
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`px-2 py-0.5 rounded text-white text-xs ${actInfo.color}`}>
                          {actInfo.label}
                        </span>
                        <span className="text-gray-700 font-medium">{entry.intentLabel}</span>
                        {!entry.isFinal && <span className="text-xs text-blue-500 animate-pulse">処理中...</span>}
                      </div>
                      {/* 翻訳行 */}
                      {entry.translation ? (
                        <p className="text-gray-800 text-lg pl-1">
                          → {entry.translation}
                        </p>
                      ) : (
                        <p className="text-gray-400 text-sm pl-1 animate-pulse">
                          → 翻訳中...
                        </p>
                      )}
                    </div>
                  );
                })}
                <div ref={timelineEndRef} />
              </>
            )}
          </div>
        </div>

        {/* トランスクリプト */}
        <div className="bg-white rounded-xl shadow-lg p-6">
          <h2 className="text-lg font-semibold text-gray-700 mb-4">トランスクリプト</h2>
          <div className="min-h-[100px] max-h-[200px] overflow-y-auto">
            {transcripts.length === 0 && !currentTranscript ? (
              <p className="text-gray-400 text-center py-4">
                録音を開始すると、ここにテキストが表示されます
              </p>
            ) : (
              <div className="space-y-2">
                {transcripts.map((entry) => (
                  <div key={entry.id} className="p-2 bg-gray-50 rounded">
                    <p className="text-gray-800 text-sm">{entry.text}</p>
                  </div>
                ))}
                {currentTranscript && (
                  <div className="p-2 bg-blue-50 rounded border-l-4 border-blue-400 animate-pulse">
                    <p className="text-gray-700 text-sm italic">{currentTranscript}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <footer className="mt-8 text-center text-sm text-gray-500 pb-24">
          <p>Streaming Mode | FastAPI + Deepgram + OpenAI</p>
        </footer>
      </div>

      {/* 会話サマリー - 画面下部に固定 */}
      {(summaryHistory.length > 0 || summary) && (
        <div className="fixed bottom-0 left-0 right-0 bg-gradient-to-r from-indigo-500 to-purple-600 shadow-lg text-white z-50">
          <div className="max-w-5xl mx-auto p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-semibold">サマリー</span>
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

export default App;
