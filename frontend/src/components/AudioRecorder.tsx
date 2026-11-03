import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Loader2, MonitorSpeaker } from 'lucide-react';

export type AudioSource = 'mic' | 'system';

export interface AudioRecorderProps {
  isConnected: boolean;
  onAudioData: (data: ArrayBuffer) => void;
  onStart: () => void;
  onStop: () => void;
  /** Show the "meeting audio" (screen/tab share) input option. */
  enableSystemAudio?: boolean;
  defaultSource?: AudioSource;
  /** 'compact': small record button + input switch for a toolbar (meeting page, uses meeting.css classes). */
  variant?: 'default' | 'compact';
  onRecordingChange?: (recording: boolean) => void;
}

/**
 * Capture the audio the PC is playing (= remote meeting participants) via screen sharing.
 * Windows Chrome/Edge: choose "Entire screen" + "Share system audio" (works with desktop Zoom/Teams).
 * Any OS: choose a Chrome tab + "Share tab audio" (browser meetings).
 */
async function getSystemAudioStream(): Promise<MediaStream> {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: true, // Chrome rejects audio-only requests
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
    systemAudio: 'include',
  } as DisplayMediaStreamOptions);
  stream.getVideoTracks().forEach((t) => t.stop()); // video is not needed
  if (stream.getAudioTracks().length === 0) {
    stream.getTracks().forEach((t) => t.stop());
    throw new Error(
      '音声が共有されていません。共有画面で「画面全体」を選び「システム音声を共有」をオンにするか、' +
      '会議のタブを選び「タブの音声を共有」をオンにしてください。'
    );
  }
  return new MediaStream(stream.getAudioTracks());
}

export function AudioRecorder({
  isConnected,
  onAudioData,
  onStart,
  onStop,
  enableSystemAudio = false,
  defaultSource = 'mic',
  variant = 'default',
  onRecordingChange,
}: AudioRecorderProps) {
  const [inputSource, setInputSource] = useState<AudioSource>(enableSystemAudio ? defaultSource : 'mic');
  const [isRecording, setIsRecording] = useState(false);
  const [isInitializing, setIsInitializing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Microphone selection
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  // Get available microphone list
  const refreshDevices = useCallback(async () => {
    try {
      // Call getUserMedia once to get permission (required for device labels)
      const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      tempStream.getTracks().forEach(t => t.stop());

      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter(d => d.kind === 'audioinput');
      setAudioDevices(mics);
      // Select first device if none selected
      if (!selectedDeviceId && mics.length > 0) {
        setSelectedDeviceId(mics[0].deviceId);
      }
    } catch (err) {
      console.error('Failed to enumerate devices:', err);
    }
  }, [selectedDeviceId]);

  // Get device list when the mic is the input (not on mount for meeting audio: avoids a needless mic prompt)
  useEffect(() => {
    if (inputSource === 'mic') refreshDevices();
  }, [inputSource]); // eslint-disable-line react-hooks/exhaustive-deps

  // Watch for device changes (e.g. Bluetooth connect/disconnect)
  useEffect(() => {
    const handler = () => refreshDevices();
    navigator.mediaDevices.addEventListener('devicechange', handler);
    return () => navigator.mediaDevices.removeEventListener('devicechange', handler);
  }, [refreshDevices]);

  const startRecording = useCallback(async () => {
    setError(null);
    setIsInitializing(true);

    try {
      // Request microphone access (specify selected device)
      const audioConstraints: MediaTrackConstraints = {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
      };
      if (selectedDeviceId) {
        audioConstraints.deviceId = selectedDeviceId;
      }
      const stream = inputSource === 'system'
        ? await getSystemAudioStream()
        : await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });

      mediaStreamRef.current = stream;
      // User pressed the browser's "Stop sharing" (or the device went away)
      stream.getAudioTracks().forEach((t) => {
        t.onended = () => stopRecordingRef.current?.();
      });

      // Start WebSocket connection
      onStart();

      // AudioContext setup
      const audioContext = new AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;

      const source = audioContext.createMediaStreamSource(stream);
      sourceRef.current = source;

      // Process audio data with ScriptProcessor
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        const inputData = event.inputBuffer.getChannelData(0);
        // Float32 to Int16 conversion
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
          : 'Failed to access microphone'
      );
    } finally {
      setIsInitializing(false);
    }
  }, [onStart, onAudioData, selectedDeviceId, inputSource]);

  const stopRecording = useCallback(() => {
    // Stop processor
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    // Stop source
    if (sourceRef.current) {
      sourceRef.current.disconnect();
      sourceRef.current = null;
    }

    // Close AudioContext
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }

    // Stop media stream
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    setIsRecording(false);
    onStop();
  }, [onStop]);

  const stopRecordingRef = useRef<(() => void) | null>(null);
  stopRecordingRef.current = stopRecording;

  // Cleanup on unmount
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

  useEffect(() => { onRecordingChange?.(isRecording); }, [isRecording, onRecordingChange]);

  if (variant === 'compact') {
    return (
      <>
        <button
          className={`rec${isRecording ? ' on' : ''}`}
          onClick={handleClick}
          disabled={isInitializing}
          title={isRecording ? '停止' : '開始'}
          aria-label={isRecording ? '停止' : '開始'}
        />
        {enableSystemAudio && (
          <div className="seg" title="音声の取り方">
            <span className="k">入力</span>
            {([
              { value: 'system', label: '会議の音声' },
              { value: 'mic', label: 'マイク' },
            ] as const).map(({ value, label }) => (
              <button key={value} className={inputSource === value ? 'on' : ''}
                onClick={() => setInputSource(value)} disabled={isRecording}>
                {label}
              </button>
            ))}
          </div>
        )}
        {inputSource === 'mic' && audioDevices.length > 1 && (
          <select className="mic-select" value={selectedDeviceId} disabled={isRecording}
            onChange={(e) => setSelectedDeviceId(e.target.value)}>
            {audioDevices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>{d.label || `Mic ${d.deviceId.slice(0, 8)}`}</option>
            ))}
          </select>
        )}
        {error && <span className="rec-error">{error}</span>}
      </>
    );
  }

  return (
    <div className="flex flex-col items-center gap-4">
      {enableSystemAudio && (
        <div className="flex rounded-lg border border-gray-300 overflow-hidden text-sm">
          {([
            { value: 'system', label: '会議の音声', icon: MonitorSpeaker },
            { value: 'mic', label: 'マイク', icon: Mic },
          ] as const).map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setInputSource(value)}
              disabled={isRecording}
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors disabled:opacity-50 ${
                inputSource === value ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Microphone selection (shown only when multiple devices available) */}
      {inputSource === 'mic' && audioDevices.length > 1 && (
        <div className="flex items-center gap-2 w-full max-w-sm">
          <label className="text-xs text-gray-500 whitespace-nowrap">Mic:</label>
          <select
            value={selectedDeviceId}
            onChange={(e) => setSelectedDeviceId(e.target.value)}
            disabled={isRecording}
            className="flex-1 text-sm px-2 py-1.5 rounded-lg border border-gray-300 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {audioDevices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label || `Mic ${device.deviceId.slice(0, 8)}`}
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
        {isInitializing && 'Initializing...'}
        {isRecording && !isInitializing && (
          <span className="flex items-center gap-2">
            <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            Recording
          </span>
        )}
        {!isRecording && !isInitializing && 'Click to start recording'}
      </div>

      {isConnected && (
        <div className="text-xs text-green-600 flex items-center gap-1">
          <span className="w-2 h-2 bg-green-500 rounded-full" />
          Server connected
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
