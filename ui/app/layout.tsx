import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Repcut",
  description: "Local-first AI video editor for gym footage.",
};

/**
 * The shell is deliberately thin: it fixes the dark canvas and the token-driven
 * base type, and nothing else. The editor chrome (topbar, panels, timeline)
 * arrives in a later prompt and will nest inside this.
 *
 * `class="dark"` is set statically rather than toggled — Repcut is dark by
 * design, not by preference, so there is no theme switch to hydrate and no
 * flash of the wrong colours.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface font-sans text-base text-fg-primary">
        {children}
      </body>
    </html>
  );
}
