import { FormEvent, useEffect, useState } from 'react';

import AsyncActionButton from './AsyncActionButton';
import { signInLeague, signOutLeague, type AccountStatus, type LeagueAccount, type LeagueConnection } from './api';

export default function AccountConnection({ account, agentAccount, onChanged, connection }: {
  account: LeagueAccount | null;
  agentAccount: AccountStatus | null;
  onChanged: () => void | Promise<void>;
  connection?: LeagueConnection | null;
}) {
  const [identity, setIdentity] = useState('');
  const [password, setPassword] = useState('');
  const [enableAgents, setEnableAgents] = useState(!agentAccount?.valid);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [latest, setLatest] = useState<LeagueAccount | null>(null);
  const [warning, setWarning] = useState('');
  useEffect(() => {setLatest(null);},[account]);
  const observed = latest ?? account;
  async function refreshAfterSuccess() {
    try {await onChanged();}
    catch {setWarning('Your account change succeeded, but the status could not refresh. Check the connection again; do not submit your password again just to refresh.');}
    finally {window.dispatchEvent(new Event('deepdeck:league-connection-changed'));}
  }

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice(''); setWarning('');
    try {
      const result = await signInLeague(identity, password, enableAgents);
      setPassword('');
      setLatest(result);
      if (!result.valid) {setError(result.reason || 'The server did not confirm your account connection.'); return;}
      setNotice(`Account connected. ${result.reason}`);
      await refreshAfterSuccess();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign-in failed.'); }
    finally { setBusy(false); }
  }

  async function logout() {
    setBusy(true); setError(''); setNotice(''); setWarning('');
    try { setLatest(await signOutLeague()); setNotice('Signed out. Your local decks and running agents are unchanged.'); await refreshAfterSuccess(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not sign out.'); }
    finally { setBusy(false); }
  }

  return <div className="account-connection">
    <div className="league-account-links"><a href={connection?.siteUrl || 'https://deepdeckleague.com'} target="_blank" rel="noreferrer">Open DeepDeck League ↗</a><small>Use the site to create an account or manage access. Then sign in here on this computer.</small></div>
    {observed?.valid ? <div className="connected-account"><span className="account-avatar">✓</span><div><strong>{observed.user?.displayName || observed.user?.username || 'Your League account'}</strong><small>Deck synchronization connected</small></div><AsyncActionButton loading={busy} loadingLabel="Signing out…" onClick={() => void logout()}>Sign out</AsyncActionButton></div> : <form onSubmit={(event) => void login(event)}>
      <p>Use the same account as Deep Deck League. Your password is never saved.</p>
      {observed?.configured && <p className="inline-notice">{observed.reason}</p>}
      <label>Email or username<input autoComplete="username" value={identity} onChange={(event) => setIdentity(event.target.value)} disabled={busy} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} required /></label>
      <label className="account-checkbox"><input type="checkbox" checked={enableAgents} onChange={(event) => setEnableAgents(event.target.checked)} disabled={busy} /><span>Enable my agents on this computer<small>Creates a 90-day agent key for League matches.</small></span></label>
      <AsyncActionButton className="primary-action" loading={busy} loadingLabel="Connecting…" disabled={!identity.trim() || !password}>Connect my account</AsyncActionButton>
    </form>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {notice && <p className="inline-success" role="status">{notice}</p>}
    {warning && <p className="inline-notice" role="status">{warning}</p>}
  </div>;
}
