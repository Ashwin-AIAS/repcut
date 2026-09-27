# Amendment 011 — HDR verification moves from Prompt 03's manual check to Prompt 04's taste gate

Date: 2026-09-19
Affects: `docs/manual-checks/prompt-03.md` (box 1's HEVC/HDR clause, box 4),
new `docs/manual-checks/prompt-04.md`, `docs/reports/prompt-03.md`
Status: ACCEPTED

## What the manual check asked for

`docs/manual-checks/prompt-03.md` criterion 19 asked the human to analyse
"3+ real gym clips, at least one HEVC/HDR, at least one VFR" (box 1), and to
judge, for the HDR clip specifically, that "the sampled frame looks like the
footage — not washed out, not soft" (box 4).

## What we found

The local real-footage test library cannot sign either box today.

Exactly one clip in it is usable HDR: HEVC Main 10, 3840x2160, BT.2020
primaries, HLG transfer, ~72s. The only other two clips in that folder have no
`moov` atom at all — `ffprobe` refuses both outright. Confirmed genuinely truncated files — checked and ruled out as cloud-sync
placeholder stubs standing in for un-downloaded media, which is a real failure
mode on this machine's storage setup — worth recording plainly so nobody
spends another hour rediscovering it.

Every other clip in the library came in through WhatsApp: h264 Baseline,
bt709, no HDR tagging at all. That rules them out for box 1's HDR clause on
its own terms. It also makes box 4 **unfalsifiable** on them two different
ways at once: with no HDR source there is no tone-map for box 4 to judge in
the first place, and several of these clips are 1024x576 — under the proxy
recipe's 720px height ceiling — so the proxy and the source are pixel-identical
and criterion 2's whole premise (source vs. proxy dimension divergence) cannot
even be exercised, let alone fail, on that clip.

Net effect: one real HDR clip exists, it is the only one that could ever sign
these two boxes, and nothing about its condition is a footage problem this
prompt can fix — it is a coverage gap in the test library, and the honest
response is to say so rather than sign against a single, un-replicated sample
under time pressure.

## Decision

Move box 1's HDR clause and all of box 4 out of Prompt 03's manual check and
into a new `docs/manual-checks/prompt-04.md`, to be judged as part of that
prompt's own HUMAN REVIEW checkpoint.

This is not a convenience move and should not be read as one. Prompt 04 is the
colour-grading prompt — it is already a HUMAN REVIEW checkpoint by design, a
human already has to look at HDR colour there regardless of this amendment,
and `docs/future-prompts/prompt-04-colour-baseline.md` already stands as an
open finding Prompt 04 must resolve before its own grading work can start
(the proxy's HDR conversion is currently incoherent — bt709 matrix tagged onto
untouched bt2020/HLG primaries and transfer, per that document). Moving HDR
verification there puts it where the relevant expertise, the open finding, and
the footage requirement already converge, instead of asking Prompt 03's gate
to adjudicate a colour-grading question with a sample size of one.

Prompt 03's **automated** criteria are unaffected and stay exactly as strict:
criterion 2 (sampled frame matches the source's display dimensions, not the
proxy's) and criterion 11 (tone-map applied, measured mean luma) both still run
against the synthetic HDR fixture and both still gate the merge. What moves is
only the human confirmation that a *real* HDR clip's extracted frame looks
right to the eye — a check the automated fixture cannot stand in for, and that
this session cannot honestly discharge either, for lack of a second sample.

## Consequences — stated plainly, including the cost

**Prompt 03 ships without anyone having looked at a tone-mapped frame from
real HDR footage.** That is a real reduction in what this prompt verified, not
a wash. If the tone-map extraction is subtly wrong — a color-space edge case
the synthetic fixture's simple BT.2020/HLG tag doesn't exercise — nothing in
Prompt 03's gate will have caught it, and the first human ever to look at real
tone-mapped HDR output will be doing so during Prompt 04's grading work, not
before it.

Prompt 04's gate must therefore treat these two migrated boxes as **blocking**
checks, not a nice-to-have carried over for completeness — if either fails
there, it is simultaneously a Prompt 04 colour finding and a live report that
Prompt 03's frame extraction shipped a real defect after being gated green.
`docs/manual-checks/prompt-04.md` states this explicitly so the file does not
read as routine.

## Principle check

**P1–P5** — no principle is touched by relocating *when* a human looks at
already-produced output; P4 (only sampled frames leave the machine, disclosed
in the UI) and P5 (€0) are unaffected since no new data leaves the machine and
no new capability is added. This amendment is a testing/process change, not a
design change.
