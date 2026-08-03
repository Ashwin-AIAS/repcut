import type { Config } from "tailwindcss";

/*
  Tailwind is the delivery mechanism for the tokens in `app/globals.css`, not a
  second place to define style. Every entry below points at a custom property;
  nothing here introduces a new value. Core utilities only — no plugins, no
  component library.

  Note: because these resolve to `var(--token)` rather than to channel triplets,
  opacity modifiers (`bg-panel/50`) do not work. That is intentional. Where a
  tinted surface is needed, add a solid token (see `--accent-surface`) whose
  contrast can actually be measured.
*/
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        panel: "var(--panel)",
        raised: "var(--raised)",
        line: {
          DEFAULT: "var(--line)",
          strong: "var(--line-strong)",
        },
        fg: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted: "var(--text-muted)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          surface: "var(--accent-surface)",
        },
        positive: "var(--positive)",
        warning: "var(--warning)",
        danger: "var(--danger)",
      },
      // A bare `border` utility would otherwise fall back to Tailwind's light
      // gray, which is an off-token colour on a dark canvas.
      borderColor: {
        DEFAULT: "var(--line)",
      },
      borderRadius: {
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
      },
      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        6: "var(--space-6)",
        8: "var(--space-8)",
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
      fontSize: {
        xs: ["var(--text-xs)", { lineHeight: "1.1rem" }],
        sm: ["var(--text-sm)", { lineHeight: "1.25rem" }],
        base: ["var(--text-base)", { lineHeight: "1.5rem" }],
        lg: ["var(--text-lg)", { lineHeight: "1.75rem" }],
        xl: ["var(--text-xl)", { lineHeight: "1.9rem" }],
        "2xl": ["var(--text-2xl)", { lineHeight: "2.25rem" }],
      },
      transitionDuration: {
        micro: "var(--motion-micro)",
        layout: "var(--motion-layout)",
      },
      transitionTimingFunction: {
        micro: "var(--ease-micro)",
        layout: "var(--ease-layout)",
      },
    },
  },
  plugins: [],
};

export default config;
