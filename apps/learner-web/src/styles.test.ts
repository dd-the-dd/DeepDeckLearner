import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
const playerStyles = readFileSync(resolve(process.cwd(), "src/player-app.css"), "utf8");

describe("Pixi game notes layout", () => {
  test('replay header keeps a row axis when legacy mobile headers become columns', () => {
    const rule = playerStyles.match(/\.replay-theater-header\s*\{([^}]*)\}/u)?.[1] ?? '';
    expect(rule).toMatch(/flex-direction:\s*row\s*;/u);
  });
  test('saved replay allocates its flexible first row to the board, with controls below', () => {
    const rule=playerStyles.match(/\.replay-stage\s+\.pixi-replay\s*\{([^}]*)\}/u)?.[1]??'';
    // Seat/audio now live inside Pixi's bottom toolbar, not as preceding grid rows.
    expect(rule).toMatch(/grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto\s*;/u);
    expect(rule).not.toMatch(/grid-template-rows:\s*auto/u);
  });
  test("keeps the collapsed notes trigger compact on the left edge", () => {
    const notesRule = styles.match(/\.pixi-game-notes\s*\{(?<declarations>[^}]*)\}/u)
      ?.groups?.declarations ?? "";

    expect(notesRule).toMatch(/position:\s*fixed/u);
    expect(notesRule).toMatch(/left:\s*\.7rem/u);
    expect(notesRule).toMatch(/right:\s*auto/u);
    expect(notesRule).toMatch(/width:\s*auto/u);
    expect(notesRule).toMatch(/height:\s*auto/u);
    expect(notesRule).toMatch(/padding:\s*0/u);
    expect(notesRule).toMatch(/border:\s*0/u);
    expect(notesRule).toMatch(/box-shadow:\s*none/u);
  });

  test("limits the full-height sidebar treatment to the application sidebar", () => {
    expect(styles).not.toMatch(/(?:^|\n)aside\s*\{/u);
    expect(styles.match(/\.app-shell\s*>\s*aside\s*\{/gu)).toHaveLength(2);
  });
});
