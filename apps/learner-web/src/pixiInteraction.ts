/* eslint-disable @typescript-eslint/no-explicit-any -- Engine actions are versioned JSON protocol values. */

export type TargetPlan = {
  actions: any[];
  index: number;
  keys: string[];
  prompt: string;
};

export function cardOrderLegend(choice: any): string {
  if (choice?.kind !== "cardOrder") return "";
  const last = Math.max(1, Number(choice.maximum ?? choice.cards?.length ?? 1));
  const prompt = String(choice.prompt ?? "").toLowerCase();
  if (prompt.includes("bottommost first")) {
    return `1 = bottommost card · ${last} = topmost of these cards`;
  }
  if (prompt.includes("topmost first")) {
    return `1 = topmost card · ${last} = deepest of these cards`;
  }
  return `1 = first card in the order · ${last} = last card`;
}

function exactEngineActions(actions: any[]): any[] {
  return actions.flatMap((action) => action?.engineTargetActions?.length
    ? action.engineTargetActions
    : [action]);
}

function targetKeysForActions(actions: any[]): string[] {
  return [...new Set(actions.flatMap((action) => [
    ...(action?.targetOrder ?? []),
    ...Object.keys(action?.targets ?? {}),
  ]).filter(Boolean))];
}

function paymentLabel(action: any): string {
  const detail = String(action?.label ?? "")
    .split(/\s+—\s+/u)
    .at(-1)
    ?.trim();
  if (!detail || detail === action?.label) return "Pay the mana cost";
  return detail.charAt(0).toUpperCase() + detail.slice(1);
}

/**
 * Turns the Engine's exact alternative-cost actions into a two-stage visual
 * choice. The submitted action ID remains one of the untouched Engine actions.
 */
export function castingPaymentPresentations(actions: any[]): any[] {
  const exactActions = exactEngineActions(actions);
  const manaActions = exactActions.filter(
    (action) => action?.decisions?.useAlternativeCost === false,
  );
  const exileActions = exactActions.filter(
    (action) => action?.decisions?.useAlternativeCost === true &&
      action?.decisions?.alternativeExileCard,
  );
  if (
    !manaActions.length ||
    !exileActions.length ||
    manaActions.length + exileActions.length !== exactActions.length
  ) {
    return actions;
  }

  const manaTargetKeys = targetKeysForActions(manaActions);
  const manaPresentation = manaTargetKeys.length
    ? {
        ...manaActions[0],
        id: `payment:mana:${manaActions[0].id}`,
        label: paymentLabel(manaActions[0]),
        engineTargetActions: manaActions,
        engineTargetKeys: manaTargetKeys,
        targets: {},
      }
    : {
        ...manaActions[0],
        label: paymentLabel(manaActions[0]),
      };
  const visualExileActions = exileActions.map((action) => {
    const alternativeExileCard = String(action.decisions.alternativeExileCard);
    return {
      ...action,
      targetOrder: [
        "alternativeExileCard",
        ...(action.targetOrder ?? []).filter(
          (key: string) => key !== "alternativeExileCard",
        ),
      ],
      targets: {
        alternativeExileCard: { card: { instanceId: alternativeExileCard } },
        ...(action.targets ?? {}),
      },
    };
  });

  return [
    manaPresentation,
    {
      ...visualExileActions[0],
      id: `payment:exile:${visualExileActions[0].id}`,
      label: "Exile a card from hand",
      engineTargetActions: visualExileActions,
      engineTargetKeys: targetKeysForActions(visualExileActions),
      targets: {},
    },
  ];
}

export function tableChoiceCardIds(projection: any): Set<string> {
  const ids = new Set<string>();
  const addCard = (card: any) => {
    const id = card?.id ?? card?.instanceId;
    if (id) ids.add(String(id));
  };
  for (const player of projection?.players ?? []) {
    (player.zones?.hand ?? []).forEach(addCard);
    const battlefield = player.zones?.battlefield ?? {};
    (battlefield.lands ?? []).forEach(addCard);
    (battlefield.creatures ?? []).forEach(addCard);
    (battlefield.nonCreaturePermanents ?? []).forEach(addCard);
    (player.zones?.commandZone?.cards ?? []).forEach(addCard);
  }
  for (const item of projection?.stack ?? []) addCard(item?.card);
  return ids;
}

function collectProtocolIds(value: unknown, ids: Set<string>, parentKey = "") {
  if (typeof value === "string") {
    const key = parentKey.toLowerCase();
    if (key === "id" || key.endsWith("id")) ids.add(value);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectProtocolIds(item, ids, parentKey));
    return;
  }
  if (!value || typeof value !== "object") return;
  Object.entries(value).forEach(([key, item]) => collectProtocolIds(item, ids, key));
}

export function actionTargetKeys(action: any): string[] {
  return [...new Set([
    ...(action?.engineTargetKeys ?? []),
    ...(action?.targetOrder ?? []),
    ...Object.keys(action?.targets ?? {}),
  ].filter(Boolean))];
}

function targetPrompt(key: string, fallback: string): string {
  if (key === "alternativeExileCard") return "Choose a highlighted card to exile from your hand.";
  if (key.toLowerCase().includes("player")) return "Choose a highlighted player.";
  if (key.toLowerCase().includes("stack") || key.toLowerCase().includes("spell")) {
    return "Choose a highlighted spell on the stack.";
  }
  if (key.toLowerCase().includes("permanent")) return "Choose a highlighted permanent.";
  if (key.toLowerCase().includes("card")) return "Choose a highlighted card.";
  return fallback;
}

