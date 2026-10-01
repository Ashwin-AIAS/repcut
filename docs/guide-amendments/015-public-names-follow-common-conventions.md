# Amendment 015 — public names follow common conventions; prompt numbers stay internal
Date: 2026-10-01
Affects: every prompt from Prompt 05 on; CLAUDE.md (Git & CI contract);
`.claude/rules/git-and-ci.md` (Branching, Commits, CI); `/run-prompt`,
`/gate`, `/checkpoint`; `.github/workflows/tag-gate.yml`;
`.github/pull_request_template.md`
Status: PROPOSED

Requested by Ashwin on 2026-10-01. Prompt 04 finishes under the current names;
this applies from the next branch onward.

## What the guide says

`.claude/rules/git-and-ci.md`, *Branching* and *Commits*:

> One prompt = one branch: `prompt-NN`. Created by `/run-prompt NN`.

> After merge: tag `prompt-NN-done`.

> Format: `prompt-NN: <imperative summary>`. Body explains *why* when non-obvious.

CLAUDE.md, *Git & CI contract*:

> One prompt = one branch `prompt-NN`. Push freely to that branch.
> … session report written. Then tag `prompt-NN-done`.

## What we found

The repository is public, and branch names, tags, commit subjects and PR
titles are what a visitor sees first. All four carry the internal
bookkeeping unit: `prompt-04`, `prompt-03-done`, `prompt-04: …`. Ashwin finds
that unprofessional for a public portfolio repo. The repo's side branches
already use the common form (`chore/…`, `fix/…`, `docs/…`), so the main work
branches are the exception, not the rule.

Two constraints decide what the replacement can be:

- **Not the guide's titles.** Amendment 006: prompt titles *are* the plan.
  Naming a branch after a prompt's title would publish that title on GitHub,
  where it stays in PR history after the branch is deleted. The plan guard
  (`check_plan_leak.py`) scans tracked files, not ref names, and cannot catch a
  single title anyway.
- **The tooling keys on the number.** `/run-prompt` checks the previous tag,
  `/gate` diffs `main...prompt-NN` and tags `prompt-NN-done`, and
  `tag-gate.yml` derives the report path from the tag.

## Why the current rule doesn't work

It doesn't break anything; it is a presentation choice the human has made
about his own public repository. The current names expose the internal
process vocabulary on every public surface. The proposal keeps that
vocabulary internal, where it is useful.

## Proposed change

**Branches.** One prompt = one branch named `feat/<slug>`. The slug is 2–4
lowercase words joined by hyphens, **in our own words, never the guide's
prompt title or a paraphrase close enough to read as it**. `/run-prompt`
proposes the slug in its plan, and Ashwin approves it with the plan. That
approval is the check, because no automated guard can catch a single title:
criterion 13 detects the plan in bulk, and criterion 22 checks tracked files
only. The branch name is recorded in the report
header (`Branch: feat/<slug>`), and `/gate NN` reads it from there instead of
deriving it from NN.

**Commits.** Conventional Commits: `<type>(<scope>): <imperative summary>`.
The type is one of `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`,
`chore`. The scope is optional and short (`engine`, `ui`, `gemini`, `colour`,
`gate`). No prompt number in the subject. The body still explains *why* when
non-obvious.

**Tags.** After merge, tag `v0.N.0`, where N is the prompt number without
padding (Prompt 05 → `v0.5.0`, Prompt 13 → `v0.13.0`). A tag is a gated
milestone, not a GitHub Release; nothing is published with it. `1.0.0` is
left for Ashwin to decide.

**PR titles.** Plain English in our own words, under the same title rule as
branch slugs. The PR template links the report (`docs/reports/prompt-NN.md`)
instead of reading "Closes prompt-NN. Wave: N" — the wave line names plan
structure on a public page and is dropped.

**Unchanged (internal).** `docs/reports/prompt-NN.md`, `make verify-NN`,
`/run-prompt NN`, `/gate NN`, the UI's prompts page, and the prompt numbering
in every rule and skill. Existing `prompt-NN` branches, `prompt-NN-done` tags
and commit history stay as they are: rewriting published history costs more
than it buys.

**Transition.**
- `tag-gate.yml` triggers on both `prompt-*-done` and `v0.*.0`, and maps
  `v0.N.0` to `docs/reports/prompt-0N.md` (zero-padded to two digits).
- `/run-prompt NN` accepts either `prompt-(NN-1)-done` or `v0.(NN-1).0` as the
  previous prompt's gate. Prompt 05 checks `prompt-04-done`, and Prompt 06
  onward checks `v0.N.0`.
- `git-and-ci.md`, CLAUDE.md's Git & CI contract, `/checkpoint`, `/gate`,
  `/run-prompt` and the PR template are rewritten to match in one commit.

## Consequences

- No gate is invalidated. verify-03 and verify-04 reference
  `prompt-02-done` and `prompt-03-done`, which keep existing; a later gate
  that needs the previous prompt's tag reads whichever name that prompt was
  tagged with.
- Only prompt titles are kept off GitHub. Prompt numbers still appear in
  report filenames and in `v0.N.0`; Ashwin chose that scope (branches, tags
  and commits) over also renaming internal docs.
- One more human step at kick-off: approving the branch slug. It comes with
  the plan approval that `/run-prompt` already waits for.

## Principle check

None of P1–P5 is touched. This only changes names. It strengthens the
amendment 006 boundary (no plan titles in public refs), and it adds no
service and no cost.
