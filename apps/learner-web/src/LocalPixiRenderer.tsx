/* eslint-disable @typescript-eslint/no-explicit-any -- @deepdeck/pixi exposes versioned Engine JSON. */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { createApp, h, shallowRef, type App as VueApp, type ShallowRef } from "vue";
import * as DeepDeckPixi from "@deepdeck/pixi";

import type { EngineView } from "./LocalPixiTable";
import type { DeckPresentation } from "./api";
import {
  actionTargetKeys,
  cardOrderLegend,
  candidateTargetIds,
  castingPaymentPresentations,
  selectPlanTarget,
  tableChoiceCardIds,
  targetPlanFromAction,
  type TargetPlan,
} from "./pixiInteraction";

type Projection = Record<string, any>;
type PixiRuntime = {
  PixiGame: any;
  gameSessionActionPresentations: (actions: any[]) => any[];
  gameSessionCardCatalogFromDeckSelections: (
    selections: DeckPresentation[],
  ) => Record<string, unknown>;
  projectGameSessionView: (view: EngineView, options: Record<string, unknown>) => Projection;
};

type Interaction = {
  orderedIds?: string[];
  selectedIds?: Set<string>;
  targetableIds?: Set<string>;
  targeting?: boolean;
  prompt?: string;
};

const pixi = DeepDeckPixi as unknown as PixiRuntime;

function sceneCard(card: any, kind: string, zone: string, interaction: Interaction) {
  const id = String(card?.id ?? "");
  const selected = Boolean(interaction.selectedIds?.has(id));
  const targetable = !selected && Boolean(interaction.targetableIds?.has(id));
  const selectedIndex = interaction.orderedIds?.indexOf(id) ?? -1;
  return {
    actionable: selected || (!interaction.targeting && Boolean(card?.actionState?.actionable)),
    attachedTo: card?.state?.attachedTo ?? null,
    commander: Boolean(card?.isCommander || zone === "commandZone"),
    counters: Object.values(card?.state?.counters ?? {}).reduce<number>(
      (sum, value) => sum + Number(value ?? 0),
      0,
    ),
    faceDown: Boolean(card?.faceDown),
    id: card?.id,
    imageUrl: card?.imageUrl ?? "",
    kind,
    linkedTo: card?.state?.linkedExileSourceId ?? null,
    name: card?.name ?? "Unknown card",
    raw: card,
    selectionOrder: selectedIndex >= 0 ? selectedIndex + 1 : null,
    sourceZone: card?.sourceZone ?? zone,
    tapped: Boolean(card?.state?.tapped),
    targetable,
    typeLine: card?.typeLine ?? "",
  };
}

