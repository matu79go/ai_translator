import asyncio
import json
import os
import re
import time
from typing import Optional, Literal

from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)
from openai import AsyncOpenAI, OpenAI

# Gemini SDK (optional)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

load_dotenv()

app = FastAPI(title="Intent-First Translation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# OpenAI clients (async for GPT-4, sync for GPT-5)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
openai_client_sync = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Gemini client
if GEMINI_AVAILABLE and GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-2.5-flash-lite")
else:
    gemini_model = None

# LLM model type
LLMModel = Literal["gpt4", "gpt5", "gemini", "gemma", "groq", "lmstudio", "gx10"]

# Groq model settings (switchable from UI)
GroqModel = Literal[
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "openai/gpt-oss-120b"
]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
groq_client = AsyncOpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY) if GROQ_API_KEY else None
groq_model_name: GroqModel = "llama-3.3-70b-versatile"  # Default model (fast & high quality)

# Ollama (Gemma 3 4B) settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
gemma_client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
# Gemma does not support concurrent requests, so limit with lock
gemma_lock = asyncio.Lock()
gemma_pending_text: str | None = None  # Pending text (keep only latest)

# LM Studio settings (no concurrent requests due to VRAM constraints)
LMSTUDIO_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "gemma-3-4b-it")  # Model name loaded in LM Studio
lmstudio_client = AsyncOpenAI(base_url=LMSTUDIO_BASE_URL, api_key="lm-studio")
lmstudio_lock = asyncio.Lock()  # Lock to prevent concurrent requests

# GX10 local vLLM (OpenAI-compatible). See docs/designs/meeting-assist-local.md
# vLLM batches concurrent requests, so translation and insight run in parallel without a lock.
# GX10_HOST: address of the GX10 box (set in backend/.env; not committed)
GX10_HOST = os.getenv("GX10_HOST", "localhost")
GX10_BASE_URL = os.getenv("GX10_BASE_URL", f"http://{GX10_HOST}:8001/v1")
GX10_MODEL = os.getenv("GX10_MODEL", "gemma4-26b-a4b")
gx10_client = AsyncOpenAI(base_url=GX10_BASE_URL, api_key="gx10")
# Nemotron ASR server on GX10 (asr_server/asr_server.py, spec §4.2). Deepgram-like protocol.
NEMOTRON_ASR_URL = os.getenv("NEMOTRON_ASR_URL", f"ws://{GX10_HOST}:8092/ws")
ASREngine = Literal["deepgram", "nemotron"]
INSIGHT_HISTORY_MAX = 30  # utterances of context given to the insight prompt
# "Story" (あらすじ): utterances are grouped into chunks and each closed chunk gets a short summary.
STORY_CHUNK_UTTERANCES = int(os.getenv("STORY_CHUNK_UTTERANCES", "8"))
STORY_CHUNK_SECONDS = float(os.getenv("STORY_CHUNK_SECONDS", "90"))
STORY_TURN_END_MIN_UTTERANCES = 4  # also close a chunk when the other party's turn ends, if this many
# Deepgram finalizes fragments mid-sentence ("...and my task" / "was to source a supplier").
# In gx10 mode fragments are joined until UtteranceEnd (utterance_end_ms of no words),
# and the final translation is made for the whole utterance.
# Speakers who never pause for 1s made utterances grow to the cap, and the partial line kept
# re-translating the same long text from the start (looked like repeating). So also cut at
# Deepgram speech_final (short pause) once the utterance has enough words; short fragments
# like "so" / "and it was" are still joined with the next one.
UTTERANCE_FLUSH_MIN_WORDS = int(os.getenv("UTTERANCE_FLUSH_MIN_WORDS", "12"))
UTTERANCE_MAX_WORDS = int(os.getenv("UTTERANCE_MAX_WORDS", "30"))
# After UtteranceEnd (already 1s without words), wait this long before the insight: ~1.5s total
GX10_INSIGHT_SILENCE_MS = 500

# Supported languages
LanguageCode = Literal["en", "ja", "es", "zh"]

LANG_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "es": "Spanish",
    "zh": "Chinese",
}

DEEPGRAM_LANG_MAP = {
    "en": "en-US",
    "ja": "ja",
    "es": "es",
    "zh": "zh",
}

# Nemotron 3.5 ASR is multilingual; the locale is chosen per connection (asr_server ?language=).
NEMOTRON_LANG_MAP = {
    "en": "en-US",
    "ja": "ja-JP",
    "es": "es-ES",
    "zh": "zh-CN",
}

# Languages written without spaces between words (docs/designs/ja-to-en.md §3).
CJK_LANGS = ("ja", "zh")
_CJK_CHAR = r"[\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]"


def normalize_asr_text(text: str, lang: str) -> str:
    """Drop spaces next to CJK characters (Nemotron "。 それでは", Deepgram word spacing)."""
    if lang in CJK_LANGS:
        text = re.sub(rf"(?<={_CJK_CHAR})\s+|\s+(?={_CJK_CHAR})", "", text).strip()
    return text


def join_text(parts: list[str], lang: str) -> str:
    return ("" if lang in CJK_LANGS else " ").join(parts)


def text_units(text: str, lang: str) -> int:
    """Length in "words": CJK counts 2 characters as 1 word, so the word thresholds still apply."""
    if lang in CJK_LANGS:
        return len(re.sub(r"[\s。、？！「」.,?!]", "", text)) // 2
    return len(text.split())

# Keyword → topic prediction mapping (for instant response)
KEYWORD_TOPICS = {
    # Schedule / Time
    "schedule": "日程調整",
    "meeting": "会議",
    "tuesday": "火曜日",
    "wednesday": "水曜日",
    "monday": "月曜日",
    "thursday": "木曜日",
    "friday": "金曜日",
    "tomorrow": "明日",
    "today": "今日",
    "next week": "来週",
    "afternoon": "午後",
    "morning": "午前",
    # Business
    "budget": "予算",
    "project": "プロジェクト",
    "deadline": "締め切り",
    "report": "レポート",
    "review": "レビュー",
    "approval": "承認",
    "proposal": "提案",
    "client": "クライアント",
    "customer": "顧客",
    # Actions
    "discuss": "議論",
    "confirm": "確認",
    "update": "更新",
    "check": "確認",
    "send": "送信",
    "prepare": "準備",
    "finish": "完了",
    "start": "開始",
    # Questions / Requests
    "can we": "依頼",
    "could you": "依頼",
    "would you": "依頼",
    "please": "依頼",
    "how about": "提案",
    "what about": "提案",
    "let's": "提案",
}

# Dynamic prompt builders based on source/target language

def build_speed_prompt(source_lang: str, target_lang: str) -> str:
    """Speed-focused prompt: minimal analysis → translation (for partial results)"""
    src = LANG_NAMES.get(source_lang, "English")
    tgt = LANG_NAMES.get(target_lang, "Japanese")
    return f"""You are a real-time simultaneous interpreter.
Translate {src} speech into {tgt} in real time.

Processing order (important):
1. Determine dialogue_act
2. Extract intent_label
3. Identify key information (slots)
4. Generate full_translation considering the above

Translation rules:
- Translate into natural, fluent {tgt}
- Even if the sentence is incomplete, infer the intent and produce a complete {tgt} sentence

Other rules:
- intent_label must be in {tgt}, max 10 characters/words
- Output must be in the specified JSON format only

Output JSON format (output in this order):
{{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "intent_label": "short {tgt} label",
  "slots": {{"when": "", "who": "", "where": "", "what": ""}},
  "full_translation": "natural {tgt} translation considering context",
  "key_terms": ["important words"],
  "confidence": 0.0-1.0,
  "is_meaning_stable": true/false
}}

IMPORTANT: Output ONLY valid JSON, nothing else."""


def build_quality_prompt(source_lang: str, target_lang: str) -> str:
    """Quality-focused prompt: analyze context first, then translate (for is_final results)"""
    src = LANG_NAMES.get(source_lang, "English")
    tgt = LANG_NAMES.get(target_lang, "Japanese")
    return f"""You are a real-time interpretation assistant.
Extract the speaker's intent and key information from {src} input, and provide an accurate {tgt} translation.

Rules:
- First analyze dialogue_act and key information (slots)
- Then provide an accurate, context-aware {tgt} translation based on your analysis
- Output must be in the specified JSON format only
- intent_label must be in {tgt}, max 10 characters/words

Output JSON format (output in this order):
{{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "slots": {{"when": "", "who": "", "where": "", "what": ""}},
  "key_terms": ["important words"],
  "confidence": 0.0-1.0,
  "is_meaning_stable": true/false,
  "intent_label": "short {tgt} label",
  "full_translation": "accurate {tgt} translation considering context"
}}

IMPORTANT: Output ONLY valid JSON, nothing else."""


