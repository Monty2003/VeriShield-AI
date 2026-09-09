/**
 * Liveness: challenge-response against the camera.
 *
 * The challenge is chosen by the server and revealed only when the session
 * opens. That ordering is the entire security property -- a recording made in
 * advance cannot contain the right action at the right moment, because the
 * attacker did not know which action would be asked for. So the UI must never
 * request a specific challenge, and never show one before /start returns.
 *
 * Frames are sent one at a time, each awaited before the next is captured.
 * Firing them in parallel would queue several face-detection passes on one
 * server and make the session slower, not faster.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, CameraOff, Play, RotateCcw, ScanFace } from 'lucide-react';
import { Banner, Spinner } from '../components/ui';
import SignalList from '../components/SignalList';
import { describeError } from '../api/client';
import {
  livenessComplete,
  livenessFrame,
  livenessStart,
} from '../api/endpoints';
import type {
  LivenessCompleteResponse,
  LivenessFrameResponse,
  LivenessStartResponse,
} from '../types/api';
import { cx } from '../lib/format';

type Phase = 'idle' | 'ready' | 'running' | 'finishing' | 'done';

/** Enough frames to see an action through without streaming indefinitely. */
const TARGET_FRAMES = 16;

export default function LivenessPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const abortRef = useRef(false);

  const [phase, setPhase] = useState<Phase>('idle');
  const [cameraOn, setCameraOn] = useState(false);
  const [error, setError] = useState('');
  const [session, setSession] = useState<LivenessStartResponse | null>(null);
  const [frames, setFrames] = useState<LivenessFrameResponse[]>([]);
  const [result, setResult] = useState<LivenessCompleteResponse | null>(null);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraOn(false);
  }, []);

  useEffect(
    () => () => {
      abortRef.current = true;
      stopCamera();
    },
    [stopCamera],
  );

  const startCamera = useCallback(async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setCameraOn(true);
      setPhase('ready');
    } catch (err) {
      setError(
        err instanceof Error && err.name === 'NotAllowedError'
          ? 'Camera permission was refused. Liveness cannot run without it.'
          : `Could not open the camera: ${describeError(err)}`,
      );
    }
  }, []);

  const grabFrame = useCallback((): Promise<Blob | null> => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return Promise.resolve(null);

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext('2d');
    if (!context) return Promise.resolve(null);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    return new Promise((resolve) =>
      canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.82),
    );
  }, []);

  async function run() {
    setError('');
    setFrames([]);
    setResult(null);
    abortRef.current = false;

    let started: LivenessStartResponse;
    try {
      // No challenge argument: choosing our own would remove exactly the
      // unpredictability the check depends on.
      started = await livenessStart();
    } catch (err) {
      setError(describeError(err));
      return;
    }

    setSession(started);
    setPhase('running');

    const limit = Math.min(TARGET_FRAMES, started.max_frames);
    const collected: LivenessFrameResponse[] = [];

    for (let index = 0; index < limit; index += 1) {
      if (abortRef.current) return;
      const blob = await grabFrame();
      if (!blob) continue;
      try {
        const observation = await livenessFrame(started.session_id, blob);
        collected.push(observation);
        setFrames(collected.slice());
      } catch (err) {
        setError(describeError(err));
        setPhase('ready');
        return;
      }
    }

    setPhase('finishing');
    try {
      setResult(await livenessComplete(started.session_id));
      setPhase('done');
    } catch (err) {
      setError(describeError(err));
      setPhase('ready');
    }
  }

  function reset() {
    setResult(null);
    setFrames([]);
    setSession(null);
    setError('');
    setPhase(cameraOn ? 'ready' : 'idle');
  }

  const latest = frames[frames.length - 1];
  const facesSeen = frames.filter((f) => f.face_detected).length;
  const progress = session ? (frames.length / Math.min(TARGET_FRAMES, session.max_frames)) * 100 : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
          <ScanFace className="h-5 w-5 text-slate-500" aria-hidden />
          Liveness
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          The server picks an action and asks for it. A photograph cannot comply.
        </p>
      </header>

      {error && <Banner>{error}</Banner>}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="card overflow-hidden">
          <div className="relative aspect-[4/3] bg-ink-900">
            <video
              ref={videoRef}
              playsInline
              muted
              className={cx(
                'h-full w-full -scale-x-100 object-cover',
                !cameraOn && 'opacity-0',
              )}
            />
            <canvas ref={canvasRef} className="hidden" />

            {!cameraOn && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
                <CameraOff className="h-6 w-6 text-slate-600" aria-hidden />
                <p className="text-sm text-slate-500">Camera is off</p>
              </div>
            )}

            {phase === 'running' && session && (
              <>
                <div className="pointer-events-none absolute inset-0 flex items-start justify-center p-6">
                  <div className="animate-fade-up rounded-xl bg-ink-900/85 px-5 py-3 text-center backdrop-blur">
                    <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-slate-500">
                      do this now
                    </p>
                    <p className="mt-1 text-lg font-semibold text-white">
                      {session.prompt}
                    </p>
                  </div>
                </div>

                <div
                  className={cx(
                    'pointer-events-none absolute inset-6 rounded-[50%] border-2 transition-colors',
                    latest?.face_detected
                      ? 'border-verdict-accept/70'
                      : 'animate-pulse-ring border-verdict-review/70',
                  )}
                />

                <div className="absolute inset-x-0 bottom-0 h-1 bg-ink-700">
                  <div
                    className="h-full bg-white transition-all duration-200"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </>
            )}

            {phase === 'finishing' && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink-900/80 backdrop-blur">
                <Spinner className="h-5 w-5 text-slate-300" />
                <p className="text-sm text-slate-300">Evaluating the sequence</p>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-ink-600/70 p-4">
            {!cameraOn ? (
              <button onClick={startCamera} className="btn-primary">
                <Camera className="h-4 w-4" aria-hidden />
                Turn on the camera
              </button>
            ) : (
              <>
                <button
                  onClick={run}
                  disabled={phase === 'running' || phase === 'finishing'}
                  className="btn-primary"
                >
                  {phase === 'running' || phase === 'finishing' ? (
                    <Spinner />
                  ) : (
                    <Play className="h-4 w-4" aria-hidden />
                  )}
                  {phase === 'running' ? 'Capturing' : 'Start a challenge'}
                </button>
                <button onClick={stopCamera} className="btn-ghost">
                  <CameraOff className="h-3.5 w-3.5" aria-hidden />
                  Stop camera
                </button>
              </>
            )}
            {result && (
              <button onClick={reset} className="btn-ghost ml-auto">
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                Again
              </button>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="card p-4">
            <p className="section-title">Live telemetry</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              Returned per frame so the UI can say &ldquo;we cannot see your
              face&rdquo; while it matters, instead of failing silently at the end.
            </p>

            <dl className="mt-4 space-y-3">
              <Telemetry
                label="Face detected"
                value={latest ? (latest.face_detected ? 'yes' : 'no') : '--'}
                tone={latest?.face_detected ? 'good' : latest ? 'bad' : 'idle'}
              />
              <Telemetry
                label="Eye openness"
                value={latest?.eye_openness != null ? latest.eye_openness.toFixed(3) : '--'}
              />
              <Telemetry
                label="Yaw"
                value={latest?.yaw != null ? `${latest.yaw.toFixed(1)}deg` : '--'}
              />
              <Telemetry
                label="Pitch"
                value={latest?.pitch != null ? `${latest.pitch.toFixed(1)}deg` : '--'}
              />
              <Telemetry
                label="Frames"
                value={`${frames.length} sent / ${facesSeen} with a face`}
              />
            </dl>

            {frames.length > 1 && (
              <Sparkline
                values={frames.map((f) => f.eye_openness ?? 0)}
                label="eye openness over the sequence"
              />
            )}
          </div>

          {result && (
            <div
              className={cx(
                'card p-4',
                result.passed
                  ? 'border-verdict-accept/40 bg-verdict-accept/5'
                  : 'border-verdict-reject/40 bg-verdict-reject/5',
              )}
            >
              <p className="section-title">Verdict</p>
              <p
                className={cx(
                  'mt-1 text-lg font-semibold',
                  result.passed ? 'text-verdict-accept' : 'text-verdict-reject',
                )}
              >
                {result.passed ? 'Challenge performed' : 'Challenge not performed'}
              </p>
              <p className="mt-1 font-mono text-[11px] text-slate-500">
                asked for: {result.challenge}
              </p>
              <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
                {result.limitations}
              </p>
            </div>
          )}
        </div>
      </div>

      {result && result.signals.length > 0 && (
        <SignalList signals={result.signals} title="Liveness evidence" />
      )}
    </div>
  );
}

function Telemetry({
  label,
  value,
  tone = 'idle',
}: {
  label: string;
  value: string;
  tone?: 'good' | 'bad' | 'idle';
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd
        className={cx(
          'font-mono text-xs tabular-nums',
          tone === 'good' && 'text-verdict-accept',
          tone === 'bad' && 'text-verdict-review',
          tone === 'idle' && 'text-slate-300',
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function Sparkline({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(...values, 0.001);
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * 100;
      const y = 100 - (value / max) * 100;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <div className="mt-4 border-t border-ink-700 pt-3">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-14 w-full">
        <polyline
          points={points}
          fill="none"
          stroke="#38bdf8"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <p className="mt-1 font-mono text-[10px] text-slate-600">{label}</p>
    </div>
  );
}
