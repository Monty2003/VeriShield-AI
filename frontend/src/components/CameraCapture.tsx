/**
 * Take a photograph with the webcam.
 *
 * The preview is mirrored, because an unmirrored self-view is disorienting --
 * you move left and the image moves right. The canvas is mirrored to match, so
 * the frame that gets sent is the frame the person actually saw. That matters
 * beyond aesthetics: the server returns face coordinates against the image it
 * received, and if the sent image were flipped relative to the preview, every
 * box drawn back onto it would land on the wrong side of the face.
 *
 * A horizontal flip does not affect face recognition -- it is the same face.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, CameraOff, RefreshCw, ScanFace } from 'lucide-react';
import { cx } from '../lib/format';

interface Props {
  /** Called with a fresh JPEG whenever the user takes or retakes a shot. */
  onCapture: (file: File | null) => void;
  disabled?: boolean;
  /** Drawn over the live view to help with framing. Not a detection claim. */
  guide?: boolean;
}

export default function CameraCapture({ onCapture, disabled, guide = true }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const previewRef = useRef<string | null>(null);

  const [live, setLive] = useState(false);
  const [error, setError] = useState('');
  const [shot, setShot] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setLive(false);
  }, []);

  // Tracks must be released or the camera light stays on after the user has
  // navigated away, which people reasonably read as being recorded.
  useEffect(
    () => () => {
      stop();
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [stop],
  );

  const start = useCallback(async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setLive(true);
    } catch (err) {
      const name = err instanceof Error ? err.name : '';
      setError(
        name === 'NotAllowedError'
          ? 'Camera permission was refused. Allow it in the browser, or switch to Upload.'
          : name === 'NotFoundError'
            ? 'No camera was found on this device. Switch to Upload instead.'
            : `Could not open the camera: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }, []);

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext('2d');
    if (!context) return;

    // Mirror to match the preview the person was looking at.
    context.translate(canvas.width, 0);
    context.scale(-1, 1);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        if (previewRef.current) URL.revokeObjectURL(previewRef.current);
        previewRef.current = URL.createObjectURL(blob);
        setShot(previewRef.current);
        onCapture(new File([blob], 'camera-capture.jpg', { type: 'image/jpeg' }));
        setFlash(true);
        window.setTimeout(() => setFlash(false), 180);
      },
      'image/jpeg',
      0.92,
    );
  }

  function retake() {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    setShot(null);
    onCapture(null);
  }

  return (
    <div className="card overflow-hidden">
      <div className="relative aspect-[4/3] bg-ink-900">
        <video
          ref={videoRef}
          playsInline
          muted
          className={cx(
            'h-full w-full -scale-x-100 object-cover',
            (!live || shot) && 'hidden',
          )}
        />
        <canvas ref={canvasRef} className="hidden" />

        {shot && (
          <img src={shot} alt="Captured face" className="h-full w-full object-cover" />
        )}

        {!live && !shot && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
            <CameraOff className="h-6 w-6 text-slate-600" aria-hidden />
            <p className="font-mono text-xs text-slate-500">Camera is off</p>
          </div>
        )}

        {live && !shot && guide && (
          <div
            className="pointer-events-none absolute inset-x-[22%] inset-y-[10%] rounded-[50%] border-2 border-cyber-cyan/50"
            aria-hidden
          >
            <span className="absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] uppercase tracking-widest text-cyber-cyan/70">
              line your face up here
            </span>
          </div>
        )}

        {flash && <div className="absolute inset-0 bg-white" aria-hidden />}

        {shot && (
          <span className="absolute left-3 top-3 flex items-center gap-1.5 bg-cyber-cyan px-2 py-1 font-mono text-[10px] font-bold uppercase text-ink-900">
            <ScanFace className="h-3 w-3" aria-hidden />
            captured
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-cyber-cyan/20 p-3">
        {!live && !shot && (
          <button onClick={start} disabled={disabled} className="btn-primary">
            <Camera className="h-4 w-4" aria-hidden />
            Turn on camera
          </button>
        )}

        {live && !shot && (
          <>
            <button onClick={capture} disabled={disabled} className="btn-primary">
              <ScanFace className="h-4 w-4" aria-hidden />
              Capture
            </button>
            <button onClick={stop} disabled={disabled} className="btn-ghost">
              <CameraOff className="h-3.5 w-3.5" aria-hidden />
              Stop
            </button>
          </>
        )}

        {shot && (
          <button onClick={retake} disabled={disabled} className="btn-ghost">
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Retake
          </button>
        )}
      </div>

      {error && (
        <p className="border-t border-verdict-review/30 bg-verdict-review/5 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-verdict-review">
          {error}
        </p>
      )}
    </div>
  );
}
