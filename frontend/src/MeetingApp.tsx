import { useCallback, useEffect, useRef, useState } from 'react';
import { Lightbulb, MessageCircleReply, Trash2 } from 'lucide-react';
import { AudioRecorder } from './components/AudioRecorder';
import { useWebSocket, type WebSocketMessage } from './hooks/useWebSocket';

/**
 * Online meeting assist (GX10 local LLM).
 * - Real-time translation of the other party (plain-text stream)
 * - Their underlying need + reply ideas when their turn ends
 * Design: docs/designs/meeting-assist-local.md
 *
 * No TTS here on purpose: with meeting-audio capture, speech played by the PC would be
 * captured again and translated in a loop.
 */

type FinalLine = { seq: number; source: string; text: string; done: boolean };
type LiveLine = { seq: number; source: string; text: string };
type Insight = { need: string; ready: boolean; replies: string[]; complete: boolean };
type AsrEngine = 'deepgram' | 'nemotron';

const ASR_KEY = 'meetingAssist.asr';
function loadAsr(): AsrEngine {
  try {
    return localStorage.getItem(ASR_KEY) === 'nemotron' ? 'nemotron' : 'deepgram';
  } catch {
    return 'deepgram';
  }
}

export default function MeetingApp() {
  const [status, setStatus] = useState('待機中');
  const [transcript, setTranscript] = useState('');
  const [finals, setFinals] = useState<FinalLine[]>([]);
  const [live, setLive] = useState<LiveLine | null>(null);
  const [insight, setInsight] = useState<Insight | null>(null);
  const [pastNeeds, setPastNeeds] = useState<string[]>([]);
  const [asr, setAsr] = useState<AsrEngine>(loadAsr);
  const maxFinalSeq = useRef(0);
  const insightRef = useRef<Insight | null>(null);
  useEffect(() => { insightRef.current = insight; }, [insight]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'transcript':
        setTranscript(message.is_final ? '' : message.transcript);
        break;
      case 'translation_partial': {
        const seq = message.seq ?? 0;
        if (message.is_final) {
          // Translation of a final transcript, still streaming
          maxFinalSeq.current = Math.max(maxFinalSeq.current, seq);
          setFinals((prev) => upsertFinal(prev, { seq, source: message.source_text, text: message.translation, done: false }));
          setLive((prev) => (prev && prev.seq < seq ? null : prev));
        } else if (seq > maxFinalSeq.current) {
          setLive((prev) => {
            if (prev && seq < prev.seq) return prev;
            // Same utterance (source grew) but the new guess is much shorter: keep the current
            // line instead of visibly shrinking it. The next update or the final replaces it.
            // (Deepgram revises interim words, so "same utterance" = same first 3 words.)
            if (prev && sameUtterance(prev.source, message.source_text)
                && message.translation.length < prev.text.length * 0.7) return prev;
            return { seq, source: message.source_text, text: message.translation };
          });
        }
        break;
      }
      case 'translation_final':
        maxFinalSeq.current = Math.max(maxFinalSeq.current, message.seq);
        setFinals((prev) => upsertFinal(prev, { seq: message.seq, source: message.source_text, text: message.translation, done: true }));
        setLive((prev) => (prev && prev.seq < message.seq ? null : prev));
        break;
      case 'insight_partial': {
        const prev = insightRef.current;
        if (prev?.complete && prev.ready && prev.need) setPastNeeds((p) => [...p.slice(-4), prev.need]);
        setInsight({ need: message.need, ready: true, replies: [], complete: false });
        break;
      }
      case 'insight':
        setInsight({ need: message.need, ready: message.ready, replies: message.replies, complete: true });
        break;
      case 'asr_changed':
        setAsr(message.engine);
        break;
      case 'status':
        setStatus(message.message);
        break;
      case 'error':
        setStatus(`エラー: ${message.message}`);
        break;
    }
  }, []);

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsHost = import.meta.env.MODE === 'development' ? `${window.location.hostname}:8000` : window.location.host;

  const { isConnected, connect, disconnect, sendAudio, sendControl } = useWebSocket({
    url: `${wsProtocol}//${wsHost}/ws/audio?asr=${asr}`,
    onMessage: handleMessage,
    onOpen: () => setStatus('接続中...'),
    // Keep an error visible (e.g. "Nemotron ASR (GX10) に接続できません") instead of overwriting it
    onClose: () => { setStatus((prev) => (prev.startsWith('エラー') ? `${prev}（切断）` : '切断')); setTranscript(''); },
    onError: () => setStatus('接続エラー'),
  });

  // Meeting mode always uses the GX10 pipeline
  useEffect(() => {
    if (isConnected) sendControl({ type: 'set_model', model: 'gx10' });
  }, [isConnected, sendControl]);

  // Keep the newest translation in view
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [finals, live]);

  const selectAsr = (engine: AsrEngine) => {
    setAsr(engine);
    try { localStorage.setItem(ASR_KEY, engine); } catch { /* storage unavailable */ }
    if (isConnected) sendControl({ type: 'set_asr', engine });
  };

  const clearAll = () => {
    setFinals([]); setLive(null); setInsight(null); setPastNeeds([]); setTranscript('');
  };

  return (
    <div className="min-h-screen bg-slate-100 p-4">
      <div className="max-w-5xl mx-auto space-y-4">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-800">Meeting Assist</h1>
            <p className="text-xs text-slate-500">
              GX10 Gemma 4 · ASR: {asr === 'nemotron' ? 'Nemotron (GX10)' : 'Deepgram'} · {status}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex rounded-lg border border-slate-300 overflow-hidden text-xs" title="音声認識エンジン">
              <span className="px-2 py-1 bg-slate-50 text-slate-500">ASR</span>
              {([
                { value: 'deepgram', label: 'Deepgram' },
                { value: 'nemotron', label: 'Nemotron (GX10)' },
              ] as const).map(({ value, label }) => (
                <button
                  key={value}
                  onClick={() => selectAsr(value)}
                  className={`px-2 py-1 transition-colors ${
                    asr === value ? 'bg-emerald-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <a href="#" onClick={() => setTimeout(() => location.reload())} className="text-xs text-slate-500 hover:underline">
              従来の画面へ
            </a>
            <button onClick={clearAll} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700">
              <Trash2 className="w-4 h-4" /> クリア
            </button>
          </div>
        </header>

        <div className="bg-white rounded-xl shadow p-4">
          <AudioRecorder
            isConnected={isConnected}
            onAudioData={sendAudio}
            onStart={connect}
            onStop={disconnect}
            enableSystemAudio
            defaultSource="system"
          />
          <p className="mt-3 text-xs text-slate-500 text-center">
            「会議の音声」: 共有画面で <b>画面全体</b> を選び <b>システム音声を共有</b> をオン（Zoom/Teams アプリでも可）。
            ブラウザ会議ならその <b>タブ</b> を選び <b>タブの音声を共有</b> をオン。
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-5">
          {/* Translation */}
          <section className="md:col-span-3 bg-white rounded-xl shadow p-4 flex flex-col">
            <h2 className="text-sm font-semibold text-slate-600 mb-2">リアルタイム翻訳</h2>
            <div ref={scrollRef} className="flex-1 max-h-[60vh] min-h-[240px] overflow-y-auto space-y-3">
              {finals.length === 0 && !live && (
                <p className="text-slate-400 text-sm text-center py-10">相手が話すと、ここに日本語訳が出ます</p>
              )}
              {finals.map((f) => (
                <div key={f.seq} className="border-l-4 border-blue-500 pl-3">
                  <p className={`text-lg leading-relaxed ${f.done ? 'text-slate-800' : 'text-slate-600'}`}>{f.text}</p>
                  <p className="text-xs text-slate-400 mt-0.5">{f.source}</p>
                </div>
              ))}
              {live && (
                <div className="border-l-4 border-blue-200 pl-3 animate-pulse">
                  <p className="text-lg leading-relaxed text-slate-400">{live.text}</p>
                </div>
              )}
            </div>
            {transcript && <p className="mt-3 text-sm italic text-blue-700 bg-blue-50 rounded px-2 py-1">{transcript}</p>}
          </section>

          {/* Insight */}
          <section className="md:col-span-2 space-y-4">
            <div className={`rounded-xl shadow p-4 transition-colors ${
              insight && insight.ready ? 'bg-amber-50 border-2 border-amber-300' : 'bg-white'
            }`}>
              <h2 className="flex items-center gap-1.5 text-sm font-semibold text-amber-700 mb-2">
                <Lightbulb className="w-4 h-4" /> 相手の本音
              </h2>
              {insight ? (
                <p className={`text-xl font-bold leading-snug ${insight.ready ? 'text-slate-900' : 'text-slate-400'}`}>
                  {insight.need}
                  {!insight.ready && <span className="block text-xs font-normal mt-1">（まだ前置きの段階かもしれません）</span>}
                </p>
              ) : (
                <p className="text-slate-400 text-sm">相手が話し終えると表示されます</p>
              )}
            </div>

            <div className="bg-white rounded-xl shadow p-4">
              <h2 className="flex items-center gap-1.5 text-sm font-semibold text-slate-600 mb-2">
                <MessageCircleReply className="w-4 h-4" /> 返事の案
              </h2>
              {insight && insight.replies.length > 0 ? (
                <ul className="space-y-2">
                  {insight.replies.map((r, i) => (
                    <li key={i} className={`text-base rounded-lg bg-slate-50 px-3 py-2 ${insight.ready ? 'text-slate-800' : 'text-slate-400'}`}>
                      {r}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-slate-400 text-sm">{insight && !insight.complete ? '考え中...' : '—'}</p>
              )}
            </div>

            {pastNeeds.length > 0 && (
              <div className="bg-white rounded-xl shadow p-4">
                <h2 className="text-xs font-semibold text-slate-500 mb-1">これまでの本音</h2>
                <ul className="text-sm text-slate-500 space-y-1">
                  {pastNeeds.slice().reverse().map((n, i) => <li key={i}>・{n}</li>)}
                </ul>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function sameUtterance(a: string, b: string): boolean {
  const head = (s: string) => s.trim().toLowerCase().split(/\s+/).slice(0, 3).join(' ');
  return head(a) !== '' && head(a) === head(b);
}

function upsertFinal(list: FinalLine[], line: FinalLine): FinalLine[] {
  const i = list.findIndex((f) => f.seq === line.seq);
  if (i === -1) return [...list, line].sort((a, b) => a.seq - b.seq);
  if (list[i].done && !line.done) return list; // late chunk after completion
  const next = [...list];
  next[i] = line;
  return next;
}
