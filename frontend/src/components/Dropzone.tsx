/**
 * File intake: click, drag, or paste.
 *
 * Paste matters more than it looks. Reviewers work from screenshots and
 * chat attachments, and Ctrl+V is how those arrive -- forcing a save-to-disk
 * round trip first is the kind of friction that gets a tool abandoned.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ImagePlus, Upload, X } from 'lucide-react';
import { bytes, cx } from '../lib/format';

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  multiple?: boolean;
  max?: number;
  label?: string;
  hint?: string;
  disabled?: boolean;
}

const ACCEPT = 'image/*,.heic,.heif';

export default function Dropzone({
  files,
  onChange,
  multiple = false,
  max = 6,
  label = 'Drop a document here',
  hint = 'JPEG, PNG or HEIC, up to 20 MB',
  disabled = false,
}: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [previews, setPreviews] = useState<string[]>([]);

  const accept = useCallback(
    (incoming: FileList | File[] | null) => {
      if (!incoming) return;
      const list = Array.from(incoming).filter((f) => f.size > 0);
      if (list.length === 0) return;
      onChange(multiple ? files.concat(list).slice(0, max) : [list[0]]);
    },
    [files, max, multiple, onChange],
  );

  // Object URLs are revoked when the file list changes; without this every
  // re-upload leaks a blob for the lifetime of the tab.
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setPreviews(urls);
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [files]);

  useEffect(() => {
    if (disabled) return;
    // Bound to the document, not the window: 'paste' is a document/element
    // event, and TypeScript is right that window never receives it.
    function onPaste(event: ClipboardEvent) {
      const items = event.clipboardData?.files;
      if (items && items.length > 0) accept(items);
    }
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [accept, disabled]);

  const full = multiple && files.length >= max;

  return (
    <div>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) accept(event.dataTransfer.files);
        }}
        onClick={() => !disabled && !full && inputRef.current?.click()}
        className={cx(
          'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition',
          dragging
            ? 'border-slate-300 bg-slate-100/5'
            : 'border-ink-600 hover:border-ink-500 hover:bg-ink-800/40',
          (disabled || full) && 'pointer-events-none opacity-50',
        )}
      >
        <div
          className={cx(
            'rounded-full bg-ink-700 p-3 transition',
            dragging && 'scale-110 bg-slate-200 text-ink-900',
          )}
        >
          {dragging ? (
            <Upload className="h-5 w-5" aria-hidden />
          ) : (
            <ImagePlus className="h-5 w-5 text-slate-400" aria-hidden />
          )}
        </div>
        <p className="mt-3 text-sm font-medium text-slate-200">{label}</p>
        <p className="mt-1 text-xs text-slate-500">{hint}</p>
        <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-slate-600">
          click &middot; drag &middot; or paste
        </p>

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple={multiple}
          hidden
          onChange={(event) => {
            accept(event.target.files);
            // Reset so re-selecting the same file still fires onChange.
            event.target.value = '';
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-3 space-y-2">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${index}`}
              className="flex items-center gap-3 rounded-lg border border-ink-700 bg-ink-900/50 p-2 animate-fade-up"
            >
              {previews[index] && (
                <img
                  src={previews[index]}
                  alt=""
                  className="h-10 w-10 shrink-0 rounded object-cover"
                />
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs text-slate-200">{file.name}</p>
                <p className="font-mono text-[10px] text-slate-500">{bytes(file.size)}</p>
              </div>
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  onChange(files.filter((_, i) => i !== index));
                }}
                disabled={disabled}
                className="shrink-0 rounded p-1.5 text-slate-500 transition hover:bg-ink-700 hover:text-verdict-reject"
                aria-label={`Remove ${file.name}`}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
