# Amendment 022 — the opening hook's placement, what "before" and "after" are, and what the summary may say
Date: 2026-10-16
Affects: Prompt 09 (Deliverables 1-4; Constraints; Success Criteria)
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). Open the edit with a short teaser of
its most striking moment, chosen from the hero moments, with the person able
to turn it off or pick another; keep the duration budget authoritative and
only show the moment twice when the length allows. Offer a before/after split
on any scene that scrubs without rendering, and a plain-words summary of what
was done that must be true. Prove it with: the hook is a top-3 hero on a
labelled session, the length stays near the target, both sides of the slider
show the same frame, and every summary line exists in the plan data.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | "Hero score x visual impact" names no impact measure | Visual impact = mean movement inside the candidate's window against the clip's own 95th percentile (the detector's scale), floored at 0.1 so a still hero still ranks by its score. Candidates are the detector's top-3 hero moments only; a hook is a hero shown early, never a new judgement (`edit/hook.py`) |
| 2 | Window and speed of the teaser | At most 2 s, centred on the moment's peak, inside its own segment. Played at normal speed: a teaser is real-time captured footage (P1); the slow version, if any, is the in-place hero shot |
| 3 | Where it goes without spending the budget | From timeline zero to the last beat at or under the window's length; the edit proper fills the rest of the same grid. The plan's length is identical with and without the hook (tested on 30 combinations of target, music and seed), which is stronger than the guide's 5% |
| 4 | "Duplicate only when total duration allows" | Read as: an edit of 20 s or more keeps the moment's footage available, so it can play again in place (usually as the slowed hero at the drop); a shorter one moves it (footage used once, not slowed again). `Plan.hook_placement` reports what actually happened: `duplicated` only if another shot shows the same footage |
| 5 | Interplay with the person's decisions (P2) | A locked cut at the opening wins (warning, no teaser); a locked teaser stays verbatim; removed footage means no teaser (warning); a manual pick that stops being a candidate falls back to the AI's pick (warning). Mode lives in the decisions (`hook`: on/off/manual, `hook_segment`); `hook_on`, `hook_off`, `hook_choose` are taste events (migration 0008) |
| 6 | "Original vs fully processed" | Before = the clip's 720p proxy (the same normalisation every clip gets at ingest); after = the scene's graded preview (correction, look, finish), rendered once by the existing preview job. Scrubbing never renders; the one wait is that preview job, which the panel offers to start |
| 7 | "Captions overlay indicator" | Captions are burned per shot at export and are not part of a scene preview, so the slider shows an indicator (on/off, style), not drawn captions |
| 8 | "Frame hash at 3 positions" | Grading changes every pixel, so a hash of pixels never matches across sides. Each side's frame is identified instead: the gate generates a clip whose every frame carries its own index as black/white blocks, runs it through the real proxy build and the real graded preview (Moody, 100%), reads both sides where the UI seeks (mid-frame, through `review/compare.py`'s mapping, mirrored in `ui/lib/compare.ts`) and hashes the index each shows. Mapping: the preview's frame i is the proxy's first frame at or after the scene start as the render command wrote it (three decimals), plus i |
| 9 | "Every claim exists in the edit plan JSON" | The summary reads one edit record: the stored plan and decisions, the grades of the scenes the edit shows, caption settings and lines, the dead space left out, the track. Every claim carries RFC 6901 pointers into that record with the value found there; a stated count equals the facts behind it; the route refuses to answer with a claim the record does not back |
| 10 | "Silences removed" | The edit removes no silence as such; what it leaves out is dead space (no movement and no speech for a second or more, `segments.dead_stretches`, the same windows the segmenter drops). The summary says exactly that. White balance is the correction's temperature shift (the largest-magnitude scene per clip). A slow-motion line notes the export's normal-speed fallback |
| 11 | Gate in this build shell | No `[HUMAN]` criterion. Criterion 1 on the real labelled session uses verify-07's labels and is pending until they exist (amendment 017's pattern). The UI halves of criteria 3 and 5 and the nested verify-08 run only on the real machine from this shell (the repo's `ui/node_modules` is the Windows install; verify-07 needs torch), as in amendment 021 #12 |

## Principle check

P1: the teaser is captured footage at normal speed; the before/after shows
the proxy and its grade, nothing generated. P2: the hook is a default with
off and three picks, each re-planning the whole edit. P3: every hook change
is a taste event with the candidates as features. P4: nothing new leaves the
machine (the gate checks `review/` and `edit/hook.py` import no network or
model library); the summary says captions were transcribed locally. P5:
nothing paid; no new dependency.
