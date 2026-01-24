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

# LLMモデル型
LLMModel = Literal["gpt4", "gpt5", "gemini", "gemma", "groq"]

# Groqモデル設定（UIから切り替え可能）
GroqModel = Literal[
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "openai/gpt-oss-120b"
]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
groq_client = AsyncOpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY) if GROQ_API_KEY else None
groq_model_name: GroqModel = "llama-3.3-70b-versatile"  # デフォルトモデル（高速・高品質）

# Ollama (Gemma 3 4B) 設定
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.183:11434/v1")
gemma_client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
# Gemmaは同時リクエスト不可なのでロックで制限
gemma_lock = asyncio.Lock()
gemma_pending_text: str | None = None  # 待機中のテキスト（最新のみ保持）

# キーワード→トピック予測マッピング（即時応答用）
KEYWORD_TOPICS = {
    # 日程・時間
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
    # ビジネス
    "budget": "予算",
    "project": "プロジェクト",
    "deadline": "締め切り",
    "report": "レポート",
    "review": "レビュー",
    "approval": "承認",
    "proposal": "提案",
    "client": "クライアント",
    "customer": "顧客",
    # アクション
    "discuss": "議論",
    "confirm": "確認",
    "update": "更新",
    "check": "確認",
    "send": "送信",
    "prepare": "準備",
    "finish": "完了",
    "start": "開始",
    # 質問・依頼
    "can we": "依頼",
    "could you": "依頼",
    "would you": "依頼",
    "please": "依頼",
    "how about": "提案",
    "what about": "提案",
    "let's": "提案",
}

# バランス重視プロンプト: 最低限の分析→翻訳（partial用）
INTENT_SYSTEM_PROMPT_SPEED = """あなたはリアルタイム同時通訳AIです。
英語の発話をリアルタイムで日本語に翻訳します。

処理順序（重要）:
1. まず対話行為(dialogue_act)を判断
2. 意図(intent_label)を抽出
3. 重要情報(slots)を特定
4. 上記を考慮して翻訳(full_translation)を生成

翻訳ルール:
- 自然で流暢な日本語に翻訳してください
- 文が途中でも、現時点で言いたいことを推測して完結した日本語にしてください

その他のルール:
- intent_labelは日本語で10文字以内
- 出力は必ず指定されたJSON形式で行ってください

出力JSON形式（この順序で出力すること）:
{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "intent_label": "短い日本語ラベル",
  "slots": {"when": "", "who": "", "where": "", "what": ""},
  "full_translation": "文脈を考慮した自然な日本語訳",
  "key_terms": ["重要な単語"],
  "confidence": 0.0〜1.0,
  "is_meaning_stable": true/false
}

注意: JSON以外の文字は出力しないでください。"""

# 品質重視プロンプト: 文脈を先に分析してから翻訳（is_final用）
INTENT_SYSTEM_PROMPT_QUALITY = """あなたはリアルタイム対話支援AIです。
入力される英語から、話者の「意図」と「重要な単語」を抽出し、正確な翻訳を行ってください。

ルール:
- まず対話行為(dialogue_act)と重要情報(slots)を分析してください
- その分析結果を踏まえて、文脈に即した正確な翻訳を行ってください
- 出力は必ず指定されたJSON形式で行ってください
- intent_labelは日本語で10文字以内

出力JSON形式（この順序で出力すること）:
{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "slots": {"when": "", "who": "", "where": "", "what": ""},
  "key_terms": ["重要な単語"],
  "confidence": 0.0〜1.0,
  "is_meaning_stable": true/false,
  "intent_label": "短い日本語ラベル",
  "full_translation": "文脈を考慮した正確な日本語訳"
}

注意: JSON以外の文字は出力しないでください。"""

# 実験用プロンプト: 推測翻訳を先に出す（speculative_translation）
INTENT_SYSTEM_PROMPT_EXPERIMENTAL = """あなたはリアルタイム同時通訳AIです。
人間の同時通訳者のように、文が完結する前に「推測」で翻訳を開始してください。

処理順序（重要 - この順序で出力すること）:
1. dialogue_act: 対話行為を即座に判断
2. intent_label: 意図を10文字以内で抽出
3. speculative_translation: ここで即座に推測翻訳を出力！（精度より速度優先）
4. slots: 重要情報を特定
5. full_translation: 全情報を考慮した正確な翻訳

推測翻訳のルール（速度最優先）:
- dialogue_act + intent_label だけで推測する
- 例: PROPOSAL + 「日程調整」 → 「日程について提案しています」
- 例: QUESTION + 「確認依頼」 → 「確認を求めています」
- 完璧でなくてよい、方向性が伝わればOK

出力JSON形式（この順序厳守）:
{
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "intent_label": "短い日本語ラベル",
  "speculative_translation": "dialogue_act+intent_labelから即座に推測した翻訳",
  "slots": {"when": "", "who": "", "where": "", "what": ""},
  "full_translation": "全情報を考慮した正確な翻訳",
  "confidence": 0.0〜1.0
}

注意: JSON以外の文字は出力しないでください。"""