// eslint-disable-next-line react-refresh/only-export-components -- exported for interaction regression coverage.
export function pixiScene(projection: Projection, interaction: Interaction = {}) {
  const players = projection.players ?? [];
  const local = players.find((player: any) => player.role === "human") ?? players[0];
  const scenePlayers = players.map((player: any) => {
    const isLocal = player.key === local?.key;
    const battlefield = player.zones?.battlefield ?? {};
    const zone = (id: string) => ({
      cards: (player.zones?.[id]?.cards ?? []).map(
        (card: any) => sceneCard(card, "zone", id, interaction),
      ),
      count: Number(player.zones?.[id]?.count ?? 0),
      id,
    });
    return {
      active: player.key === projection.step?.playerKey,
      battlefield: [
        ...(battlefield.lands ?? []).map(
          (card: any) => sceneCard(card, "land", "battlefield", interaction),
        ),
        ...(battlefield.creatures ?? []).map(
          (card: any) => sceneCard(card, "creature", "battlefield", interaction),
        ),
        ...(battlefield.nonCreaturePermanents ?? []).map(
          (card: any) => sceneCard(card, "other", "battlefield", interaction),
        ),
      ],
      commanderDamage: player.commanderDamage ?? [],
      hand: (player.zones?.hand ?? []).map((card: any) => {
        const presented = sceneCard(card, "hand", "hand", interaction);
        return isLocal || card?.knownToViewer
          ? presented
          : {
              ...presented,
              actionable: false,
              faceDown: true,
              imageUrl: "",
              name: "Hidden card",
              raw: { id: presented.id, typeLine: "Card" },
              targetable: false,
              typeLine: "Card",
            };
      }),
      handCount: Number(player.zones?.handCount ?? 0),
      id: player.key,
      life: Number(player.life ?? 0),
      local: isLocal,
      mana: Object.entries(player.zones?.manaPool ?? {}).map(([symbol, amount]) => ({ symbol, amount })),
      name: player.name,
      priority: player.key === projection.priorityPlayer?.key,
      raw: player,
      targetable: Boolean(interaction.targetableIds?.has(String(player.key))),
      zones: [zone("commandZone"), zone("graveyard"), {
        cards: Number(player.zones?.libraryCount ?? 0)
          ? [{ faceDown: true, id: `${player.key}:library`, kind: "zone", name: "Library" }]
          : [],
        count: Number(player.zones?.libraryCount ?? 0),
        id: "library",
      }, zone("exile")],
    };
  });
  const outcome = projection.state?.outcome;
  const winner = outcome?.winner
    ? players.find((player: any) => player.key === outcome.winner)
    : null;
  return {
    combat: projection.combat ?? [],
    controls: {
      advanceLabel: "Choose a highlighted action",
      autoPassActive: false,
      canAdvance: false,
      canPassPriority: !interaction.targeting && Boolean(projection.passAction),
      canPassStack: false,
      passStackActive: false,
      showPassPriority: !interaction.targeting && Boolean(projection.passAction),
      showPassStack: false,
    },
    outcome: outcome ? {
      draw: !outcome.winner,
      reason: outcome.reason,
      winnerId: outcome.winner ?? null,
      winnerName: winner?.name ?? outcome.winner ?? null,
    } : null,
    phase: projection.step?.phase ?? "",
    players: scenePlayers,
    replay: false,
    stack: (projection.stack ?? []).map((item: any) => ({
      card: item.card,
      detail: item.type === "spell" ? "Spell" : "Ability on the stack",
      id: item.id,
      imageUrl: item.card?.imageUrl ?? "",
      name: item.label,
      raw: item,
      targetable: Boolean(
        interaction.targetableIds?.has(String(item.id)) ||
        interaction.targetableIds?.has(String(item.card?.id)),
      ),
      type: item.type,
    })),
    status: interaction.prompt || (outcome
      ? winner ? `GG, ${winner.name}!` : "Game over: draw"
      : `Turn ${projection.step?.turn ?? 1} · ${projection.step?.playerName ?? "Waiting"}`),
  };
}

type PixiEventRefs = {
  card: RefObject<(payload: any) => void>;
  exit: RefObject<() => void>;
  hover: RefObject<(payload: any) => void>;
  leave: RefObject<() => void>;
  pass: RefObject<() => void>;
  player: RefObject<(player: any) => void>;
  stack: RefObject<(item: any) => void>;
};

function mountPixi(
  host: HTMLDivElement,
  initialScene: Record<string, unknown>,
  events: PixiEventRefs,
) {
  const currentScene = shallowRef(initialScene);
  const vue = createApp({
    render: () => h(pixi.PixiGame, {
      brandName: "Deep Deck Learner",
      cardBackUrl: "/api/scryfall-images/back.png",
      mode: "play",
      scene: currentScene.value,
      onCardClick: (payload: any) => events.card.current(payload),
      onCardHover: (payload: any) => events.hover.current(payload),
      onCardLeave: () => events.leave.current(),
      onExit: () => events.exit.current(),
      onPassPriority: () => events.pass.current(),
      onPlayerClick: (player: any) => events.player.current(player),
      onStackClick: (item: any) => events.stack.current(item),
    }),
  });
  vue.config.errorHandler = (error) => {
    console.warn("Pixi table runtime error", error);
  };
  vue.mount(host);
  return { currentScene, vue };
}

function actionLabel(action: any) {
  return action?.label ?? action?.kind ?? action?.id ?? "Choose action";
}

