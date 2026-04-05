# Intent-First Translation

A real-time speech translation prototype that shows **speculative translations before the speaker finishes talking**, powered by LLM intent estimation.

Traditional translation tools wait for a complete sentence before translating. This project takes a different approach: it analyzes the speaker's **intent** (dialogue act + intent label) from partial speech and generates a speculative translation immediately, then refines it once the sentence is finalized. This reduces perceived latency by ~500ms-1s.

## How It Works

1. **Speech capture** -- Browser microphone captures audio via MediaDevices API
2. **Real-time ASR** -- Audio streams to Deepgram for partial + final transcriptions
3. **Intent estimation** -- Each transcript update is sent to an LLM, which outputs:
   - `dialogue_act` (Question, Proposal, Agreement, etc.)
   - `intent_label` (short description of what the speaker intends)
   - `speculative_translation` (fast, based on intent alone)
   - `full_translation` (accurate, based on complete analysis)
4. **Live UI update** -- The speculative translation appears first, then gets replaced by the final translation

## Supported Languages

All combinations of the following 4 languages are supported for bidirectional translation:

| Language | Speech Input (ASR) | Translation Output | TTS Output |
|----------|-------------------|-------------------|------------|
| English  | Deepgram `en-US`  | Yes               | `en-US`    |
| Japanese | Deepgram `ja`     | Yes               | `ja-JP`    |
| Spanish  | Deepgram `es`     | Yes               | `es-ES`    |
| Chinese  | Deepgram `zh`     | Yes               | `zh-CN`    |

This means 12 translation directions are available (e.g., English to Japanese, Japanese to English, Spanish to Chinese, etc.).

### Switching Languages

Use the language dropdowns at the top of the page:

- **Left dropdown** -- Source language (what you speak)
- **Right dropdown** -- Target language (translation output)
- **Swap button** (⇄) -- Quickly swap source and target

Changing the source language will automatically restart the speech recognition engine with the correct language model. You can switch languages while recording is stopped.

## Tech Stack

### Backend
- **Python 3.10+** with FastAPI and WebSockets
- **Deepgram SDK** -- Streaming speech-to-text with partial results
- **OpenAI API** (GPT-4o-mini) -- Primary LLM for intent estimation and translation
- **Google Gemini** (optional) -- Alternative LLM backend
- **Groq API** (optional) -- Ultra-fast inference with Llama models
- **Ollama / LM Studio** (optional) -- Local LLM support

### Frontend
- **React 19** with TypeScript
- **Vite** -- Build tool and dev server
- **Tailwind CSS** -- Styling
- **Lucide React** -- Icons
- **Web Speech API** -- TTS for translated text

## Setup

### Prerequisites

- Node.js 18+
- Python 3.10+
- API keys for Deepgram and at least one LLM provider

### 1. Clone the repository

```bash
git clone <repository-url>
cd ai_translator
```

### 2. Backend setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` with your API keys:

```env
DEEPGRAM_API_KEY=your_deepgram_key      # Required
OPENAI_API_KEY=your_openai_key          # Required for GPT-4
GOOGLE_API_KEY=your_google_key          # Optional, for Gemini
GROQ_API_KEY=your_groq_key             # Optional, for Groq
```

### 3. Frontend setup

```bash
cd frontend
npm install
```

## Running

Start both servers in separate terminals:

```bash
# Terminal 1: Backend (port 8000)
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Frontend dev server (port 5173)
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

### Mobile Testing (via ngrok)

To test on a mobile device (HTTPS required for microphone access):

```bash
# 1. Build the frontend
cd frontend && npm run build

# 2. Start the backend (serves built frontend too)
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 3. In another terminal, start ngrok
ngrok http 8000
```

Open the ngrok URL on your mobile browser.

## Usage

1. Select your **source language** (the language you will speak) and **target language** (translation output)
2. Choose an **LLM model** (GPT-4 is the default and recommended)
3. Click the **microphone button** to start recording
4. Speak -- you'll see:
   - Real-time transcript of your speech
   - Speculative translation (appears fast, may be approximate)
   - Final translation (appears after sentence is complete, more accurate)
5. Click the microphone button again to stop

### Display Modes

- **Side by Side** -- Shows speculative and final translations next to each other for comparison
- **Overwrite** -- Shows only one translation at a time; speculative is replaced by final when ready

### TTS (Text-to-Speech)

Click the **TTS** button to enable automatic reading of translations. The TTS language automatically matches your selected target language.

### LLM Models

| Model | Provider | Speed | Notes |
|-------|----------|-------|-------|
| GPT-4 | OpenAI | Fast | Default, recommended |
| Gemini | Google | Fast | Alternative cloud option |
| Groq | Groq | Ultra-fast | Multiple Llama models available |
| Gemma 3 | Ollama | Varies | Local, requires Ollama server |
| LM Studio | LM Studio | Varies | Local, requires LM Studio |

## License

MIT
