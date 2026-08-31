import { describe, expect, test, vi } from "vitest";

vi.mock("@deepdeck/pixi", () => ({}));

import { pixiScene } from "./LocalPixiRenderer";

describe("Pixi local-seat projection", () => {
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
  });
});
