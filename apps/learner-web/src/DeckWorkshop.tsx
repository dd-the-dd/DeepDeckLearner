import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import EntityLabels, { parseLabels, supportsEntityLabels } from './EntityLabels';

import AsyncActionButton from './AsyncActionButton';
import CardResearch from './CardResearch';
import DeckStudies, {type StudyDraft} from './DeckStudies';
import {mergeStudyNotes} from './deck-studies';
import { DeckArtwork, ManaColors, ManaCost } from './DeckVisuals';
import {
  autocompleteCards,
  ApiError,
  createDeckWorkspace,
  deleteDeckWorkspace,
  downloadDeck,
  forkDeckWorkspace,
  loadDeckWorkspace,
  loadDeckWorkspaces,
  loadServerDeckWorkspaces,
  importServerDeckWorkspace,
  saveDeckWorkspace,
  syncDeckWorkspace,
  searchDeckPage,
  type DeckSummary,
  type DeckWorkspace,
  type DeckWorkspaceCard,
  type DeckWorkspaceSummary,
  type LeagueAccount,
  type ServerDeckWorkspace,
} from './api';

type Format = 'legacy' | 'commander';
type Section = DeckWorkspaceCard['section'];

const SECTIONS: Array<{ id: Section; label: string }> = [
  { id: 'main', label: 'Main deck' },
  { id: 'sideboard', label: 'Sideboard' },
  { id: 'considering', label: 'Considering' },
  { id: 'commander', label: 'Commander' },
  { id: 'companion', label: 'Companion' },
];
const cardTags = (text: string) => [...new Set(text.split(',').map(t=>t.trim()).filter(Boolean))];

function colors(deck: DeckSummary) {
  return <ManaColors colors={deck.colors} />;
}

function LocalDeckCard({ deck, onOpen, busy }: { deck: DeckSummary; onOpen: () => void; busy: boolean }) {
  return <article className="workshop-deck-card">
    <DeckArtwork deck={deck} className="workshop-deck-art" />
    <div><small>{deck.format ?? 'Magic'} · version {deck.version}</small><h3>{deck.name}</h3>{colors(deck)}<p>{deck.playableCardCount} cards · saved on this computer</p></div>
    <AsyncActionButton className="secondary-action" loading={busy} loadingLabel="Opening…" onClick={onOpen}>Open deck</AsyncActionButton>
  </article>;
}

