import { FormEvent, useEffect, useMemo, useState } from "react";

import AsyncActionButton from "./AsyncActionButton";
import { submitChatOnEnter } from './chat-keyboard';
import {
  chatWithAgentStudio,
  createProgrammaticAgent,
  downloadDeck,
  loadHeuristicCatalog,
  loadLlmSettings,
  loadProgrammaticAgents,
  saveLlmSettings,
  searchDecks,
  updateProgrammaticAgent,
  type DeckSummary,
  type HeuristicCatalog,
  type LlmSettings,
  type ProgrammaticAgent,
} from "./api";

type ChatMessage = { role: "user" | "assistant"; content: string };
type Archetype = "aggro" | "tempo" | "midrange" | "control" | "combo";

const ARCHETYPES: Archetype[] = ["aggro", "tempo", "midrange", "control", "combo"];
const DEFAULT_MIX: Record<Archetype, number> = {
  aggro: .15,
  tempo: .2,
  midrange: .35,
  control: .2,
  combo: .1,
};
const PROFILE_MIXES: Record<"cautious" | "balanced" | "aggressive", Record<Archetype, number>> = {
  cautious: { aggro: .05, tempo: .15, midrange: .25, control: .5, combo: .05 },
  balanced: DEFAULT_MIX,
  aggressive: { aggro: .55, tempo: .25, midrange: .1, control: .03, combo: .07 },
};

function names(value: string) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

