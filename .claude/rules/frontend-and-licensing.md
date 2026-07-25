# Rule: Frontend, design & content licensing

## Design system
- Premium **dark** aesthetic. Editor shell follows CapCut-style *UX patterns*
  — timeline, preview, inspector panel, layer stack.
- **Legal line, hard:** patterns and layout conventions only. Never copy
  CapCut's (or any product's) assets, icons, illustrations, exact color values,
  fonts, or copy text. No screenshots as reference material in the repo.
  Convergent UX is fine; cloned assets are infringement.
- Tokens (color, spacing, type scale, radius, motion) live in one place and are
  the only source of style. No ad-hoc hex values in components.
- Tailwind core utilities only. No component libraries.

## Interaction requirements driven by principles
- **P2 lives in the UI.** Every AI-produced value renders as an editable
  control with a visible "AI suggested" state and a reset-to-suggestion action.
  When the user overrides, dependents visibly re-sync — with an animation that
  makes the re-sync legible, not a silent jump.
- **P4 lives in the UI.** Before frames are sent to Gemini, show what is being
  sent and why. Not buried in settings.
- Long jobs stream progress over WebSocket. Every job has: queued, running with
  percentage and current step, succeeded, failed-with-cause, and cancel.
  Never a spinner with no information.
- Optimistic UI only where it is reversible.

## Accessibility (baseline, not optional)
Keyboard operable timeline, focus-visible everywhere, ARIA on custom controls,
contrast ≥ 4.5:1 for text, `prefers-reduced-motion` respected.

## Repcut's own licence
Repcut is **AGPL-3.0**. Consequences to respect when adding a dependency:
- A dependency's licence must be AGPL-compatible. **GPL-incompatible licences
  cannot be linked in** — check before adding, not after.
- Model weights carry their own terms, separate from the code's. YOLO
  (Ultralytics) is AGPL-3.0; RIFE and faster-whisper have their own — verify
  each before shipping, and record it in the session report.
- A dependency whose licence forbids commercial or hosted use is a blocker:
  stop and flag it.

## Music & content licensing
- The music library is **local, user-supplied**, in `data/music/` (gitignored).
- **Never commit audio files.** Never bundle tracks with the repo. Never
  download tracks from a source whose license is unverified.
- Maintain `data/music/LICENSES.md` (local, untracked) recording per track:
  source, license, attribution requirement.
- Surface attribution requirements in the export UI when a track requires it.
- "Export without music" is a first-class path — it is the safe option for
  platform posting and must never be second-class in the UI.
- Flag to the human, do not decide alone: any track whose license is unclear,
  and any feature that would republish third-party audio.