def build_experimental_prompt(source_lang: str, target_lang: str) -> str:
    """Experimental prompt: output speculative translation first"""
    src = LANG_NAMES.get(source_lang, "English")
    tgt = LANG_NAMES.get(target_lang, "Japanese")
    return f"""You are a real-time simultaneous interpreter.
Like a human interpreter, start translating speculatively before the sentence is complete.
Translate from {src} to {tgt}.

Processing order (important - output in this exact order):
1. dialogue_act: determine the dialogue act immediately
2. intent_label: extract intent in max 10 characters/words in {tgt}
3. speculative_translation: output a speculative {tgt} translation NOW (speed over accuracy)
4. slots: identify key information
5. full_translation: accurate {tgt} translation considering all information

Speculative translation rules (speed first):
- Speculate based on dialogue_act + intent_label alone
- Does not need to be perfect, just convey the right direction
- Example: PROPOSAL + "scheduling" → a brief {tgt} sentence about scheduling proposal

Output JSON format (strict order):
{{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "intent_label": "short {tgt} label",
  "speculative_translation": "speculative {tgt} translation from dialogue_act+intent_label",
  "slots": {{"when": "", "who": "", "where": "", "what": ""}},
  "full_translation": "accurate {tgt} translation considering all information",
  "confidence": 0.0-1.0
}}

IMPORTANT: Output ONLY valid JSON, nothing else."""


def build_translation_only_prompt(source_lang: str, target_lang: str) -> str:
    """GX10 meeting mode: plain-text translation. No JSON, so characters stream from the first token."""
    src = LANG_NAMES.get(source_lang, "English")
    tgt = LANG_NAMES.get(target_lang, "Japanese")
    return f"""You are a real-time simultaneous interpreter. Translate the {src} utterance into natural {tgt}.
- If the utterance is incomplete, still produce a natural {tgt} sentence for what has been said so far.
- Use the previous utterances only as context; translate only the current utterance.
- Never translate or repeat the previous utterances, even if the current one is a short fragment.
- Output ONLY the {tgt} translation. One version only. No quotes, no notes, no alternatives."""


def build_insight_prompt(target_lang: str = "ja") -> str:
    """GX10 meeting mode: the other party's situation, underlying need, and replies.

    situation comes first so the model fixes whose perspective it is before writing the need
    (without it, a lecture's topic was written as the speaker's wish and echoed back as the
    user's wish: "知りたい" -> "教えてください"). It costs ~0.16s before the need appears.
    The Japanese text is the original (en -> ja); other target languages use the English version.
    """
    if target_lang != "ja":
        tgt = LANG_NAMES.get(target_lang, "English")
        return f"""You are a conversation assistant supporting a user in an online meeting.
"They" are the people speaking in another language; "the user" listens and replies to them next.
From the flow of what they said, think in this order and output JSON. Write every value in {tgt}.
- situation: what position they are speaking from right now (e.g. asking a question / explaining or presenting / making a proposal / raising a concern / making small talk), max 6 words
- need: what they really want from the user, in one sentence of max 15 words. Not a rephrasing of the topic: their underlying wish or concern. If they are explaining or presenting, it is a speaker's wish such as "wants to know it landed" or "wants agreement"
- ready: false if they are still in the preamble and the main point is not visible yet
- replies: two replies the user could say to them. They must meet or answer the need. Do not restate the need as the user's own wish. Natural spoken {tgt} the user can say as is, max 20 words each
JSON only, in this order: {{"situation":"...","need":"...","ready":true,"replies":["...","..."]}}"""
    return """あなたはオンライン会議で日本人ユーザーを支える会話アシスタントです。
「相手」は英語で話している人、「ユーザー」はそれを聞いていて、次に相手に返事をする人です。
相手の発言の流れから、次の順に考えてJSONで出力してください。
- situation: 相手が今どういう立場で話しているか（例: 質問している / 説明・講義している / 提案している / 不満を伝えている / 雑談している）を日本語で15字以内
- need: 相手がユーザーに本当に求めているもの（本音）を日本語で1文、30字以内。話のテーマの言い換えではなく、相手の欲求・懸念を書く。説明・講義している相手なら「伝わったか」「納得してほしい」など話し手としての欲求になる
- ready: 相手がまだ前置きの段階で本題が見えなければ false
- replies: ユーザーが相手に向かって言う返事を2つ。need を満たす・応える内容にする。need をユーザー自身の願望として言い直さない。そのまま言える自然な日本語の口語、各40字以内
JSONのみ、この順で: {"situation":"...","need":"...","ready":true,"replies":["...","..."]}"""


def build_story_prompt(target_lang: str = "ja") -> str:
    """GX10 meeting mode: summary of one chunk of the conversation, for the running synopsis."""
    if target_lang != "ja":
        tgt = LANG_NAMES.get(target_lang, "English")
        return f"""You take notes for an online meeting.
Summarize one chunk of the conversation (what the other party said) briefly in {tgt}.
- title: a heading for the topic of this chunk, max 6 words (e.g. Introductions and background / Why this company / Concerns about price)
- summary: what was said, max 30 words. Keep proper nouns and numbers
- If previous headings are given, write a heading that continues the story without repeating them
JSON only: {{"title":"...","summary":"..."}}"""
    return """あなたはオンライン会議の議事メモ係です。
英語の会話の一区切り（相手の発言）を、日本語で短くまとめてください。
- title: この区切りの話題の見出し。日本語15字以内（例: 自己紹介と経歴 / 志望理由 / 価格への懸念）
- summary: 何が話されたかのあらすじ。日本語60字以内。固有名詞・数字は残す
- これまでの見出しが与えられたら、話の続きとして重複しない見出しにする
JSONのみ: {"title":"...","summary":"..."}"""


class StorySummarizer:
    """Groups utterances into chunks (8 utterances / 90 s / turn end) and summarizes each closed chunk.

    Messages: story_progress {index, start, n} while a chunk fills up;
              story {index, start, n, first, title, summary} when it is summarized.
    `start` is seconds since the session started; `first` is the first utterance's text
    (= translation source_text) so the page can jump to it.
    """

    def __init__(self, websocket: WebSocket, target_lang: str = "ja"):
        self.websocket = websocket
        self.target_lang = target_lang
        self.t0 = time.time()
        self.items: list[str] = []
        self.chunk_start = 0.0
        self.index = 0
        self.titles: list[str] = []

    async def add(self, utterance: str):
        now = time.time() - self.t0
        if not self.items:
            self.chunk_start = now
        self.items.append(utterance)
        if len(self.items) >= STORY_CHUNK_UTTERANCES or (
                now - self.chunk_start >= STORY_CHUNK_SECONDS and len(self.items) >= 2):
            await self.close()
        else:
            await self._send({"type": "story_progress", "index": self.index,
                              "start": round(self.chunk_start), "n": len(self.items)})

    async def on_turn_end(self):
        if len(self.items) >= STORY_TURN_END_MIN_UTTERANCES:
            await self.close()

    async def close(self):
        if not self.items:
            return
        items, index, start = self.items, self.index, self.chunk_start
        self.items, self.index = [], self.index + 1
        asyncio.create_task(self._summarize(items, index, start))

    async def _summarize(self, items: list[str], index: int, start: float):
        ja = self.target_lang == "ja"
        prev_label, chunk_label = ("これまでの見出し: ", "この区切りの発言:\n") if ja else ("Previous headings: ", "This chunk:\n")
        prev = (prev_label + " / ".join(self.titles[-5:]) + "\n\n") if self.titles else ""
        t = time.time()
        try:
            r = await gx10_client.chat.completions.create(
                model=GX10_MODEL, temperature=0.3, max_tokens=200,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": build_story_prompt(self.target_lang)},
                          {"role": "user", "content": prev + chunk_label + "\n".join(items)}],
            )
            out = r.choices[0].message.content or ""
            data = json.loads(out[out.find("{"):out.rfind("}") + 1])
        except Exception as e:
            print(f"[Story] #{index} error: {type(e).__name__}: {e}", flush=True)
            data = {"title": "（要約できませんでした）" if ja else "(Could not summarize)", "summary": ""}
        title = str(data.get("title", ""))[:30 if ja else 60]
        self.titles.append(title)
        print(f"[Story] #{index} ({len(items)} utt) in {(time.time() - t)*1000:.0f}ms: {title}", flush=True)
        await self._send({"type": "story", "index": index, "start": round(start), "n": len(items),
                          "first": items[0], "title": title, "summary": str(data.get("summary", ""))})

    async def _send(self, msg: dict):
        try:
            await self.websocket.send_json(msg)
        except Exception:
            pass