function DeckEditor({
  initial,
  onBack,
  onChanged,
  openAssistant,
  account,
  openSettings,
  onDirtyChange,
  onCreateAgent,
}: {
  initial: DeckWorkspace;
  onBack: () => void;
  onChanged: () => void | Promise<void>;
  openAssistant: () => void;
  account: LeagueAccount | null;
  openSettings: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onCreateAgent?: (deck:DeckWorkspace) => void;
}) {
  const [deck, setDeck] = useState(initial);
  const [entityTags, setEntityTags] = useState((initial.tags ?? []).join(', '));
  const [entityCollections, setEntityCollections] = useState((initial.collections ?? []).join(', '));
  const savedText = useRef(initial);
  const [studies,setStudies] = useState<StudyDraft>(()=>({combos:structuredClone(initial.combos ?? []),openingHands:structuredClone(initial.openingHands ?? [])}));
  const [studyEditorOpen,setStudyEditorOpen] = useState(false);
  const [studyEpoch,setStudyEpoch] = useState(0);
  const studyDirty = JSON.stringify(studies.combos)!==JSON.stringify(savedText.current.combos ?? []) || JSON.stringify(studies.openingHands)!==JSON.stringify(savedText.current.openingHands ?? []);
  const studyPayload = {combos:studies.combos,openingHands:studies.openingHands};
  const [tagDraft,setTagDraft] = useState<{name:string;section:Section;text:string;version:number}|null>(null);
  const [localConflict, setLocalConflict] = useState<DeckWorkspace | null>(null);
  const labelsDirty = JSON.stringify(parseLabels(entityTags)) !== JSON.stringify(savedText.current.tags ?? []) || JSON.stringify(parseLabels(entityCollections)) !== JSON.stringify(savedText.current.collections ?? []);
  const detailsDirty = labelsDirty || deck.name !== savedText.current.name || deck.description !== savedText.current.description;
  const unsaved = detailsDirty || tagDraft !== null || studyDirty || studyEditorOpen;
  const labelPayload = supportsEntityLabels(initial) ? {tags: parseLabels(entityTags), collections: parseLabels(entityCollections)} : {};
  function accept(updated: DeckWorkspace,keepStudyDraft=false) {
    savedText.current = updated; setDeck(updated);
    setEntityTags((updated.tags ?? []).join(', '));setEntityCollections((updated.collections ?? []).join(', '));
    setLocalConflict(null);
    setTagDraft(null);
    if(!keepStudyDraft) {setStudies({combos:structuredClone(updated.combos ?? []),openingHands:structuredClone(updated.openingHands ?? [])});setStudyEditorOpen(false);setStudyEpoch(value=>value+1);}
  }
  useEffect(() => {
    if(initial === savedText.current) return;
    if(initial.id===deck.id && initial.version<savedText.current.version) return;
    if (initial.id === deck.id && initial.version !== savedText.current.version && unsaved) {
      setLocalConflict(initial); return;
    }
    if (!unsaved || initial.id !== deck.id) accept(initial);
    else setDeck(current => ({...initial,name:current.name,description:current.description}));
  }, [initial]);
  const [section, setSection] = useState<Section>('main');
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  useEffect(() => {onDirtyChange?.(unsaved || saving);return () => onDirtyChange?.(false);}, [unsaved,saving,onDirtyChange]);
  useEffect(() => {
    if (!unsaved && !saving) return;
    const warn=(event: BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
    window.addEventListener('beforeunload',warn);return ()=>window.removeEventListener('beforeunload',warn);
  }, [unsaved,saving]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState<'sync' | 'publish' | 'pull' | null>(null);
  const visible = deck.cards.map((card, index) => ({ card, index })).filter(({ card }) => card.section === section);
  const total = useMemo(() => deck.cards.filter((card) => card.section !== 'considering' && !card.isGamePiece && !card.isToken).reduce((sum, card) => sum + card.quantity, 0), [deck.cards]);

  useEffect(() => {
    window.localStorage.setItem('deepdeck.activeDeckId', initial.id);
  }, [initial.id]);

  useEffect(() => {
    const value = query.trim();
    if (value.length < 2) { setSuggestions([]); return; }
    const timer = window.setTimeout(() => {
      void autocompleteCards(value).then(setSuggestions).catch(() => setSuggestions([]));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function persist(payload: Parameters<typeof saveDeckWorkspace>[1], success: string,saveStudies=false) {
    if(studyEditorOpen) {setError('Apply or cancel the open study note before saving other deck edits.');return false;}
    if (localConflict) {setError('Resolve the local version conflict before saving. Your draft is preserved.');return false;}
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const updated = await saveDeckWorkspace(deck.id, { name: deck.name, description: deck.description, ...labelPayload, ...(saveStudies && studyDirty?studyPayload:{}), ...payload, expectedVersion: payload.expectedVersion ?? deck.version });
      accept(updated,!saveStudies && studyDirty);
      setMessage(success);
      await onChanged();
      return true;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        try {setLocalConflict(await loadDeckWorkspace(deck.id));} catch { /* Keep the draft even if reload is unavailable. */ }
      }
      setError(reason instanceof Error ? reason.message : 'The deck could not be saved.');
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function leaveEditor() {
    if(studyEditorOpen) {setError('Apply or cancel the open study note before returning to the library.');return;}
    if (tagDraft) {setError('Finish saving the card tags before leaving, or resolve the local conflict.');return;}
    if (detailsDirty || studyDirty) {
      if (!await persist({}, 'Deck details saved.',true)) return;
    }
    onBack();
  }

  async function saveDetails() {
    if (tagDraft) {
      const cards=deck.cards.map(c=>c.name===tagDraft.name && c.section===tagDraft.section?{...c,tags:cardTags(tagDraft.text)}:c);
      return persist({cards,expectedVersion:tagDraft.version},'Deck details and card tags saved.',true);
    }
    return persist({},'Deck details saved.',true);
  }

  async function keepDraftDetails() {
    if(studyEditorOpen) {setError('Apply or cancel the open study note before combining deck versions.');return;}
    if (!localConflict) return;
    setSaving(true);setError('');
    try {
      const payload: Parameters<typeof saveDeckWorkspace>[1] = {expectedVersion:localConflict.version};
      if (deck.name !== savedText.current.name) payload.name=deck.name;
      if (deck.description !== savedText.current.description) payload.description=deck.description;
      if (JSON.stringify(parseLabels(entityTags))!==JSON.stringify(savedText.current.tags ?? [])) payload.tags=parseLabels(entityTags);
      if (JSON.stringify(parseLabels(entityCollections))!==JSON.stringify(savedText.current.collections ?? [])) payload.collections=parseLabels(entityCollections);
      if(JSON.stringify(studies.combos)!==JSON.stringify(savedText.current.combos ?? [])) payload.combos=mergeStudyNotes(savedText.current.combos ?? [],studies.combos,localConflict.combos ?? []);
      if(JSON.stringify(studies.openingHands)!==JSON.stringify(savedText.current.openingHands ?? [])) payload.openingHands=mergeStudyNotes(savedText.current.openingHands ?? [],studies.openingHands,localConflict.openingHands ?? []);
      if (tagDraft) {
        const matches=localConflict.cards.filter(c=>c.name===tagDraft.name && c.section===tagDraft.section);
        if (matches.length!==1) throw new Error('The edited card moved or was removed. Your tag text is preserved; load the latest deck and reapply it to the intended card.');
        payload.cards=localConflict.cards.map(c=>c===matches[0]?{...c,tags:cardTags(tagDraft.text)}:c);
      }
      const updated=await saveDeckWorkspace(deck.id,payload);accept(updated);
      setMessage('Only your edited details were applied to the latest deck. Other changes were kept.');await onChanged();
    } catch(reason) {
      if(reason instanceof ApiError && reason.status===409) {try {setLocalConflict(await loadDeckWorkspace(deck.id));} catch { /* Preserve the draft. */ }}
      setError(reason instanceof Error?reason.message:'Your draft could not be reapplied.');
    } finally {setSaving(false);}
  }

  async function synchronize(action: 'sync' | 'publish' | 'pull', replace = false) {
    if(studyEditorOpen) {setError('Apply or cancel the open study note before synchronizing.');return;}
    if (tagDraft) {setError('Finish saving the card tags before synchronizing.');return;}
    if (localConflict) {setError('Resolve the local version conflict before synchronizing.');return;}
    setSaving(true); setError(''); setMessage(''); setConflict(null);
    let savingDetails = false;
    let expectedVersion = deck.version;
    try {
      // Save text fields first as card edits already persist automatically.
      // A pull must not overwrite unsent form edits without conflict protection.
      if (detailsDirty || studyDirty) {
        savingDetails = true;
        const saved = await saveDeckWorkspace(deck.id, { name: deck.name, description: deck.description, ...labelPayload, ...(studyDirty?studyPayload:{}), expectedVersion: deck.version });
        accept(saved);
        expectedVersion = saved.version;
        savingDetails = false;
      }
      const updated = await syncDeckWorkspace(deck.id, action, replace, expectedVersion);
      accept(updated);
      setMessage(action === 'publish' ? 'Published on the League. Your local agent keeps this deck attached.' : action === 'pull' ? 'Site changes downloaded. Your previous local revision is backed up.' : 'Draft saved to your League account.');
      await onChanged();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        if (savingDetails || reason.code === 'local_deck_revision_conflict') {try {setLocalConflict(await loadDeckWorkspace(deck.id));} catch { /* Draft stays local. */ }}
        else setConflict(action);
      }
      setError(reason instanceof Error ? reason.message : 'Synchronization failed. Your local deck is still saved.');
    } finally { setSaving(false); }
  }

  async function addCard(name: string, reportFailure = false) {
    const cardName = name.trim();
    if (!cardName) return;
    const existing = deck.cards.findIndex((card) => card.name.toLocaleLowerCase() === cardName.toLocaleLowerCase() && card.section === section);
    const cards = deck.cards.map((card) => ({ ...card, tags: [...card.tags] }));
    if (existing >= 0) cards[existing].quantity += 1;
    else cards.push({ name: cardName, quantity: 1, section, tags: [] });
    setQuery('');
    setSuggestions([]);
    if (!await persist({ cards }, `${cardName} added.`) && reportFailure) throw new Error(`${cardName} could not be saved. Please try again.`);
  }

  async function changeCard(index: number, patch: Partial<DeckWorkspaceCard>, expectedVersion = deck.version) {
    const cards = deck.cards.map((card, cardIndex) => cardIndex === index ? { ...card, ...patch } : card);
    await persist({ cards, expectedVersion }, 'Card updated.');
  }

  async function removeCard(index: number) {
    const name = deck.cards[index].name;
    await persist({ cards: deck.cards.filter((_, cardIndex) => cardIndex !== index) }, `${name} removed.`);
  }

  return <div className="deck-editor-shell">
    <header className="deck-editor-header">
      <button className="text-action" type="button" disabled={saving} onClick={() => void leaveEditor()}>← Library</button>
      <div><span className="section-kicker">Local deck · {deck.format}</span><h2>{deck.name}</h2><p>{total} playable cards · revision {deck.version}</p></div>
      <div className="deck-editor-actions"><button className="secondary-action" type="button" onClick={openAssistant}>✦ Ask the deck assistant</button><AsyncActionButton className="primary-action" loading={saving} disabled={Boolean(localConflict) || studyEditorOpen} loadingLabel="Saving…" onClick={() => void saveDetails()}>Save deck</AsyncActionButton></div>
    </header>

    {onCreateAgent && <section className="deck-agent-handoff" aria-label="Create a deck specialist"><div><strong>Give this deck its own agent</strong><p>Prepare a private Python agent with this saved deck attached. Choose its strategy next; nothing is created, published or launched yet.</p>{unsaved && <small>Save your deck and finish open notes or card tags before continuing.</small>}{!total && <small>Add playable cards before preparing an agent.</small>}</div><button type="button" disabled={saving || unsaved || Boolean(localConflict) || !total} onClick={()=>onCreateAgent(savedText.current)}>Create an agent for this deck →</button></section>}

    {localConflict && <section className="sync-conflict local-deck-conflict" role="alert"><strong>This local deck has a newer version</strong><p>Your draft is preserved. The copilot or another window saved revision {localConflict.version}. Load it, or explicitly reapply only your edited details and card tags. Unsaved card actions must be repeated after loading the latest deck.</p><div><button type="button" disabled={saving} onClick={()=>{accept(localConflict);setError('');setMessage('Latest local deck loaded. The unsaved draft was discarded.');}}>Load latest deck</button><button type="button" disabled={saving || !unsaved} onClick={()=>void keepDraftDetails()}>Keep my edited details</button></div>{tagDraft && <p>Unsaved tags for {tagDraft.name}: <code>{tagDraft.text}</code></p>}</section>}
    <section className="deck-sync-bar" aria-label="Deck synchronization">
      <div><span className={`sync-indicator ${deck.syncStatus?.linked ? 'linked' : ''}`} /><div><strong>{deck.syncStatus?.needsPublishing === false ? 'Published on the League' : deck.syncStatus?.linked ? deck.syncStatus.needsUpload ? 'Local changes to sync' : 'Site draft saved · not published' : 'Private local draft'}</strong><small>{deck.syncStatus?.syncedAt ? `Last synced ${new Date(deck.syncStatus.syncedAt).toLocaleString()}` : 'Keep your list here, or connect it to your League account.'}</small></div></div>
      {account?.valid ? <div className="sync-buttons"><AsyncActionButton loading={saving} loadingLabel="Syncing…" onClick={() => void synchronize('sync')}>Save to site</AsyncActionButton><AsyncActionButton className="primary-action" loading={saving} loadingLabel="Publishing…" onClick={() => void synchronize('publish')}>Publish for League</AsyncActionButton>{deck.syncStatus?.linked && <button type="button" disabled={saving} onClick={() => void synchronize('pull')}>Download site changes</button>}</div> : <button className="secondary-action" type="button" onClick={openSettings}>Connect to sync</button>}
    </section>
    {conflict && <section className="sync-conflict" role="alert"><strong>Choose which version to keep</strong><p>{error} A backup is kept before replacing either draft.</p><div><button type="button" disabled={saving} onClick={() => void synchronize('pull', true)}>Use site version</button><button type="button" disabled={saving} onClick={() => void synchronize(conflict === 'publish' ? 'publish' : 'sync', true)}>Use this local version</button><button type="button" onClick={() => { setConflict(null); setError(''); }}>Cancel</button></div></section>}

    <div className="deck-editor-layout">
      <aside className="deck-editor-meta feature-card">
        <fieldset disabled={saving} style={{border:0,padding:0,margin:0,minWidth:0,display:'grid',gap:16}}>
        <label>Deck name<input value={deck.name} onChange={(event) => setDeck({ ...deck, name: event.target.value })} /></label>
        <label>Game plan<textarea rows={6} value={deck.description} onChange={(event) => setDeck({ ...deck, description: event.target.value })} placeholder="What should this deck accomplish? The assistant uses this intent when teaching an agent." /></label>
        {supportsEntityLabels(initial) ? <EntityLabels tags={entityTags} collections={entityCollections} onChange={(field, value) => field === 'tags' ? setEntityTags(value) : setEntityCollections(value)} /> : <p>Restart the app after your active games finish to enable tags and collections.</p>}
        </fieldset><div className="deck-health"><span><i className={total > 0 ? 'ready' : ''} />{total > 0 ? 'Ready for experiments' : 'Add cards to play'}</span><small>The Engine validates exact rules when a match starts.</small></div>
      </aside>

      <section className="deck-card-editor feature-card">
        <nav className="deck-zone-tabs">{SECTIONS.filter((item) => deck.format === 'commander' || item.id !== 'commander').map((item) => { const count = deck.cards.filter((card) => card.section === item.id && !card.isGamePiece && !card.isToken).reduce((sum, card) => sum + card.quantity, 0); return <button type="button" className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)} key={item.id}>{item.label}<span>{count}</span></button>; })}</nav>
        {visible.some(({ card }) => card.isGamePiece || card.isToken) && <p className="inline-notice">Tokens and markers stay available as game pieces. They do not count toward the deck or sideboard size.</p>}
        <form className="deck-card-search" onSubmit={(event) => { event.preventDefault(); void addCard(query); }}><span>＋</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Add a card to ${section}…`} disabled={saving} /><button type="submit" disabled={!query.trim() || saving}>Add</button>{suggestions.length > 0 && <div className="card-suggestions">{suggestions.slice(0, 8).map((name) => <button type="button" onClick={() => void addCard(name)} key={name}>{name}</button>)}</div>}</form>
        <CardResearch key={deck.id} deckId={deck.id} format={deck.format} zone={SECTIONS.find((item) => item.id === section)?.label || section} disabled={saving} onAdd={(name) => addCard(name, true)} />
        {visible.length ? <div className="deck-card-list">{visible.map(({ card, index }) => <article key={`${card.name}-${card.section}`}>
          <div className="card-mini-art">{card.imageUri ? <img src={card.imageUri} alt="" /> : <span>{card.name.slice(0, 1)}</span>}</div>
          <div className="deck-card-copy"><strong>{card.name}</strong><small>{card.typeLine || 'Resolving card metadata…'} {card.manaCost && <ManaCost value={card.manaCost} />}</small><input aria-label={`Tags for ${card.name}`} disabled={saving || Boolean(tagDraft && (tagDraft.name!==card.name || tagDraft.section!==card.section))} value={tagDraft?.name===card.name && tagDraft.section===card.section?tagDraft.text:card.tags.join(', ')} placeholder="Tags: removal, combo-piece…" onChange={event=>setTagDraft({name:card.name,section:card.section,text:event.target.value,version:tagDraft?.version ?? deck.version})} onBlur={(event) => { const tags = cardTags(event.target.value); if (tags.join('|') !== card.tags.join('|')) void changeCard(index, { tags }, tagDraft?.version ?? deck.version); else setTagDraft(null); }} /></div>
          <select aria-label={`Zone for ${card.name}`} value={card.section} onChange={(event) => void changeCard(index, { section: event.target.value as Section })}>{SECTIONS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
          <div className="quantity-stepper"><button type="button" disabled={saving} onClick={() => card.quantity === 1 ? void removeCard(index) : void changeCard(index, { quantity: card.quantity - 1 })}>−</button><strong>{card.quantity}</strong><button type="button" disabled={saving} onClick={() => void changeCard(index, { quantity: card.quantity + 1 })}>＋</button></div>
          <button className="remove-card" type="button" onClick={() => void removeCard(index)} aria-label={`Remove ${card.name}`}>×</button>
        </article>)}</div> : <div className="zone-empty"><span>◇</span><h3>This zone is empty</h3><p>Search for an exact card name above or ask the assistant to shape the list.</p></div>}
      </section>
    </div>
    <DeckStudies key={`${deck.id}:study:${studyEpoch}`} value={studies} cards={deck.cards} serverState={deck.serverState} version={deck.version} disabled={saving} dirty={studyDirty} onSave={saveDetails} saveDisabled={Boolean(localConflict)} onChange={setStudies} onEditingChange={setStudyEditorOpen} onDiscard={()=>setStudies({combos:structuredClone(savedText.current.combos ?? []),openingHands:structuredClone(savedText.current.openingHands ?? [])})} openAssistant={openAssistant} latest={localConflict?{combos:localConflict.combos ?? [],openingHands:localConflict.openingHands ?? []}:undefined}/>
    {studyEditorOpen && <p className="inline-notice">Apply or cancel the study note before saving the deck. Your form is not saved yet.</p>}
    {(message || error) && <p className={error ? 'inline-error' : 'inline-success'} role="status">{error || message}</p>}
  </div>;
}

export default function DeckWorkshop({
  downloaded,
  onImported,
  openAssistant,
  revisionKey = 0,
  account,
  openSettings,
  onDirtyChange,
  onCreateAgent,
}: {
  downloaded: DeckSummary[];
  onImported: () => void | Promise<void>;
  openAssistant: () => void;
  revisionKey?: number;
  account: LeagueAccount | null;
  openSettings: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onCreateAgent?: (deck:DeckWorkspace) => void;
}) {
  const [format, setFormat] = useState<Format>('legacy');
  const [workspaces, setWorkspaces] = useState<DeckWorkspaceSummary[]>([]);
  const [editor, setEditor] = useState<DeckWorkspace | null>(null);
  const editorDirty = useRef(false);
  const trackDirty = useCallback((dirty: boolean) => {editorDirty.current=dirty;onDirtyChange?.(dirty);},[onDirtyChange]);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DeckSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [serverDecks, setServerDecks] = useState<ServerDeckWorkspace[]>([]);
  const [serverLoading, setServerLoading] = useState(false);
  const [serverError, setServerError] = useState('');

  async function refreshServer() {
    setServerLoading(true); setServerError('');
    try { setServerDecks(await loadServerDeckWorkspaces()); }
    catch (reason) { setServerError(reason instanceof Error ? reason.message : 'Your site decks could not be loaded.'); }
    finally { setServerLoading(false); }
  }

  useEffect(() => { if (account?.valid) void refreshServer(); else setServerDecks([]); }, [account?.valid]);

  async function openServerDeck(workspace: ServerDeckWorkspace) {
    setBusy(workspace.id); setServerError('');
    try {
      const existing = workspaces.find((item) => item.syncStatus?.workspaceId === workspace.id);
      const opened = existing ? await loadDeckWorkspace(existing.id) : await importServerDeckWorkspace(workspace.id);
      setEditor(opened); await refresh();
    } catch (reason) { setServerError(reason instanceof Error ? reason.message : 'Import failed.'); }
    finally { setBusy(''); }
  }

  async function refresh() {
    setWorkspaces(await loadDeckWorkspaces());
    await onImported();
  }

  useEffect(() => {
    let active = true;
    void loadDeckWorkspaces().then((items) => { if (active) setWorkspaces(items); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'The local library could not be loaded.'); });
    const target = editorDirty.current ? editor?.id ?? window.localStorage.getItem('deepdeck.activeDeckId') : window.localStorage.getItem('deepdeck.activeDeckId');
    if (editorDirty.current && editor) window.localStorage.setItem('deepdeck.activeDeckId',editor.id);
    if (revisionKey > 0 && target) void loadDeckWorkspace(target).then((updated) => { if (active) setEditor(updated); }).catch(() => undefined);
    return () => { active = false; };
  }, [revisionKey]);

  async function createNew() {
    setBusy('new'); setError('');
    try { const created = await createDeckWorkspace({ name: `Untitled ${format === 'legacy' ? 'Legacy' : 'Commander'} deck`, format }); setEditor(created); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The deck could not be created.'); }
    finally { setBusy(''); }
  }

  async function openDeck(deck: DeckSummary) {
    setBusy(deck.id); setError('');
    try { const opened = deck.id.startsWith('localdeck-') ? await loadDeckWorkspace(deck.id) : await forkDeckWorkspace(deck.id); setEditor(opened); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The deck could not be opened.'); }
    finally { setBusy(''); }
  }

  async function search(event?: FormEvent) {
    event?.preventDefault(); setLoading(true); setError('');
    try { setResults((await searchDeckPage(query, format)).items); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'League search failed.'); }
    finally { setLoading(false); }
  }

  async function importLeague(deck: DeckSummary) {
    setBusy(deck.id); setError('');
    try { await downloadDeck(deck.id); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The deck could not be imported.'); }
    finally { setBusy(''); }
  }

  async function removeWorkspace(deck: DeckWorkspaceSummary) {
    if (!window.confirm(`Delete the local deck “${deck.name}”?`)) return;
    setBusy(deck.id);
    try { await deleteDeckWorkspace(deck.id); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The deck could not be deleted.'); }
    finally { setBusy(''); }
  }

  if (editor) return <DeckEditor key={editor.id} initial={editor} onBack={() => setEditor(null)} onChanged={refresh} openAssistant={openAssistant} account={account} openSettings={openSettings} onDirtyChange={trackDirty} onCreateAgent={onCreateAgent} />;

  const library = downloaded.filter((deck) => deck.format === format);
  return <div className="deck-workshop deck-workshop-v2">
    <section className="feature-card deck-library-card">
      <header className="feature-heading"><div><span className="section-kicker">On this computer</span><h2>Your deck workshop</h2><p>Create a list without code, fork a League deck, then teach an agent to pilot it.</p></div><AsyncActionButton className="primary-action" loading={busy === 'new'} loadingLabel="Creating…" onClick={() => void createNew()}>＋ New deck</AsyncActionButton></header>
      <div className="segmented compact"><button className={format === 'legacy' ? 'active' : ''} type="button" onClick={() => setFormat('legacy')}>Legacy</button><button className={format === 'commander' ? 'active' : ''} type="button" onClick={() => setFormat('commander')}>Commander</button></div>
      {library.length ? <div className="workshop-deck-grid">{library.map((deck) => <div key={deck.id}><LocalDeckCard deck={deck} busy={busy === deck.id} onOpen={() => void openDeck(deck)} />{deck.id.startsWith('localdeck-') && <button className="delete-workspace" type="button" onClick={() => { const summary = workspaces.find((item) => item.id === deck.id); if (summary) void removeWorkspace(summary); }}>Delete local copy</button>}</div>)}</div> : <div className="zone-empty"><span>◇</span><h3>Start with an idea</h3><p>Create a blank deck or import one from Deep Deck League.</p><button className="primary-action" type="button" onClick={() => void createNew()}>Create my first deck</button></div>}
    </section>

    <div className="deck-workshop-sidebar"><section className="feature-card owned-site-decks">
      <header className="feature-heading"><div><span className="section-kicker">Your League account</span><h2>My site decks</h2><p>Continue a deck you started on the site, with its tags and sideboard.</p></div>{account?.valid ? <button type="button" disabled={serverLoading} onClick={() => void refreshServer()}>{serverLoading ? 'Refreshing…' : 'Refresh'}</button> : <button className="secondary-action" type="button" onClick={openSettings}>Connect my account</button>}</header>
      {account?.valid && !serverDecks.length && !serverLoading && !serverError && <p className="inline-notice">No site drafts yet. Publish a local list above to start your collection.</p>}
      {serverLoading && <p role="status">Loading your site decks…</p>}
      <div className="owned-deck-list">{serverDecks.filter((deck) => !deck.state.format || deck.state.format === format).map((deck) => <article key={deck.id}><div><strong>{deck.name}</strong><small>{deck.state.format || 'Choose a format'} · {deck.hasUnpublishedChanges ? 'Unpublished draft' : `Published v${deck.version || 1}`}</small></div><AsyncActionButton loading={busy === deck.id} loadingLabel="Opening…" disabled={Boolean(busy)} onClick={() => void openServerDeck(deck)}>{workspaces.some((item) => item.syncStatus?.workspaceId === deck.id) ? 'Open local copy' : 'Import & edit'}</AsyncActionButton></article>)}</div>
      {serverError && <p className="inline-error" role="alert">{serverError}</p>}
    </section>

    <section className="feature-card league-deck-search">
      <header className="feature-heading"><div><span className="section-kicker">Deep Deck League</span><h2>Find a starting point</h2><p>Search public lists, keep a local copy, or fork one into your workshop.</p></div><span className="sync-state"><i />{library.length} available locally</span></header>
      <form className="prominent-search" onSubmit={search}><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search Delver, Reanimator, creator…" /><button type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search League'}</button></form>
      <div className="filter-row"><button className="semantic-shortcut" type="button" onClick={openAssistant}>✦ Describe the deck you want</button></div>
      {results.length > 0 && <div className="search-results">{results.map((deck) => { const exists = downloaded.some((item) => item.id === deck.id); return <article key={deck.id}><DeckArtwork deck={deck} /><div><strong>{deck.name}</strong><small>{deck.creator || 'League player'} · {deck.playableCardCount} cards · v{deck.version}</small>{colors(deck)}</div><AsyncActionButton loading={busy === deck.id} loadingLabel="Importing…" disabled={exists || Boolean(busy)} onClick={() => void importLeague(deck)}>{exists ? 'On this computer' : 'Import'}</AsyncActionButton></article>; })}</div>}
      {!results.length && <div className="search-inspiration"><span>Not sure where to begin?</span><button type="button" onClick={openAssistant}>“Build a resilient Legacy tempo shell with a low curve”</button><button type="button" onClick={openAssistant}>“Find something similar to my Dimir list”</button></div>}
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
    </div>
  </div>;
}
