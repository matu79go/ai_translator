import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AudioRecorder } from './components/AudioRecorder';
import { MiniWindow } from './components/MiniWindow';
import { isMiniWindowSupported, useMiniWindow } from './hooks/useMiniWindow';
import { useWebSocket, type WebSocketMessage } from './hooks/useWebSocket';
import { LANG_PAIRS, STRINGS, type LangPair, type Strings } from './i18n';
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
type NowState = { label: string; cls: string; ja: string; en: string; placeholder?: boolean };

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

const LANG_KEY = 'meetingAssist.langPair';
function loadLangPair(): LangPair {
  try {
    return localStorage.getItem(LANG_KEY) === 'ja-en' ? 'ja-en' : 'en-ja';
  } catch {
    return 'en-ja';
  }
}

export default function MeetingApp() {
  const [langPair, setLangPair] = useState<LangPair>(loadLangPair);
  const pair = LANG_PAIRS.find((p) => p.value === langPair) ?? LANG_PAIRS[0];
  const t = STRINGS[pair.tgt];
  const cjkSource = pair.src === 'ja';
  const [status, setStatus] = useState(t.idle);
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
  const mini = useMiniWindow();

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
            if (prev && sameUtterance(prev.source, message.source_text, cjkSource)
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
        setStatus(`${t.errorPrefix}: ${message.message}`);
        break;
    }
  }, [t, cjkSource]);

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsHost = import.meta.env.MODE === 'development' ? `${window.location.hostname}:8000` : window.location.host;

  const { isConnected, connect, disconnect, sendAudio, sendControl } = useWebSocket({
    url: `${wsProtocol}//${wsHost}/ws/audio?asr=${asr}&src=${pair.src}&tgt=${pair.tgt}`,
    onMessage: handleMessage,
    onOpen: () => setStatus(t.connecting),
    // Keep an error visible (e.g. "Nemotron ASR (GX10) に接続できません") instead of overwriting it
    onClose: () => { setStatus((prev) => (prev.startsWith(t.errorPrefix) ? `${prev}${t.disconnectedSuffix}` : t.disconnected)); setTranscript(''); },
    onError: () => setStatus(t.connError),
  });

  // Page language follows the translation's target language
  useEffect(() => {
    document.documentElement.lang = pair.tgt;
  }, [pair.tgt]);

  const selectLangPair = (value: LangPair) => {
    if (recording || value === langPair) return;
    setLangPair(value);
    const next = LANG_PAIRS.find((p) => p.value === value) ?? LANG_PAIRS[0];
    if (!isConnected) setStatus(STRINGS[next.tgt].idle);
    try { localStorage.setItem(LANG_KEY, value); } catch { /* storage unavailable */ }
    clearAll();  // history/insight of the other direction would be mixed in
  };

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
  let nowState: NowState;
  if (live) {
    nowState = { label: t.speaking, cls: 'speaking', ja: live.text, en: live.source };
  } else if (transcript) {
    nowState = { label: t.listening, cls: 'speaking', ja: t.preparing, en: transcript, placeholder: true };
  } else if (lastFinal) {
    const ended = turnEnds.includes(lastFinal.seq);
    nowState = { label: ended ? t.theyFinished : lastFinal.done ? t.final : t.finalizing, cls: 'done', ja: lastFinal.text, en: lastFinal.source };
  } else {
    nowState = { label: recording ? t.idle : t.stopped, cls: 'done', ja: recording ? t.waitingHint : t.startHint, en: '', placeholder: true };
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
      <button className={tab === 'conv' ? 'on' : ''} onClick={() => setTab('conv')}>{t.conv}</button>
      <button className={tab === 'story' ? 'on' : ''} onClick={() => setTab('story')}>{t.story}</button>
    </span>
  );

  const connClass = status.startsWith(t.errorPrefix) || status === t.connError ? 'err' : isConnected ? 'on' : '';
  const connText = connClass === 'err' ? status
    : isConnected ? `${recording ? t.recordingState : t.connectedState} · ${asr === 'nemotron' ? 'Nemotron' : 'Deepgram'}`
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
          strings={t}
        />
        <div className="seg" title={t.langTitle}>
          <span className="k">{t.langKey}</span>
          {LANG_PAIRS.map(({ value, label }) => (
            <button key={value} className={langPair === value ? 'on' : ''} disabled={recording}
              onClick={() => selectLangPair(value)}>{label}</button>
          ))}
        </div>
        <div className="seg" title={t.asrTitle}>
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
          <button className="icon" onClick={() => setHelpOpen((v) => !v)} title={t.helpTitle} aria-label={t.helpTitle}>?</button>
          {helpOpen && (
            <div className="pop">
              <b>{t.helpAudio[0]}</b>{t.helpAudio[1]}<br />
              <b>{t.helpAsr[0]}</b>{t.helpAsr[1]}<br />
              <a href="#" onClick={() => setTimeout(() => location.reload())}>{t.legacy}</a>
            </div>
          )}
        </span>
        <button className="icon" onClick={toggleTheme} title={t.themeTitle} aria-label={t.themeAria}>
          {theme === 'light' ? '☾' : '☀'}
        </button>
        <button className="icon" onClick={clearAll} title={t.clear} aria-label={t.clear}>⌫</button>
        {isMiniWindowSupported() && (
          <button className={`icon${mini.win ? ' on' : ''}`} onClick={mini.win ? mini.close : mini.open}
            title={mini.win ? t.miniClose : t.miniOpen} aria-label={t.miniAria}>⧉</button>
        )}
      </div>

      {/* Mini window: subtitle + need only, always on top over the meeting app */}
      <MiniWindow win={mini.win}>
        <MiniView theme={theme} now={nowState} recording={recording} insight={insight} insFresh={insFresh}
          history={history} turnEnds={turnEnds} stories={stories} t={t} />
      </MiniWindow>

      <main className={tab === 'story' ? 'tab-story' : ''}>
        <section className={`now ${nowState.cls}`}>
          <div className="state">{nowState.label}</div>
          <div className={`ja${nowState.placeholder ? ' placeholder' : ''}`}>{nowState.ja}</div>
          {nowState.en && <div className="en">{nowState.en}</div>}
        </section>

        <aside className={`panel ins${insOpen ? ' open' : ''}${insFresh ? ' fresh' : ''}`}>
          <div className="ins-body">
            {!insight && <p className="thinking">{t.insightEmpty}</p>}
            {insight && (
              <>
                <div className="lead">
                  <div className="situation">{t.insightHead}{insight.situation && t.situation(insight.situation)}</div>
                  <div className={`need${insight.ready ? '' : ' not-ready'}`}>
                    {insight.need}
                    {!insight.ready && <div className="thinking">{t.notReady}</div>}
                  </div>
                  <button className="toggle" onClick={() => setInsOpen((v) => !v)}>
                    {insOpen ? t.repliesClose : t.repliesOpen}
                  </button>
                </div>
                <div className="more">
                  <div className="label">{t.replies}</div>
                  {insight.replies.length > 0
                    ? insight.replies.map((r, i) => <div key={i} className="reply">{r}</div>)
                    : <p className="thinking">{insight.complete ? '—' : t.thinking}</p>}
                </div>
              </>
            )}
          </div>
        </aside>

        <section className="panel histp">
          <h2><span className="pc-title">{t.historyTitle}</span>{tabs}</h2>
          {!follow && (
            <button className="jump" onClick={() => { setFollow(true); histRef.current?.scrollTo({ top: 0, behavior: 'smooth' }); }}>
              {t.latest}
            </button>
          )}
          <div className="hist" ref={histRef} onScroll={onHistScroll}>
            {history.length === 0 && <p className="empty">{t.historyEmpty}</p>}
            {history.map((f) => (
              <div key={f.seq}>
                {turnEnds.includes(f.seq) && <div className="turn">{t.turnEnd}</div>}
                <div className="h" data-seq={f.seq}>
                  <div className="ja">{f.text}</div>
                  <div className="en">{f.source}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel storyp">
          <h2><span className="pc-title">{t.storyTitle}</span>{tabs}</h2>
          <div className="story">
            {stories.length === 0 && <p className="empty">{t.storyEmpty}</p>}
            {stories.map((st) => (
              <div key={st.index} className={`st${st.done ? '' : ' progress'}`}>
                <div className="top">
                  <time>{fmtTime(st.start)}</time>
                  {st.done
                    ? <button className="title" onClick={() => jumpTo(st.first)} title={t.storyJump}>{st.title}</button>
                    : <span className="title">{t.storyProgress(st.n)}</span>}
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

/**
 * Mini window content (Document Picture-in-Picture, always on top).
 * Short window: subtitle + need only. Taller than 300px (CSS media query in the PiP window):
 * also 会話 / あらすじ tabs below, newest first, Japanese only to fit the narrow window.
 */
function MiniView({ theme, now, recording, insight, insFresh, history, turnEnds, stories, t }: {
  theme: Theme; now: NowState; recording: boolean; insight: Insight | null; insFresh: boolean;
  history: FinalLine[]; turnEnds: number[]; stories: StoryChunk[]; t: Strings;
}) {
  const [tab, setTab] = useState<'conv' | 'story'>('conv');
  const [follow, setFollow] = useState(true);
  const histRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (follow) histRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [history.length, follow, tab]);
  const onHistScroll = () => {
    const el = histRef.current;
    if (el) setFollow(el.scrollTop < 48);
  };
  const jumpTo = (first?: string) => {
    const f = first && history.find((x) => x.source === first);
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

  return (
    <div className="ma mini" data-theme={theme}>
      <section className={`now ${now.cls}`}>
        <div className="state">{now.label}</div>
        <div className="ja-wrap">
          <div className={`ja${now.placeholder ? ' placeholder' : ''}`}>
            {recording || !now.placeholder ? now.ja : t.miniStartHint}
          </div>
        </div>
      </section>
      <div className={`mini-need${insFresh ? ' fresh' : ''}${insight && !insight.ready ? ' not-ready' : ''}`}
        title={insight?.need}>
        💡 {insight ? insight.need : t.miniNeedEmpty}
      </div>

      <section className="panel mini-more">
        <div className="mini-tabs">
          <button className={tab === 'conv' ? 'on' : ''} onClick={() => setTab('conv')}>{t.conv}</button>
          <button className={tab === 'story' ? 'on' : ''} onClick={() => setTab('story')}>{t.story}</button>
        </div>
        {tab === 'conv' && !follow && (
          <button className="jump" onClick={() => { setFollow(true); histRef.current?.scrollTo({ top: 0, behavior: 'smooth' }); }}>
            {t.latest}
          </button>
        )}
        {tab === 'conv' ? (
          <div className="hist" ref={histRef} onScroll={onHistScroll}>
            {history.length === 0 && <p className="empty">{t.historyEmpty}</p>}
            {history.map((f) => (
              <div key={f.seq}>
                {turnEnds.includes(f.seq) && <div className="turn">{t.turnEnd}</div>}
                <div className="h" data-seq={f.seq}><div className="ja">{f.text}</div></div>
              </div>
            ))}
          </div>
        ) : (
          <div className="story">
            {stories.length === 0 && <p className="empty">{t.storyEmpty}</p>}
            {stories.map((st) => (
              <div key={st.index} className={`st${st.done ? '' : ' progress'}`}>
                <div className="top">
                  <time>{fmtTime(st.start)}</time>
                  {st.done
                    ? <button className="title" onClick={() => jumpTo(st.first)} title={t.storyJump}>{st.title}</button>
                    : <span className="title">{t.storyProgress(st.n)}</span>}
                </div>
                {st.done && st.summary && <div className="sum">{st.summary}</div>}
              </div>
            ))}
          </div>
        )}
      </section>
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

/** Same utterance = same first 3 words (CJK: first 6 characters, there are no spaces). */
function sameUtterance(a: string, b: string, cjk = false): boolean {
  const head = cjk
    ? (s: string) => s.replace(/\s+/g, '').slice(0, 6)
    : (s: string) => s.trim().toLowerCase().split(/\s+/).slice(0, 3).join(' ');
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
