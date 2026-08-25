/**
 * WCAG 2.1 relative luminance and contrast ratio.
 *
 * Pure arithmetic over hex strings, kept out of the test file so the gate and
 * the suite compute contrast the same way rather than each carrying its own
 * copy of the formula.
 *
 * Dark themes fail the 4.5:1 floor far more often than light ones, and the
 * failure is invisible to whoever picked the colours — it needs computing, not
 * eyeballing.
 */

/** WCAG's floor for body text against its background. */
export const TEXT_CONTRAST_FLOOR = 4.5;

/** WCAG's floor for the boundary of a UI component (1.4.11). */
export const UI_CONTRAST_FLOOR = 3;

export interface Rgb {
  readonly r: number;
  readonly g: number;
  readonly b: number;
}

/**
 * Parse `#rgb` or `#rrggbb` into channels 0–255.
 *
 * Returns `null` rather than throwing: the caller is scanning a stylesheet and
 * a token that is not a plain hex colour (a `var()` reference, an `rgb()` with
 * alpha) is a thing to skip, not an error.
 */
export function parseHex(value: string): Rgb | null {
  const hex = value.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]+$/.test(hex)) return null;

  if (hex.length === 3) {
    const [r, g, b] = [...hex].map((c) => parseInt(c + c, 16));
    return { r, g, b };
  }
  if (hex.length === 6) {
    return {
      r: parseInt(hex.slice(0, 2), 16),
      g: parseInt(hex.slice(2, 4), 16),
      b: parseInt(hex.slice(4, 6), 16),
    };
  }
  return null;
}

/** WCAG relative luminance. The 0.03928 knee and the 2.4 exponent are the spec's. */
export function luminance({ r, g, b }: Rgb): number {
  const channel = (raw: number): number => {
    const c = raw / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/**
 * Contrast ratio between two colours, 1 to 21.
 *
 * Symmetric by construction — the lighter of the two always takes the numerator,
 * so callers never have to know which argument is the foreground.
 */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}
