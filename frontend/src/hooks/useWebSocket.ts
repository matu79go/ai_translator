import { useCallback, useEffect, useRef, useState } from 'react';

export type TranscriptMessage = {
  type: 'transcript';
  transcript: string;
  is_final: boolean;
  speech_final: boolean;
  confidence: number;
};

export type StatusMessage = {
  type: 'status';
  message: string;
};

export type ErrorMessage = {
  type: 'error';
  message: string;
};

export type IntentData = {
  dialogue_act: string;
  intent_label: string;
  slots: {
    when?: string;
    who?: string;
    where?: string;
    what?: string;
  };
  key_terms: string[];
  confidence: number;
  is_meaning_stable: boolean;
  full_translation?: string;
};

export type IntentMessage = {
  type: 'intent';
  data: IntentData;
  source_text: string;
  is_final: boolean;
};

export type SummaryMessage = {
  type: 'summary';
  summary: string;
  topics: string[];
  streaming?: boolean;
};

export type HintsMessage = {
  type: 'hints';
  hints: string[];
};

export type IntentPartialMessage = {
  type: 'intent_partial';
  intent_label: string;
  source_text: string;
};

export type TranslationPartialMessage = {
  type: 'translation_partial';
  translation: string;
  source_text: string;
};

export type WebSocketMessage = TranscriptMessage | StatusMessage | ErrorMessage | IntentMessage | SummaryMessage | HintsMessage | IntentPartialMessage | TranslationPartialMessage;

type UseWebSocketOptions = {
  url: string;
  onMessage?: (message: WebSocketMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
};

type UseWebSocketReturn = {
  isConnected: boolean;
  connect: () => void;
  disconnect: () => void;
  sendAudio: (data: ArrayBuffer) => void;
  sendControl: (message: object) => void;
};

export function useWebSocket({
  url,
  onMessage,
  onOpen,
  onClose,
  onError,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);

  const cleanup = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const connect = useCallback(() => {
    cleanup();

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      onOpen?.();
    };

    ws.onclose = () => {
      setIsConnected(false);
      onClose?.();
    };

    ws.onerror = (error) => {
      onError?.(error);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as WebSocketMessage;
        onMessage?.(message);
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };
  }, [url, onMessage, onOpen, onClose, onError, cleanup]);

  const disconnect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }));
    }
    cleanup();
  }, [cleanup]);

  const sendAudio = useCallback((data: ArrayBuffer) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const sendControl = useCallback((message: object) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  useEffect(() => {
    return () => {
      cleanup();
    };
  }, [cleanup]);

  return {
    isConnected,
    connect,
    disconnect,
    sendAudio,
    sendControl,
  };
}