export function targetPlanFromAction(action: any, prompt?: string): TargetPlan | null {
  const actions = action?.engineTargetActions?.length
    ? action.engineTargetActions
    : [action];
  const keys = actionTargetKeys(action);
  if (!keys.length) return null;
  return {
    actions,
    index: 0,
    keys,
    prompt: prompt || targetPrompt(keys[0], action?.label || "Choose a highlighted target."),
  };
}

export function candidateTargetIds(plan: TargetPlan | null): Set<string> {
  const ids = new Set<string>();
  if (!plan) return ids;
  const key = plan.keys[plan.index];
  for (const action of plan.actions) {
    collectProtocolIds(action?.targets?.[key], ids);
  }
  return ids;
}

export function selectPlanTarget(
  plan: TargetPlan,
  entityIds: Iterable<string>,
): { actionId?: string; plan?: TargetPlan } | null {
  const selected = new Set(entityIds);
  const key = plan.keys[plan.index];
  const matching = plan.actions.filter((action) => {
    const candidateIds = new Set<string>();
    collectProtocolIds(action?.targets?.[key], candidateIds);
    return [...candidateIds].some((id) => selected.has(id));
  });
  if (!matching.length) return null;
  if (plan.index === plan.keys.length - 1) {
    return { actionId: matching[0].id };
  }
  return {
    plan: {
      ...plan,
      actions: matching,
      index: plan.index + 1,
      prompt: targetPrompt(plan.keys[plan.index + 1], plan.prompt),
    },
  };
}

export function castingXNumberChoice(actions: any[]): {
  minimum: number;
  maximum: number;
  options: Map<number, any>;
} | null {
  if (actions.length < 2) return null;
  const options = new Map<number, any>();
  for (const action of actions) {
    const value = action?.decisions?.xValue;
    if (!Number.isInteger(value) || options.has(value)) return null;
    options.set(value, action);
  }
  const values = [...options.keys()].sort((left, right) => left - right);
  const minimum = values[0];
  const maximum = values.at(-1)!;
  if (values.length !== maximum - minimum + 1) return null;
  return { minimum, maximum, options };
}

export function selectOnlyAttackDefender(
  action: any,
  plan: TargetPlan | null,
): { actionId?: string; plan?: TargetPlan } | null {
  if (
    action?.kind !== "declareAttacker" ||
    !plan ||
    plan.keys[plan.index] !== "defender"
  ) {
    return null;
  }
  const targets = candidateTargetIds(plan);
  if (targets.size !== 1) return null;
  return selectPlanTarget(plan, targets);
}

export function clickedEntityIds(value: any): string[] {
  const ids = new Set<string>();
  collectProtocolIds(value, ids);
  for (const candidate of [
    value?.id,
    value?.key,
    value?.instanceId,
    value?.card?.id,
    value?.card?.instanceId,
  ]) {
    if (candidate) ids.add(String(candidate));
  }
  return [...ids];
}

// Pixi owns a pending Engine decision only on a live table, for a seat this
// host controls, and when the decision maps to a dedicated Pixi primitive.
// Everything else keeps the learner's existing decision panels.
export function pixiOwnedDecision(
  projection: any,
  options: { replay?: boolean; unsupportedDecisionId?: string } = {},
): any | null {
  const model = projection?.decisionModel;
  if (options.replay || !model?.decisionId || model.unsupported) return null;
  if (model.decisionId === options.unsupportedDecisionId) return null;
  const owner = (projection.players ?? []).find(
    (player: any) => String(player?.key ?? "") === String(model.playerId ?? ""),
  );
  return owner?.role === "human" ? model : null;
}

// Converts a Pixi `decision-submit` payload into the learner's existing
// action submission. The action must be one of the pending Engine options and
// typed extras are forwarded only for the choice kind that accepts them.
export function pixiDecisionSubmission(
  decision: any,
  submission: any,
): { actionId: string; extra: Record<string, unknown> } | null {
  const actionId = String(submission?.actionId ?? "");
  if (!decision?.id || !actionId) return null;
  if (submission.decisionId && submission.decisionId !== decision.id) return null;
  if (!(decision.options ?? []).some((action: any) => action?.id === actionId)) return null;
  const choiceKind = decision.choice?.kind;
  const extra: Record<string, unknown> = {};
  if (choiceKind === "numberSelection") {
    const value = Number(submission.numberValue);
    const minimum = Number(decision.choice.minimum);
    const maximum = Number(decision.choice.maximum);
    if (!Number.isInteger(value) || value < minimum || value > maximum) return null;
    extra.numberValue = value;
  }
  if (Array.isArray(submission.cardInstanceIds)) {
    if (!["cardSelection", "cardOrder"].includes(choiceKind)) return null;
    extra.cardInstanceIds = submission.cardInstanceIds.map(String);
  }
  if (choiceKind === "cardNameSelection") {
    const cardName = String(submission.cardName ?? "").trim();
    if (!cardName || cardName.length > 256) return null;
    extra.cardName = cardName;
  }
  return { actionId, extra };
}
