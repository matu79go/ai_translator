"""Nemotron-3.5 ASR streaming server with a Deepgram-like WebSocket protocol.

Spec: ai_translator docs/designs/meeting-assist-local.md §4.2, §8.3.

Client -> server: binary 16 kHz mono int16 PCM, or {"type": "CloseStream"}
Server -> client:
  {"type": "Results", "transcript": str, "is_final": bool, "speech_final": bool}
  {"type": "UtteranceEnd"}
  {"type": "Ready"}  (model accepted the stream)

Nemotron has no finality signal, so endpointing is done here from token activity:
  - no new token for --final-silence (0.7s) -> final segment (is_final = speech_final = true)
  - segment >= LONG_SEGMENT_WORDS words   -> final at the next . ? ! ,
  - no new token for UTTERANCE_END_S      -> UtteranceEnd (once)
"""
import argparse
import asyncio
import json
import re
import threading
import time

import numpy as np
import torch
import websockets
from transformers import AutoModelForRNNT, AutoProcessor
from transformers.generation.streamers import BaseStreamer

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="0.0.0.0")
ap.add_argument("--port", type=int, default=8092)
ap.add_argument("--lookahead", type=int, default=3, help="3 -> 320 ms chunks (see eval report)")
ap.add_argument("--language", default="en-US")
# Tokens arrive once per chunk (320 ms), so the silence must span more than 2 chunks;
# 0.3 s cut segments between chunks, i.e. mid-word ("indus|trial").
ap.add_argument("--final-silence", type=float, default=0.7)
ap.add_argument("--utterance-end", type=float, default=1.0)
ap.add_argument("--long-segment-words", type=int, default=25)
# generate() is not safe to run concurrently on one model instance (a second stream crashed with
# AttributeError ... get_audio_features and the first one stopped), so each connection gets its own
# instance. ~2.6 GB of GPU memory per stream.
ap.add_argument("--streams", type=int, default=2, help="max concurrent streams (model instances)")
args = ap.parse_args()

MID = "nvidia/nemotron-3.5-asr-streaming-0.6b"
proc = AutoProcessor.from_pretrained(MID)
proc.set_num_lookahead_tokens(args.lookahead)
MODELS = [AutoModelForRNNT.from_pretrained(MID, device_map="cuda", dtype=torch.float32).eval() for _ in range(args.streams)]
FREE_MODELS = list(MODELS)  # touched only from the event loop thread
SR = proc.feature_extractor.sampling_rate
HOP, NFFT = proc.feature_extractor.hop_length, proc.feature_extractor.n_fft
print(f"loaded {MID} x{args.streams}: chunk {proc.streaming_latency_ms} ms, gpu {torch.cuda.memory_allocated() / 1e9:.2f} GB", flush=True)


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,?!])", r"\1", text)  # "yourself ?" -> "yourself?"
    return text.strip()


class AudioQueue:
    """Growing PCM buffer shared between the websocket (producer) and generate() (consumer)."""

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.float32)
        self.closed = False
        self.cv = threading.Condition()

    def push(self, pcm16: bytes):
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        with self.cv:
            self.buf = np.concatenate([self.buf, x])
            self.cv.notify_all()

    def close(self, tail_s: float = 1.0):
        with self.cv:
            self.buf = np.concatenate([self.buf, np.zeros(int(tail_s * SR), dtype=np.float32)])
            self.closed = True
            self.cv.notify_all()

    def wait_for(self, end: int) -> bool:
        """Block until `end` samples exist. False if the stream closed before that."""
        with self.cv:
            while len(self.buf) < end and not self.closed:
                self.cv.wait()
            return len(self.buf) >= end


class TokenStreamer(BaseStreamer):
    """Forwards emitted token ids to the event loop."""

    def __init__(self, on_ids):
        self.on_ids, self.first = on_ids, True

    def put(self, value):
        if self.first:  # generate() first pushes the prompt ids
            self.first = False
            return
        ids = value.flatten().tolist()
        if ids:
            self.on_ids(ids)

    def end(self):
        pass


def run_generate(model, audio: AudioQueue, streamer: TokenStreamer):
    """Blocking: feed audio chunks to generate() as they arrive (same chunking as nemo_stream.py)."""
    n0 = proc.num_samples_first_audio_chunk
    if not audio.wait_for(n0):
        return
    first = proc(audio.buf[:n0], sampling_rate=SR, is_streaming=True, is_first_audio_chunk=True,
                 language=args.language, return_tensors="pt").to(model.device, dtype=model.dtype)

    def features():
        yield first.input_features[:, : proc.num_mel_frames_first_audio_chunk, :]
        mel = proc.num_mel_frames_first_audio_chunk
        while True:
            s = mel * HOP - NFFT // 2
            e = s + proc.num_samples_per_audio_chunk
            if not audio.wait_for(e):
                return
            seg = audio.buf[s:e] if s >= 0 else np.concatenate([np.zeros(-s, dtype=np.float32), audio.buf[:e]])
            x = proc(seg, sampling_rate=SR, is_streaming=True, is_first_audio_chunk=False,
                     language=args.language, return_tensors="pt").to(model.device, dtype=model.dtype)
            yield x.input_features
            mel += proc.num_mel_frames_per_audio_chunk

    with torch.inference_mode():
        model.generate(**{**first, "input_features": features(), "streamer": streamer})


