import { useEffect, useRef, useState, type ReactNode } from 'react';

const storageKey = 'deepdeck.chatPanelWidth';
const defaultWidth = 440;

export default function ResizableChatPanel({ children, label, open }: { children: ReactNode; label: string; open: boolean }) {
  const [preferredWidth, setPreferredWidth] = useState(() => {
    try {
      const stored = Number(window.localStorage.getItem(storageKey));
      return Number.isFinite(stored) && stored >= 320 && stored <= 1120 ? stored : defaultWidth;
    } catch { return defaultWidth; }
  });
  const [viewport, setViewport] = useState(window.innerWidth);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ x: number; width: number; pointer: number } | null>(null);
  const maximum = Math.max(1, Math.min(1120, viewport - 16));
  const minimum = Math.min(320, maximum);
  const width = Math.max(minimum, Math.min(maximum, preferredWidth));
  function resize(value: number) { setPreferredWidth(Math.max(minimum, Math.min(maximum, value))); }
  function finish() { drag.current = null; setResizing(false); }
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem(storageKey, String(preferredWidth)); } catch { /* Private browsing/storage quotas must not block chat. */ }
  }, [preferredWidth]);
  useEffect(() => { if (!open) finish(); }, [open]);
  useEffect(() => {
    if (!resizing) return;
    const previousCursor = document.body.style.cursor;
    const previousSelection = document.body.style.userSelect;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    const move = (event: PointerEvent) => {
      const start = drag.current;
      if (start && start.pointer === event.pointerId) {
        setPreferredWidth(Math.max(minimum, Math.min(maximum, start.width + start.x - event.clientX)));
      }
    };
    const stop = () => { drag.current = null; setResizing(false); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    window.addEventListener('blur', stop);
    return () => {
      document.body.style.cursor = previousCursor; document.body.style.userSelect = previousSelection;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      window.removeEventListener('blur', stop);
    };
  }, [resizing, minimum, maximum]);
  return <aside className={`assistant-drawer${resizing ? ' is-resizing' : ''}`} aria-label={label} style={{width}}>
    <div className="assistant-resize-handle" role="separator" aria-label="Chat panel width" aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={maximum} aria-valuenow={Math.round(width)} tabIndex={open ? 0 : -1}
      title="Drag to resize chat · Arrow keys to adjust · Double-click to reset"
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault();
        drag.current = {x:event.clientX, width, pointer:event.pointerId};
        event.currentTarget.focus();
        setResizing(true);
      }}
      onDoubleClick={() => resize(defaultWidth)}
      onKeyDown={event => {
        const value = event.key === 'ArrowLeft' ? width + 32 : event.key === 'ArrowRight' ? width - 32 : event.key === 'Home' ? minimum : event.key === 'End' ? maximum : null;
        if (value !== null) { event.preventDefault(); resize(value); }
      }}><span /></div>
    {children}
  </aside>;
}