SUMMARY_SYSTEM_PROMPT = """あなたは会話要約AIです。
複数の発話から、会話全体の要約と主要なトピックを抽出してください。

出力JSON形式:
{
  "summary": "会話全体の要約（日本語、100-150文字程度）",
  "topics": ["トピック1", "トピック2"]
}

ルール:
- summaryは会話の内容・目的・結論を含む詳細な要約
- 何について話しているか、何を決めようとしているかを明確に
- topicsは最大5つまで
- 日本語で出力
- JSON以外の文字は出力しないでください"""


class KeywordPredictor:
    """即座にキーワードからトピックを予測するクラス"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.last_hints: set[str] = set()

    async def predict(self, text: str):
        """テキストからキーワードを抽出して即座にヒントを送信"""
        text_lower = text.lower()
        hints = set()

        for keyword, topic in KEYWORD_TOPICS.items():
            if keyword in text_lower:
                hints.add(topic)

        # 新しいヒントがある場合のみ送信
        if hints and hints != self.last_hints:
            self.last_hints = hints
            await self.websocket.send_json({
                "type": "hints",
                "hints": list(hints)[:5],  # 最大5つ
            })


class SummaryGenerator:
    """会話サマリーを生成するクラス（ストリーミング対応、マルチモデル）"""

    def __init__(self, websocket: WebSocket, silence_threshold_ms: int = 1500,
                 model: LLMModel = "gpt4"):
        self.websocket = websocket
        self.silence_threshold_ms = silence_threshold_ms
        self.model = model  # "gpt4" or "gemini"
        self.transcript_history: list[str] = []
        self.pending_summary_task: Optional[asyncio.Task] = None
        self.last_summary_hash = ""

    def add_transcript(self, text: str):
        """確定したトランスクリプトを追加"""
        self.transcript_history.append(text)

        if self.pending_summary_task and not self.pending_summary_task.done():
            self.pending_summary_task.cancel()

        self.pending_summary_task = asyncio.create_task(
            self._delayed_summary()
        )

    async def _delayed_summary(self):
        """沈黙後にサマリーを生成"""
        await asyncio.sleep(self.silence_threshold_ms / 1000)

        if len(self.transcript_history) >= 2:
            if self.model == "gemini":
                await self._generate_summary_gemini()
            elif self.model == "gemma":
                # Gemma（ローカルLLM）は同時リクエスト不可のため、サマリーはGPT-4を使用
                print(f"[Summary] Gemma selected, but using GPT-4 for summary (concurrent requests not supported)", flush=True)
                await self._generate_summary_streaming(use_gpt4_fallback=True)
            else:
                await self._generate_summary_streaming()

    async def _generate_summary_streaming(self, use_gpt4_fallback: bool = False):
        """ストリーミングでサマリーを生成"""
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
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"以下の発話を要約してください:\n\n{all_text}"},
                ],
                temperature=0.3,
                max_tokens=200,
                stream=True,
            )

            full_response = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    full_response += chunk.choices[0].delta.content

                    # JSONが完成したかチェック
                    try:
                        # 部分的なJSONでも解析を試みる
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

            # 最終結果を送信
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

    async def _generate_summary_gemini(self):
        """Geminiでサマリーを生成"""
        if not gemini_model:
            return

        current_hash = hash(tuple(self.transcript_history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        all_text = "\n".join(self.transcript_history)
        print(f"[Summary-Gemini] Generating summary for {len(self.transcript_history)} utterances")

        try:
            full_prompt = f"{SUMMARY_SYSTEM_PROMPT}\n\n以下の発話を要約してください:\n\n{all_text}"

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

            # JSONを抽出
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
    """意図推定を行うクラス（ストリーミング対応、マルチモデル）"""

    def __init__(self, websocket: WebSocket, debounce_ms: int = 300,
                 is_final_only: bool = False, min_words: int = 5,
                 model: LLMModel = "gpt4", experimental: bool = False):
        self.websocket = websocket
        self.debounce_ms = debounce_ms
        self.is_final_only = is_final_only  # Trueの場合、is_final=Trueのみ処理
        self.min_words = min_words  # partial時の最小単語数
        self.model = model  # "gpt4", "gpt5", "gemini"
        self.experimental = experimental  # 実験モード（推測翻訳）
        self.last_text = ""
        self.last_call_time = 0
        self.pending_task: Optional[asyncio.Task] = None
        self.context_history: list[str] = []

    async def estimate(self, text: str, is_final: bool):
        """テキストから意図を推定（ストリーミング）"""
        # モデルに応じてクライアントをチェック
        if self.model in ("gpt4", "gpt5") and not openai_client:
            return
        if self.model == "gemini" and not gemini_model:
            return
        # gemmaはgemma_clientを使用（常に利用可能）
        if not text.strip():
            return

        if text == self.last_text:
            return

        self.last_text = text
        current_time = time.time() * 1000
        word_count = len(text.split())

        if is_final:
            self.context_history.append(text)
            if len(self.context_history) > 5:
                self.context_history.pop(0)
            await self._call_llm_streaming(text, is_final)
            return

        # partial（進行中）の処理
        # is_final_onlyモードの場合、partialはスキップ
        if self.is_final_only:
            print(f"[LLM] Skipped partial (is_final_only mode): {text[:30]}...", flush=True)
            return

        # 短いテキストはスキップ（断片的な翻訳を防ぐ）
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
        """ストリーミングでLLMを呼び出して意図を推定"""
        if self.model == "gemini":
            await self._call_gemini(text, is_final)
        elif self.model == "gemma":
            await self._call_gemma(text, is_final)
        elif self.model == "groq":
            await self._call_groq(text, is_final)
        elif self.model == "gpt5":
            await self._call_gpt5(text, is_final)
        else:
            await self._call_gpt4(text, is_final)

    async def _call_gpt4(self, text: str, is_final: bool):
        """GPT-4o-mini APIでストリーミング"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # 実験モードの場合は専用プロンプト
        if self.experimental:
            system_prompt = INTENT_SYSTEM_PROMPT_EXPERIMENTAL
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = INTENT_SYSTEM_PROMPT_QUALITY
            mode = "QUALITY"
        else:
            system_prompt = INTENT_SYSTEM_PROMPT_SPEED
            mode = "SPEED"
        print(f"[GPT-4] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"

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
            sent_speculative = False  # 実験モード用
            first_chunk_time = None

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        print(f"[GPT-4] First chunk in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                    full_response += chunk.choices[0].delta.content

                    # 実験モード: speculative_translation を先に送信
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
        """GPT-5 APIでストリーミング（新しいresponses API）"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        system_prompt = INTENT_SYSTEM_PROMPT_QUALITY if is_final else INTENT_SYSTEM_PROMPT_SPEED
        mode = "QUALITY" if is_final else "SPEED"
        print(f"[GPT-5] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"

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
        """Gemini APIで呼び出し"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        system_prompt = INTENT_SYSTEM_PROMPT_QUALITY if is_final else INTENT_SYSTEM_PROMPT_SPEED
        mode = "QUALITY" if is_final else "SPEED"
        print(f"[Gemini] Start [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        try:
            # Gemini API (非ストリーミング、asyncioで実行)
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
        """Gemma 3 (Ollama) APIでストリーミング - 並列リクエスト対応"""
        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # 実験モードの場合は専用プロンプト
        if self.experimental:
            system_prompt = INTENT_SYSTEM_PROMPT_EXPERIMENTAL
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = INTENT_SYSTEM_PROMPT_QUALITY
            mode = "QUALITY"
        else:
            system_prompt = INTENT_SYSTEM_PROMPT_SPEED
            mode = "SPEED"
        print(f"[Gemma] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"

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

                    # 実験モード: speculative_translation を先に送信
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

    async def _call_groq(self, text: str, is_final: bool):
        """Groq APIでストリーミング（超高速）"""
        if not groq_client:
            print("[Groq] Client not configured (missing GROQ_API_KEY)", flush=True)
            return

        self.last_call_time = time.time() * 1000
        start_time = time.time()

        # 実験モードの場合は専用プロンプト
        if self.experimental:
            system_prompt = INTENT_SYSTEM_PROMPT_EXPERIMENTAL
            mode = "EXPERIMENTAL"
        elif is_final:
            system_prompt = INTENT_SYSTEM_PROMPT_QUALITY
            mode = "QUALITY"
        else:
            system_prompt = INTENT_SYSTEM_PROMPT_SPEED
            mode = "SPEED"
        print(f"[Groq/{groq_model_name}] Start streaming [{mode}] for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"

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

                    # 実験モード: speculative_translation を先に送信
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
        """ストリーミングチャンクを処理して部分結果を送信"""
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
        """最終レスポンスを処理して送信"""
        end_time = time.time()
        print(f"[{provider}] Complete in {(end_time - start_time)*1000:.0f}ms", flush=True)

        try:
            # JSONを抽出（前後の余分な文字を除去）
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


class TranscriptionManager:
    """Deepgramとのストリーミング接続を管理"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.dg_connection = None
        self.is_running = False
        self.intent_estimator = IntentEstimator(websocket, min_words=5)
        self.summary_generator = SummaryGenerator(websocket)
        self.keyword_predictor = KeywordPredictor(websocket)

    def set_mode(self, is_final_only: bool):
        """翻訳モードを切り替え"""
        self.intent_estimator.is_final_only = is_final_only
        mode_name = "FINAL_ONLY (QUALITY)" if is_final_only else "HYBRID (SPEED+QUALITY)"
        print(f"[Mode] Switched to {mode_name}", flush=True)

    def set_min_words(self, min_words: int):
        """partial処理の最小単語数を設定"""
        self.intent_estimator.min_words = min_words
        print(f"[Config] min_words set to {min_words}", flush=True)

    def set_model(self, model: LLMModel) -> bool:
        """LLMモデルを切り替え（IntentEstimatorとSummaryGenerator両方）
        Returns: Gemmaの場合True（FINAL_ONLYモード強制を通知するため）"""
        self.intent_estimator.model = model
        self.summary_generator.model = model
        print(f"[Model] Switched to {model.upper()} (intent + summary)", flush=True)

        # Ollama (Gemma) は並列リクエスト対応のため、FINAL_ONLY強制は不要
        # if model == "gemma":
        #     self.intent_estimator.is_final_only = True
        #     print(f"[Model] Gemma selected: forcing FINAL_ONLY mode (parallel requests not supported)", flush=True)
        #     return True
        return False

    def set_experimental(self, enabled: bool):
        """実験モード（推測翻訳）を切り替え"""
        self.intent_estimator.experimental = enabled
        mode_name = "EXPERIMENTAL (speculative)" if enabled else "NORMAL"
        print(f"[Mode] Switched to {mode_name}", flush=True)

    async def start(self):
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

            options = LiveOptions(
                model="nova-2",
                language="en-US",
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
                    "message": "接続完了 - 高速モード有効"
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
                transcript = transcript_data.transcript

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

                    # 即座にキーワードヒントを送信（軽量なので同期）
                    await self.keyword_predictor.predict(transcript)

                    # 意図推定を非ブロッキングで実行（Deepgramのkeepaliveをブロックしない）
                    asyncio.create_task(self.intent_estimator.estimate(transcript, is_final))

                    # 確定したトランスクリプトをサマリー用に追加
                    if is_final:
                        self.summary_generator.add_transcript(transcript)

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
        if self.dg_connection and self.is_running:
            await self.dg_connection.send(audio_data)

    async def close(self):
        self.is_running = False
        if self.dg_connection:
            await self.dg_connection.finish()


# 静的ファイルのパス
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
        manager = TranscriptionManager(websocket)
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
                    # モード切り替え: {"type": "set_mode", "is_final_only": true/false}
                    is_final_only = message.get("is_final_only", False)
                    manager.set_mode(is_final_only)
                    await websocket.send_json({
                        "type": "mode_changed",
                        "is_final_only": is_final_only,
                        "mode_name": "FINAL_ONLY" if is_final_only else "HYBRID"
                    })
                elif msg_type == "set_min_words":
                    # 最小単語数設定: {"type": "set_min_words", "min_words": 5}
                    min_words = message.get("min_words", 5)
                    manager.set_min_words(min_words)
                    await websocket.send_json({
                        "type": "config_changed",
                        "min_words": min_words
                    })
                elif msg_type == "set_model":
                    # LLMモデル切り替え: {"type": "set_model", "model": "gpt4" | "gpt5" | "gemini" | "gemma" | "groq"}
                    model = message.get("model", "gpt4")
                    if model in ("gpt4", "gpt5", "gemini", "gemma", "groq"):
                        forced_final_only = manager.set_model(model)
                        await websocket.send_json({
                            "type": "model_changed",
                            "model": model,
                            "forced_final_only": forced_final_only,
                            "groq_model": groq_model_name if model == "groq" else None
                        })
                        # Gemmaの場合、モード変更も通知
                        if forced_final_only:
                            await websocket.send_json({
                                "type": "mode_changed",
                                "is_final_only": True,
                                "mode_name": "FINAL_ONLY",
                                "reason": "gemma"  # Gemmaによる強制
                            })
                elif msg_type == "set_groq_model":
                    # Groqモデル切り替え
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
                    # 実験モード切り替え: {"type": "set_experimental", "enabled": true/false}
                    enabled = message.get("enabled", False)
                    manager.set_experimental(enabled)
                    await websocket.send_json({
                        "type": "experimental_changed",
                        "enabled": enabled
                    })

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if manager:
            await manager.close()
        print("WebSocket connection closed")


# 静的ファイル配信（フロントエンドビルド後）
if FRONTEND_DIST.exists():
    # 静的アセット（js, css, etc）
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    # SPAルーティング: すべてのパスでindex.htmlを返す
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # ファイルが存在すればそれを返す
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # それ以外はindex.html（SPAルーティング）
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
