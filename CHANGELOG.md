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
