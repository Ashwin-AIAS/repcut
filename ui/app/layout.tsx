import type { Metadata } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";
import "./globals.css";

/**
 * The UI workhorse. Three weights, which is the whole range the design system
 * uses — a fourth would be a style decision made in a component rather than in
 * the tokens.
 *
 * `display: "swap"` over `"optional"`: the faces are served from this machine,
 * so the swap window is microseconds and there is no realistic flash. Rendering
 * nothing at all while a local file loads would be the worse trade.
 */
const bodyFont = localFont({
  src: [
    { path: "./fonts/IBMPlexSans-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/IBMPlexSans-Medium.woff2", weight: "500", style: "normal" },
    { path: "./fonts/IBMPlexSans-SemiBold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-ui",
  display: "swap",
  // Matched to the system stack so the swap does not reflow. Without this a
  // fallback with different metrics shifts every label by a pixel or two on
  // load, which reads as cheap on a tool that is meant to feel expensive.
  fallback: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
});

/**
 * The display face. One weight, because it is used for headings and the
 * wordmark and nothing else — see `app/fonts/README.md` for why this face.
 */
const displayFont = localFont({
  src: [{ path: "./fonts/Sora-SemiBold.woff2", weight: "600", style: "normal" }],
  // `--font-display-face`, not `--font-display`: globals.css owns the latter as
  // the composed stack (face + fallbacks). Emitting both under one name would
  // put a class rule and a `:root` rule at equal specificity, leaving source
  // order to decide which wins.
  variable: "--font-display-face",
  display: "swap",
  fallback: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
});

export const metadata: Metadata = {
  title: "Repcut",
  description: "Local-first AI video editor for gym footage.",
};

/**
 * The shell is deliberately thin: it fixes the dark canvas, binds the two
 * typefaces to the token layer, and nothing else. Every surface nests inside it.
 *
 * The font variables land on `<html>` rather than `<body>` so that anything
 * portalled outside the body — a modal, a toast — still resolves them.
 *
 * `class="dark"` is set statically rather than toggled — Repcut is dark by
 * design, not by preference, so there is no theme switch to hydrate and no
 * flash of the wrong colours.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html
      lang="en"
      className={`dark ${bodyFont.variable} ${displayFont.variable}`}
    >
      <body className="min-h-screen bg-surface font-sans text-base text-fg-primary">
        {children}
      </body>
    </html>
  );
}
