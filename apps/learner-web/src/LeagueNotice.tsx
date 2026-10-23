import type { ReactNode } from 'react';

export default function LeagueNotice({ children, error = false, label, onDismiss }: {
  children: ReactNode;
  error?: boolean;
  label: string;
  onDismiss: () => void;
}) {
  return <div className={`league-notice ${error ? 'inline-error' : 'inline-success'}`} role={error ? 'alert' : 'status'} aria-label={label}>
    <span>{children}</span>
    <button type="button" className="league-dismiss-button" aria-label={`Dismiss ${label}`} title="Dismiss notification" onClick={onDismiss}>
      <span aria-hidden="true">×</span>
    </button>
  </div>;
}
