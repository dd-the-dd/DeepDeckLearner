import { describe, expect, test } from "vitest";

import {
  cardOrderLegend,
  candidateTargetIds,
  castingPaymentPresentations,
  selectPlanTarget,
  tableChoiceCardIds,
  targetPlanFromAction,
} from "./pixiInteraction";

describe("visual Pixi targeting", () => {
  test("explains both ends of a library ordering decision", () => {
    expect(cardOrderLegend({
      kind: "cardOrder",
      maximum: 4,
      prompt: "Order the remaining cards for the bottom of the library, bottommost first.",
    })).toBe("1 = bottommost card · 4 = topmost of these cards");
    expect(cardOrderLegend({
      kind: "cardOrder",
      maximum: 3,
      prompt: "Order the cards for the top of the library, topmost first.",
    })).toBe("1 = topmost card · 3 = deepest of these cards");
  });

  test("chooses mana or hand exile before highlighting the exact alternative card", () => {
    const target = (playerId: string) => ({
      targetPlayer: { player: { playerId } },
    });
    const exact = (id: string, playerId: string, decisions: Record<string, unknown>) => ({
      id,
      kind: "castSpell",
      label: decisions.useAlternativeCost
        ? `Cast Unmask from hand — exile ${decisions.alternativeExileCard} from hand`
        : "Cast Unmask from hand — pay {3}{B}",
      decisions,
      targetOrder: ["targetPlayer"],
      targets: target(playerId),
    });
    const manaActions = [
      exact("mana:p1", "player-1", { useAlternativeCost: false }),
      exact("mana:p2", "player-2", { useAlternativeCost: false }),
    ];
    const griefActions = [
      exact("grief:p1", "player-1", {
        useAlternativeCost: true,
        alternativeExileCard: "hand:grief",
      }),
      exact("grief:p2", "player-2", {
        useAlternativeCost: true,
        alternativeExileCard: "hand:grief",
      }),
    ];
    const thoughtseizeActions = [
      exact("thoughtseize:p1", "player-1", {
        useAlternativeCost: true,
        alternativeExileCard: "hand:thoughtseize",
      }),
      exact("thoughtseize:p2", "player-2", {
        useAlternativeCost: true,
        alternativeExileCard: "hand:thoughtseize",
      }),
    ];
    const presentations = castingPaymentPresentations([
      {
        ...manaActions[0],
        engineTargetActions: manaActions,
        engineTargetKeys: ["targetPlayer"],
        targets: {},
      },
      {
        ...griefActions[0],
        engineTargetActions: griefActions,
        engineTargetKeys: ["targetPlayer"],
        targets: {},
      },
      {
        ...thoughtseizeActions[0],
        engineTargetActions: thoughtseizeActions,
        engineTargetKeys: ["targetPlayer"],
        targets: {},
      },
    ]);

    expect(presentations).toHaveLength(2);
    expect(presentations.map((action) => action.label)).toEqual([
      "Pay {3}{B}",
      "Exile a card from hand",
    ]);

    const manaPlan = targetPlanFromAction(presentations[0]);
    expect(candidateTargetIds(manaPlan)).toEqual(new Set(["player-1", "player-2"]));

    const exilePlan = targetPlanFromAction(presentations[1]);
    expect(exilePlan?.prompt).toBe("Choose a highlighted card to exile from your hand.");
    expect(candidateTargetIds(exilePlan)).toEqual(
      new Set(["hand:grief", "hand:thoughtseize"]),
    );
    const playerStep = selectPlanTarget(exilePlan!, ["hand:thoughtseize"]);
    expect(playerStep?.plan?.prompt).toBe("Choose a highlighted player.");
    expect(candidateTargetIds(playerStep?.plan ?? null)).toEqual(
      new Set(["player-1", "player-2"]),
    );
    expect(selectPlanTarget(playerStep!.plan!, ["player-2"])).toEqual({
      actionId: "thoughtseize:p2",
    });
  });

  test("groups exact Engine combinations into successive visual targets", () => {
    const action = {
      engineTargetKeys: ["targetPlayer", "targetPermanent"],
      engineTargetActions: [
        {
          id: "cast:p1:c1",
          targets: {
            targetPlayer: { player: { playerId: "player-1" } },
            targetPermanent: { permanent: { instanceId: "creature-1" } },
          },
        },
        {
          id: "cast:p2:c2",
          targets: {
            targetPlayer: { player: { playerId: "player-2" } },
            targetPermanent: { permanent: { instanceId: "creature-2" } },
          },
        },
      ],
      label: "Cast spell",
    };
    const first = targetPlanFromAction(action);

    expect(candidateTargetIds(first)).toEqual(new Set(["player-1", "player-2"]));
    const second = selectPlanTarget(first!, ["player-2"]);
    expect(candidateTargetIds(second?.plan ?? null)).toEqual(new Set(["creature-2"]));
    expect(selectPlanTarget(second!.plan!, ["creature-2"])).toEqual({
      actionId: "cast:p2:c2",
    });
  });

  test("keeps hand, battlefield, command-zone, and stack choices on the table", () => {
    const ids = tableChoiceCardIds({
      players: [{
        zones: {
          battlefield: {
            creatures: [{ id: "creature" }],
            lands: [{ id: "land" }],
            nonCreaturePermanents: [{ id: "artifact" }],
          },
          commandZone: { cards: [{ id: "commander" }] },
          exile: { cards: [{ id: "exiled" }] },
          graveyard: { cards: [{ id: "dead" }] },
          hand: [{ id: "hand" }],
          sideboard: { cards: [{ id: "sideboard" }] },
        },
      }],
      stack: [{ card: { id: "spell" } }],
    });

    expect(ids).toEqual(new Set([
      "hand",
      "land",
      "creature",
      "artifact",
      "commander",
      "spell",
    ]));
  });
});