export default function LocalPixiRenderer({ deckSelections, matchup, view, onAction, onExit }: {
  deckSelections: DeckPresentation[];
  matchup: string;
  view: EngineView;
  onAction: (actionId: string, extra?: Record<string, unknown>) => void;
  onExit: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const vue = useRef<VueApp<Element> | null>(null);
  const sceneRef = useRef<ShallowRef<Record<string, unknown>> | null>(null);
  const [targetPlan, setTargetPlan] = useState<TargetPlan | null>(null);
  const [sourceChoices, setSourceChoices] = useState<any[]>([]);
  const [selectedCardIds, setSelectedCardIds] = useState<string[]>([]);
  const [numberValue, setNumberValue] = useState(0);
  const [hoveredCard, setHoveredCard] = useState<any | null>(null);
  const [visualError, setVisualError] = useState("");

  const cardCatalog = useMemo(
    () => pixi.gameSessionCardCatalogFromDeckSelections(deckSelections),
    [deckSelections],
  );
  const projection = useMemo(
    () => pixi.projectGameSessionView(view, {
      cardCatalog,
      playerRoleById: { "local-human": "human", "local-agent": "ai" },
    }),
    [cardCatalog, view],
  );
  const directPresentations = useMemo(
    () => pixi.gameSessionActionPresentations(projection.directActions ?? []),
    [projection.directActions],
  );
  const automaticTargetPlan = useMemo(() => {
    const resolution = projection.resolutionTargetChoice;
    if (resolution) {
      return targetPlanFromAction({
        engineTargetActions: resolution.actions,
        engineTargetKeys: [resolution.targetKey],
        label: resolution.prompt,
      }, resolution.prompt);
    }
    const visualDirect = directPresentations.filter(
      (action: any) => actionTargetKeys(action).length > 0,
    );
    return visualDirect.length === 1 ? targetPlanFromAction(visualDirect[0]) : null;
  }, [directPresentations, projection.resolutionTargetChoice]);
  const activeTargetPlan = targetPlan ?? automaticTargetPlan;
  const cardChoice = projection.resolutionCardChoice;
  const tableCardIds = useMemo(() => tableChoiceCardIds(projection), [projection]);
  const tableChoiceCards = useMemo(
    () => (cardChoice?.cards ?? []).filter((card: any) => tableCardIds.has(String(card.id))),
    [cardChoice, tableCardIds],
  );
  const zoneChoiceCards = useMemo(
    () => (cardChoice?.cards ?? []).filter((card: any) => !tableCardIds.has(String(card.id))),
    [cardChoice, tableCardIds],
  );
  const tableChoiceIds = useMemo(
    () => new Set<string>(tableChoiceCards.map((card: any) => String(card.id))),
    [tableChoiceCards],
  );
  const selectedIds = useMemo(() => new Set(selectedCardIds), [selectedCardIds]);
  const targetableIds = useMemo(() => {
    if (activeTargetPlan) return candidateTargetIds(activeTargetPlan);
    const maximumReached = selectedCardIds.length >= Number(cardChoice?.maximum ?? 0);
    return new Set<string>([...tableChoiceIds].filter(
      (id) => !maximumReached || selectedIds.has(id),
    ));
  }, [activeTargetPlan, cardChoice?.maximum, selectedCardIds.length, selectedIds, tableChoiceIds]);
  const interaction = useMemo<Interaction>(() => ({
    orderedIds: selectedCardIds,
    prompt: activeTargetPlan
      ? `${activeTargetPlan.prompt} (${activeTargetPlan.index + 1}/${activeTargetPlan.keys.length})`
      : cardChoice && tableChoiceCards.length
        ? cardChoice.prompt
      : undefined,
    selectedIds,
    targetableIds,
    targeting: Boolean(activeTargetPlan || cardChoice),
  }), [activeTargetPlan, cardChoice, selectedCardIds, selectedIds, tableChoiceCards.length, targetableIds]);
  const scene = useMemo(() => pixiScene(projection, interaction), [interaction, projection]);
  const initialScene = useRef(scene);
  const initialSelectionKey = JSON.stringify(
    projection.resolutionCardChoice?.initialSelectedCardIds ?? [],
  );
  const minimumNumber = Number(projection.numberChoice?.minimum ?? 0);

  const submit = useCallback((actionId: string, extra?: Record<string, unknown>) => {
    setTargetPlan(null);
    setSourceChoices([]);
    onAction(actionId, extra);
  }, [onAction]);

  const chooseCard = useCallback((cardId: string) => {
    if (!cardChoice || !cardChoice.cards.some((card: any) => String(card.id) === cardId)) return;
    const minimum = Number(cardChoice.minimum ?? 0);
    const maximum = Number(cardChoice.maximum ?? 0);
    if (
      cardChoice.kind !== "cardOrder" &&
      minimum === 1 &&
      maximum === 1 &&
      cardChoice.options?.[0]?.actionId
    ) {
      submit(cardChoice.options[0].actionId, { cardInstanceIds: [cardId] });
      return;
    }
    setSelectedCardIds((current) => {
      if (current.includes(cardId)) return current.filter((id) => id !== cardId);
      if (current.length >= maximum) return current;
      return [...current, cardId];
    });
  }, [cardChoice, submit]);

  const beginAction = useCallback((action: any) => {
    const plan = targetPlanFromAction(action);
    setSourceChoices([]);
    if (plan) setTargetPlan(plan);
    else if (action?.id) submit(action.id);
  }, [submit]);

  const chooseEntity = useCallback((ids: string[]) => {
    if (!activeTargetPlan) return;
    const result = selectPlanTarget(activeTargetPlan, ids);
    if (!result) return;
    if (result.actionId) submit(result.actionId);
    else if (result.plan) setTargetPlan(result.plan);
  }, [activeTargetPlan, submit]);

  const handleCard = useCallback((payload: any) => {
    const card = payload?.card ?? payload;
    if (activeTargetPlan) {
      chooseEntity([String(card?.id ?? card?.instanceId ?? "")].filter(Boolean));
      return;
    }
    const cardId = String(card?.id ?? card?.instanceId ?? "");
    if (cardChoice && tableChoiceIds.has(cardId)) {
      chooseCard(cardId);
      return;
    }
    const choices = castingPaymentPresentations(card?.actionState?.options ?? []);
    if (choices.length === 1) beginAction(choices[0]);
    else if (choices.length > 1) setSourceChoices(choices);
  }, [activeTargetPlan, beginAction, cardChoice, chooseCard, chooseEntity, tableChoiceIds]);

  const cardRef = useRef(handleCard);
  const hoverRef = useRef<(payload: any) => void>(() => undefined);
  const leaveRef = useRef<() => void>(() => undefined);
  const playerRef = useRef<(player: any) => void>(() => undefined);
  const stackRef = useRef<(item: any) => void>(() => undefined);
  const passRef = useRef<() => void>(() => undefined);
  const exitRef = useRef(onExit);
  useEffect(() => { cardRef.current = handleCard; }, [handleCard]);
  useEffect(() => {
    hoverRef.current = (payload: any) => setHoveredCard(payload?.card ?? payload ?? null);
    leaveRef.current = () => setHoveredCard(null);
  }, []);
  useEffect(() => {
    playerRef.current = (player: any) => {
      const id = player?.key ?? player?.id;
      if (id) chooseEntity([String(id)]);
    };
    stackRef.current = (item: any) => {
      const cardId = [item?.card?.id, item?.card?.instanceId]
        .filter(Boolean)
        .map(String)
        .find((id) => tableChoiceIds.has(id));
      if (cardChoice && cardId) {
        chooseCard(cardId);
        return;
      }
      chooseEntity([
        item?.id,
        item?.card?.id,
        item?.card?.instanceId,
      ].filter(Boolean).map(String));
    };
    passRef.current = () => {
      if (projection.passAction?.id) submit(projection.passAction.id);
    };
  }, [cardChoice, chooseCard, chooseEntity, projection.passAction, submit, tableChoiceIds]);
  useEffect(() => { exitRef.current = onExit; }, [onExit]);
  useEffect(() => {
    setTargetPlan(null);
    setSourceChoices([]);
    setHoveredCard(null);
    setSelectedCardIds(JSON.parse(initialSelectionKey) as string[]);
    setNumberValue(minimumNumber);
  }, [initialSelectionKey, minimumNumber, view.decision?.id]);
  useEffect(() => {
    if (!host.current) return;
    try {
      const mounted = mountPixi(host.current, initialScene.current, {
        card: cardRef,
        exit: exitRef,
        hover: hoverRef,
        leave: leaveRef,
        pass: passRef,
        player: playerRef,
        stack: stackRef,
      });
      vue.current = mounted.vue;
      sceneRef.current = mounted.currentScene;
    } catch (reason) {
      setVisualError(reason instanceof Error ? reason.message : "Pixi could not start.");
    }
    return () => {
      vue.current?.unmount();
      vue.current = null;
      sceneRef.current = null;
    };
  }, []);
  useEffect(() => {
    if (sceneRef.current) sceneRef.current.value = scene;
  }, [scene]);

  const visualDirectChoices = directPresentations.filter(
    (action: any) => actionTargetKeys(action).length > 0,
  );
  const abstractActions = directPresentations.filter((action: any) =>
    actionTargetKeys(action).length === 0 &&
    action.kind !== "passPriority" &&
    !projection.resolutionCardChoice &&
    !projection.numberChoice,
  );
  const cardSelectionValid = Boolean(
    cardChoice &&
    selectedCardIds.length >= Number(cardChoice.minimum) &&
    selectedCardIds.length <= Number(cardChoice.maximum),
  );
  const orderLegend = cardOrderLegend(cardChoice);
  const zoneChoicePanelWidth = Math.min(
    54,
    Math.max(16, Math.min(zoneChoiceCards.length, 6) * 8.25 + 1.6),
  );

  return <>
    <div className="local-pixi-vue-host" ref={host} />
    <div className="pixi-matchup-badge">{matchup}</div>
    {hoveredCard?.imageUrl && <div className="pixi-hover-preview" aria-hidden="true">
      <img src={hoveredCard.imageUrl} alt="" />
    </div>}
    {visualError && <div className="pixi-visual-error" role="alert">Pixi: {visualError}</div>}
    {activeTargetPlan && <div className="pixi-target-prompt" role="status">
      <strong>{activeTargetPlan.prompt}</strong>
      {activeTargetPlan.keys.length > 1 && <span>Step {activeTargetPlan.index + 1}/{activeTargetPlan.keys.length}</span>}
      {targetPlan && <button type="button" onClick={() => setTargetPlan(null)}>Cancel</button>}
    </div>}
    {!activeTargetPlan && cardChoice && tableChoiceCards.length > 0 && <div className="pixi-target-prompt" role="status">
      <strong>{cardChoice.prompt}</strong>
      <span>Choose the highlighted cards directly on the table · selected {selectedCardIds.length}/{cardChoice.minimum}–{cardChoice.maximum}</span>
      {orderLegend && <span className="pixi-order-legend">{orderLegend}</span>}
      <button
        type="button"
        disabled={!cardSelectionValid}
        onClick={() => submit(cardChoice.options[0].actionId, { cardInstanceIds: selectedCardIds })}
      >Confirm</button>
    </div>}
    {!activeTargetPlan && sourceChoices.length > 0 && <div className="pixi-action-palette">
      <strong>Choose how to use this card</strong>
      <div>{sourceChoices.map((action) => <button key={action.id} type="button" onClick={() => beginAction(action)}>{actionLabel(action)}</button>)}</div>
      <button type="button" className="subtle" onClick={() => setSourceChoices([])}>Cancel</button>
    </div>}
    {!activeTargetPlan && sourceChoices.length === 0 && visualDirectChoices.length > 1 && <div className="pixi-action-palette">
      <strong>Choose an action, then its highlighted targets</strong>
      <div>{visualDirectChoices.map((action: any) => <button key={action.id} type="button" onClick={() => beginAction(action)}>{actionLabel(action)}</button>)}</div>
    </div>}
    {!activeTargetPlan && sourceChoices.length === 0 && abstractActions.length > 0 && <div className="pixi-action-palette">
      <strong>{projection.decision?.kind === "mulligan" ? "Choose your opening hand" : projection.decision?.choice?.prompt ?? "Choose an action"}</strong>
      <div>{abstractActions.map((action: any) => <button key={action.id} type="button" onClick={() => submit(action.id)}>{actionLabel(action)}</button>)}</div>
    </div>}
    {cardChoice && zoneChoiceCards.length > 0 && <div
      className={`pixi-card-choice${tableChoiceCards.length ? " mixed-zone-choice" : ""}`}
      role="dialog"
      aria-label={cardChoice.prompt}
      style={tableChoiceCards.length ? undefined : {
        width: `min(calc(100vw - 2rem), ${zoneChoicePanelWidth}rem)`,
      }}
    >
      <header><div>
        <strong>{tableChoiceCards.length ? "Cards outside the table" : cardChoice.prompt}</strong>
        <span>Select {cardChoice.minimum}–{cardChoice.maximum}</span>
        {orderLegend && <small className="pixi-order-legend">{orderLegend}</small>}
      </div></header>
      <div className="pixi-card-choice-grid">{zoneChoiceCards.map((card: any) => {
        const order = selectedCardIds.indexOf(card.id);
        return <button key={card.id} type="button" className={order >= 0 ? "selected" : ""} onClick={() => chooseCard(String(card.id))}>
          <img src={card.imageUrl} alt="" />
          <span>{card.name}</span>
          {order >= 0 && <b>{order + 1}</b>}
        </button>;
      })}</div>
      {tableChoiceCards.length === 0 && <button type="button" disabled={!cardSelectionValid} onClick={() => submit(cardChoice.options[0].actionId, { cardInstanceIds: selectedCardIds })}>Confirm selection</button>}
    </div>}
    {projection.numberChoice && <div className="pixi-action-palette">
      <strong>{projection.numberChoice.prompt}</strong>
      <input type="number" min={projection.numberChoice.minimum} max={projection.numberChoice.maximum} value={numberValue} onChange={(event) => setNumberValue(Number(event.target.value))} />
      <button type="button" onClick={() => submit(projection.actions[0].id, { numberValue })}>Confirm</button>
    </div>}
  </>;
}
