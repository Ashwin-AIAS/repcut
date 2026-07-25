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
tokens. Suggested scale:

```
surface:   bg-neutral-950   base canvas
panel:     bg-neutral-900   inspector, sidebars
raised:    bg-neutral-800   cards, controls
border:    border-neutral-800 / -700 on hover
text:      neutral-100 primary · neutral-400 secondary · neutral-500 muted
accent:    a single saturated hue, used sparingly and only for AI-suggested state
positive / warning / danger: one each, semantic only
radius:    rounded-lg default · rounded-xl panels
spacing:   4 / 8 / 12 / 16 / 24 / 32
motion:    150ms ease-out micro · 300ms ease-in-out layout
```

Contrast ≥ 4.5:1 for text. Dark themes fail this more often than light ones —
check, don't assume.

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
