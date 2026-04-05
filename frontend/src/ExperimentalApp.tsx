import { useCallback, useState, useRef, useEffect } from 'react';
import { AudioRecorder } from './components/AudioRecorder';
import { useWebSocket } from './hooks/useWebSocket';
import type { WebSocketMessage, IntentData } from './hooks/useWebSocket';

// Display mode
type DisplayMode = 'separate' | 'unified';

// Speculative translation entry
interface SpeculativeEntry {
  id: number;
  intentLabel: string;
  dialogueAct: string;
  speculativeTranslation: string | null;  // Speculative (fast)
  finalTranslation: string | null;         // Final (accurate)
  isFinal: boolean;
  timestamp: Date;
}

const DIALOGUE_ACT_LABELS: Record<string, { label: string; color: string }> = {
  QUESTION: { label: 'Question', color: 'bg-blue-500' },
  PROPOSAL: { label: 'Proposal', color: 'bg-green-500' },
  AGREEMENT: { label: 'Agreement', color: 'bg-emerald-500' },
  DISAGREEMENT: { label: 'Disagreement', color: 'bg-red-500' },
  STATEMENT: { label: 'Statement', color: 'bg-gray-500' },
  GREETING: { label: 'Greeting', color: 'bg-purple-500' },
  OTHER: { label: 'Other', color: 'bg-gray-400' },
};

// LLM model type
type ModelType = 'gpt4' | 'gemini' | 'gemma' | 'groq' | 'lmstudio';
const MODEL_OPTIONS: { value: ModelType; label: string; color: string }[] = [
  { value: 'gpt4', label: 'GPT-4', color: 'bg-emerald-600' },
  { value: 'gemini', label: 'Gemini', color: 'bg-blue-600' },
  { value: 'gemma', label: 'Gemma 3 (Ollama)', color: 'bg-orange-600' },
  { value: 'lmstudio', label: 'LM Studio', color: 'bg-purple-600' },
  { value: 'groq', label: 'Groq', color: 'bg-pink-600' },
];

