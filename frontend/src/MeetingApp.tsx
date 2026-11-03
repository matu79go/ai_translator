import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AudioRecorder } from './components/AudioRecorder';
import { useWebSocket, type WebSocketMessage } from './hooks/useWebSocket';
import './meeting.css';

/**
 * Online meeting assist (GX10 local LLM).
 * - Real-time translation of the other party (plain-text stream)
 * - Their underlying need + reply ideas when their turn ends
 * Design: docs/designs/meeting-assist-local.md, layout: docs/mock/b-subtitle.html
 * (subtitle style: the sentence being spoken is always shown big in the same place, history below,
 *  newest first). Theme: white (default) / navy.
 *
 * No TTS here on purpose: with meeting-audio capture, speech played by the PC would be
 * captured again and translated in a loop.
 */

type FinalLine = { seq: number; source: string; text: string; done: boolean };
type LiveLine = { seq: number; source: string; text: string };
type Insight = { need: string; situation: string; ready: boolean; replies: string[]; complete: boolean };
type AsrEngine = 'deepgram' | 'nemotron';
type StoryChunk = { index: number; start: number; n: number; first?: string; title?: string; summary?: string; done: boolean };

type Theme = 'light' | 'dark';

const THEME_KEY = 'meetingAssist.theme';
function loadTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

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
  const [stories, setStories] = useState<StoryChunk[]>([]);
  const [tab, setTab] = useState<'conv' | 'story'>('conv');  // phones: which panel the bottom area shows
  const [asr, setAsr] = useState<AsrEngine>(loadAsr);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [turnEnds, setTurnEnds] = useState<number[]>([]);  // final seqs after which the other party's turn ended
  const [recording, setRecording] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [insOpen, setInsOpen] = useState(false);
  const [insFresh, setInsFresh] = useState(false);
  const maxFinalSeq = useRef(0);
  const histRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

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
        setInsight({ need: message.need, situation: message.situation ?? '', ready: true, replies: [], complete: false });
        setTurnEnds((t) => (t.includes(maxFinalSeq.current) ? t : [...t, maxFinalSeq.current]));
        setInsFresh(true);
        break;
      }
      case 'insight':
        setInsight({ need: message.need, situation: message.situation ?? '', ready: message.ready, replies: message.replies, complete: true });
        break;
      case 'story_progress':
        setStories((prev) => upsertStory(prev, { index: message.index, start: message.start, n: message.n, done: false }));
        break;
      case 'story':
        setStories((prev) => upsertStory(prev, { ...message, done: true }));
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

  useEffect(() => {
    if (!insFresh) return;
    const t = setTimeout(() => setInsFresh(false), 1500);
    return () => clearTimeout(t);
  }, [insFresh]);

  const selectAsr = (engine: AsrEngine) => {
    setAsr(engine);
    try { localStorage.setItem(ASR_KEY, engine); } catch { /* storage unavailable */ }
    if (isConnected) sendControl({ type: 'set_asr', engine });
  };

  const toggleTheme = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage unavailable */ }
  };

  const clearAll = () => {
    setFinals([]); setLive(null); setInsight(null); setStories([]); setTranscript(''); setTurnEnds([]);
  };

  // ── what the "now" panel shows: live line > English being heard > latest final ──
  const lastFinal = finals.length ? finals[finals.length - 1] : null;
  let nowState: { label: string; cls: string; ja: string; en: string; placeholder?: boolean };
  if (live) {
    nowState = { label: '話し中', cls: 'speaking', ja: live.text, en: live.source };
  } else if (transcript) {
    nowState = { label: '聞き取り中', cls: 'speaking', ja: '訳を準備中…', en: transcript, placeholder: true };
  } else if (lastFinal) {
    const ended = turnEnds.includes(lastFinal.seq);
    nowState = { label: ended ? '相手が話し終えました' : lastFinal.done ? '確定' : '訳を確定中', cls: 'done', ja: lastFinal.text, en: lastFinal.source };
  } else {
    nowState = { label: recording ? '待機中' : '停止中', cls: 'done', ja: recording ? '相手が話すと、ここに大きく訳が出ます' : '左上の ● を押して開始します', en: '', placeholder: true };
  }
  // History = finals not shown in the now panel, newest first
  const showsLastFinal = !live && !transcript && !!lastFinal;
  const history = (showsLastFinal ? finals.slice(0, -1) : finals).slice().reverse();

  // Keep the newest history line in view (top) unless the user scrolled down to read
  useLayoutEffect(() => {
    if (follow) histRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [history.length, follow]);
  const onHistScroll = () => {
    const el = histRef.current;
    if (el) setFollow(el.scrollTop < 48);
  };

  // あらすじ: jump to the chunk's first line in the history
  const jumpTo = (first?: string) => {
    const f = first && finals.find((x) => x.source === first);
    if (!f) return;
    setTab('conv');
    setFollow(false);
    setTimeout(() => {
      const el = histRef.current?.querySelector<HTMLElement>(`[data-seq="${f.seq}"]`);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1500);
    }, 50);
  };

  const tabs = (
    <span className="tabs">
      <button className={tab === 'conv' ? 'on' : ''} onClick={() => setTab('conv')}>会話</button>
      <button className={tab === 'story' ? 'on' : ''} onClick={() => setTab('story')}>あらすじ</button>
    </span>
  );

  const connClass = status.startsWith('エラー') || status === '接続エラー' ? 'err' : isConnected ? 'on' : '';
  const connText = connClass === 'err' ? status
    : isConnected ? `${recording ? '録音中' : '接続中'} · ${asr === 'nemotron' ? 'Nemotron' : 'Deepgram'}`
    : status;

  return (
    <div className="ma" data-theme={theme}>
      <div className="bar">
        <h1>AI Translator</h1>
        <AudioRecorder
          variant="compact"
          isConnected={isConnected}
          onAudioData={sendAudio}
          onStart={connect}
          onStop={disconnect}
          onRecordingChange={setRecording}
          enableSystemAudio
          defaultSource="system"
        />
        <div className="seg" title="音声認識エンジン">
          <span className="k">ASR</span>
          {([
            { value: 'deepgram', label: 'Deepgram' },
            { value: 'nemotron', label: 'Nemotron' },
          ] as const).map(({ value, label }) => (
            <button key={value} className={asr === value ? 'on' : ''} onClick={() => selectAsr(value)}>{label}</button>
          ))}
        </div>
        <span className={`conn ${connClass}`} title={status}>{connText}</span>
        <span className="help">
          <button className="icon" onClick={() => setHelpOpen((v) => !v)} title="使い方" aria-label="使い方">?</button>
          {helpOpen && (
            <div className="pop">
              <b>会議の音声</b>：● を押したら、共有画面で「画面全体」を選び「システム音声を共有」をオン（Zoom / Teams アプリでも可）。
              ブラウザ会議ならその「タブ」を選び「タブの音声を共有」をオン。<br />
              <b>ASR</b>：Nemotron は GX10 で動く音声認識（英語のみ）。何も出ないときは Deepgram に切り替え。<br />
              <a href="#" onClick={() => setTimeout(() => location.reload())}>従来の画面へ</a>
            </div>
          )}
        </span>
        <button className="icon" onClick={toggleTheme} title="白 / 紺 を切り替え" aria-label="テーマ切り替え">
          {theme === 'light' ? '☾' : '☀'}
        </button>
        <button className="icon" onClick={clearAll} title="クリア" aria-label="クリア">⌫</button>
      </div>

      <main className={tab === 'story' ? 'tab-story' : ''}>
        <section className={`now ${nowState.cls}`}>
          <div className="state">{nowState.label}</div>
          <div className={`ja${nowState.placeholder ? ' placeholder' : ''}`}>{nowState.ja}</div>
          {nowState.en && <div className="en">{nowState.en}</div>}
        </section>

        <aside className={`panel ins${insOpen ? ' open' : ''}${insFresh ? ' fresh' : ''}`}>
          <div className="ins-body">
            {!insight && <p className="thinking">💡 相手が話し終えると、ここに相手の本音と返事の案が出ます</p>}
            {insight && (
              <>
                <div className="lead">
                  <div className="situation">💡 相手の本音{insight.situation && ` · 相手は${insight.situation}`}</div>
                  <div className={`need${insight.ready ? '' : ' not-ready'}`}>
                    {insight.need}
                    {!insight.ready && <div className="thinking">（まだ前置きの段階かもしれません）</div>}
                  </div>
                  <button className="toggle" onClick={() => setInsOpen((v) => !v)}>
                    {insOpen ? '返事の案を閉じる' : '返事の案を見る'}
                  </button>
                </div>
                <div className="more">
                  <div className="label">返事の案</div>
                  {insight.replies.length > 0
                    ? insight.replies.map((r, i) => <div key={i} className="reply">{r}</div>)
                    : <p className="thinking">{insight.complete ? '—' : '考え中…'}</p>}
                </div>
              </>
            )}
          </div>
        </aside>

        <section className="panel histp">
          <h2><span className="pc-title">これまでの会話（新しい順）</span>{tabs}</h2>
          {!follow && (
            <button className="jump" onClick={() => { setFollow(true); histRef.current?.scrollTo({ top: 0, behavior: 'smooth' }); }}>
              ↑ 最新へ
            </button>
          )}
          <div className="hist" ref={histRef} onScroll={onHistScroll}>
            {history.length === 0 && <p className="empty">確定した訳がここに新しい順で並びます</p>}
            {history.map((f) => (
              <div key={f.seq}>
                {turnEnds.includes(f.seq) && <div className="turn">── 相手が話し終えました</div>}
                <div className="h" data-seq={f.seq}>
                  <div className="ja">{f.text}</div>
                  <div className="en">{f.source}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel storyp">
          <h2><span className="pc-title">📖 あらすじ</span>{tabs}</h2>
          <div className="story">
            {stories.length === 0 && <p className="empty">会話がある程度たまると、区切りごとの要約がここに並びます</p>}
            {stories.map((st) => (
              <div key={st.index} className={`st${st.done ? '' : ' progress'}`}>
                <div className="top">
                  <time>{fmtTime(st.start)}</time>
                  {st.done
                    ? <button className="title" onClick={() => jumpTo(st.first)} title="この区切りの会話へ">{st.title}</button>
                    : <span className="title">進行中（{st.n}文）…</span>}
                </div>
                {st.done && st.summary && <div className="sum">{st.summary}</div>}
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function upsertStory(list: StoryChunk[], chunk: StoryChunk): StoryChunk[] {
  const i = list.findIndex((c) => c.index === chunk.index);
  if (i === -1) return [...list, chunk].sort((x, y) => x.index - y.index);
  if (list[i].done && !chunk.done) return list;
  const next = [...list];
  next[i] = { ...list[i], ...chunk };
  return next;
}

function fmtTime(sec: number): string {
  const m = Math.floor(sec / 60), s2 = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s2).padStart(2, '0')}`;
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
