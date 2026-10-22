# Changelog

All notable changes to Repcut are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) once `v0.N.0` tags begin. Earlier
milestones are tagged `prompt-NN-done`. Each stage's full detail lives in its
report under [`docs/reports/`](docs/reports/).

## [Unreleased]

Built and passing its automated gates; awaiting the human review pass before
merging to `main`.

### Added

- **Media** — resumable uploads with size budgets; a content-addressed media
  store; ingest to a CFR, bt709, single-audio-rate proxy with rotation applied;
  thumbnail strips.
- **Analysis** — scene detection, one sampled frame per scene, motion and audio
  energy, and Gemini scene tagging that is cached per scene and rate-limited
  client-side, degrading to heuristics when the free quota runs out.
- **Grading** — themed colour grades from TOML packs, per-scene measurement and
  correction, graded stills and previews, with every override logged as a
  taste signal.
- **Music & edit** — a local music library with beat analysis and ranked track
  suggestions; a deterministic, seeded cut planner over usable footage; beat
  snapping; override re-sync; voice ducking.
- **Captions** — local speech-to-text, editable caption timeline, three styles,
  burned in per shot so a caption edit re-renders only its shots.
- **Export** — platform presets, manual reframing offsets, resumable segment
  rendering, an x264 master with bt709 tagging and fast-start, and a
  post-render platform check.
- **Slow motion** — hero-moment detection and 2x interpolation from captured
  frames only, with a measured confidence and a normal-speed fallback.
- **Reference matching** — measure a reference clip's look (as a capped,
  saveable theme), its cut rhythm (as planner numbers) and its captions (the
  nearest style); apply all three in one click, detach any part.
- **Opening and trust** — the edit can open on a short teaser of its best
  moment (on, off, or one of three), inside the same length; a
  frame-accurate before/after slider on any scene; and a "What Repcut did"
  summary whose every line is checked against the edit's own record.
- **Style, platforms, gym** — a style profile learned from your overrides
  (theme, grade strength, pacing, caption style, music energy): bounded
  priors with their evidence in words, switchable and resettable per item;
  subject-tracked reframing (a pinned YOLO person detector, or movement) and
  one-click export of reel, square and landscape; rep counting on hero
  moments (pinned YOLO pose) and set captions that are burned in only once
  confirmed.
- **Copilot** — say what to change and it runs the same operations as the
  panels' controls, through a fixed, validated allowlist; several changes in
  one message, one question when a request is ambiguous, a named refusal for
  anything outside natural-only, a log of what it could not do. Every change,
  from either door, is on one undo stack with a card to undo it; with no key,
  budget or network the panel says why and nothing else changes.
- **Quality harness** — `make test` (engine suite with 80% coverage floors on
  edit, grading and analysis; UI tests; a browser happy path), `make eval`
  (every product claim measured on real footage cut locally from your own
  clips, with a scorecard; a claim with no fixture yet is reported as not
  measured, never as a pass), a mutation check that a broken beat-sync fails,
  an order-independent suite, and `make gc` for unreferenced store bytes.
- **UI** — project list, editor workspace with player, analysis, grading,
  edit, caption and export panels; live job progress over WebSocket; engine
  health page; in-house design system.
- **Tooling** — per-stage executable gates with `-auto` mode, plan-leak and
  secret guards, environment diagnostics, model fetching, and builders for
  human review pages.
- Project scaffolding: `CONTRIBUTING.md`, `SECURITY.md`, this changelog,
  `docs/architecture.md`, a docs index, and issue templates.

### Security

- Loopback bind, exact `Host` allow-list, explicit CORS without credentials,
  WebSocket `Origin` checks, bounded request models, a path store confined to
  `$DATA_DIR`, validated FFmpeg inputs, strict CSP with
  `frame-ancestors 'none'`, and `pip-audit` / `npm audit` / gitleaks in CI.
- A regression test for every security finding to date, checked against the
  reviews' summary tables.
