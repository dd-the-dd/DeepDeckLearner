import { describe, expect, test, vi } from "vitest";

vi.mock("@deepdeck/pixi", () => ({
  pixiCombatLinks: (combat: {
    attackers?: Array<{
      attackerId: string;
      defender?: {
        permanent?: { instanceId: string };
        player?: { playerId: string };
      };
    }>;
    blockers?: Array<{ attackerId: string; blockerId: string }>;
  } = {}) => [
    ...(combat.attackers ?? []).map((assignment) => ({
      kind: "attack",
      sourceCardId: assignment.attackerId,
      targetCardId: assignment.defender?.permanent?.instanceId ?? "",
      targetPlayerId: assignment.defender?.player?.playerId ?? "",
    })),
    ...(combat.blockers ?? []).map((assignment) => ({
      kind: "block",
      sourceCardId: assignment.blockerId,
      targetCardId: assignment.attackerId,
      targetPlayerId: "",
    })),
  ],
}));

import {
  gameNoteCardPresentation,
  pixiScene,
  sortGameNoteCards,
  stackEventPlaybackEntries,
  visibleCardNameChoices,
} from "./LocalPixiRenderer";

describe("Pixi local-seat projection", () => {
  test("enriches observed cards with their catalog art and sortable characteristics", () => {
    expect(gameNoteCardPresentation({ count: 2, name: "Force of Will" }, {
      force: {
        imageUrl: "force.jpg",
        manaValue: 5,
        name: "Force of Will",
        typeLine: "Instant",
      },
    })).toEqual({
      count: 2,
      imageUrl: "force.jpg",
      manaValue: 5,
      name: "Force of Will",
      typeLine: "Instant",
    });
  });

  test("sorts observed cards by name, type, mana value, or quantity without losing their original order", () => {
    const cards = [
      { count: 1, manaValue: 5, name: "Force of Will", typeLine: "Instant" },
      { count: 4, manaValue: 1, name: "Birds of Paradise", typeLine: "Creature — Bird" },
      { count: 2, manaValue: null, name: "Unknown", typeLine: "" },
      { count: 3, manaValue: 0, name: "Forest", typeLine: "Basic Land — Forest" },
    ];

    expect(sortGameNoteCards(cards, "observed", "desc").map(card => card.name)).toEqual([
      "Force of Will", "Birds of Paradise", "Unknown", "Forest",
    ]);
    expect(sortGameNoteCards(cards, "name", "asc").map(card => card.name)).toEqual([
      "Birds of Paradise", "Force of Will", "Forest", "Unknown",
    ]);
    expect(sortGameNoteCards(cards, "type", "asc").map(card => card.name)).toEqual([
      "Unknown", "Forest", "Birds of Paradise", "Force of Will",
    ]);
    expect(sortGameNoteCards(cards, "mana", "desc").map(card => card.name)).toEqual([
      "Force of Will", "Birds of Paradise", "Forest", "Unknown",
    ]);
    expect(sortGameNoteCards(cards, "quantity", "desc").map(card => card.name)).toEqual([
      "Birds of Paradise", "Forest", "Unknown", "Force of Will",
    ]);
    expect(cards.map(card => card.name)).toEqual([
      "Force of Will", "Birds of Paradise", "Unknown", "Forest",
    ]);
  });

  test("plays every new stack object for 300ms and compresses repeated triggers", () => {
    expect(stackEventPlaybackEntries([
      {
        cardInstanceId: "spell",
        detail: { stackId: "stack:1" },
        kind: "spellCast",
        sequence: 8,
      },
      {
        cardInstanceId: "permanent",
        detail: { stackId: "stack:2" },
        kind: "triggeredAbilityPutOnStack",
        sequence: 9,
      },
      {
        cardInstanceId: "permanent",
        detail: { stackId: "stack:3" },
        kind: "triggeredAbilityPutOnStack",
        sequence: 10,
      },
      {
        cardInstanceId: "copy:spell:spell:1",
        detail: { stackId: "stack:4" },
        kind: "stackObjectCopied",
        sequence: 11,
      },
      { kind: "lifeLost", sequence: 12 },
    ], 7)).toEqual([
      expect.objectContaining({
        cardInstanceId: "spell",
        count: 1,
        durationMs: 300,
        kind: "spell",
      }),
      expect.objectContaining({
        cardInstanceId: "permanent",
        count: 2,
        durationMs: 3,
        firstSequence: 9,
        lastSequence: 10,
      }),
      expect.objectContaining({
        cardInstanceId: "copy:spell:spell:1",
        durationMs: 300,
        kind: "stackCopy",
      }),
    ]);
  });

  test("offers only visible hand, graveyard, and exile cards as clickable names", () => {
    const choices = visibleCardNameChoices({
      players: [
        {
          key: "local-human",
          role: "human",
          zones: {
            exile: { cards: [{ id: "exiled", name: "Swords to Plowshares" }] },
            graveyard: { cards: [{ id: "buried", name: "Cabal Therapy" }] },
            hand: [{ id: "mine", name: "Ponder" }],
            libraryTop: { id: "known-top", name: "Brainstorm" },
          },
        },
        {
          key: "opponent",
          role: "ai",
          zones: {
            exile: { cards: [] },
            graveyard: { cards: [] },
            hand: [
              { id: "known", knownToViewer: true, name: "Force of Will" },
              { id: "hidden", name: "Daze" },
            ],
          },
        },
      ],
    });

    expect(choices.map((card) => (card as { name: string }).name)).toEqual([
      "Cabal Therapy",
      "Swords to Plowshares",
      "Ponder",
      "Force of Will",
    ]);
  });

  test("shows the local hand and turns every opponent hand card face down", () => {
    const scene = pixiScene({
      players: [
        {
          key: "local-human",
          name: "You",
          role: "human",
          life: 20,
          zones: {
            battlefield: {},
            hand: [{ id: "mine", imageUrl: "mine.jpg", name: "Ponder" }],
          },
        },
        {
          key: "local-agent",
          name: "AI",
          role: "ai",
          life: 20,
          zones: {
            battlefield: {},
            hand: [{ id: "theirs", imageUrl: "secret.jpg", name: "Show and Tell" }],
          },
        },
      ],
      state: { outcome: null },
      step: { playerKey: "local-human", playerName: "You", turn: 1 },
    });

    expect(scene.players[0].local).toBe(true);
    expect(scene.players[0].hand[0]).toMatchObject({
      faceDown: false,
      imageUrl: "mine.jpg",
      name: "Ponder",
    });
    expect(scene.players[1].local).toBe(false);
    expect(scene.players[1].hand[0]).toMatchObject({
      actionable: false,
      faceDown: true,
      imageUrl: "",
      name: "Hidden card",
      targetable: false,
    });
  });

  test("keeps individually known opponent hand cards visible and targetable", () => {
    const scene = pixiScene({
      players: [
        {
          key: "local-human",
          name: "You",
          role: "human",
          life: 20,
          zones: { battlefield: {}, hand: [] },
        },
        {
          key: "local-agent",
          name: "AI",
          role: "ai",
          life: 20,
          zones: {
            battlefield: {},
            hand: [
              {
                id: "known-card",
                imageUrl: "known.jpg",
                knownToViewer: true,
                name: "Brainstorm",
              },
              {
                id: "unknown-card",
                imageUrl: "secret.jpg",
                name: "Secret card",
              },
            ],
          },
        },
      ],
      state: { outcome: null },
      step: { playerKey: "local-human", playerName: "You", turn: 1 },
    }, {
      targetableIds: new Set(["known-card"]),
      targeting: true,
    });

    expect(scene.players[1].hand[0]).toMatchObject({
      faceDown: false,
      imageUrl: "known.jpg",
      name: "Brainstorm",
      targetable: true,
    });
    expect(scene.players[1].hand[1]).toMatchObject({
      faceDown: true,
      imageUrl: "",
      name: "Hidden card",
      targetable: false,
    });
  });

  test("shows a known library top face up with its knowledge marker", () => {
    const scene = pixiScene({
      players: [{
        key: "local-agent",
        life: 20,
        name: "AI",
        role: "ai",
        zones: {
          battlefield: {},
          hand: [],
          libraryCount: 42,
          libraryTop: {
            id: "known-top",
            imageUrl: "known-top.jpg",
            knownToViewer: true,
            name: "Force of Will",
          },
        },
      }],
      state: { outcome: null },
      step: null,
    });

    expect(scene.players[0].zones[2].cards[0]).toMatchObject({
        faceDown: false,
        imageUrl: "known-top.jpg",
        knownToViewer: true,
      });
  });

  test("marks existing table entities as visual targets while targeting", () => {
    const scene = pixiScene({
      players: [
        {
          key: "local-human",
          name: "You",
          role: "human",
          life: 20,
          zones: {
            battlefield: {
              creatures: [{ id: "creature-1", name: "Target creature" }],
            },
            hand: [{
              id: "spell-1",
              name: "Target spell",
              actionState: { actionable: true },
            }],
          },
        },
        {
          key: "local-agent",
          name: "AI",
          role: "ai",
          life: 20,
          zones: { battlefield: {}, hand: [] },
        },
      ],
      stack: [{ id: "stack-1", label: "Spell on the stack", type: "spell" }],
      state: { outcome: null },
      step: { playerKey: "local-human", playerName: "You", turn: 1 },
    }, {
      selectedIds: new Set(["spell-1"]),
      targeting: true,
      targetableIds: new Set(["spell-1", "creature-1", "local-agent", "stack-1"]),
    });

    expect(scene.players[0].hand[0]).toMatchObject({
      actionable: true,
      targetable: false,
    });
    expect(scene.players[0].battlefield[0].targetable).toBe(true);
    expect(scene.players[1].targetable).toBe(true);
    expect(scene.stack[0].targetable).toBe(true);
    expect(scene.controls.canPassPriority).toBe(false);
    expect(scene.controls.advanceLabel).toBe("Choose a highlighted target");
  });

  test("does not ask for a highlighted object when the choice is abstract", () => {
    const scene = pixiScene({
      players: [],
      state: { outcome: null },
      step: null,
    });

    expect(scene.controls.advanceLabel).toBe("Use the decision panel");
  });

  test("projects the click order onto cards already visible on the table", () => {
    const scene = pixiScene({
      players: [{
        key: "local-human",
        name: "You",
        role: "human",
        life: 20,
        zones: {
          battlefield: {},
          hand: [
            { id: "first-card", name: "First" },
            { id: "second-card", name: "Second" },
          ],
        },
      }],
      state: { outcome: null },
      step: { playerKey: "local-human", playerName: "You", turn: 1 },
    }, {
      orderedIds: ["second-card", "first-card"],
      selectedIds: new Set(["first-card", "second-card"]),
    });

    expect(scene.players[0].hand[0].selectionOrder).toBe(2);
    expect(scene.players[0].hand[1].selectionOrder).toBe(1);
  });

  test("turns Engine combat assignments into Pixi attack and block arrows", () => {
    const scene = pixiScene({
      combat: {
        attackers: [{
          attackerId: "attacker",
          defender: { player: { playerId: "opponent" } },
        }],
        blockers: [{ attackerId: "attacker", blockerId: "blocker" }],
      },
      players: [],
      state: { outcome: null },
      step: null,
    });

    expect(scene.combat).toEqual([
      expect.objectContaining({
        kind: "attack",
        sourceCardId: "attacker",
        targetPlayerId: "opponent",
      }),
      expect.objectContaining({
        kind: "block",
        sourceCardId: "blocker",
        targetCardId: "attacker",
      }),
    ]);
  });
});