// Groq sub-model
type GroqModelType = 'llama-3.1-8b-instant' | 'llama-3.3-70b-versatile' | 'meta-llama/llama-4-maverick-17b-128e-instruct' | 'openai/gpt-oss-120b';
const GROQ_MODEL_OPTIONS: { value: GroqModelType; label: string }[] = [
  { value: 'meta-llama/llama-4-maverick-17b-128e-instruct', label: 'Llama 4 Maverick (Fastest)' },
  { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B (Recommended)' },
  { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B' },
  { value: 'openai/gpt-oss-120b', label: 'GPT-OSS 120B (High Quality)' },
];

// Language options
type LanguageCode = 'en' | 'ja' | 'es' | 'zh';
const LANGUAGE_OPTIONS: { value: LanguageCode; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'ja', label: 'Japanese' },
  { value: 'es', label: 'Spanish' },
  { value: 'zh', label: 'Chinese' },
];

const TTS_LANG_MAP: Record<LanguageCode, string> = {
  en: 'en-US',
  ja: 'ja-JP',
  es: 'es-ES',
  zh: 'zh-CN',
};

function ExperimentalApp() {
  const [currentTranscript, setCurrentTranscript] = useState<string>('');
  const [timeline, setTimeline] = useState<SpeculativeEntry[]>([]);
  const timelineEndRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string>('Disconnected');
  const [displayMode, setDisplayMode] = useState<DisplayMode>('separate');
  // LLM model switching (5 models)
  const [model, setModel] = useState<ModelType>('gpt4');
  // Groq sub-model
  const [groqModel, setGroqModel] = useState<GroqModelType>('meta-llama/llama-4-maverick-17b-128e-instruct');
  // Language selection
  const [sourceLang, setSourceLang] = useState<LanguageCode>('en');
  const [targetLang, setTargetLang] = useState<LanguageCode>('ja');
  // Summary
  const [summary, setSummary] = useState<string>('');
  const [summaryHistory, setSummaryHistory] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const summaryEndRef = useRef<HTMLDivElement>(null);
  // TTS (translation speech)
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const ttsEnabledRef = useRef(ttsEnabled);
  useEffect(() => { ttsEnabledRef.current = ttsEnabled; }, [ttsEnabled]);

  const isMobile = /iPhone|iPad|Android/i.test(navigator.userAgent);
  const ttsRate = isMobile ? 1.3 : 3.0;
  const lastSpokenRef = useRef<string>('');

  const speakTranslation = useCallback((text: string) => {
    if (!ttsEnabledRef.current) return;
    // Prevent duplicate speech for same translation
    if (lastSpokenRef.current === text) return;
    lastSpokenRef.current = text;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = TTS_LANG_MAP[targetLang];
    utterance.rate = ttsRate;
    window.speechSynthesis.speak(utterance);
  }, [ttsRate, targetLang]);

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
        // Show intent label early
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
        // Speculative translation arrived (early stage)
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
        // Complete result
        const data = message.data as IntentData & { speculative_translation?: string };
        // TTS: speak translation (dedup handled in speakTranslation)
        if (data.full_translation) {
          speakTranslation(data.full_translation);
        }
        setTimeline((prev) => {
          const lastPartialIndex = prev.findLastIndex((e) => !e.isFinal);
          // Keep existing speculative translation (don't overwrite)
          const existingSpeculative = lastPartialIndex !== -1
            ? prev[lastPartialIndex].speculativeTranslation
            : null;
          const newEntry: SpeculativeEntry = {
            id: Date.now(),
            intentLabel: data.intent_label || '',
            dialogueAct: data.dialogue_act || 'OTHER',
            // Priority: existing speculative > JSON speculative > null
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
        setStatus(`Error: ${message.message}`);
        break;
    }
  }, [speakTranslation]);

  const handleOpen = useCallback(() => {
    setStatus('Connecting...');
  }, []);

  const handleClose = useCallback(() => {
    setStatus('Disconnected');
    setCurrentTranscript('');
  }, []);

  const handleError = useCallback(() => {
    setStatus('Connection error');
  }, []);

  // WebSocket URL: wss for https, ws for http
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

  // Model selection
  const selectModel = useCallback((newModel: ModelType) => {
    setModel(newModel);
    sendControl({ type: 'set_model', model: newModel });
  }, [sendControl]);

  // Groq sub-model selection
  const selectGroqModel = useCallback((newGroqModel: GroqModelType) => {
    setGroqModel(newGroqModel);
    sendControl({ type: 'set_groq_model', groq_model: newGroqModel });
  }, [sendControl]);

  // Language selection
  const selectSourceLang = useCallback((lang: LanguageCode) => {
    // If same as target, swap them
    if (lang === targetLang) {
      setTargetLang(sourceLang);
    }
    setSourceLang(lang);
    sendControl({ type: 'set_languages', source: lang, target: lang === targetLang ? sourceLang : targetLang });
  }, [sendControl, sourceLang, targetLang]);

  const selectTargetLang = useCallback((lang: LanguageCode) => {
    // If same as source, swap them
    if (lang === sourceLang) {
      setSourceLang(targetLang);
    }
    setTargetLang(lang);
    sendControl({ type: 'set_languages', source: lang === sourceLang ? targetLang : sourceLang, target: lang });
  }, [sendControl, sourceLang, targetLang]);

  const swapLanguages = useCallback(() => {
    const newSource = targetLang;
    const newTarget = sourceLang;
    setSourceLang(newSource);
    setTargetLang(newTarget);
    sendControl({ type: 'set_languages', source: newSource, target: newTarget });
  }, [sendControl, sourceLang, targetLang]);

  // Enable experimental mode on connection + sync all settings
  useEffect(() => {
    if (isConnected) {
      sendControl({ type: 'set_experimental', enabled: true });
      sendControl({ type: 'set_languages', source: sourceLang, target: targetLang });
      if (model !== 'gpt4') {
        sendControl({ type: 'set_model', model });
      }
      if (model === 'groq') {
        sendControl({ type: 'set_groq_model', groq_model: groqModel });
      }
    }
  }, [isConnected, sendControl, model, groqModel, sourceLang, targetLang]);

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

  // Auto-scroll timeline
  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [timeline]);

  // Auto-scroll summary history
  useEffect(() => {
    summaryEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [summaryHistory]);

  // Unified mode: determine which translation to display
  const getDisplayTranslation = (entry: SpeculativeEntry) => {
    // Use final if available, otherwise speculative
    return entry.finalTranslation || entry.speculativeTranslation;
  };

  // Unified mode: determine translation status
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
          <p className="text-orange-600 text-sm mb-2">
            Speculative translation driven by dialogue_act + intent_label
          </p>

          {/* Language selection */}
          <div className="flex justify-center items-center gap-2 mt-3 mb-3">
            <select
              value={sourceLang}
              onChange={(e) => selectSourceLang(e.target.value as LanguageCode)}
              className="px-3 py-2 rounded-lg text-sm font-medium border-2 border-orange-300 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-orange-500"
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <button
              onClick={swapLanguages}
              className="px-3 py-2 rounded-lg text-sm font-bold bg-orange-200 text-orange-700 hover:bg-orange-300 transition-colors"
              title="Swap languages"
            >
              ⇄
            </button>
            <select
              value={targetLang}
              onChange={(e) => selectTargetLang(e.target.value as LanguageCode)}
              className="px-3 py-2 rounded-lg text-sm font-medium border-2 border-green-300 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-green-500"
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Model selection */}
          <div className="flex justify-center gap-1 mt-2 mb-2">
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

          {/* Groq sub-model selection (shown only when Groq is selected) */}
          {model === 'groq' && (
            <div className="flex justify-center items-center gap-2 mt-2">
              <span className="text-sm text-gray-600">Groq Model:</span>
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

          {/* Display mode toggle + TTS */}
          <div className="flex justify-center gap-2 mt-2 mb-2">
            <button
              onClick={() => setDisplayMode('separate')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                displayMode === 'separate'
                  ? 'bg-orange-600 text-white'
                  : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
              }`}
            >
              Side by Side
            </button>
            <button
              onClick={() => setDisplayMode('unified')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                displayMode === 'unified'
                  ? 'bg-purple-600 text-white'
                  : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
              }`}
            >
              Overwrite
            </button>
            <button
              onClick={() => {
                if (ttsEnabled) {
                  window.speechSynthesis.cancel();
                } else {
                  // Mobile browser workaround: unlock speech with silent utterance on user tap
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
              {ttsEnabled ? 'TTS ON' : 'TTS OFF'}
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

        {/* Current recognition text */}
        {currentTranscript && (
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-4">
            <p className="text-blue-800 italic">{currentTranscript}</p>
          </div>
        )}

        {/* Speculative translation timeline */}
        <div className="bg-white rounded-xl shadow-lg p-6 mb-6">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-gray-700">
              {displayMode === 'separate' ? 'Speculative vs Final' : 'Real-time Translation (Speculative \u2192 Final)'}
            </h2>
            {timeline.length > 0 && (
              <button
                onClick={clearAll}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                Clear
              </button>
            )}
          </div>
          <div className="max-h-[500px] overflow-y-auto space-y-4">
            {timeline.length === 0 ? (
              <p className="text-gray-400 text-center py-8">
                {displayMode === 'separate'
                  ? 'Start speaking to compare speculative and final translations'
                  : 'Start speaking \u2014 speculative translation appears first, then replaced by final'
                }
              </p>
            ) : (
              <>
                {timeline.map((entry, i) => {
                  const actInfo = DIALOGUE_ACT_LABELS[entry.dialogueAct] || DIALOGUE_ACT_LABELS.OTHER;
                  const isLatest = i === timeline.length - 1;

                  if (displayMode === 'unified') {
                    // Overwrite display mode
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
                        {/* Intent label row */}
                        <div className="flex items-center gap-2 mb-2">
                          <span className={`px-2 py-0.5 rounded text-white text-xs ${actInfo.color}`}>
                            {actInfo.label}
                          </span>
                          <span className="text-gray-700 font-medium">{entry.intentLabel}</span>
                          {translationStatus === 'speculative' && (
                            <span className="text-xs text-orange-500 bg-orange-100 px-2 py-0.5 rounded animate-pulse">
                              Speculating
                            </span>
                          )}
                          {translationStatus === 'final' && (
                            <span className="text-xs text-green-600 bg-green-100 px-2 py-0.5 rounded">
                              Final
                            </span>
                          )}
                        </div>

                        {/* Translation (single display) */}
                        {translation ? (
                          <p className={`text-lg pl-1 ${
                            translationStatus === 'final' ? 'text-green-800' : 'text-orange-800'
                          }`}>
                            {'\u2192'} {translation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-1 animate-pulse">
                            {'\u2192'} Translating...
                          </p>
                        )}
                      </div>
                    );
                  }

                  // Side-by-side display mode
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
                      {/* Intent label row */}
                      <div className="flex items-center gap-2 mb-3">
                        <span className={`px-2 py-0.5 rounded text-white text-xs ${actInfo.color}`}>
                          {actInfo.label}
                        </span>
                        <span className="text-gray-700 font-medium">{entry.intentLabel}</span>
                        {!entry.isFinal && <span className="text-xs text-orange-500 animate-pulse">Speculating...</span>}
                      </div>

                      {/* Speculative translation (fast) */}
                      <div className="mb-2">
                        <span className="text-xs text-orange-600 font-medium">Speculative:</span>
                        {entry.speculativeTranslation ? (
                          <p className="text-orange-800 text-lg pl-2">
                            {entry.speculativeTranslation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-2 animate-pulse">
                            Speculating...
                          </p>
                        )}
                      </div>

                      {/* Final translation (accurate) */}
                      <div>
                        <span className="text-xs text-green-600 font-medium">Final:</span>
                        {entry.finalTranslation ? (
                          <p className="text-green-800 text-lg pl-2">
                            {entry.finalTranslation}
                          </p>
                        ) : (
                          <p className="text-gray-400 text-sm pl-2">
                            {entry.isFinal ? '(none)' : 'Waiting for final...'}
                          </p>
                        )}
                      </div>

                      {/* Comparison: highlight when speculative and final differ */}
                      {entry.speculativeTranslation && entry.finalTranslation &&
                       entry.speculativeTranslation !== entry.finalTranslation && (
                        <div className="mt-2 text-xs text-red-500 bg-red-50 p-2 rounded">
                          Speculative and final translations differ
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

        {/* Mode explanation */}
        <div className="bg-white/50 rounded-lg p-4 mb-6">
          <p className="text-sm text-gray-600">
            {displayMode === 'separate' ? (
              <>
                <strong>Side by Side:</strong> Shows speculative and final translations together for accuracy comparison.
              </>
            ) : (
              <>
                <strong>Overwrite:</strong> Shows speculative translation first, then replaces with final.
                Translation appears ~500ms{'\u2013'}1s faster.
              </>
            )}
          </p>
        </div>

        <footer className="mt-8 text-center text-sm text-gray-500 pb-24">
          <p>Intent-First Translation | Speculative Mode</p>
        </footer>
      </div>

      {/* Conversation summary - fixed at bottom */}
      {(summaryHistory.length > 0 || summary) && (
        <div className="fixed bottom-0 left-0 right-0 bg-gradient-to-r from-indigo-500 to-purple-600 shadow-lg text-white z-50">
          <div className="max-w-5xl mx-auto p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-semibold">Summary</span>
              {(model === 'gemma' || model === 'groq') && <span className="text-xs bg-white/30 px-2 py-0.5 rounded">Generated by GPT-4</span>}
              {isStreaming && <span className="text-xs bg-white/20 px-2 py-0.5 rounded animate-pulse">Updating...</span>}
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
