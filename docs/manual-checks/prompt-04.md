# Prompt 04 — real-footage manual check

Prompt 04 is a HUMAN REVIEW taste checkpoint (colour grading) in its own
right, and its gate should treat this file the same way Prompt 03's gate
treats `docs/manual-checks/prompt-03.md`: it exits non-zero while any box
below is unticked, and it never passes on its own.

## Why this file exists on day one of Prompt 04

Two boxes below did not originate here. They were written for Prompt 03's
manual check and moved by
`docs/guide-amendments/011-hdr-check-moves-to-prompt-04.md`: the only usable
real HDR clip available at the time was a single sample, so Prompt 03 could
not honestly sign either "at least one real HEVC/HDR clip analysed" or "the
sampled frame looks like the footage, not washed out" — one un-replicated
sample under time pressure is a rubber stamp, not a check.

They land here rather than being dropped, because Prompt 04 is already the
right place for them: it is the colour prompt, it is already a HUMAN REVIEW
checkpoint, and `docs/future-prompts/prompt-04-colour-baseline.md` already
stands as an open finding this prompt must resolve regardless — the proxy's
current HDR conversion is incoherent (bt709 matrix tagged onto untouched
bt2020/HLG primaries and transfer) and renders flat and desaturated. A human
has to look at real HDR colour here no matter what; moving the verification
here puts it where the expertise, the open finding, and the footage
requirement already converge.

**These two boxes are blocking, not carried-over nice-to-haves.** Prompt 03
shipped without anyone having seen a tone-mapped frame from real HDR footage.
If Prompt 03's frame extraction is subtly wrong, this checklist — specifically
box 2 below — is the first place that would show it, and a failure here is
simultaneously a Prompt 04 colour finding and a report that Prompt 03's
already-merged frame extraction has a real defect. Do not wave it through.

**Nothing here goes in the repository except the verdicts.** Do not attach
clips, screenshots or file paths — a path on this machine contains the OS
username (`.claude/rules/secrets.md`).

## Checklist

Two phases, one file (amendment 012). `make verify-04` criterion 8 reads the
Phase A section alone; criterion 18 reads every box in the file.

### Phase A — signed before any grading code exists

- [ ] Fresh HDR clips shot with HDR-off twins; ffprobe confirms bt2020 + HLG/PQ on the HDR ones and bt709 on the twins
- [ ] Tone-map operator chosen from `baseline.html`: ________
- [ ] In the browser, the proxy matches its HDR-off twin: blacks not milky, skin not grey or orange, bright lights roll off instead of clipping to flat patches
- [ ] A portrait clip's proxy is 720 wide
- [ ] The sampled/extracted frame from that clip looks like the footage —
      not washed out, not soft
- [ ] Phase A signed off by: ________  Date: ________

### Phase B — signed at STOP B

- [ ] At least one real HEVC/HDR clip graded (HLG or PQ transfer, BT.2020
      primaries — not a colour tag on an SDR test pattern)
- [ ] The correction-only theme reads as the footage with the lights fixed, and nothing more
- [ ] One theme holds across the dim and the bright setups — one video, not two looks
- [ ] Judged on the phone screen, not only the laptop
- [ ] Grain judged on a full-resolution still, not on the proxy
- [ ] The guide's human criterion for Prompt 04, answered honestly
- [ ] Phase B signed off by: ________  Date: ________

## What to look for, per box

**HDR clip coverage.** Confirm the clip actually is HDR before judging
anything else against it — check its tagged primaries/transfer (`ffprobe`, or
whatever this prompt's tooling surfaces), not just that it "looks cinematic."
A WhatsApp-recompressed or otherwise SDR clip cannot stand in for this box no
matter how it was shot.

**The operator.** `docs/reviews/prompt-04/baseline.html` shows each HDR clip
beside its HDR-off twin under every candidate, with numbers. The twin is the
phone's own SDR rendering of the same scene — judge against it, not against a
memory of what the gym looked like. Write the chosen name on the line; it
becomes one constant, and a change from the current one bumps four versions.

**Frame quality.** This is the frame the pipeline actually extracted and
graded from. It should look like a normal, correctly exposed photo — not the
washed-out, desaturated result an un-tone-mapped or incoherently-tagged HDR
extract produces. `docs/reports/prompt-02.md`'s proxy-recipe finding
(bt709 matrix silently applied over untouched bt2020/HLG primaries and
transfer) is exactly the failure mode this box exists to catch; Prompt 03's
frame extraction has its own, separate tone-map path (amendment 008,
resolution 3) that this box verifies for the first time against real footage.
If it looks wrong, treat it as a live defect report against Prompt 03's
shipped code, not a Prompt 04 taste note.
