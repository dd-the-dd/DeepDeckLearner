import {useState} from 'react';
import AsyncActionButton from './AsyncActionButton';
import {saveExperienceSettings, type ExperienceSettings} from './api';
import {replayInterval} from './ReplayCollectionStatus';

export default function ReplayStorageSettings({settings,onSaved}:{settings:ExperienceSettings|null;onSaved:(value:ExperienceSettings)=>void}) {
  const [quota,setQuota]=useState<number|null>(null);
  const [collect,setCollect]=useState<boolean|null>(null);
  const [saving,setSaving]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const limit=quota ?? settings?.replayStorageLimitBytes ?? 2*1024**3;
  const enabled=collect ?? settings?.autoCollectLeagueReplays ?? false;
  const dirty=quota!==null || collect!==null;
  async function save() {
    if(!settings || saving)return;
    setSaving(true);setMessage('');setError('');
    try {
      const saved=await saveExperienceSettings({...(quota!==null?{replayStorageLimitBytes:quota}:{}),...(collect!==null?{autoCollectLeagueReplays:collect}:{})});
      onSaved(saved);setQuota(null);setCollect(null);setMessage('Replay settings saved. Active agents read them on their next collection check.');
    } catch(reason) {setError(reason instanceof Error?reason.message:'Replay settings could not be saved. Your choices are kept.');}
    finally {setSaving(false);}
  }
  return <section className="replay-storage-settings" aria-label="Replay storage settings">
    <h3>Replay storage</h3>
    {!settings && <p role="status">Checking replay storage settings…</p>}
    <label className="replay-collect-toggle"><input type="checkbox" checked={enabled} disabled={!settings||saving} onChange={event=>setCollect(event.target.checked)}/>Automatically collect League replays</label>
    <p>Collects finalized games from agents running on this computer. Pausing collection does not stop an agent or delete recordings. A collection check already in progress may finish.</p>
    <label>Maximum replay storage<div className="range-value"><input aria-label="Maximum replay storage" type="range" min=".25" max="100" step=".25" disabled={!settings||saving} value={limit/1024**3} onChange={event=>setQuota(Math.round(Number(event.target.value)*1024**3))}/><output>{(limit/1024**3).toFixed(limit<1024**3?2:1)} GB</output></div></label>
    <p>This is a storage cap, not preallocated disk space. Reducing it may remove the oldest non-favorites. Favorites and recordings being watched remain protected.</p>
    {settings && <p>Collection checks every {replayInterval(settings.replayPollSeconds)} while an agent runs. The current interval is preserved when saving.</p>}
    <div className="replay-settings-actions"><AsyncActionButton type="button" loading={saving} loadingLabel="Saving replay settings…" disabled={!settings||!dirty} onClick={()=>void save()}>Save replay settings</AsyncActionButton>{dirty && <button type="button" disabled={saving} onClick={()=>{setQuota(null);setCollect(null);setMessage('');setError('');}}>Discard replay changes</button>}</div>
    {message && <p className="settings-message success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {settings?.replayStorageError && <p className="inline-error" role="alert">{settings.replayStorageError}</p>}
  </section>;
}
