import asyncio
import json
import os
import re
import time
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)
from openai import AsyncOpenAI

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

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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

INTENT_SYSTEM_PROMPT = """あなたはリアルタイム対話支援AIです。
入力される英語から、話者の「意図」と「重要な単語」を即座に抽出してください。

ルール:
- 完璧な文章になるのを待たず、今ある情報から予測してください
- 出力は必ず指定されたJSON形式で行ってください
- intent_labelは日本語で10文字以内
- 確信度が低い場合は confidence を低く設定してください

出力JSON形式（この順序で出力すること）:
{
  "intent_label": "短い日本語ラベル",
  "full_translation": "日本語訳",
  "dialogue_act": "QUESTION | PROPOSAL | AGREEMENT | DISAGREEMENT | STATEMENT | GREETING | OTHER",
  "slots": {"when": "", "who": "", "where": "", "what": ""},
  "key_terms": ["重要な単語"],
  "confidence": 0.0〜1.0,
  "is_meaning_stable": true/false
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
    """会話サマリーを生成するクラス（ストリーミング対応）"""

    def __init__(self, websocket: WebSocket, silence_threshold_ms: int = 1500):
        self.websocket = websocket
        self.silence_threshold_ms = silence_threshold_ms
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
            await self._generate_summary_streaming()

    async def _generate_summary_streaming(self):
        """ストリーミングでサマリーを生成"""
        if not openai_client:
            return

        current_hash = hash(tuple(self.transcript_history))
        if current_hash == self.last_summary_hash:
            return
        self.last_summary_hash = current_hash

        all_text = "\n".join(self.transcript_history)
        print(f"[Summary] Streaming summary for {len(self.transcript_history)} utterances")

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


class IntentEstimator:
    """意図推定を行うクラス（ストリーミング対応）"""

    def __init__(self, websocket: WebSocket, debounce_ms: int = 300):
        self.websocket = websocket
        self.debounce_ms = debounce_ms
        self.last_text = ""
        self.last_call_time = 0
        self.pending_task: Optional[asyncio.Task] = None
        self.context_history: list[str] = []

    async def estimate(self, text: str, is_final: bool):
        """テキストから意図を推定（ストリーミング）"""
        if not openai_client or not text.strip():
            return

        if text == self.last_text:
            return

        self.last_text = text
        current_time = time.time() * 1000

        if is_final:
            self.context_history.append(text)
            if len(self.context_history) > 5:
                self.context_history.pop(0)
            await self._call_llm_streaming(text, is_final)
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
        self.last_call_time = time.time() * 1000
        start_time = time.time()
        print(f"[LLM] Start streaming for: {text[:30]}...", flush=True)

        context = ""
        if self.context_history:
            context = "過去の発話:\n" + "\n".join(self.context_history[-3:]) + "\n\n"

        user_prompt = f"{context}現在の発話（{'確定' if is_final else '進行中'}）:\n{text}"

        try:
            stream = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                stream=True,
            )

            full_response = ""
            sent_intent_partial = False
            sent_translation_partial = False
            first_chunk_time = None

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        print(f"[LLM] First chunk received in {(first_chunk_time - start_time)*1000:.0f}ms", flush=True)

                    full_response += chunk.choices[0].delta.content

                    # 部分的なJSONの解析を試みる（intent_labelが取得できたら即送信）
                    if not sent_intent_partial and '"intent_label"' in full_response:
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
                                sent_intent_partial = True
                        except:
                            pass

                    # full_translationが取得できたら即送信
                    if not sent_translation_partial and '"full_translation"' in full_response:
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
                                sent_translation_partial = True
                        except:
                            pass

            # 最終結果を送信
            end_time = time.time()
            print(f"[LLM] Complete in {(end_time - start_time)*1000:.0f}ms", flush=True)

            try:
                intent_data = json.loads(full_response)
                await self.websocket.send_json({
                    "type": "intent",
                    "data": intent_data,
                    "source_text": text,
                    "is_final": is_final,
                })
            except json.JSONDecodeError:
                print(f"[LLM] Failed to parse: {full_response[:100]}")

        except Exception as e:
            print(f"[LLM] Error: {type(e).__name__}: {e}")


class TranscriptionManager:
    """Deepgramとのストリーミング接続を管理"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.dg_connection = None
        self.is_running = False
        self.intent_estimator = IntentEstimator(websocket)
        self.summary_generator = SummaryGenerator(websocket)
        self.keyword_predictor = KeywordPredictor(websocket)

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


@app.get("/")
async def root():
    return {"message": "Intent-First Translation API", "status": "running"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "deepgram_configured": DEEPGRAM_API_KEY is not None,
        "openai_configured": OPENAI_API_KEY is not None,
    }


@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket):
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
                if message.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if manager:
            await manager.close()
        print("WebSocket connection closed")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