def build_summary_prompt(target_lang: str) -> str:
    """Summary prompt in target language"""
    tgt = LANG_NAMES.get(target_lang, "Japanese")
    return f"""You are a conversation summarization AI.
Extract an overall summary and main topics from multiple utterances.
Output in {tgt}.

Output JSON format:
{{
  "summary": "detailed summary of the conversation in {tgt} (100-150 chars/words)",
  "topics": ["topic1", "topic2"]
}}

Rules:
- summary should include the content, purpose, and conclusions of the conversation
- Clearly state what is being discussed and what decisions are being made
- topics: max 5
- Output in {tgt}
- Output ONLY valid JSON, nothing else"""


class KeywordPredictor:
    """Predicts topics from keywords instantly"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.last_hints: set[str] = set()

    async def predict(self, text: str):
        """Extract keywords from text and send hints immediately"""
        text_lower = text.lower()
        hints = set()

        for keyword, topic in KEYWORD_TOPICS.items():
            if keyword in text_lower:
                hints.add(topic)

        # Only send if there are new hints
        if hints and hints != self.last_hints:
            self.last_hints = hints
            await self.websocket.send_json({
                "type": "hints",
                "hints": list(hints)[:5],  # Max 5
            })


class SummaryGenerator:
    """Generates conversation summaries (streaming, multi-model)"""

    def __init__(self, websocket: WebSocket, silence_threshold_ms: int = 1500,
                 model: LLMModel = "gpt4", target_lang: LanguageCode = "ja"):
        self.websocket = websocket
        self.silence_threshold_ms = silence_threshold_ms
        self.model = model  # "gpt4" or "gemini"
        self.target_lang = target_lang
        self.transcript_history: list[str] = []
        self.pending_summary_task: Optional[asyncio.Task] = None
        self.last_summary_hash = ""

    def add_transcript(self, text: str, start_timer: bool = True):
        """Add finalized transcript. start_timer=False only records it (gx10: utterance cut at
        a short pause, the turn is not over)."""
        self.transcript_history.append(text)
        if not start_timer:
            return

        if self.pending_summary_task and not self.pending_summary_task.done():
            self.pending_summary_task.cancel()

        self._waiting_silence = True
        self.pending_summary_task = asyncio.create_task(
            self._delayed_summary()
        )

    def restart_silence_timer(self):
        if self.pending_summary_task and not self.pending_summary_task.done():
            self.pending_summary_task.cancel()
        self._waiting_silence = True
        self.pending_summary_task = asyncio.create_task(self._delayed_summary())

    def on_speech_activity(self):
        """A transcript arrived: the speaker is still talking, so cancel the waiting silence timer.

        Only in gx10 mode, where the timer marks the end of the other party's turn. Without
        this, the insight fires mid-turn. Only a timer that is still waiting is cancelled;
        a generation already running is left alone.
        """
        if self.model != "gx10" or not getattr(self, "_waiting_silence", False):
            return
        # The speaker resumed: the turn is not over. The timer starts again at the next
        # utterance end (TranscriptionManager._flush_utterance -> add_transcript).
        if self.pending_summary_task and not self.pending_summary_task.done():
            self.pending_summary_task.cancel()
        self._waiting_silence = False

    async def _delayed_summary(self):
        """Generate summary after silence"""
        await asyncio.sleep(self.silence_threshold_ms / 1000)
        self._waiting_silence = False  # not reached if cancelled

        if self.model == "gx10":
            hook = getattr(self, "on_turn_end", None)
            if hook:
                await hook()
            await self._generate_insight_gx10()
            return

        if len(self.transcript_history) >= 2:
            if self.model == "gemini":
                await self._generate_summary_gemini()
            elif self.model == "gemma":
                # Gemma (local LLM) does not support concurrent requests, use GPT-4 for summary
                print(f"[Summary] Gemma selected, but using GPT-4 for summary (concurrent requests not supported)", flush=True)
                await self._generate_summary_streaming(use_gpt4_fallback=True)
            elif self.model == "lmstudio":
                # LM Studio supports parallel requests
                await self._generate_summary_lmstudio()
            else:
                await self._generate_summary_streaming()

    async def _generate_summary_streaming(self, use_gpt4_fallback: bool = False):
        """Generate summary via streaming"""
        if not openai_client:
            return

        current_hash = hash(tuple(self.transcript_history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        all_text = "\n".join(self.transcript_history)
        provider_note = " (GPT-4 fallback)" if use_gpt4_fallback else ""
        print(f"[Summary{provider_note}] Streaming summary for {len(self.transcript_history)} utterances")

        try:
            stream = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": build_summary_prompt(self.target_lang)},
                    {"role": "user", "content": f"Summarize the following utterances:\n\n{all_text}"},
                ],
                temperature=0.3,
                max_tokens=200,
                stream=True,
            )

            full_response = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    full_response += chunk.choices[0].delta.content

                    # Check if JSON is complete
                    try:
                        # Try parsing even partial JSON
                        if "{" in full_response and "}" in full_response:
                            json_match = re.search(r'\{[^{}]*\}', full_response)
                            if json_match:
                                partial_data = json.loads(json_match.group())
                                if "summary" in partial_data:
                                    await self.websocket.send_json({
                                        "type": "summary",
                                        "summary": partial_data.get("summary", ""),
                                        "topics": partial_data.get("topics", []),
                                        "streaming": True,
                                    })
                    except json.JSONDecodeError:
                        pass

            # Send final result
            try:
                final_data = json.loads(full_response)
                await self.websocket.send_json({
                    "type": "summary",
                    "summary": final_data.get("summary", ""),
                    "topics": final_data.get("topics", []),
                    "streaming": False,
                })
            except json.JSONDecodeError:
                print(f"[Summary] Failed to parse final response: {full_response[:100]}")

        except Exception as e:
            print(f"[Summary] Error: {type(e).__name__}: {e}")

    async def _generate_summary_lmstudio(self):
        """Generate summary via LM Studio (serialized with lock)"""
        # Skip if lock is busy
        if lmstudio_lock.locked():
            print(f"[Summary LMStudio] Skipped (lock busy)")
            return

        current_hash = hash(tuple(self.transcript_history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        all_text = "\n".join(self.transcript_history)

        async with lmstudio_lock:
            print(f"[Summary LMStudio] Streaming summary for {len(self.transcript_history)} utterances")

            try:
                stream = await lmstudio_client.chat.completions.create(
                    model=LMSTUDIO_MODEL,
                    messages=[
                        {"role": "system", "content": build_summary_prompt(self.target_lang)},
                        {"role": "user", "content": f"Summarize the following utterances:\n\n{all_text}"},
                    ],
                    temperature=0.3,
                    max_tokens=200,
                    stream=True,
                )

                full_response = ""
                async for chunk in stream:
                    if chunk.choices[0].delta.content:
                        full_response += chunk.choices[0].delta.content

                        # Check if JSON is complete
                        try:
                            if "{" in full_response and "}" in full_response:
                                json_match = re.search(r'\{[^{}]*\}', full_response)
                                if json_match:
                                    partial_data = json.loads(json_match.group())
                                    if "summary" in partial_data:
                                        await self.websocket.send_json({
                                            "type": "summary",
                                            "summary": partial_data.get("summary", ""),
                                            "topics": partial_data.get("topics", []),
                                            "streaming": True,
                                        })
                        except json.JSONDecodeError:
                            pass

                # Send final result
                try:
                    final_data = json.loads(full_response)
                    await self.websocket.send_json({
                        "type": "summary",
                        "summary": final_data.get("summary", ""),
                        "topics": final_data.get("topics", []),
                        "streaming": False,
                    })
                except json.JSONDecodeError:
                    print(f"[Summary LMStudio] Failed to parse final response: {full_response[:100]}")

            except Exception as e:
                print(f"[Summary LMStudio] Error: {type(e).__name__}: {e}")

    async def _generate_insight_gx10(self):
        """Meeting mode: after a turn ends (silence), stream the other party's need, then reply ideas."""
        if not self.transcript_history:
            return
        history = self.transcript_history[-INSIGHT_HISTORY_MAX:]
        current_hash = hash(tuple(history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        ja = self.target_lang == "ja"
        conversation = "\n".join(f"{'相手' if ja else 'They'}: {t}" for t in history)
        start = time.time()
        print(f"[Insight GX10] Start for {len(history)} utterances", flush=True)
        out = ""
        sent_need = False
        try:
            stream = await gx10_client.chat.completions.create(
                model=GX10_MODEL, temperature=0.5, max_tokens=300, stream=True,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": build_insight_prompt(self.target_lang)},
                          {"role": "user", "content": ("会話の流れ:\n" if ja else "Conversation so far:\n") + conversation}],
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if not delta:
                    continue
                out += delta
                if not sent_need:
                    m = re.search(r'"need"\s*:\s*"([^"]+)"', out)
                    if m:
                        sent_need = True
                        sit = re.search(r'"situation"\s*:\s*"([^"]*)"', out)
                        print(f"[Insight GX10] @{(time.time() - start)*1000:.0f}ms NEED: {m.group(1)}", flush=True)
                        await self.websocket.send_json({
                            "type": "insight_partial",
                            "need": m.group(1),
                            "situation": sit.group(1) if sit else "",
                        })
            data = json.loads(out[out.find("{"):out.rfind("}") + 1])
            print(f"[Insight GX10] Complete in {(time.time() - start)*1000:.0f}ms ready={data.get('ready')}", flush=True)
            await self.websocket.send_json({
                "type": "insight",
                "need": data.get("need", ""),
                "situation": data.get("situation", ""),
                "ready": bool(data.get("ready", True)),
                "replies": [r for r in data.get("replies", []) if isinstance(r, str)],
            })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Insight GX10] Error: {type(e).__name__}: {e} | {out[:100]}", flush=True)

    async def _generate_summary_gemini(self):
        """Generate summary via Gemini"""
        if not gemini_model:
            return

        current_hash = hash(tuple(self.transcript_history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        all_text = "\n".join(self.transcript_history)
        print(f"[Summary-Gemini] Generating summary for {len(self.transcript_history)} utterances")

        try:
            summary_prompt = build_summary_prompt(self.target_lang)
            full_prompt = f"{summary_prompt}\n\nSummarize the following utterances:\n\n{all_text}"

            response = await asyncio.to_thread(
                lambda: gemini_model.generate_content(
                    full_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=300,
                    ),
                )
            )

            full_response = response.text if response.text else ""
            print(f"[Summary-Gemini] Response received", flush=True)

            # Extract JSON
            try:
                json_match = re.search(r'\{[\s\S]*\}', full_response)
                if json_match:
                    final_data = json.loads(json_match.group())
                    await self.websocket.send_json({
                        "type": "summary",
                        "summary": final_data.get("summary", ""),
                        "topics": final_data.get("topics", []),
                        "streaming": False,
                    })
            except json.JSONDecodeError:
                print(f"[Summary-Gemini] Failed to parse response: {full_response[:100]}")

        except Exception as e:
            print(f"[Summary-Gemini] Error: {type(e).__name__}: {e}")


class IntentEstimator:
    """Intent estimation class (streaming, multi-model)"""

    def __init__(self, websocket: WebSocket, debounce_ms: int = 300,
                 is_final_only: bool = False, min_words: int = 5,
                 model: LLMModel = "gpt4", experimental: bool = False,
                 source_lang: LanguageCode = "en", target_lang: LanguageCode = "ja"):
        self.websocket = websocket
        self.debounce_ms = debounce_ms
        self.is_final_only = is_final_only  # If True, only process is_final=True
        self.min_words = min_words  # Minimum word count for partial processing
        self.model = model  # "gpt4", "gpt5", "gemini"
        self.experimental = experimental  # Experimental mode (speculative translation)
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.last_text = ""
        self.last_call_time = 0
        self.pending_task: Optional[asyncio.Task] = None
        self.context_history: list[str] = []

    async def estimate(self, text: str, is_final: bool):
        """Estimate intent from text (streaming)"""
        # Check client based on model
        if self.model in ("gpt4", "gpt5") and not openai_client:
            return
        if self.model == "gemini" and not gemini_model:
            return
        # gemma uses gemma_client (always available)
        if not text.strip():
            return

        # A final identical to the last interim must still be processed: it is the
        # only request that produces the final translation.
        if text == self.last_text and not is_final:
            return

        self.last_text = text
        current_time = time.time() * 1000
        word_count = text_units(text, self.source_lang)

        if is_final:
            # A debounced partial still waiting would run after this final and re-show
            # the same sentence as an in-progress line.
            if self.pending_task and not self.pending_task.done():
                self.pending_task.cancel()
            self.context_history.append(text)
            if len(self.context_history) > 5:
                self.context_history.pop(0)
            await self._call_llm_streaming(text, is_final)
            return

        # Process partial (in-progress)
        # In is_final_only mode, skip partials
        if self.is_final_only:
            print(f"[LLM] Skipped partial (is_final_only mode): {text[:30]}...", flush=True)
            return

        # Skip short text (prevent fragmented translations)
        if word_count < self.min_words:
            print(f"[LLM] Skipped partial ({word_count} words < {self.min_words}): {text[:30]}...", flush=True)
            return

        time_since_last = current_time - self.last_call_time

        if self.pending_task and not self.pending_task.done():
            self.pending_task.cancel()

        if time_since_last < self.debounce_ms:
            wait_time = (self.debounce_ms - time_since_last) / 1000
            self.pending_task = asyncio.create_task(
                self._delayed_call(text, is_final, wait_time)
            )
        else:
            await self._call_llm_streaming(text, is_final)

    async def _delayed_call(self, text: str, is_final: bool, delay: float):
        await asyncio.sleep(delay)
        await self._call_llm_streaming(text, is_final)

    async def _call_llm_streaming(self, text: str, is_final: bool):
        """Call LLM via streaming to estimate intent"""
        if self.model == "gemini":
            await self._call_gemini(text, is_final)
        elif self.model == "gemma":
            await self._call_gemma(text, is_final)
        elif self.model == "lmstudio":
            await self._call_lmstudio(text, is_final)
        elif self.model == "gx10":
            await self._call_gx10(text, is_final)
        elif self.model == "groq":
            await self._call_groq(text, is_final)
        elif self.model == "gpt5":
            await self._call_gpt5(text, is_final)
        else:
            await self._call_gpt4(text, is_final)

    async def _call_gpt4(self, text: str, is_final: bool):
        """GPT-4o-mini API streaming"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # Use experimental prompt if in experimental mode
        if self.experimental:
            system_prompt = build_experimental_prompt(self.source_lang, self.target_lang)
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = build_quality_prompt(self.source_lang, self.target_lang)
            mode = "QUALITY"
        else:
            system_prompt = build_speed_prompt(self.source_lang, self.target_lang)
            mode = "SPEED"
        print(f"[GPT-4] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"

        try:
            stream = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                stream=True,
            )

            full_response = ""
            sent_intent_partial = False
            sent_translation_partial = False
            sent_speculative = False  # For experimental mode
            first_chunk_time = None

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        print(f"[GPT-4] First chunk in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                    full_response += chunk.choices[0].delta.content

                    # Experimental mode: send speculative_translation early
                    if self.experimental and not sent_speculative and '"speculative_translation"' in full_response:
                        try:
                            match = re.search(r'"speculative_translation"\s*:\s*"([^"]+)"', full_response)
                            if match:
                                speculative = match.group(1)
                                spec_time = (time.time() - start_time) * 1000
                                print(f"[GPT-4] @{spec_time:.0f}ms SPECULATIVE: {speculative[:40]}...", flush=True)
                                await self.websocket.send_json({
                                    "type": "speculative_translation",
                                    "translation": speculative,
                                    "source_text": text,
                                })
                                sent_speculative = True
                        except:
                            pass

                    await self._process_streaming_chunk(
                        full_response, text, sent_intent_partial, sent_translation_partial
                    )
                    if not sent_intent_partial and '"intent_label"' in full_response:
                        sent_intent_partial = True
                    if not sent_translation_partial and '"full_translation"' in full_response:
                        full_time = (time.time() - start_time) * 1000
                        print(f"[GPT-4] @{full_time:.0f}ms FULL_TRANSLATION detected", flush=True)
                        sent_translation_partial = True

            await self._finalize_response(full_response, text, is_final, start_time, "GPT-4")

        except Exception as e:
            print(f"[GPT-4] Error: {type(e).__name__}: {e}")

    async def _call_gpt5(self, text: str, is_final: bool):
        """GPT-5 API streaming (new responses API)"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        system_prompt = build_quality_prompt(self.source_lang, self.target_lang) if is_final else build_speed_prompt(self.source_lang, self.target_lang)
        mode = "QUALITY" if is_final else "SPEED"
        print(f"[GPT-5] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"

        try:
            # GPT-5 uses responses.stream API (sync, run in thread)
            def run_gpt5_stream():
                full_response = ""
                stream = openai_client_sync.responses.stream(
                    model="gpt-5-mini",
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_output_tokens=500,
                )
                for event in stream:
                    if event.type == "response.output_text.delta":
                        full_response += event.delta
                return full_response

            full_response = await asyncio.to_thread(run_gpt5_stream)
            print(f"[GPT-5] Response received in {(time.time() - start_time)*1000:.0f}ms", flush=True)

            await self._finalize_response(full_response, text, is_final, start_time, "GPT-5")

        except Exception as e:
            print(f"[GPT-5] Error: {type(e).__name__}: {e}")

    async def _call_gemini(self, text: str, is_final: bool):
        """Gemini API call"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        system_prompt = build_quality_prompt(self.source_lang, self.target_lang) if is_final else build_speed_prompt(self.source_lang, self.target_lang)
        mode = "QUALITY" if is_final else "SPEED"
        print(f"[Gemini] Start [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        try:
            # Gemini API (non-streaming, run via asyncio)
            response = await asyncio.to_thread(
                lambda: gemini_model.generate_content(
                    full_prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=1000,
                    ),
                )
            )

            full_response = response.text if response.text else ""
            print(f"[Gemini] Response received in {(time.time() - start_time)*1000:.0f}ms", flush=True)

            await self._finalize_response(full_response, text, is_final, start_time, "Gemini")

        except Exception as e:
            print(f"[Gemini] Error: {type(e).__name__}: {e}")

    async def _call_gemma(self, text: str, is_final: bool):
        """Gemma 3 (Ollama) API streaming"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # Use experimental prompt if in experimental mode
        if self.experimental:
            system_prompt = build_experimental_prompt(self.source_lang, self.target_lang)
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = build_quality_prompt(self.source_lang, self.target_lang)
            mode = "QUALITY"
        else:
            system_prompt = build_speed_prompt(self.source_lang, self.target_lang)
            mode = "SPEED"
        print(f"[Gemma] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"

        try:
            stream = await gemma_client.chat.completions.create(
                model="gemma3:4b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                stream=True,
            )

            full_response = ""
            sent_intent_partial = False
            sent_translation_partial = False
            sent_speculative = False
            first_chunk_time = None

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        print(f"[Gemma] First chunk in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                    full_response += chunk.choices[0].delta.content

                    # Experimental mode: send speculative_translation early
                    if self.experimental and not sent_speculative and '"speculative_translation"' in full_response:
                        try:
                            match = re.search(r'"speculative_translation"\s*:\s*"([^"]+)"', full_response)
                            if match:
                                speculative = match.group(1)
                                spec_time = (time.time() - start_time) * 1000
                                print(f"[Gemma] @{spec_time:.0f}ms SPECULATIVE: {speculative[:40]}...", flush=True)
                                await self.websocket.send_json({
                                    "type": "speculative_translation",
                                    "translation": speculative,
                                    "source_text": text,
                                })
                                sent_speculative = True
                        except:
                            pass

                    await self._process_streaming_chunk(
                        full_response, text, sent_intent_partial, sent_translation_partial
                    )
                    if not sent_intent_partial and '"intent_label"' in full_response:
                        sent_intent_partial = True
                    if not sent_translation_partial and '"full_translation"' in full_response:
                        full_time = (time.time() - start_time) * 1000
                        print(f"[Gemma] @{full_time:.0f}ms FULL_TRANSLATION detected", flush=True)
                        sent_translation_partial = True

            await self._finalize_response(full_response, text, is_final, start_time, "Gemma")

        except Exception as e:
            print(f"[Gemma] Error: {type(e).__name__}: {e}")

    async def _call_lmstudio(self, text: str, is_final: bool):
        """LM Studio API streaming (serialized with lock)"""
        # Skip if lock is busy for non-final requests
        # For is_final, wait for lock (must process)
        if not is_final and lmstudio_lock.locked():
            print(f"[LMStudio] Skipped (lock busy, partial): {text[:30]}...", flush=True)
            return

        async with lmstudio_lock:
            self.last_call_time = time.time() * 1000
            start_time = time.time()

            # Use experimental prompt if in experimental mode
            if self.experimental:
                system_prompt = build_experimental_prompt(self.source_lang, self.target_lang)
                mode = "EXPERIMENTAL"
            elif is_final:
                system_prompt = build_quality_prompt(self.source_lang, self.target_lang)
                mode = "QUALITY"
            else:
                system_prompt = build_speed_prompt(self.source_lang, self.target_lang)
                mode = "SPEED"
            print(f"[LMStudio] Start streaming [{mode}] for: {text[:30]}...", flush=True)

            context = ""
            if self.context_history:
                context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

            user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"

            try:
                stream = await lmstudio_client.chat.completions.create(
                    model=LMSTUDIO_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=500,
                    stream=True,
                )

                full_response = ""
                sent_intent_partial = False
                sent_translation_partial = False
                sent_speculative = False
                first_chunk_time = None

                async for chunk in stream:
                    if chunk.choices[0].delta.content:
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                            print(f"[LMStudio] First chunk in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                        full_response += chunk.choices[0].delta.content

                        # Experimental mode: send speculative_translation early
                        if self.experimental and not sent_speculative and '"speculative_translation"' in full_response:
                            try:
                                match = re.search(r'"speculative_translation"\s*:\s*"([^"]+)"', full_response)
                                if match:
                                    speculative = match.group(1)
                                    spec_time = (time.time() - start_time) * 1000
                                    print(f"[LMStudio] @{spec_time:.0f}ms SPECULATIVE: {speculative[:40]}...", flush=True)
                                    await self.websocket.send_json({
                                        "type": "speculative_translation",
                                        "translation": speculative,
                                        "source_text": text,
                                    })
                                    sent_speculative = True
                            except:
                                pass

                        await self._process_streaming_chunk(
                            full_response, text, sent_intent_partial, sent_translation_partial
                        )
                        if not sent_intent_partial and '"intent_label"' in full_response:
                            sent_intent_partial = True
                        if not sent_translation_partial and '"full_translation"' in full_response:
                            full_time = (time.time() - start_time) * 1000
                            print(f"[LMStudio] @{full_time:.0f}ms FULL_TRANSLATION detected", flush=True)
                            sent_translation_partial = True

                await self._finalize_response(full_response, text, is_final, start_time, "LMStudio")

            except Exception as e:
                print(f"[LMStudio] Error: {type(e).__name__}: {e}")

    async def _call_gx10(self, text: str, is_final: bool):
        """Meeting mode: stream a plain-text translation from GX10.

        Every request gets a sequence number sent with each message. A partial is sent when it
        finishes, unless a newer translation was already sent (dropping every superseded request
        would show nothing while someone talks without pause). Final results are always sent.
        """
        self.gx10_seq = getattr(self, "gx10_seq", 0) + 1
        seq = self.gx10_seq
        self.last_call_time = time.time() * 1000
        start = time.time()

        # On final, the current text was already appended to context_history by estimate().
        history = self.context_history[:-1] if is_final else self.context_history
        context = ("Previous utterances:\n" + "\n".join(history[-3:]) + "\n\n") if history else ""
        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"
        print(f"[GX10] #{seq} start [{'FINAL' if is_final else 'PARTIAL'}]: {text[:40]}", flush=True)

        out = ""
        try:
            stream = await gx10_client.chat.completions.create(
                model=GX10_MODEL, temperature=0.3, max_tokens=300, stream=True,
                messages=[{"role": "system", "content": build_translation_only_prompt(self.source_lang, self.target_lang)},
                          {"role": "user", "content": user_prompt}],
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if not delta:
                    continue
                if not out:
                    print(f"[GX10] #{seq} @{(time.time() - start)*1000:.0f}ms first chars", flush=True)
                out += delta
                if not is_final and seq < getattr(self, "gx10_sent_seq", 0):
                    print(f"[GX10] #{seq} superseded, dropping", flush=True)
                    return
            print(f"[GX10] #{seq} @{(time.time() - start)*1000:.0f}ms done: {out.strip()[:40]}", flush=True)
            # Send only the finished translation. Streaming token by token restarted the
            # displayed line from the first character on every new request (0.3-0.5s apart),
            # so it kept vanishing and reappearing. One translation takes ~0.3-0.8s.
            if not is_final:
                if seq > getattr(self, "gx10_sent_seq", 0):
                    self.gx10_sent_seq = seq
                    await self.websocket.send_json({
                        "type": "translation_partial",
                        "translation": out.strip(),
                        "source_text": text,
                        "seq": seq,
                        "is_final": False,
                    })
            else:
                self.gx10_sent_seq = max(getattr(self, "gx10_sent_seq", 0), seq)
                await self.websocket.send_json({
                    "type": "translation_final",
                    "translation": out.strip(),
                    "source_text": text,
                    "seq": seq,
                })
        except Exception as e:
            print(f"[GX10] #{seq} Error: {type(e).__name__}: {e}", flush=True)

    async def _call_groq(self, text: str, is_final: bool):
        """Groq API streaming (ultra-fast)"""
        if not groq_client:
            print("[Groq] Client not configured (missing GROQ_API_KEY)", flush=True)
            return

        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # Use experimental prompt if in experimental mode
        if self.experimental:
            system_prompt = build_experimental_prompt(self.source_lang, self.target_lang)
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = build_quality_prompt(self.source_lang, self.target_lang)
            mode = "QUALITY"
        else:
            system_prompt = build_speed_prompt(self.source_lang, self.target_lang)
            mode = "SPEED"
        print(f"[Groq/{groq_model_name}] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "Previous utterances:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}Current utterance ({'final' if is_final else 'in progress'}):\n{text}"

        try:
            stream = await groq_client.chat.completions.create(
                model=groq_model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                stream=True,
            )

            full_response = ""
            sent_intent_partial = False
            sent_translation_partial = False
            sent_speculative = False
            first_chunk_time = None

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        print(f"[Groq] First chunk in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                    full_response += chunk.choices[0].delta.content

                    # Experimental mode: send speculative_translation early
                    if self.experimental and not sent_speculative and '"speculative_translation"' in full_response:
                        try:
                            match = re.search(r'"speculative_translation"\s*:\s*"([^"]+)"', full_response)
                            if match:
                                speculative = match.group(1)
                                spec_time = (time.time() - start_time) * 1000
                                print(f"[Groq] @{spec_time:.0f}ms SPECULATIVE: {speculative[:40]}...", flush=True)
                                await self.websocket.send_json({
                                    "type": "speculative_translation",
                                    "translation": speculative,
                                    "source_text": text,
                                })
                                sent_speculative = True
                        except:
                            pass

                    await self._process_streaming_chunk(
                        full_response, text, sent_intent_partial, sent_translation_partial
                    )
                    if not sent_intent_partial and '"intent_label"' in full_response:
                        sent_intent_partial = True
                    if not sent_translation_partial and '"full_translation"' in full_response:
                        full_time = (time.time() - start_time) * 1000
                        print(f"[Groq] @{full_time:.0f}ms FULL_TRANSLATION detected", flush=True)
                        sent_translation_partial = True

            await self._finalize_response(full_response, text, is_final, start_time, f"Groq/{groq_model_name}")

        except Exception as e:
            print(f"[Groq] Error: {type(e).__name__}: {e}")

    async def _process_streaming_chunk(self, full_response: str, text: str,
                                        sent_intent: bool, sent_translation: bool):
        """Process streaming chunk and send partial results"""
        if not sent_intent and '"intent_label"' in full_response:
            try:
                match = re.search(r'"intent_label"\s*:\s*"([^"]+)"', full_response)
                if match:
                    partial_intent = match.group(1)
                    print(f"[LLM] Sending partial intent: {partial_intent}", flush=True)
                    await self.websocket.send_json({
                        "type": "intent_partial",
                        "intent_label": partial_intent,
                        "source_text": text,
                    })
            except:
                pass

        if not sent_translation and '"full_translation"' in full_response:
            try:
                match = re.search(r'"full_translation"\s*:\s*"([^"]+)"', full_response)
                if match:
                    partial_translation = match.group(1)
                    print(f"[LLM] Sending partial translation: {partial_translation[:30]}...", flush=True)
                    await self.websocket.send_json({
                        "type": "translation_partial",
                        "translation": partial_translation,
                        "source_text": text,
                    })
            except:
                pass

    async def _finalize_response(self, full_response: str, text: str,
                                  is_final: bool, start_time: float, provider: str):
        """Process and send final response"""
        end_time = time.time()
        print(f"[{provider}] Complete in {(end_time - start_time)*1000:.0f}ms", flush=True)

        try:
            # Extract JSON (strip extra characters)
            json_match = re.search(r'\{[\s\S]*\}', full_response)
            if json_match:
                intent_data = json.loads(json_match.group())
                await self.websocket.send_json({
                    "type": "intent",
                    "data": intent_data,
                    "source_text": text,
                    "is_final": is_final,
                })
            else:
                print(f"[{provider}] No JSON found in response: {full_response[:100]}")
        except json.JSONDecodeError:
            print(f"[{provider}] Failed to parse: {full_response[:100]}")


class _AsrAlternative:
    def __init__(self, transcript: str):
        self.transcript = transcript
        self.confidence = 1.0


class _AsrChannel:
    def __init__(self, transcript: str):
        self.alternatives = [_AsrAlternative(transcript)]


class AsrResult:
    """Nemotron server result shaped like a Deepgram LiveResultResponse (what _on_transcript reads)."""

    def __init__(self, msg: dict):
        self.channel = _AsrChannel(msg.get("transcript", ""))
        self.is_final = bool(msg.get("is_final"))
        self.speech_final = bool(msg.get("speech_final"))


class TranscriptionManager:
    """Manages the streaming ASR connection (Deepgram, or Nemotron on GX10)"""

    def __init__(self, websocket: WebSocket, source_lang: LanguageCode = "en",
                 target_lang: LanguageCode = "ja", asr_engine: ASREngine = "deepgram"):
        self.websocket = websocket
        self.asr_engine: ASREngine = asr_engine
        self.nm_ws = None              # websocket to the Nemotron ASR server
        self.nm_task: Optional[asyncio.Task] = None
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.dg_connection = None
        self.is_running = False
        self.intent_estimator = IntentEstimator(
            websocket, min_words=5,
            source_lang=source_lang, target_lang=target_lang
        )
        self.summary_generator = SummaryGenerator(
            websocket, target_lang=target_lang
        )
        self.keyword_predictor = KeywordPredictor(websocket)
        self.story = StorySummarizer(websocket, target_lang=target_lang)
        self.summary_generator.on_turn_end = self.story.on_turn_end
        self.utterance_fragments: list[str] = []  # gx10: finalized fragments of the current utterance
        self._flush_fallback: Optional[asyncio.Task] = None

    def _gx10(self) -> bool:
        return self.intent_estimator.model == "gx10"

    def _schedule_flush_fallback(self):
        """gx10: if a final arrives after UtteranceEnd (or UtteranceEnd never comes), flush anyway."""
        if self._flush_fallback and not self._flush_fallback.done():
            self._flush_fallback.cancel()

        async def fallback():
            await asyncio.sleep(1.5)
            await self._flush_utterance("fallback")
        self._flush_fallback = asyncio.create_task(fallback())

    async def _flush_utterance(self, reason: str):
        """gx10: the utterance ended -> final translation of the joined fragments, add to insight history."""
        if self._flush_fallback and not self._flush_fallback.done() and reason != "fallback":
            self._flush_fallback.cancel()
        if not self.utterance_fragments:
            return
        utterance = join_text(self.utterance_fragments, self.source_lang)
        self.utterance_fragments = []
        print(f"[Utterance] flush ({reason}): {utterance[:60]}", flush=True)
        asyncio.create_task(self.intent_estimator.estimate(utterance, True))
        # Only a real pause (UtteranceEnd = 1s without words, or the fallback) may end the turn;
        # cuts at speech_final / max words just record the utterance.
        turn_may_end = reason in ("UtteranceEnd", "fallback")
        self.summary_generator.add_transcript(utterance, start_timer=turn_may_end)
        await self.story.add(utterance)

    async def _on_utterance_end(self, *args, **kwargs):
        if self._gx10():
            try:
                if self.utterance_fragments:
                    await self._flush_utterance("UtteranceEnd")
                elif self.summary_generator.transcript_history:
                    # Utterance was already cut at speech_final; the 1s pause still ends the turn.
                    self.summary_generator.restart_silence_timer()
            except Exception as e:
                print(f"UtteranceEnd processing error: {e}")

    def set_mode(self, is_final_only: bool):
        """Switch translation mode"""
        self.intent_estimator.is_final_only = is_final_only
        mode_name = "FINAL_ONLY (QUALITY)" if is_final_only else "HYBRID (SPEED+QUALITY)"
        print(f"[Mode] Switched to {mode_name}", flush=True)

    def set_min_words(self, min_words: int):
        """Set minimum word count for partial processing"""
        self.intent_estimator.min_words = min_words
        print(f"[Config] min_words set to {min_words}", flush=True)

    def set_model(self, model: LLMModel) -> bool:
        """Switch LLM model (both IntentEstimator and SummaryGenerator).
        Returns: True for local LLMs (to notify forced FINAL_ONLY mode)"""
        self.intent_estimator.model = model
        self.summary_generator.model = model
        if model == "gx10":
            self.summary_generator.silence_threshold_ms = GX10_INSIGHT_SILENCE_MS
        print(f"[Model] Switched to {model.upper()} (intent + summary)", flush=True)

        # LM Studio forces FINAL_ONLY mode (to prevent VRAM overload)
        if model == "lmstudio":
            self.intent_estimator.is_final_only = True
            print(f"[Model] LM Studio selected: forcing FINAL_ONLY mode (to prevent crashes)", flush=True)
            return True
        return False

    def set_experimental(self, enabled: bool):
        """Switch experimental mode (speculative translation)"""
        self.intent_estimator.experimental = enabled
        mode_name = "EXPERIMENTAL (speculative)" if enabled else "NORMAL"
        print(f"[Mode] Switched to {mode_name}", flush=True)

    async def set_languages(self, source_lang: LanguageCode, target_lang: LanguageCode) -> bool:
        """Set source/target languages. Restarts the ASR (Deepgram or Nemotron) if the source changed."""
        old_source = self.source_lang
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.intent_estimator.source_lang = source_lang
        self.intent_estimator.target_lang = target_lang
        self.summary_generator.target_lang = target_lang
        self.story.target_lang = target_lang
        print(f"[Lang] Set {LANG_NAMES[source_lang]} → {LANG_NAMES[target_lang]}", flush=True)

        if self.asr_engine == "nemotron" and source_lang not in NEMOTRON_LANG_MAP:
            await self.close()
            self.asr_engine = "deepgram"
        # Restart the ASR if the source language changed
        if old_source != source_lang and self.is_running:
            print(f"[Lang] Source language changed, restarting {self.asr_engine}...", flush=True)
            await self.close()
            return await self.start()
        return True

    async def start(self):
        self.session_started = True  # the client is streaming audio; set_asr must (re)start the engine
        if self.asr_engine == "nemotron":
            return await self._start_nemotron()
        return await self._start_deepgram()

    async def set_asr(self, engine: ASREngine) -> bool:
        """Switch ASR engine; reconnects if running. Returns False if the switch failed."""
        if engine == "nemotron" and self.source_lang not in NEMOTRON_LANG_MAP:
            await self.websocket.send_json({"type": "error", "message": f"Nemotron ASR: {LANG_NAMES[self.source_lang]} is not supported"})
            return False
        if engine == self.asr_engine:
            return True
        print(f"[ASR] Switching {self.asr_engine} -> {engine}", flush=True)
        if self.is_running:
            await self.close()
        self.asr_engine = engine
        return await self.start() if getattr(self, "session_started", False) else True

    async def _start_nemotron(self):
        import websockets  # uvicorn[standard] dependency
        locale = NEMOTRON_LANG_MAP.get(self.source_lang, "en-US")
        url = f"{NEMOTRON_ASR_URL}{'&' if '?' in NEMOTRON_ASR_URL else '?'}language={locale}"
        try:
            self.nm_ws = await asyncio.wait_for(
                websockets.connect(url, max_size=None, ping_interval=20), timeout=5)
            ready = json.loads(await asyncio.wait_for(self.nm_ws.recv(), timeout=5))
            if ready.get("type") == "Error":  # e.g. all streams busy
                await self.websocket.send_json({"type": "error", "message": ready.get("message", "Nemotron ASR error")})
                self.nm_ws = None
                return False
            if ready.get("type") != "Ready":
                raise RuntimeError(f"unexpected first message: {ready}")
        except Exception as e:
            print(f"[Nemotron] connect failed: {type(e).__name__}: {e}", flush=True)
            await self.websocket.send_json({
                "type": "error",
                "message": f"Nemotron ASR (GX10) に接続できません: {NEMOTRON_ASR_URL} ({type(e).__name__})",
            })
            self.nm_ws = None
            return False
        self.is_running = True
        self.nm_task = asyncio.create_task(self._nemotron_receive(self.nm_ws))
        print(f"[Nemotron] connected {url}", flush=True)
        await self.websocket.send_json({"type": "status", "message": "Connected - Nemotron ASR (GX10)"})
        return True

    async def _nemotron_receive(self, ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "Results":
                    await self._on_transcript(result=AsrResult(msg))
                elif msg.get("type") == "UtteranceEnd":
                    await self._on_utterance_end()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Nemotron] receive error: {type(e).__name__}: {e}", flush=True)
        if self.is_running and self.asr_engine == "nemotron" and self.nm_ws is ws:
            self.is_running = False
            try:
                await self.websocket.send_json({"type": "error", "message": "Nemotron ASR (GX10) との接続が切れました"})
            except Exception:
                pass

    async def _start_deepgram(self):
        if not DEEPGRAM_API_KEY:
            await self.websocket.send_json({
                "type": "error",
                "message": "DEEPGRAM_API_KEY is not configured"
            })
            return False

        try:
            config = DeepgramClientOptions(options={"keepalive": "true"})
            deepgram = DeepgramClient(DEEPGRAM_API_KEY, config)

            self.dg_connection = deepgram.listen.asyncwebsocket.v("1")

            self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
            self.dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self.dg_connection.on(LiveTranscriptionEvents.Close, self._on_close)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)

            dg_lang = DEEPGRAM_LANG_MAP.get(self.source_lang, "en-US")
            print(f"[Deepgram] Starting with language: {dg_lang}", flush=True)
            options = LiveOptions(
                model="nova-2",
                language=dg_lang,
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim_results=True,
                utterance_end_ms=1000,
                vad_events=True,
            )

            if await self.dg_connection.start(options):
                self.is_running = True
                await self.websocket.send_json({
                    "type": "status",
                    "message": "Connected - fast mode enabled"
                })
                return True
            else:
                await self.websocket.send_json({
                    "type": "error",
                    "message": "Failed to start Deepgram connection"
                })
                return False

        except Exception as e:
            await self.websocket.send_json({
                "type": "error",
                "message": f"Deepgram initialization error: {str(e)}"
            })
            return False

    async def _on_transcript(self, *args, **kwargs):
        result = kwargs.get("result")
        if result is None and args:
            result = args[1] if len(args) > 1 else args[0]

        if result:
            try:
                transcript_data = result.channel.alternatives[0]
                transcript = normalize_asr_text(transcript_data.transcript, self.source_lang)

                if transcript:
                    is_final = result.is_final
                    speech_final = getattr(result, "speech_final", False)

                    await self.websocket.send_json({
                        "type": "transcript",
                        "transcript": transcript,
                        "is_final": is_final,
                        "speech_final": speech_final,
                        "confidence": transcript_data.confidence,
                    })

                    # Send keyword hints immediately (lightweight, synchronous)
                    await self.keyword_predictor.predict(transcript)

                    if self._gx10():
                        # Join finalized fragments; translate "fragments so far + interim" as a partial.
                        if is_final:
                            self.utterance_fragments.append(transcript)
                            joined = join_text(self.utterance_fragments, self.source_lang)
                            self.summary_generator.on_speech_activity()
                            self._schedule_flush_fallback()
                            n_words = text_units(joined, self.source_lang)
                            if n_words >= UTTERANCE_MAX_WORDS:
                                await self._flush_utterance("max words")
                            elif speech_final and n_words >= UTTERANCE_FLUSH_MIN_WORDS:
                                await self._flush_utterance("speech_final")
                            else:
                                asyncio.create_task(self.intent_estimator.estimate(joined, False))
                        else:
                            joined = join_text(self.utterance_fragments + [transcript], self.source_lang)
                            asyncio.create_task(self.intent_estimator.estimate(joined, False))
                            self.summary_generator.on_speech_activity()
                            if self._flush_fallback and not self._flush_fallback.done():
                                self._flush_fallback.cancel()  # still talking
                        return

                    # Run intent estimation non-blocking (don't block Deepgram keepalive)
                    asyncio.create_task(self.intent_estimator.estimate(transcript, is_final))

                    # Add finalized transcript for summary generation
                    if is_final:
                        self.summary_generator.add_transcript(transcript)
                    else:
                        self.summary_generator.on_speech_activity()

            except Exception as e:
                print(f"Transcript processing error: {e}")

    async def _on_error(self, *args, **kwargs):
        error = kwargs.get("error", args[1] if len(args) > 1 else "Unknown error")
        print(f"Deepgram error: {error}")
        try:
            await self.websocket.send_json({
                "type": "error",
                "message": f"Deepgram error: {str(error)}"
            })
        except:
            pass

    async def _on_close(self, *args, **kwargs):
        self.is_running = False
        print("Deepgram connection closed")

    async def send_audio(self, audio_data: bytes):
        if not self.is_running:
            return
        if self.asr_engine == "nemotron":
            if self.nm_ws:
                try:
                    await self.nm_ws.send(audio_data)
                except Exception as e:
                    print(f"[Nemotron] send error: {type(e).__name__}: {e}", flush=True)
        elif self.dg_connection:
            await self.dg_connection.send(audio_data)

    async def close(self):
        self.is_running = False
        if self.asr_engine == "nemotron":
            ws, task = self.nm_ws, self.nm_task
            self.nm_ws, self.nm_task = None, None
            if ws:
                try:
                    await ws.send(json.dumps({"type": "CloseStream"}))
                    await ws.close()
                except Exception:
                    pass
            if task:
                task.cancel()
        elif self.dg_connection:
            await self.dg_connection.finish()
            self.dg_connection = None


# Path to static files
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "deepgram_configured": DEEPGRAM_API_KEY is not None,
        "openai_configured": OPENAI_API_KEY is not None,
    }


@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket):
    global groq_model_name
    await websocket.accept()
    print("WebSocket connection established")

    manager: Optional[TranscriptionManager] = None

    try:
        asr = websocket.query_params.get("asr", "deepgram")
        src = websocket.query_params.get("src", "en")
        tgt = websocket.query_params.get("tgt", "ja")
        if src not in LANG_NAMES or tgt not in LANG_NAMES or src == tgt:
            src, tgt = "en", "ja"
        if asr == "nemotron" and src not in NEMOTRON_LANG_MAP:
            asr = "deepgram"
        manager = TranscriptionManager(websocket, source_lang=src, target_lang=tgt,
                                       asr_engine=asr if asr in ("deepgram", "nemotron") else "deepgram")
        if not await manager.start():
            await websocket.close()
            return

        while True:
            data = await websocket.receive()

            if "bytes" in data:
                audio_data = data["bytes"]
                await manager.send_audio(audio_data)
            elif "text" in data:
                message = json.loads(data["text"])
                msg_type = message.get("type")
                if msg_type == "stop":
                    break
                elif msg_type == "set_mode":
                    # Mode switch: {"type": "set_mode", "is_final_only": true/false}
                    is_final_only = message.get("is_final_only", False)
                    manager.set_mode(is_final_only)
                    await websocket.send_json({
                        "type": "mode_changed",
                        "is_final_only": is_final_only,
                        "mode_name": "FINAL_ONLY" if is_final_only else "HYBRID"
                    })
                elif msg_type == "set_min_words":
                    # Min word count: {"type": "set_min_words", "min_words": 5}
                    min_words = message.get("min_words", 5)
                    manager.set_min_words(min_words)
                    await websocket.send_json({
                        "type": "config_changed",
                        "min_words": min_words
                    })
                elif msg_type == "set_model":
                    # LLM model switch: {"type": "set_model", "model": "gpt4" | "gpt5" | "gemini" | "gemma" | "groq" | "lmstudio" | "gx10"}
                    model = message.get("model", "gpt4")
                    if model in ("gpt4", "gpt5", "gemini", "gemma", "groq", "lmstudio", "gx10"):
                        forced_final_only = manager.set_model(model)
                        await websocket.send_json({
                            "type": "model_changed",
                            "model": model,
                            "forced_final_only": forced_final_only,
                            "groq_model": groq_model_name if model == "groq" else None
                        })
                        # For local LLMs, also notify mode change
                        if forced_final_only:
                            await websocket.send_json({
                                "type": "mode_changed",
                                "is_final_only": True,
                                "mode_name": "FINAL_ONLY",
                                "reason": "gemma"  # Forced by local LLM
                            })
                elif msg_type == "set_groq_model":
                    # Groq model switch
                    new_groq_model = message.get("groq_model", "llama-3.3-70b-versatile")
                    valid_groq_models = (
                        "llama-3.1-8b-instant",
                        "llama-3.3-70b-versatile",
                        "meta-llama/llama-4-maverick-17b-128e-instruct",
                        "openai/gpt-oss-120b"
                    )
                    if new_groq_model in valid_groq_models:
                        groq_model_name = new_groq_model
                        print(f"[Groq] Model switched to {groq_model_name}", flush=True)
                        await websocket.send_json({
                            "type": "groq_model_changed",
                            "groq_model": groq_model_name
                        })
                elif msg_type == "set_experimental":
                    # Experimental mode: {"type": "set_experimental", "enabled": true/false}
                    enabled = message.get("enabled", False)
                    manager.set_experimental(enabled)
                    await websocket.send_json({
                        "type": "experimental_changed",
                        "enabled": enabled
                    })
                elif msg_type == "set_asr":
                    # ASR switch: {"type": "set_asr", "engine": "deepgram" | "nemotron"}
                    engine = message.get("engine")
                    if engine in ("deepgram", "nemotron"):
                        ok = await manager.set_asr(engine)
                        await websocket.send_json({"type": "asr_changed", "engine": manager.asr_engine, "ok": ok})
                elif msg_type == "set_languages":
                    # Language switch: {"type": "set_languages", "source": "en", "target": "ja"}
                    source = message.get("source", "en")
                    target = message.get("target", "ja")
                    valid_langs = ("en", "ja", "es", "zh")
                    if source in valid_langs and target in valid_langs and source != target:
                        await manager.set_languages(source, target)
                        await websocket.send_json({
                            "type": "languages_changed",
                            "source": source,
                            "target": target,
                        })

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if manager:
            await manager.close()
        print("WebSocket connection closed")


# Serve static files (after frontend build)
if FRONTEND_DIST.exists():
    # Static assets (js, css, etc.)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    # SPA routing: return index.html for all paths
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Return file if it exists
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # Otherwise return index.html (SPA routing)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
