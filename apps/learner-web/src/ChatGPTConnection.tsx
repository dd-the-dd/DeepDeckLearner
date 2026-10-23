import { useEffect, useRef, useState } from 'react';
import { chatGPTAction, loadChatGPTAccounts, loadChatGPTFlow, loadChatGPTModels, type ChatGPTAccounts } from './api';
import AsyncActionButton from './AsyncActionButton';

export default function ChatGPTConnection({ initial, model, onModel, onReady }: {
  initial?: ChatGPTAccounts; model: string; onModel: (value: string) => void; onReady: (ready: boolean) => void;
}) {
  const [accounts, setAccounts] = useState(initial);
  const [flowId, setFlowId] = useState(initial?.pendingFlowId ?? '');
  const [models, setModels] = useState<Array<{ id: string; name: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [welcome, setWelcome] = useState(false);
  const welcomeDialog = useRef<HTMLDialogElement>(null);
  const active = accounts?.accounts.find((item) => item.id === accounts.activeAccountId && item.connected);
  useEffect(() => { if (active && !window.localStorage.getItem('deepdeck.chatgptPlanWelcomeSeen')) setWelcome(true); }, [active?.id]);
  useEffect(() => { if (welcome && welcomeDialog.current) { if (typeof welcomeDialog.current.showModal === 'function') welcomeDialog.current.showModal(); else welcomeDialog.current.setAttribute('open', ''); } }, [welcome]);
  function acknowledgeWelcome() { window.localStorage.setItem('deepdeck.chatgptPlanWelcomeSeen', '1'); welcomeDialog.current?.close?.(); setWelcome(false); }
  useEffect(() => { onReady(Boolean(active && models.some((item) => item.id === model))); }, [active?.id, models, model, onReady]);
  useEffect(() => {
    let mounted = true;
    loadChatGPTAccounts().then((value) => { if (mounted) { setAccounts(value); setFlowId(value.pendingFlowId ?? ''); } }).catch(() => { if (mounted) setError('ChatGPT connection could not be loaded.'); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    let mounted = true;
    setModels([]);
    if (active) loadChatGPTModels().then((value) => { if (mounted) setModels(value.items); }).catch((reason: unknown) => { if (mounted) setError(reason instanceof Error ? reason.message : 'Models could not be loaded.'); });
    return () => { mounted = false; };
  }, [active?.id]);
  useEffect(() => {
    if (!flowId) return;
    let mounted = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await loadChatGPTFlow(flowId);
        if (!mounted) return;
        if (value.status === 'pending') { timer = setTimeout(() => void poll(), 1500); return; }
        setFlowId('');
        if (value.status === 'completed') {
          setAccounts(await loadChatGPTAccounts()); setNotice('ChatGPT connected. Choose a model and save this connection.');
        } else setError(value.status === 'cancelled' ? 'Sign-in cancelled or expired.' : 'Sign-in could not be completed. Your previous connection was kept.');
      } catch (reason) { if (mounted) { setFlowId(''); setError(reason instanceof Error ? reason.message : 'Sign-in could not be checked.'); } }
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => { mounted = false; clearTimeout(timer); };
  }, [flowId]);
  async function action(kind: 'start' | 'cancel' | 'select' | 'disconnect', accountId = '') {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await chatGPTAction(kind, kind === 'cancel' ? { flowId } : accountId ? { accountId } : {});
      if (kind === 'start') setFlowId(value.flowId || '');
      else if (kind === 'cancel') setFlowId('');
      else {
        setAccounts(value);
        if (kind === 'select') onModel('');
        if (kind === 'disconnect') setNotice(value.revocationConfirmed ? 'Signed out.' : 'Signed out locally. Remote revocation was not confirmed; disconnect this app in ChatGPT settings.');
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'ChatGPT connection failed.'); }
    finally { setBusy(false); }
  }
  return <section className="chatgpt-connection" aria-label="ChatGPT subscription connection">
    <p>Use an eligible ChatGPT plan with explicit permission. Usage is shared with your plan—not an unlimited or separate allowance. No automatic API billing fallback.</p>
    {accounts?.accounts.map((item) => <div className="chatgpt-account" key={item.id}><span><strong>{item.label}</strong><small>Registration {item.registration} · {item.connected ? item.id === active?.id ? 'Active' : 'Saved connection' : 'Signed out'}</small></span>{item.connected && item.id !== active?.id && <button type="button" disabled={busy || Boolean(flowId)} onClick={() => void action('select', item.id)}>Use account</button>}{!item.connected && <button type="button" disabled={busy || Boolean(flowId)} onClick={() => void action('start', item.id)}>Sign in again</button>}{item.connected && <button type="button" disabled={busy || Boolean(flowId)} onClick={() => void action('disconnect', item.id)}>Sign out</button>}</div>)}
    {flowId ? <><p role="status">Complete sign-in in your browser. Waiting for permission…</p><button type="button" disabled={busy} onClick={() => void action('cancel')}>Cancel sign-in</button></> : <AsyncActionButton className="chatgpt-sign-in" type="button" loading={busy} loadingLabel="Opening browser…" onClick={() => void action('start')}>Continue with ChatGPT</AsyncActionButton>}
    {active && <><label>ChatGPT model<select value={model} onChange={(event) => onModel(event.target.value)}><option value="">Choose an available model</option>{models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><small>The model catalog is not an access guarantee; successful requests confirm account access.</small></>}
    <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage ChatGPT usage and app access ↗</a>
    {notice && <p role="status">{notice}</p>}{error && <p className="inline-error" role="alert">{error}</p>}
    {welcome && <dialog className="chatgpt-welcome" ref={welcomeDialog} aria-labelledby="chatgpt-welcome-title" onCancel={acknowledgeWelcome}><h3 id="chatgpt-welcome-title">You're using your ChatGPT plan</h3><p>Eligible assistant requests will use your ChatGPT plan and its shared allowance. You can review limits and app access in ChatGPT settings. DeepDeck never switches this connection to paid API billing automatically.</p><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage usage ↗</a><button type="button" className="primary-action" onClick={acknowledgeWelcome}>Got it</button></dialog>}
  </section>;
}
