---
name: repcut-design-system
description: Repcut's dark UI design system — tokens, editor shell layout, AI-suggestion and override controls, job progress states, accessibility. Use for any ui/ work or when adding an override control for an AI decision.
---

# Repcut design system

Premium, dark, calm. The UI's job is to make an AI-made edit feel **trustworthy
and editable** — not to look clever.

## Legal line, first

Editor **UX patterns** from CapCut and similar tools are fine to converge on:
timeline, preview, inspector panel, layer stack. Never copy their assets,
icons, illustrations, exact color values, fonts, or copy text. No product
screenshots stored in the repo as reference.

## Tokens — the only source of style

No ad-hoc hex values in components. Tailwind core utilities only, mapped to
tokens. **The values are decided and live in `ui/app/globals.css`**; that file is
the source of truth and this table is the record of what was chosen and why
(amendment 004 §4).

```
surface:  #0b0d10  base canvas
panel:    #14171c  inspector, sidebars
raised:   #1c2027  cards, controls
line:     #272c35  separation · line-strong #646e7e for control edges
text:     #f3f5f8 primary · #afb8c6 secondary · #8b96a6 muted
accent:   #b49bff  RESERVED for AI-suggested state · accent-surface #241f3a
positive: #58d08b   warning: #e8b04b   danger: #ff8080
focus:    #f3f5f8  deliberately NOT the accent, which is spoken for
radius:   rounded-lg 0.5rem default · rounded-xl 0.875rem panels
spacing:  4 / 8 / 12 / 16 / 24 / 32
motion:   150ms ease-out micro · 300ms ease-in-out layout
type:     Sora 600 display · IBM Plex Sans 400/500/600 UI · system mono
```

**The accent is `#b49bff`** — a desaturated violet. It was chosen against the
constraint that it is the *only* hue meaning "the AI decided this", so it has to
read as deliberate beside `positive`, `warning` and `danger` without competing
with any of them, and it must not be mistaken for a focus ring.

Contrast ≥ 4.5:1 for text, **computed, not assumed** — dark themes fail this
more often than light ones. Measured against every surface a token may sit on
(surface / panel / raised):

| Token | on surface | on panel | on raised |
|---|---|---|---|
| text-primary | 17.81 | 16.45 | 14.96 |
| text-secondary | 9.72 | 8.98 | 8.17 |
| text-muted | 6.50 | 6.00 | 5.45 |
| accent | 8.41 | 7.76 | 7.06 |
| positive | 10.02 | 9.25 | 8.41 |
| warning | 9.95 | 9.19 | 8.36 |
| danger | 8.02 | 7.40 | 6.73 |

Worst case 5.45:1. `line-strong` is a non-text token at 3.17:1 on raised, above
the 3:1 floor for UI component boundaries. `make verify-02` criterion 12
recomputes these from the parsed tokens, so a future edit that breaks the floor
fails the build rather than the review.

Two typefaces, both SIL OFL 1.1, committed as latin-subset woff2 and loaded with
`next/font/local` — provenance and licence in `ui/app/fonts/README.md`. Reach
them as `font-display` and `font-sans`; never name a family in a component.

## Editor shell

```
┌───────────────────────────────────────────────────────┐
│ topbar: project · export · presence of AI disclosures │
├──────────────┬──────────────────────┬─────────────────┤
│ media library│      preview         │   inspector     │
│              │                      │ (AI decisions + │
│              │                      │  overrides)     │
├──────────────┴──────────────────────┴─────────────────┤
│ timeline: scenes · beat grid · captions · music        │
└───────────────────────────────────────────────────────┘
```

The beat grid is **visible on the timeline**. Seeing cuts land on beats is what
makes the sync legible rather than magical — and legible is the goal.

## P2 lives here — the AI-suggestion control

Every AI-produced value renders as a control that:
1. shows an **AI-suggested** state visually (the accent hue, used nowhere else)
2. becomes a **user-set** state once overridden, visibly different
3. offers **reset to suggestion**, always
4. **re-syncs dependents visibly** — change the song and the user *sees* cuts
   re-snap along the timeline

A silent re-sync is as bad as none. The animation is not decoration; it is how
the user learns the system is consistent and comes to trust it.

## P4 lives here — the privacy disclosure

Before sampled frames go to Gemini, show what is being sent and why, inline at
the moment it happens. Not buried in settings. One frame per scene, stated
plainly.

## Job states — never a bare spinner

Every long job surfaces: `queued` → `running` (percent + current step name) →
`succeeded` | `failed` (human cause) — plus **cancel**. Streamed over WebSocket.
Video work is slow; an uninformative wait is where users decide the product is
broken.

## Accessibility baseline

Keyboard-operable timeline (arrows to scrub, space to play, enter to select),
`focus-visible` everywhere, ARIA on custom controls, `prefers-reduced-motion`
respected — including the re-sync animation.
