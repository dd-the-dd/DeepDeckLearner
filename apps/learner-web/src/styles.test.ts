import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

describe("Pixi game notes layout", () => {
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
