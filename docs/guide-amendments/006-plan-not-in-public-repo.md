# Amendment 006 — the build plan is never transcribed into the repo; criterion 13 rewritten, criterion 22 added
Date: 2026-08-23
Affects: Section 4 (Architecture — Storage), Prompt 01 (Success Criteria —
criterion 13), Prompt 02 (Success Criteria — new criterion 22), CLAUDE.md (Build
plan), `.claude/rules/git-and-ci.md` (Commits — never commit)
Status: ACCEPTED

## What the guide and CLAUDE.md say

CLAUDE.md, *Build plan*:

> The authoritative 13-prompt build plan lives OUTSIDE this repo (it is not
> published). Local path is set in `.env` as `REPCUT_GUIDE_PATH`.

`.claude/rules/git-and-ci.md`, *Commits*:

> Never commit: secrets (see `secrets.md`), `data/`, media files, models,
> `node_modules`, `.env`, the build-plan documents.

Both sentences name a **document**. Neither says what happens when the plan is
retyped into a source file instead of copied in as a file, which is what
happened.

## What we found

`engine/repcut/prompts_data.py` — 305 lines, merged to `main` on 2026-08-04 in
PR #3 (`ae9209d`), as part of an off-plan prompt-completion dashboard — carried
the whole plan as Python literals: every prompt's id, name, wave, one-line
summary, deliverables list, human-review flag, key tech, gate command, report
path and calendar-time estimate. Its module docstring said so plainly:
*"Derived from guide prompts.pdf and repo guide amendments."*

The repository is public. That content had been world-readable for nineteen
days when it was found on 2026-08-23, during the Prompt 02 close-out.

Three things were checked and are worth recording:

- **The guide documents themselves were never tracked.** `guide prompts.pdf`,
  `Repcut_Prompt_Guide_v1.md` and `Repcut_Project_Instructions.md` are untracked
  and matched by `.gitignore` lines 56–59. The leak is a transcription, not the
  source.
- **This is not a credential.** Nothing here is revocable and nothing is
  rotatable; `secrets.md`'s incident procedure does not apply.
- **No history rewrite.** Rewriting public history breaks every clone and
  un-publishes nothing — the content was fetchable for nineteen days and must be
  assumed to have been fetched. The fix is forward-only.

### Criterion 13 existed for exactly this and did not fire

`verify-01` criterion 13 is titled *"build plan not transcribed into any tracked
file (content, not filename)"*. Its comment even names the case it was built to
catch: *"an `engine/` module carrying all 15 prompt entries past criterion 11,
the pre-commit guard and gitleaks."*

It matched this, and nothing else:

```text
Wave [0-5][[:space:]]*(—|–|-)[[:space:]]*(<title 1>|<title 2>|…|<title 6>)
```

The six alternatives are the guide's own wave titles, written here as
placeholders on purpose: spelling them out would put the wave structure into a
tracked file, which is the leak this amendment exists to prohibit. It is also
why the replacement guard matches the *shape* of a wave heading rather than an
alternation of the real values — see `scripts/check_plan_leak.py`.

Three or more distinct wave titles in the guide's own formatting. But
`prompts_data.py` stored the wave as `wave="Wave 0"` and kept the title in a
separate field — so it contained **no wave title at all**, matched zero times,
and the criterion printed PASS over the very file it was written to catch.

The guard was written against the shape of the **source document**. A leak has
the shape of a **leak**: a transcription is free to paraphrase, restructure,
rename fields and split one string into two, and this one did all four.

### The guard was written the day before the leak landed, and named it

`scripts/precommit_guard.sh` carried a byte-identical copy of that regex, so the
pre-commit hook had the same hole as the gate — rewriting only criterion 13
would have left the fix one layer deep. Its comment is worth reading in full:

> This happened — a `prompts_data.py` under engine/ carrying all 15 prompt
> entries passed every rule here, gitleaks, and verify-01. […] Measured before
> shipping: 0 of the tracked files match, the transcription that slipped
> through matched 6.

