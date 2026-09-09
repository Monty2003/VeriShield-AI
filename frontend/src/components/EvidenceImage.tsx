/**
 * The document, with the evidence drawn on it.
 *
 * Regions arrive in absolute pixels against the image the backend decoded, so
 * they are converted to percentages of that reference size.
 *
 * The overlay is anchored to the image's CONTENT rect, not to its element
 * box. Those differ whenever object-contain letterboxes a tall image, and
 * anchoring to the element box puts every region off by the letterbox --
 * which looks like a coordinate bug in the backend and is not one.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Eye, EyeOff, ScanFace } from 'lucide-react';
import type { DocumentAnalysis } from '../types/api';
import { STATUS_META, cx, percent } from '../lib/format';

interface Props {
  analysis: DocumentAnalysis;
  src: string;
  activeCode?: string | null;
  onHover?: (code: string | null) => void;
}

interface Box {
  /** Unique per region across the whole document -- codes can repeat. */
  key: string;
  code: string;
  title: string;
  hex: string;
  left: number;
  top: number;
  width: number;
  height: number;
  label: string;
  suspicion: number | null;
}

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export default function EvidenceImage({ analysis, src, activeCode, onHover }: Props) {
  const [showOverlay, setShowOverlay] = useState(true);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [content, setContent] = useState<Rect>({ left: 0, top: 0, width: 0, height: 0 });
  const imgRef = useRef<HTMLImageElement>(null);

  // Prefer the dimensions the backend reported: the coordinates are expressed
  // against those. naturalWidth is only a fallback.
  const refWidth = analysis.image_width ?? natural?.w ?? 0;
  const refHeight = analysis.image_height ?? natural?.h ?? 0;

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

  const boxes = useMemo<Box[]>(() => {
    if (!refWidth || !refHeight) return [];
    const result: Box[] = [];

    analysis.signals.forEach((s, signalIndex) => {
      s.regions.forEach((r, index) => {
        result.push({
          key: `${signalIndex}-${index}`,
          code: s.code,
          title: s.title,
          hex: STATUS_META[s.status].hex,
          left: (r.x / refWidth) * 100,
          top: (r.y / refHeight) * 100,
          width: (r.width / refWidth) * 100,
          height: (r.height / refHeight) * 100,
          label: r.label ?? `${s.title} ${index + 1}`,
          suspicion: r.suspicion ?? null,
        });
      });
    });

    return result;
  }, [analysis.signals, refWidth, refHeight]);

  const face = analysis.face_region;
  const faceBox =
    face && refWidth && refHeight
      ? {
          left: (face.x / refWidth) * 100,
          top: (face.y / refHeight) * 100,
          width: (face.width / refWidth) * 100,
          height: (face.height / refHeight) * 100,
        }
      : null;

  const nothingToDraw = boxes.length === 0 && !faceBox;

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-ink-600/70 px-4 py-3">
        <div className="min-w-0">
          <p className="section-title">Submitted image</p>
          <p className="mt-0.5 truncate text-xs text-slate-400">{analysis.filename}</p>
        </div>
        <button
          onClick={() => setShowOverlay((value) => !value)}
          className="btn-ghost shrink-0 px-2.5 py-1.5 text-xs"
          disabled={nothingToDraw}
        >
          {showOverlay ? (
            <EyeOff className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Eye className="h-3.5 w-3.5" aria-hidden />
          )}
          Overlay
        </button>
      </div>

      <div className="relative bg-ink-900">
        <img
          ref={imgRef}
          src={src}
          alt={analysis.filename}
          className="block max-h-[34rem] w-full object-contain"
          onLoad={(event) => {
            setNatural({
              w: event.currentTarget.naturalWidth,
              h: event.currentTarget.naturalHeight,
            });
            measure();
          }}
        />

        {showOverlay && content.width > 0 && (
          <div
            className="pointer-events-none absolute"
            style={{
              left: content.left,
              top: content.top,
              width: content.width,
              height: content.height,
            }}
          >
            {faceBox && (
              <div
                className="absolute rounded border-2 border-sky-400/80"
                style={{
                  left: `${faceBox.left}%`,
                  top: `${faceBox.top}%`,
                  width: `${faceBox.width}%`,
                  height: `${faceBox.height}%`,
                }}
              >
                <span className="absolute -top-5 left-0 flex items-center gap-1 whitespace-nowrap rounded bg-sky-400 px-1.5 py-0.5 text-[10px] font-medium text-ink-900">
                  <ScanFace className="h-3 w-3" aria-hidden />
                  portrait
                </span>
              </div>
            )}

            {boxes.map((box) => {
              const active = activeCode === box.code;
              const tint = box.suspicion
                ? Math.round(box.suspicion * 96)
                    .toString(16)
                    .padStart(2, '0')
                : '14';
              return (
                <div
                  key={box.key}
                  onMouseEnter={() => onHover?.(box.code)}
                  onMouseLeave={() => onHover?.(null)}
                  className={cx(
                    'pointer-events-auto absolute rounded border-2 transition-all duration-200',
                    active ? 'z-10' : 'opacity-75',
                  )}
                  style={{
                    left: `${box.left}%`,
                    top: `${box.top}%`,
                    width: `${box.width}%`,
                    height: `${box.height}%`,
                    borderColor: box.hex,
                    backgroundColor: `${box.hex}${tint}`,
                    boxShadow: active ? `0 0 14px ${box.hex}99` : undefined,
                  }}
                >
                  {active && (
                    <span
                      className="absolute -top-5 left-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-900"
                      style={{ backgroundColor: box.hex }}
                    >
                      {box.label}
                      {box.suspicion !== null ? ` ${percent(box.suspicion)}` : ''}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ink-600/70 px-4 py-2.5 font-mono text-[10px] text-slate-500">
        <span>
          {refWidth} &times; {refHeight} px
        </span>
        <span>
          {boxes.length} region{boxes.length === 1 ? '' : 's'}
        </span>
        {nothingToDraw && (
          <span className="text-slate-600">
            no stage reported coordinates for this document
          </span>
        )}
      </div>
    </div>
  );
}
