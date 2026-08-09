import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  TEXT_CONTRAST_FLOOR,
  UI_CONTRAST_FLOOR,
  contrastRatio,
  parseHex,
} from "@/lib/contrast";

/**
 * The tokens, checked as data.
 *
 * `globals.css` is the single source of style, which makes it the single place
 * a contrast regression can be introduced — and a contrast regression is
 * invisible in review: nothing looks wrong to the person who chose the colour.
 * These tests recompute every ratio from the file itself, so the design system
 * cannot drift below the floor without a red build.
 *
 * axe cannot cover this. It measures computed styles, and in jsdom no
 * stylesheet is loaded, so `color-contrast` is disabled there and covered here
 * instead (see `components/primitives/primitives.test.tsx`).
 */
const cssPath = fileURLToPath(new URL("../app/globals.css", import.meta.url));
const css = readFileSync(cssPath, "utf8");

function token(name: string): string {
  const match = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (match === null) throw new Error(`token --${name} is not declared`);
  return match[1].trim();
}

function rgb(name: string) {
  const parsed = parseHex(token(name));
  if (parsed === null) throw new Error(`token --${name} is not a hex colour`);
  return parsed;
}

/** Every surface a foreground token is allowed to sit on. */
const SURFACES = ["surface", "panel", "raised"] as const;

const TEXT_TOKENS = [
  "text-primary",
  "text-secondary",
  "text-muted",
  "accent",
  "positive",
  "warning",
  "danger",
] as const;

describe("token contrast", () => {
  it.each(TEXT_TOKENS)("--%s clears 4.5:1 on every surface", (name) => {
    for (const surface of SURFACES) {
      const ratio = contrastRatio(rgb(name), rgb(surface));
      expect(
        ratio,
        `--${name} on --${surface} is ${ratio.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(TEXT_CONTRAST_FLOOR);
    }
  });

  it("--line-strong clears the 3:1 floor for component boundaries", () => {
    // A non-text token: it draws control edges, which WCAG 1.4.11 holds to 3:1
    // rather than 4.5:1. Held to its own floor rather than exempted.
    for (const surface of SURFACES) {
      const ratio = contrastRatio(rgb("line-strong"), rgb(surface));
      expect(
        ratio,
        `--line-strong on --${surface} is ${ratio.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(UI_CONTRAST_FLOOR);
    }
  });

  it("the focus ring is not the accent hue", () => {
    // The accent means "the AI chose this" and nothing else. A focus ring in
    // the same colour would make every focused control look AI-suggested.
    expect(token("focus")).not.toBe(token("accent"));
  });

  it("declares the motion and reduced-motion contract", () => {
    expect(token("motion-micro")).toBe("150ms");
    expect(token("motion-layout")).toBe("300ms");
    // The global reduced-motion block is what makes every component's
    // transition respect the OS setting without knowing it exists.
    expect(css).toContain("prefers-reduced-motion: reduce");
  });
});

describe("token completeness", () => {
  it.each([
    "surface",
    "panel",
    "raised",
    "line",
    "line-strong",
    "scrim",
    "accent",
    "accent-surface",
    "radius-lg",
    "radius-xl",
    "font-sans",
    "font-display",
    "font-mono",
  ])("--%s is declared", (name) => {
    expect(() => token(name)).not.toThrow();
  });

  it("the spacing scale is exactly 4/8/12/16/24/32px", () => {
    // Components are held to this scale by verify-02 criterion 11, so the
    // scale itself has to be pinned or the check drifts with it.
    expect(token("space-1")).toBe("0.25rem");
    expect(token("space-2")).toBe("0.5rem");
    expect(token("space-3")).toBe("0.75rem");
    expect(token("space-4")).toBe("1rem");
    expect(token("space-6")).toBe("1.5rem");
    expect(token("space-8")).toBe("2rem");
  });
});