The dates make this precise:

| When | What |
|---|---|
| 2026-08-03 | `1f58306` (prompt-01) adds both guards, naming `prompts_data.py` as the case they exist to catch |
| 2026-08-04 | `ae9209d` (PR #3) merges `prompts_data.py` to `main` |
| 2026-08-23 | Found, by reading `main` during an unrelated close-out |

So the guard was written **one day before** the file it names arrived, and then
did not stop it. And the number offered as proof was never true of that file:

```
$ RE='Wave [0-5][[:space:]]*(—|–|-)[[:space:]]*(<title 1>|…|<title 6>)'
$ git show ae9209d:engine/repcut/prompts_data.py | grep -ohE "$RE" | sort -u | wc -l
0
$ git show ae9209d:engine/repcut/prompts_data.py | grep -ohE 'Wave [0-5]' | sort -u | wc -l
6
```

Checked against **every version of that file that has ever existed** — there is
only one, `f317440` on `project-process-dashboard` — the shipped regex matches
it **0 times**. The 6 is what a bare `Wave [0-5]` finds: a different, looser
pattern than the one in the code.

That is the failure in its purest form. Not an untested guard — a guard whose
**evidence was for a different guard**. The measurement was real; it was taken
against something other than what shipped, written down beside it, and read as
proof for nineteen days.

(The comment's "15 prompt entries" is also wrong — there are 14. A small thing,
but it is the same tell: prose describing code that nobody re-derived from the
code.)

Both copies are now the one shared implementation, tested against the real file.

## Proposed change

**1. Add to CLAUDE.md, *Build plan*, and to `git-and-ci.md`'s never-commit
list:**

> The plan may not be transcribed into the repository **in any form** — not as
> data, not in a fixture, not in a test, not in a docstring example, not as
> prose in a comment. Prompt titles, summaries, deliverables, wave structure and
> calendar estimates are the plan. Code that needs them reads them at runtime
> from `REPCUT_GUIDE_PATH`.

**2. The dashboard reads the plan at runtime.** `prompts_data.py` keeps the
Pydantic models and gains a parser — the mechanism is ours to publish — and
loads the prompt list from the gitignored guide. `prompt_tracker.py` adds only
what the filesystem can answer. `ui/lib/prompts.ts` and the `/prompts` page
render whatever the engine returns and hold no plan content.

**3. Absence of the guide is a supported state, not an error.** A clone without
it gets a working engine and a dashboard that says the build plan is not
available. `/prompts` still answers 200 with `guide_available: false` and a
named reason, because the UI needs a reachable engine to render the gap — the
same argument `/health` already makes. No reason string ever contains the
guide's path: a path on this machine carries the OS username (`secrets.md`).

**4. Criterion 13 is rewritten** as `scripts/check_plan_leak.py`, which counts
several formatting-independent signals per tracked file — prompt-id-to-title
records, plan-shaped table rows, gate-command lists, calendar estimates carrying
wave or prompt context, report-path indexes — and fails on bulk. The existing
tolerance is kept deliberately: one or two hits is a quotation and stays
allowed, and only families with more than one distinct hit count toward the
combined score.

## What this enforces, and the half that had no check

Section 1 forbids five things by name: **prompt titles, summaries, deliverables,
wave structure and calendar estimates**. Section 4 gives it a check. The two are
not the same size, and stating that here is the whole purpose of this section —
a clause that reads as covered while nothing executes against it is the failure
mode catalogued under *The pattern* below, and leaving it implied would have
made this amendment the fifth entry in its own table.

`check_plan_leak.py` is a **bulk** detector, deliberately. It scores signal
families per file and fails on three distinct hits in one family or six across
families, and the tolerance beneath that is not slack — one or two hits is a
quotation, and a session report citing two gate commands and their two report
paths has to keep passing. Four of the five clauses leak only at that scale;
nobody transcribes a single deliverable. For those four the detector's shape is
the leak's shape, and `prompts_data.py` duly scored five families at once.

**A title does not leak at that scale.** One sits in one sentence, and it reaches
no family at all: `prompt_entries` wants an `id=`/`name=` record, `prompt_rows`
wants a plan-shaped table row, and a title in prose is neither. Criterion 13
passes it and always would have. So does `precommit_guard.sh`, which runs the
same shared implementation and nothing besides.

Not hypothetical. Four tracked files carried a guide prompt title verbatim while
passing criterion 13, the pre-commit guard and gitleaks:

| File | Where the title sat |
|---|---|
| `docs/prompts/run-prompt-02.md` | the kick-off doc's H1 |
| `docs/chat-context.md` | the "what I want from this chat" line |
| `docs/guide-amendments/004-…` | an aside naming which prompt owns orphan GC |
| `.claude/agents/copilot-engineer.md` | the agent's `description` frontmatter |

All four are fixed: the number kept, the title dropped. The numbers are not the
plan and are already throughout the repo.

### The complement — criterion 22

`scripts/check_plan_titles.py`, wired as **criterion 22 of `verify-02`** (it
ships on the `prompt-02` branch, which is where `verify_02.sh` lives). Exact,
case-insensitive, whitespace-normalised phrase matching, and **one hit fails** —
a title has no bulk threshold to reach. It is a second script rather than a sixth
family because the two answer different questions: 13 asks whether a file is
*shaped* like the plan, 22 asks whether a file contains a *string from* it.

Three properties are load-bearing:

- **The titles are never stored in the repo.** A fixed list of them in a tracked
  file would *be* the leak the check exists to prevent — the check would become
  the violation. They are read at runtime from `REPCUT_GUIDE_PATH`, the same
  source the dashboard now reads (section 2). It is also why the titles are
  redacted from the transcript below.
- **No guide means SKIP, not PASS.** Exit code 2 with the reason named. CI has no
  guide and never will, so criterion 22 prints SKIP there — the honest verdict,
  counted apart rather than folded into the denominator (amendment 004 section 3).
- **It was run against real failing input before it was trusted**, per the
  standing consequence recorded below.

The negative control is the three files as they stood before the fix
(`aa91e2d^`), scanned by the finished check:

```
$ python scripts/check_plan_titles.py run-prompt-02.md chat-context.md 004-amendment.md
BUILD PLAN TITLE in 3 file(s), matched against the guide:
  run-prompt-02.md: <the Prompt 02 title>
  chat-context.md:  <the Prompt 02 title>
  004-amendment.md: <the Prompt 12 title>
exit=1
```

The same three files at `aa91e2d` scan clean, and with `REPCUT_GUIDE_PATH`
pointed at a file that does not exist the criterion prints `[SKIP]`, not
`[PASS]`.

### What is still not enforced

Stated plainly, so nobody reads the pair of checks as a wall:

- **Paraphrase defeats both.** Criterion 22 matches the guide's exact wording; a
  title reworded by one word is invisible to it and still nowhere near bulk for
  13. Neither check is a substitute for not retyping the plan.
- **Criterion 22 only runs where the guide is.** Green on the machine that has
  it, SKIP on CI, on a fresh clone, and on any contributor's box. It is a gate a
  person walks through, not a wall a push hits.
- **The pre-commit guard has no title check.** It shares `check_plan_leak.py`
  only. A commit adding a single title is not blocked at commit time; it is
  caught at the next `make verify-02`.

## Consequences

- **The dashboard now shows 13 prompts, not 14.** The guide defines 01–13;
  Prompt 00 exists only by amendment 000 and has no entry in the plan document,
  so it no longer appears. That is the loader being honest about its source. If
  Prompt 00 should appear, the guide is where it gets added.
- **Wave titles and timelines now come from the guide**, so they change when it
  changes instead of drifting against a hardcoded copy.
- **CI has no guide and never will.** `/prompts` returns `guide_available:
  false` there, which is asserted rather than worked around.
- **The negative control is part of the record, not a one-off.** The rewritten
  criterion was run against `prompts_data.py` exactly as it exists on `main`
  today and **fails** it, on five independent signals:

  ```
  $ git show ae9209d:engine/repcut/prompts_data.py > /tmp/prompts_data_MAIN.py
  $ python scripts/check_plan_leak.py /tmp/prompts_data_MAIN.py
  BUILD PLAN TRANSCRIBED into 1 tracked file(s):
    prompts_data_MAIN.py  [gate_commands=14, prompt_entries=14,
                           report_paths=14, timelines=6, wave_titles=1]
  exit=1
  ```

  Note `wave_titles=1` — the only family the old check could see scored one hit,
  below its own threshold of three. That single number is the whole finding.

  `engine/tests/test_plan_leak_guard.py` pins this permanently: it reproduces
  the leaked file's structure with invented content, asserts the guard fails it,
  asserts the fixture carries no wave title (so it cannot pass for the old
  reason), and asserts a session report's four-column criteria table is still
  allowed. A widened regex that would not have caught the real thing is not a
  fix, it is a wider regex.

## The pattern — this is the fourth

Four times now, a check has read as covering something while never executing
against the real case. This is no longer four incidents; it is a failure mode
this project reliably produces, and it belongs on the record as one.

| # | Guard | Read as | Actually |
|---|---|---|---|
| 1 | `warn_if_data_dir_synced()` | DATA_DIR-under-cloud-sync is detected | Its only caller was the FastAPI `lifespan`, and nothing in the project opens a lifespan scope. Detection worked; it never ran. |
| 2 | Criterion 10's `any` clause | `zero any in ui/**/*.{ts,tsx}` is enforced | The gate ran the four commands and never checked the second half of its own criterion. `tsc --strict` rejects an *implicit* any and says nothing about a written one. |
| 3 | Criterion 19 | `make dev` works | The gate spawned the launcher through a resolver that rejects WSL; the Makefile spawned `bash` by bare name, which on a PowerShell PATH is WSL. Gate and human were running it on different machines. |
| 4 | Criterion 13 | The plan is not transcribed | Matched wave titles in the guide's formatting. The leak stored no wave title, so it matched zero times. |

The common shape: **the check was written against the thing it was defending,
as the author imagined it, and was never executed against a real instance of
the thing it was defending against.** Each printed PASS, and each PASS read as
evidence that the path had been checked — which is strictly worse than having no
check, because it stops anyone looking.

The standing consequence, and the thing to actually do:

> **A guard is not finished until it has been run against a real failing input
> and observed to fail.** Construct the failing case, watch the check reject it,
> and keep that case as a test. If the failing case cannot be constructed, the
> guard cannot be trusted and the report says so.

This is the same rule as *"every prompt owes at least one criterion that starts
the product the way a person starts it"* (`docs/reports/prompt-02.md`, Open
issues), pointed at guards rather than at gates. Both come from the same root:
verifying the mechanism instead of the outcome.

## Principle check

**P4 (privacy & honesty)** — the reason this matters. Publishing the plan is not
a P4 breach in the footage sense, but P4's honesty half is engaged: the
dashboard claimed a reference it should not have been carrying. Error strings
are fixed sentences and never carry the guide path, which would carry the OS
username.

**P5 (€0)** — unaffected. Nothing added, nothing paid.

**P1, P2, P3** — untouched. No change to what the editor produces, to
overridability, or to taste logging.

**Not a `secrets.md` incident.** No credential was exposed, so there is nothing
to revoke or rotate and the stop-everything procedure does not apply. Recorded
here rather than there deliberately: the two failure modes need different
responses, and conflating them would make the secrets procedure noisier without
making this one safer.
