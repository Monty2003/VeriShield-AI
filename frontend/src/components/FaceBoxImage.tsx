/**
 * One image with the detected face drawn on it.
 *
 * Same anchoring rule as EvidenceImage: the box is positioned against the
 * image's CONTENT rect, not its element box, because object-contain
 * letterboxes anything whose aspect ratio differs from the frame and a box
 * placed against the element would sit off by exactly that letterbox.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ScanFace } from 'lucide-react';
import type { Region } from '../types/api';
import { cx, percent } from '../lib/format';

interface Props {
  src: string;
  region: Region | null;
  /** Dimensions the region coordinates are expressed against. */
  refWidth: number | null;
  refHeight: number | null;
  label: string;
  colour: string;
  /** Shown instead of a box when the model found nothing. */
  emptyNote?: string;
  detectionConfidence?: number | null;
}

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export default function FaceBoxImage({
  src,
  region,
  refWidth,
  refHeight,
  label,
  colour,
  emptyNote,
  detectionConfidence,
}: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [content, setContent] = useState<Rect>({ left: 0, top: 0, width: 0, height: 0 });
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);

  const measure = useCallback(() => {
    const el = imgRef.current;
    if (!el || !el.naturalWidth || !el.naturalHeight) return;
    const boxWidth = el.clientWidth;
    const boxHeight = el.clientHeight;
    const scale = Math.min(boxWidth / el.naturalWidth, boxHeight / el.naturalHeight);
    const width = el.naturalWidth * scale;
    const height = el.naturalHeight * scale;
    setContent({
      left: (boxWidth - width) / 2,
      top: (boxHeight - height) / 2,
      width,
      height,
    });
  }, []);

  useEffect(() => {
    measure();
    const el = imgRef.current;
    if (!el || typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [measure, src]);

  // The backend reports the dimensions it decoded; fall back to the browser's
  // only if it could not.
  const width = refWidth ?? natural?.w ?? 0;
  const height = refHeight ?? natural?.h ?? 0;
  const drawable = region !== null && width > 0 && height > 0 && content.width > 0;

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-cyber-cyan/20 px-4 py-2.5">
        <p className="section-title">{label}</p>
        {detectionConfidence !== null && detectionConfidence !== undefined && (
          <span className="font-mono text-[10px] text-slate-500">
            detected {percent(detectionConfidence)}
          </span>
        )}
      </div>

      <div className="relative bg-ink-900">
        <img
          ref={imgRef}
          src={src}
          alt={label}
          className="block h-64 w-full object-contain"
          onLoad={(event) => {
            setNatural({
              w: event.currentTarget.naturalWidth,
              h: event.currentTarget.naturalHeight,
            });
            measure();
          }}
        />

        {drawable && (
          <div
            className="pointer-events-none absolute"
            style={{
              left: content.left,
              top: content.top,
              width: content.width,
              height: content.height,
            }}
          >
            <div
              className="absolute border-2"
              style={{
                left: `${(region.x / width) * 100}%`,
                top: `${(region.y / height) * 100}%`,
                width: `${(region.width / width) * 100}%`,
                height: `${(region.height / height) * 100}%`,
                borderColor: colour,
                boxShadow: `0 0 14px ${colour}99`,
              }}
            >
              <span
                className="absolute -top-5 left-0 flex items-center gap-1 whitespace-nowrap px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-900"
                style={{ backgroundColor: colour }}
              >
                <ScanFace className="h-3 w-3" aria-hidden />
                {region.width}&times;{region.height}
              </span>
            </div>
          </div>
        )}

        {!region && (
          <div className="absolute inset-0 flex items-end justify-center p-3">
            <p
              className={cx(
                'bg-ink-900/90 px-3 py-1.5 text-center font-mono text-[10px] leading-relaxed',
                'text-verdict-review',
              )}
            >
              {emptyNote ?? 'No face found in this image'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
