import { useId, useState } from 'react';

/** Keyboard, pointer and touch help. Never submits the surrounding form. */
export default function HelpTip({ label, children }: { label: string; children: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <span className="field-help" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
    <span role="button" tabIndex={0} className="field-help-button" aria-label={`Help: ${label}`} aria-expanded={open} aria-describedby={open ? id : undefined}
      onClick={event => { event.preventDefault(); event.stopPropagation(); setOpen(value => !value); }}
      onBlur={() => setOpen(false)} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); } if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); event.stopPropagation(); setOpen(value => !value);} }}>?</span>
    {open && <span role="tooltip" id={id} className="field-tooltip">{children}</span>}
  </span>;
}