export default function AgentStudio({
  refresh,
  openPlaytest,
  revisionKey = "",
}: {
  refresh: () => void | Promise<void>;
  openPlaytest: (agent?: ProgrammaticAgent) => void;
  revisionKey?: string;
}) {
  const [agents, setAgents] = useState<ProgrammaticAgent[]>([]);
  const [catalog, setCatalog] = useState<HeuristicCatalog | null>(null);
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const selected = agents.find((agent) => agent.id === selectedId) ?? null;
  const [error, setError] = useState("");

  const [name, setName] = useState("");
  const [format, setFormat] = useState<"legacy" | "commander">("legacy");
  const [profile, setProfile] = useState<"cautious" | "balanced" | "aggressive">("balanced");
  const [deckQuery, setDeckQuery] = useState("");
  const [deckResults, setDeckResults] = useState<DeckSummary[]>([]);
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [creating, setCreating] = useState(false);

  const [weights, setWeights] = useState<Record<string, number>>({});
  const [archetypeMix, setArchetypeMix] = useState<Record<Archetype, number>>(DEFAULT_MIX);
  const [temperature, setTemperature] = useState(0);
  const [deckAdaptation, setDeckAdaptation] = useState(.65);
  const [decisionSeed, setDecisionSeed] = useState(0);
  const [editProfile, setEditProfile] = useState<"cautious" | "balanced" | "aggressive">("balanced");
  const [strategyNotes, setStrategyNotes] = useState("");
  const [preferCards, setPreferCards] = useState("");
  const [avoidCards, setAvoidCards] = useState("");
  const [holdCards, setHoldCards] = useState("");
  const [savingAgent, setSavingAgent] = useState(false);

  const [providerName, setProviderName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [savingLlm, setSavingLlm] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [chatting, setChatting] = useState(false);

  async function reload() {
    const [loadedAgents, nextCatalog, loadedLlm] = await Promise.all([
      loadProgrammaticAgents(),
      loadHeuristicCatalog(),
      loadLlmSettings(),
    ]);
    const nextAgents = loadedAgents ?? [];
    const nextLlm = loadedLlm ?? {
      providerName: "OpenAI-compatible endpoint",
      baseUrl: "",
      model: "",
      hasApiKey: false,
      configured: false,
    };
    setAgents(nextAgents);
    setCatalog(nextCatalog);
    setLlm(nextLlm);
    setProviderName(nextLlm.providerName);
    setBaseUrl(nextLlm.baseUrl);
    setLlmModel(nextLlm.model);
    setSelectedId((current) => {
      const activeId = window.localStorage.getItem("deepdeck.activeAgentId");
      return nextAgents.find((agent) => agent.id === activeId)?.id ?? (nextAgents.some((agent) => agent.id === current) ? current : nextAgents[0]?.id ?? "");
    });
  }

  useEffect(() => {
    void reload().catch((reason) => setError(reason instanceof Error ? reason.message : "Unable to load Agent Studio."));
  }, [revisionKey]);

  useEffect(() => {
    if (!selected) return;
    window.localStorage.setItem("deepdeck.activeAgentId", selected.id);
    setWeights(selected.heuristics);
    setArchetypeMix(selected.strategyGraph?.archetypeMix ?? DEFAULT_MIX);
    setTemperature(selected.strategyGraph?.temperature ?? 0);
    setDeckAdaptation(selected.strategyGraph?.deckAdaptation ?? .65);
    setDecisionSeed(selected.strategyGraph?.seed ?? 0);
    setEditProfile(selected.profile);
    setStrategyNotes(selected.strategyNotes);
    setPreferCards(selected.cardPreferences.prefer.join(", "));
    setAvoidCards(selected.cardPreferences.avoid.join(", "));
    setHoldCards(selected.cardPreferences.holdForInteraction.join(", "));
  }, [selected]);

  useEffect(() => {
    setDecks([]);
    setDeckResults([]);
  }, [format]);

  const deckIds = useMemo(() => decks.map((deck) => deck.id), [decks]);

  async function findDecks() {
    setError("");
    try {
      setDeckResults(await searchDecks(deckQuery, format));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to search decks.");
    }
  }

  async function toggleDeck(deck: DeckSummary) {
    if (decks.some((item) => item.id === deck.id)) {
      setDecks((current) => current.filter((item) => item.id !== deck.id));
      return;
    }
    setError("");
    try {
      const downloaded = await downloadDeck(deck.id);
      setDecks((current) => [...current, { ...deck, playableCardCount: downloaded.cardCount }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to download this deck.");
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError("");
    try {
      const agent = await createProgrammaticAgent({ name, format, profile, deckVersionIds: deckIds });
      setName("");
      setDecks([]);
      await reload();
      setSelectedId(agent.id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create this agent.");
    } finally {
      setCreating(false);
    }
  }

  async function saveAgent() {
    if (!selected) return;
    setSavingAgent(true);
    setError("");
    try {
      const updated = await updateProgrammaticAgent(selected.id, {
        profile: editProfile,
        heuristics: weights,
        strategyGraph: {
          archetypeMix,
          deckAdaptation,
          temperature,
          seed: decisionSeed,
          patterns: selected.strategyGraph?.patterns ?? [],
        },
        revisionReason: "Manual policy edit from Agent Studio.",
        strategyNotes,
        cardPreferences: {
          prefer: names(preferCards),
          avoid: names(avoidCards),
          holdForInteraction: names(holdCards),
        },
      });
      setAgents((current) => current.map((agent) => agent.id === updated.id ? updated : agent));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save this policy.");
    } finally {
      setSavingAgent(false);
    }
  }

  async function saveProvider(event: FormEvent) {
    event.preventDefault();
    setSavingLlm(true);
    setError("");
    try {
      const payload: Record<string, unknown> = { providerName, baseUrl, model: llmModel };
      if (apiKey) payload.apiKey = apiKey;
      const next = await saveLlmSettings(payload);
      setLlm(next);
      setApiKey("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save the LLM connection.");
    } finally {
      setSavingLlm(false);
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content || chatting || !llm?.configured) return;
    const conversation = [...messages, { role: "user" as const, content }];
    setMessages(conversation);
    setPrompt("");
    setChatting(true);
    setError("");
    try {
      const result = await chatWithAgentStudio(conversation);
      setMessages([...conversation, { role: "assistant", content: result.message }]);
      setAgents(result.agents);
      if (!selectedId && result.agents[0]) setSelectedId(result.agents[0].id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The LLM request failed.");
    } finally {
      setChatting(false);
    }
  }

  return <div className="studio-layout">
    <section className="panel studio-intro">
      <div><span className="eyebrow">Deterministic by design</span><h2>Build deck specialists, with or without an LLM</h2></div>
      <p>Agents are versioned JSON policies in <code>.deepdeck/agents/</code>. The committed runtime owns legality and reusable heuristics; chat can only call bounded agent tools, never write or execute Python.</p>
    </section>

    <section className="panel studio-create">
      <div className="section-heading"><div><span className="eyebrow">Manual workflow</span><h2>Create your agent</h2><p className="section-lead">Choose the decks it understands, then tune transparent behavior.</p></div><span className="step">01</span></div>
      <form onSubmit={create}>
        <div className="field-grid">
          <label>Agent name<input value={name} maxLength={64} onChange={(event) => setName(event.target.value)} placeholder="Tempo specialist" /></label>
          <label>Base profile<select value={profile} onChange={(event) => setProfile(event.target.value as typeof profile)}><option value="cautious">Cautious</option><option value="balanced">Balanced</option><option value="aggressive">Aggressive</option></select></label>
          <label>Format<select value={format} onChange={(event) => setFormat(event.target.value as typeof format)}><option value="legacy">Legacy</option><option value="commander">Commander</option></select></label>
        </div>
        <div className="studio-decks">
          <div className="deck-finder"><label>Find {format} decks<input value={deckQuery} onChange={(event) => setDeckQuery(event.target.value)} placeholder="Deck name or creator" /></label><button type="button" onClick={() => void findDecks()}>Search</button></div>
          {decks.length > 0 && <div className="selected-training-pool">{decks.map((deck) => <button type="button" key={deck.id} onClick={() => void toggleDeck(deck)}><span><strong>{deck.name}</strong><small>v{deck.version}</small></span><b>×</b></button>)}</div>}
          {deckResults.length > 0 && <div className="training-deck-grid">{deckResults.map((deck) => { const active = deckIds.includes(deck.id); return <button type="button" className={active ? "selected" : ""} key={deck.id} onClick={() => void toggleDeck(deck)}><span>{active ? "✓" : "+"}</span><strong>{deck.name}</strong><small>{deck.playableCardCount} cards</small></button>; })}</div>}
        </div>
        <AsyncActionButton className="primary" loading={creating} loadingLabel="Creating agent…" disabled={name.trim().length < 2 || decks.length === 0}>Create agent</AsyncActionButton>
      </form>
    </section>

    <section className="panel studio-policy">
      <div className="section-heading"><div><span className="eyebrow">Reusable heuristics</span><h2>Tune its strategy</h2><a className="strategy-map-link" href="/assets/strategy-graphs.html" target="_blank" rel="noreferrer">Explore the strategy graph ↗</a></div><span className="step">02</span></div>
      {agents.length === 0 ? <div className="empty"><span>AI</span><p>Create an agent above or ask the chat to create one.</p></div> : <>
        <div className="field-grid"><label>Agent<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · r{agent.revision}</option>)}</select></label><label>Base profile<select value={editProfile} onChange={(event) => { const next = event.target.value as typeof editProfile; setEditProfile(next); setArchetypeMix(PROFILE_MIXES[next]); }}><option value="cautious">Cautious</option><option value="balanced">Balanced</option><option value="aggressive">Aggressive</option></select></label></div>
        {selected && <>
          <div className="strategy-graph-heading"><div><span className="eyebrow">Live game plan</span><h3>Archetype blend</h3><p>The agent can borrow from several plans and still change roles from one board to the next.</p></div><code>{selected.strategyGraph?.version ?? catalog?.modelVersion}</code></div>
          <div className="archetype-grid">{ARCHETYPES.map((archetype) => <label key={archetype}><span><strong>{archetype}</strong><output>{Math.round((archetypeMix[archetype] ?? 0) * 100)}%</output></span><input aria-label={`${archetype} weight`} type="range" min="0" max="1" step="0.05" value={archetypeMix[archetype] ?? 0} onChange={(event) => setArchetypeMix({ ...archetypeMix, [archetype]: Number(event.target.value) })} /></label>)}</div>
          <div className="noise-control"><label><span><strong>Adapt to deck</strong><small>Blend the detected cards and tags into the plan. Lower it when you want a fully hand-authored identity.</small></span><output>{Math.round(deckAdaptation * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={deckAdaptation} onChange={(event) => setDeckAdaptation(Number(event.target.value))} /></label><label><span><strong>Decision variety</strong><small>0 always chooses the top score. Higher values explore only choices close enough to remain plausible.</small></span><output>{Math.round(temperature * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={temperature} onChange={(event) => setTemperature(Number(event.target.value))} /></label><label className="seed-field">Reproducible seed<input type="number" min="0" max="2147483647" value={decisionSeed} onChange={(event) => setDecisionSeed(Number(event.target.value))} /></label></div>
          <div className="heuristic-grid">{catalog?.items.map((heuristic) => <label key={heuristic.id}><span><strong>{heuristic.name}</strong><small>{heuristic.description}</small></span><output>{(weights[heuristic.id] ?? 1).toFixed(1)}</output><input type="range" min="0" max="2" step="0.1" value={weights[heuristic.id] ?? 1} onChange={(event) => setWeights({ ...weights, [heuristic.id]: Number(event.target.value) })} /></label>)}</div>
          <div className="pattern-summary"><div><span className="eyebrow">Deck-specific patterns</span><h3>{selected.strategyGraph?.patterns.length ?? 0} custom rules</h3><p>Ask the chat to recognize a combo piece, protect a named engine, or increase a known strategic concern. The LLM can only edit bounded tags and weights.</p></div>{selected.strategyGraph?.patterns.length > 0 && <ul>{selected.strategyGraph.patterns.map((pattern) => <li key={pattern.id}><strong>{pattern.id}</strong><small>{[...pattern.cardNames, ...pattern.addTags.map((tag) => `#${tag}`)].join(" · ") || "Text/tag matcher"}</small></li>)}</ul>}</div>
          <label>Strategy notes<textarea rows={4} value={strategyNotes} onChange={(event) => setStrategyNotes(event.target.value)} placeholder="What this deck is trying to accomplish…" /></label>
          <div className="field-grid"><label>Prefer cards<input value={preferCards} onChange={(event) => setPreferCards(event.target.value)} placeholder="Card A, Card B" /></label><label>Avoid cards<input value={avoidCards} onChange={(event) => setAvoidCards(event.target.value)} /></label><label>Hold for interaction<input value={holdCards} onChange={(event) => setHoldCards(event.target.value)} /></label></div>
          <div className="form-actions"><AsyncActionButton className="primary" type="button" loading={savingAgent} loadingLabel="Saving policy…" onClick={() => void saveAgent()}>Save policy</AsyncActionButton><button type="button" onClick={() => openPlaytest(selected)}>Playtest this agent →</button><small>{selected.decks.map((deck) => deck.name).join(" · ")}</small></div>
        </>}
      </>}
    </section>

    <section className="panel studio-llm">
      <div className="section-heading"><div><span className="eyebrow">Optional LLM</span><h2>Connect any OpenAI-compatible endpoint</h2><p className="section-lead">Ollama, LiteLLM, OpenRouter, or another compatible gateway. DeepDeckLearner has no provider SDK dependency.</p></div><span className="step">03</span></div>
      <form onSubmit={saveProvider}>
        <div className="field-grid"><label>Provider label<input value={providerName} onChange={(event) => setProviderName(event.target.value)} placeholder="Local Ollama" /></label><label>Base URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://localhost:11434/v1" /></label><label>Model<input value={llmModel} onChange={(event) => setLlmModel(event.target.value)} placeholder="qwen3:8b" /></label><label>API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={llm?.hasApiKey ? "Saved — leave blank to keep" : "Optional for local endpoints"} /></label></div>
        <AsyncActionButton type="submit" loading={savingLlm} loadingLabel="Saving connection…" disabled={!baseUrl || !llmModel}>Save LLM connection</AsyncActionButton>
      </form>
      <div className="studio-chat" aria-live="polite">
        {messages.length === 0 && <div className="empty"><span>✦</span><p>Try: “Make my tempo agent more interactive and hold Force of Will for the opponent’s turn.”</p></div>}
        {messages.map((message, index) => <article className={message.role} key={`${message.role}-${index}`}><strong>{message.role === "user" ? "You" : providerName || "Agent Studio"}</strong><p>{message.content}</p></article>)}
      </div>
      <form className="studio-prompt" onSubmit={sendMessage}><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => submitChatOnEnter(event, !chatting && Boolean(llm?.configured) && Boolean(prompt.trim()))} rows={3} title="Enter to send · Shift+Enter for a new line" placeholder="Describe the agent or change you want…" /><AsyncActionButton className="primary" loading={chatting} loadingLabel="Working with agent tools…" disabled={!llm?.configured || !prompt.trim()}>Send</AsyncActionButton></form>
      {!llm?.configured && <small>Chat stays disabled until an endpoint and model are configured. Manual creation and editing remain fully available.</small>}
    </section>
    {error && <p className="form-error studio-error" role="alert">{error}</p>}
  </div>;
}
