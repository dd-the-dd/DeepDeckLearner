import {useRef,useState} from 'react';
import ReplayStorageSettings from './ReplayStorageSettings';
import ReplayCollectionStatus from './ReplayCollectionStatus';
import type {ExperienceSettings,Job} from './api';
import './replay-storage-control.css';

export default function ReplayStorageControl({settings,jobs,onSaved,onRecovered}:{settings:ExperienceSettings|null;jobs:Job[];onSaved?:(value:ExperienceSettings)=>void;onRecovered?:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [visited,setVisited]=useState(false),[open,setOpen]=useState(false);
  function show(){setVisited(true);setOpen(true);dialog.current?.showModal();}
  return <div className="replay-storage-control">
    <button className="replay-storage-trigger" type="button" onClick={show} aria-haspopup="dialog" aria-expanded={open}>
      <svg className="dd-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="m10 3-.7 2.3-2 .9L5 5.7 2.8 9.5l1.8 1.6v1.8l-1.8 1.6L5 18.3l2.3-.5 2 .9L10 21h4l.7-2.3 2-.9 2.3.5 2.2-3.8-1.8-1.6v-1.8l1.8-1.6L19 5.7l-2.3.5-2-.9L14 3z"/></svg>
      <span>Limit replay storage</span>
    </button>
    <dialog className="replay-storage-dialog" ref={dialog} aria-labelledby="replay-storage-title" onClose={()=>setOpen(false)}>
      <header><h2 id="replay-storage-title">Replay settings</h2><button type="button" aria-label="Close replay settings" onClick={()=>dialog.current?.close()}>×</button></header>
      {visited&&<ReplayStorageSettings settings={settings} onSaved={value=>onSaved?.(value)}/>}
      {open&&<details><summary>Collection status and pending downloads</summary><ReplayCollectionStatus settings={settings} jobs={jobs} onRecovered={onRecovered}/></details>}
    </dialog>
  </div>;
}
