import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';

export interface AudioRecorderProps {
  isConnected: boolean;
  onAudioData: (data: ArrayBuffer) => void;
  onStart: () => void;
  onStop: () => void;
}

export function AudioRecorder({
  isConnected,
  onAudioData,
  onStart,
  onStop,
}: AudioRecorderProps) {
  const [isRecording, setIsRecording] = useState(false);
  const [isInitializing, setIsInitializing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // マイク選択
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  // 利用可能なマイク一覧を取得
  const refreshDevices = useCallback(async () => {
    try {
      // 権限を取得するために一度getUserMediaを呼ぶ（デバイスラベル取得に必要）
      const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      tempStream.getTracks().forEach(t => t.stop());

      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter(d => d.kind === 'audioinput');
      setAudioDevices(mics);
      // 未選択なら最初のデバイスを選択
      if (!selectedDeviceId && mics.length > 0) {
        setSelectedDeviceId(mics[0].deviceId);
      }
    } catch (err) {
      console.error('Failed to enumerate devices:', err);
    }
  }, [selectedDeviceId]);

  // 初回マウント時にデバイス一覧を取得
  useEffect(() => {
    refreshDevices();
  }, []);

  // デバイス変更を監視（Bluetooth接続/切断時など）
  useEffect(() => {
    const handler = () => refreshDevices();
    navigator.mediaDevices.addEventListener('devicechange', handler);
    return () => navigator.mediaDevices.removeEventListener('devicechange', handler);
  }, [refreshDevices]);

  const startRecording = useCallback(async () => {
    setError(null);
    setIsInitializing(true);

    try {
      // マイクアクセスを要求（選択されたデバイスを指定）
      const audioConstraints: MediaTrackConstraints = {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
      };
      if (selectedDeviceId) {
        audioConstraints.deviceId = selectedDeviceId;
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: audioConstraints,
      });

      mediaStreamRef.current = stream;

      // WebSocket接続を開始
      onStart();

      // AudioContext設定
      const audioContext = new AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;

      const source = audioContext.createMediaStreamSource(stream);
      sourceRef.current = source;

      // ScriptProcessorで音声データを処理
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        const inputData = event.inputBuffer.getChannelData(0);
        // Float32 to Int16変換
        const int16Data = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          const s = Math.max(-1, Math.min(1, inputData[i]));
          int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        onAudioData(int16Data.buffer);
      };

      source.connect(processor);
      processor.connect(audioContext.destination);

      setIsRecording(true);
    } catch (err) {
      console.error('Failed to start recording:', err);
      setError(
        err instanceof Error
          ? err.message
          : 'マイクへのアクセスに失敗しました'
      );
    } finally {
      setIsInitializing(false);
    }
  }, [onStart, onAudioData, selectedDeviceId]);

  const stopRecording = useCallback(() => {
    // プロセッサーを停止
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    // ソースを停止
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }

    // AudioContextを閉じる
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }

    // メディアストリームを停止
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    setIsRecording(false);
    onStop();
  }, [onStop]);

  // コンポーネントアンマウント時にクリーンアップ
  useEffect(() => {
    return () => {
      if (isRecording) {
        stopRecording();
      }
    };
  }, [isRecording, stopRecording]);

  const handleClick = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  return (
    <div className="flex flex-col items-center gap-4">
      {/* マイク選択（複数デバイスがある場合のみ表示） */}
      {audioDevices.length > 1 && (
        <div className="flex items-center gap-2 w-full max-w-sm">
          <label className="text-xs text-gray-500 whitespace-nowrap">マイク:</label>
          <select
            value={selectedDeviceId}
            onChange={(e) => setSelectedDeviceId(e.target.value)}
            disabled={isRecording}
            className="flex-1 text-sm px-2 py-1.5 rounded-lg border border-gray-300 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {audioDevices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label || `マイク ${device.deviceId.slice(0, 8)}`}
              </option>
            ))}
          </select>
        </div>
      )}

      <button
        onClick={handleClick}
        disabled={isInitializing}
        className={`
          flex items-center justify-center
          w-20 h-20 rounded-full
          transition-all duration-200
          ${
            isRecording
              ? 'bg-red-500 hover:bg-red-600 text-white shadow-lg shadow-red-500/50'
              : 'bg-blue-500 hover:bg-blue-600 text-white shadow-lg shadow-blue-500/50'
          }
          ${isInitializing ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
          disabled:opacity-50 disabled:cursor-not-allowed
        `}
      >
        {isInitializing ? (
          <Loader2 className="w-8 h-8 animate-spin" />
        ) : isRecording ? (
          <MicOff className="w-8 h-8" />
        ) : (
          <Mic className="w-8 h-8" />
        )}
      </button>

      <div className="text-sm text-gray-600">
        {isInitializing && '初期化中...'}
        {isRecording && !isInitializing && (
          <span className="flex items-center gap-2">
            <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            録音中
          </span>
        )}
        {!isRecording && !isInitializing && 'クリックして録音開始'}
      </div>

      {isConnected && (
        <div className="text-xs text-green-600 flex items-center gap-1">
          <span className="w-2 h-2 bg-green-500 rounded-full" />
          サーバー接続済み
        </div>
      )}

      {error && (
        <div className="text-sm text-red-500 bg-red-50 px-4 py-2 rounded-lg">
          {error}
        </div>
      )}
    </div>
  );
}
