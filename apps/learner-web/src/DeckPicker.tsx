import { useEffect, useRef, useState, type FormEvent } from "react";

import AsyncActionButton from "./AsyncActionButton";
import { DeckArtwork, ManaColors } from "./DeckVisuals";
import { downloadDeck, searchDeckPage, type DeckSummary } from "./api";

export default function DeckPicker({ decks, format, onPick, onClose, onImported, title = 'Choose your deck', description = 'Use a local list or bring a deck home from the League.', selectedIds = [] }: {
  decks: DeckSummary[];
  format: "legacy" | "commander";
  onPick: (deckId: string) => void | Promise<void>;
  onClose: () => void;
  onImported: () => void | Promise<void>;
  title?: string;
  description?: string;
  selectedIds?: string[];
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [source, setSource] = useState<"local" | "league">("local");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DeckSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [offline, setOffline] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function search(event?: FormEvent, nextPage = 1) {
    event?.preventDefault();
    setLoading(true); setError("");
    try {
      const result = await searchDeckPage(query, format, nextPage);
      setResults(result.items); setPage(nextPage); setHasMore(result.items.length >= 12);
      setOffline(Boolean(result.offline));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "League decks could not be loaded."); }
    finally { setLoading(false); }
  }
  async function choose(deck: DeckSummary) {
    setImporting(deck.id); setError("");
    try {
      if (!decks.some((item) => item.id === deck.id)) { await downloadDeck(deck.id); await onImported(); }
      await onPick(deck.id);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "This deck could not be imported."); }
    finally { setImporting(""); }
  }
  const visible = source === "local" ? decks.filter((deck) => deck.format === format && `${deck.name} ${deck.creator ?? ""}`.toLowerCase().includes(query.toLowerCase())) : results;
  return <dialog ref={dialog} className="deck-picker" aria-labelledby="deck-picker-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><div><span className="section-kicker">Deck library · {format}</span><h2 id="deck-picker-title">{title}</h2><p>{description}</p></div><button type="button" onClick={onClose} aria-label="Close deck picker">×</button></header>
    <div className="segmented compact"><button type="button" className={source === "local" ? "active" : ""} onClick={() => { setSource("local"); setError(""); }}>On this computer</button><button type="button" className={source === "league" ? "active" : ""} onClick={() => { setSource("league"); void search(); }}>League library</button></div>
    <form className="prominent-search" onSubmit={(event) => { if (source === "local") event.preventDefault(); else void search(event); }}><input aria-label="Search decks" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search deck name or creator…" /><button type="submit" disabled={loading}>{loading ? "Searching…" : "Search"}</button></form>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {source === "league" && offline && <p className="offline-deck-notice" role="status">The League is unavailable. These downloaded lists are still ready to play.</p>}
    <div className="deck-picker-results">{visible.map((deck) => <article key={deck.id}><DeckArtwork deck={deck} /><div><strong>{deck.name}</strong><small>{deck.creator ? `by ${deck.creator} · ` : ""}Version {deck.version} · {deck.playableCardCount} cards</small><ManaColors colors={deck.colors} /></div><AsyncActionButton loading={importing === deck.id} disabled={Boolean(importing) || selectedIds.includes(deck.id)} loadingLabel="Preparing…" onClick={() => void choose(deck)}>{selectedIds.includes(deck.id) ? "Already selected" : decks.some((item) => item.id === deck.id) ? "Use deck" : "Import & use"}</AsyncActionButton></article>)}{!visible.length && !loading && <div className="dd-empty"><h3>{source === "local" ? "No matching local deck" : "No matching League deck"}</h3><p>{source === "local" ? "Try another search or open the League library." : "Try a deck name, creator, or another search."}</p></div>}</div>
    {source === "league" && <footer><button type="button" disabled={loading || page <= 1} onClick={() => void search(undefined, page - 1)}>Previous</button><span>Page {page}</span><button type="button" disabled={loading || !hasMore} onClick={() => void search(undefined, page + 1)}>Next</button></footer>}
  </dialog>;
}