class Endpointer:
    """Turns the append-only token stream into Deepgram-like interim / final / UtteranceEnd events."""

    def __init__(self, send):
        self.send = send
        self.ids: list[int] = []
        self.seg_start = 0          # index into ids where the current segment starts
        self.last_token_t = None    # time of the last token that changed the text
        self.utterance_end_sent = True
        self.last_interim = ""

    def seg_text(self) -> str:
        # Punctuation emitted right after a cut belongs to the previous segment: drop it here.
        return clean(proc.decode(self.ids[self.seg_start:], skip_special_tokens=True)).lstrip(".,?! ")

    async def on_ids(self, new_ids):
        before = self.seg_text()
        self.ids += new_ids
        text = self.seg_text()
        if text == before:
            return
        self.last_token_t = time.monotonic()
        self.utterance_end_sent = False
        words = text.split()
        if len(words) >= args.long_segment_words and text[-1:] in ".?!,":
            await self.finalize()
            return
        # The last word may still be growing ("apo" -> "apostle"): only send complete words,
        # unless the text ends with punctuation.
        interim = text if text[-1:] in ".?!," else " ".join(words[:-1])
        if interim and interim != self.last_interim:
            self.last_interim = interim
            await self.send({"type": "Results", "transcript": interim, "is_final": False, "speech_final": False})

    async def finalize(self):
        text = self.seg_text()
        self.seg_start = len(self.ids)
        self.last_interim = ""
        if text:
            await self.send({"type": "Results", "transcript": text, "is_final": True, "speech_final": True})

    async def tick(self):
        if self.last_token_t is None:
            return
        idle = time.monotonic() - self.last_token_t
        if idle >= args.final_silence and self.seg_start < len(self.ids) and self.seg_text():
            await self.finalize()
        if idle >= args.utterance_end and not self.utterance_end_sent:
            self.utterance_end_sent = True
            await self.send({"type": "UtteranceEnd"})


async def handle(ws):
    if ws.request.path not in ("/ws", "/ws/"):
        await ws.close(code=4404, reason="use /ws")
        return
    loop = asyncio.get_running_loop()
    peer = ws.remote_address
    if not FREE_MODELS:
        print(f"[conn] reject {peer}: all {len(MODELS)} streams busy", flush=True)
        await ws.send(json.dumps({"type": "Error", "message": f"Nemotron ASR は同時に {len(MODELS)} 接続までです"}))
        await ws.close(code=1013, reason="busy")
        return
    model = FREE_MODELS.pop()
    print(f"[conn] open {peer} ({len(FREE_MODELS)} free)", flush=True)

    async def send(msg):
        try:
            await ws.send(json.dumps(msg))
        except websockets.ConnectionClosed:
            pass

    ep = Endpointer(send)
    audio = AudioQueue()
    def on_ids(ids):
        fut = asyncio.run_coroutine_threadsafe(ep.on_ids(ids), loop)
        fut.add_done_callback(lambda f: f.exception() and print(f"[conn] on_ids error: {f.exception()!r}", flush=True))

    streamer = TokenStreamer(on_ids)

    def gen_logged():
        try:
            run_generate(model, audio, streamer)
        except Exception as e:  # noqa: BLE001
            print(f"[conn] generate error: {type(e).__name__}: {e}", flush=True)
            raise
        finally:
            print(f"[conn] generate finished: {len(ep.ids)} tokens, {len(audio.buf) / SR:.1f}s audio", flush=True)

    gen_task = loop.run_in_executor(None, gen_logged)

    async def ticker():
        while True:
            await asyncio.sleep(0.05)
            await ep.tick()

    tick_task = asyncio.create_task(ticker())
    await send({"type": "Ready"})
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                audio.push(msg)
            else:
                if json.loads(msg).get("type") == "CloseStream":
                    break
    except websockets.ConnectionClosed:
        pass
    finally:
        audio.close()
        try:
            await asyncio.wait_for(gen_task, timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"[conn] generate ended with {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(0.1)
        await ep.finalize()
        tick_task.cancel()
        if gen_task.done():
            FREE_MODELS.append(model)
        else:  # generate still running (should not happen after audio.close): don't hand this instance out
            print(f"[conn] WARNING generate did not finish; instance not reused", flush=True)
        print(f"[conn] closed {peer} ({len(FREE_MODELS)} free)", flush=True)


def warmup():
    q = AudioQueue()
    q.push(np.zeros(2 * SR, dtype="<i2").tobytes())
    q.close()
    t = time.monotonic()
    for m in MODELS:
        run_generate(m, q, TokenStreamer(lambda ids: None))
    print(f"warmup done in {time.monotonic() - t:.1f}s", flush=True)


async def main():
    warmup()
    async with websockets.serve(handle, args.host, args.port, max_size=None, ping_interval=20):
        print(f"listening on ws://{args.host}:{args.port}/ws", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
