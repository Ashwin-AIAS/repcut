# Prompt 04 — colour baseline and grading engine
Branch: prompt-04 · Gate: Phase A at STOP A (Phase B not started) · Date: 2026-09-27, updated 2026-09-28

Draft at STOP A. Phase B, the operator choice and every STOP B override are
added when they happen. The 2026-09-28 update covers the CI failure on the
draft PR, amendment 013 and the gate-script audit; see "Session 2026-09-28".

## Built (Phase A)
- `ffmpeg_builder.normalise_to_sdr` + `NormalisationRecipe` (media/artifacts.py):
  one stage, one recipe object, held by both the proxy and the frame recipe.
- Proxy v2: HDR normalised to bt709 SDR, **short** side capped at 720
  (`proxy_dimensions`). `PARAMS_VERSION[proxy]` 1→2, `SCENE_PARAMS_VERSION` 1→2,
  one commit. (That commit left the frame argv byte-identical; the frame moved
  later, in `a3b9ae9` and again on 2026-09-28. Current versions: see
  "Version arithmetic".)
- `repcut/fingerprints.py`: argv digests pinned per version; the scene digest
  contains the proxy's; each Gemini prompt version is pinned to the frame
  digest it was asked about (closes open issue 12 as a test).
- `repcut/freshness.py` + `POST /media/{id}/ensure-current`, called by the UI on
  clip selection: lazy regeneration, nothing enqueued while a job for the clip
  is active, never a startup sweep.
- `scripts/verify_04.sh` criteria 1–8; `scripts/baseline_review.py`.

## Decisions made autonomously
- **Criterion 1's tolerance was fixed before the fixture was measured**: v2
  error ≤ 0.30 × v1 error AND ≤ 20 codes (mean abs RGB error, 0–255). Before
  that, a first fixture with SDR white at 100 cd/m² had been measured; the
  rule was then written down, and the fixture was changed on principle to
  BT.2408 (SDR white at 203 cd/m² in HLG, what phone HLG does), not to reach
  a result. Criterion 6's tolerances (saturation 15, black 8) were set with
  those offline numbers in view.
- **Criterion 1 fails under the current operator, and I left it failing.** On
  the BT.2408 fixture: v1 74.2; hable 33.4 (0.45); mobius 13.8 (0.19);
  reinhard 18.7 (0.25); clip 6.6 (0.09). The fixture has no highlights above
  diffuse white, which penalises hable's headroom. Picking the operator is
  STOP A's decision, so neither the operator nor the rule was changed.
- **The review page is `$DATA_DIR/reviews/prompt-04/baseline.html`, not
  `docs/reviews/`.** The stills are real footage and the repo is in a
  OneDrive-redirected folder; amendment 004 keeps `$DATA_DIR` outside sync for
  exactly this. The script refuses to write if `$DATA_DIR` is in a sync root.
  No pointer file is written into the repo: the first draft wrote one holding
  an absolute `file://` URI (username, `secrets.md`); it was deleted before
  anyone opened it and replaced, at STOP A, by stills named relative to the
  page, `$DATA_DIR` printed literally in the terminal, and a check that
  refuses to write a page containing any absolute path.
- **Existing library held at v1 until the operator is named** (Ashwin, STOP A):
  any pick but hable bumps proxy and scene again, and regenerating twice wastes
  the time. Opening an existing clip in the UI on this branch would regenerate
  it, so the fresh clips go into a new project.
- A duplicate upload of a **stale** clip enqueues regeneration (as Prompt 02's
  upload path always did for missing artifacts); of a current clip, zero jobs.
- A failed regeneration job is retried on the next open, not suppressed; each
  attempt is visible in the jobs panel.

## Bugs fixed in earlier prompts' code
- **No gate's browser had ever loaded video data.** `cdp_browser` opened its
  page in a background tab (`visibilityState: hidden`), and Chrome defers media
  on hidden pages: every `<video>` sat at readyState 0. verify-02/03 checked
  the DOM, not playback, so this was invisible. Fixed with `Page.bringToFront`.
  **Every earlier "the player works" claim therefore rested on the human
  checks alone** (verify-02 and verify-03's manual checklists); no automated
  criterion had ever seen a frame decode.
- `uploads._artifacts_complete` matched any kind at any current version number;
  the moved check matches each kind to its own version.

## Assumed
- "Playwright" in the kick-off read as "a real browser against `make dev`"
  (approved): `cdp_browser.py` with installed Chrome.
- HLG fixture: HEVC Main 10 via libx265 (present on FFmpeg 8.1 here; x264
  High 10 fallback coded).
- Baseline candidates: six CPU `tonemap` operators at npl 100, plus hable and
  mobius at npl 203.
- Timestamps on the review page: 20/50/80 % of the shorter clip of a pair.

## Deviations from the guide
- The colour baseline phase, proxy v2 and the thirteen collisions:
  `docs/guide-amendments/012-prompt-04-colour-baseline-and-grading.md` (ACCEPTED).
- Every colour conversion stated in the filter graph, replacing 012's "nothing
  for SDR", with the FFmpeg floor measured at 6.1:
  `docs/guide-amendments/013-every-colour-conversion-stated.md` (PROPOSED).

## Open questions for the human
- Operator (STOP A box). If it is not hable, four versions bump in one commit:
  proxy 3→4, scene 3→4, frame 3→4, Gemini prompt 4→5 (see "Version arithmetic").
- Amendment 013 is PROPOSED. Recommendation: accept.
- Criterion 1 is kept exactly as written. If the chosen operator fails it,
  the next step is a proposed amendment with reasoning, never a changed rule.

## Open issues
- **verify-03 criterion 17 makes live Gemini calls — owned by Prompt 12.** Its
  `make dev` stack inherits the developer's key, and the criterion needs a real
  send to see the disclosure step. Synthetic frames only, so no P4 harm, but it
  spends quota and breaks "zero live calls". Prompt 12 puts the one live smoke
  test behind an opt-in env flag. verify-03 is unchanged in this prompt;
  verify-04's own stack runs with Gemini off.

## Gate status (Phase A, 2026-09-27 - superseded by the 2026-09-28 table below)
| # | Criterion | Result |
|---|---|---|
| 1 | proxy colour from the file | FAIL under hable only — tags bt709/bt709/bt709 tv yuv420p, luma 101.6 in band, SDR luma v1 125.7 = v2 125.7; error ratio 0.45 |
| 2 | one normalisation | PASS — shared object, calls [tv, pc], operator change moves both argvs |
| 3 | short-side cap | PASS — portrait 720x1280, landscape 1280x720, small 640x360 |
| 4 | versions move together | PASS — proxy 1→2, scene 1→2; negative controls 2 and 1 problems |
| 5 | stale regeneration | PASS — jobs per open 2, 0, 0; v1 file kept; scenes [1, 2]; duplicate +0 |
| 6 | a browser sees it | PASS — HDR sat 244.7 / black 16 vs SDR 251.1 / 18; both played 1.2 s |
| 7 | verify-03 green | NOT RUN to completion — the full `make verify-04` run was stopped by the host for low memory during this criterion. Ashwin runs `make verify-03` from Git Bash |
| 8 | [HUMAN] Phase A boxes | FAIL until signed |

`make test-gpu`: not applicable (no GPU code; torch deferred, amendment 003).

## Session 2026-09-28 — CI failure, amendment 013, gate-script audit

### The CI failure: the instrument, not the frame
Draft PR, "ci / Engine (Python)": 4 failed, 471 passed. All four were
`test_sampled_frame_decodes_to_the_sources_colours_as_jfif_says`. Reproduced
bit-for-bit on an FFmpeg 6.1.1 build (the same numbers as CI's log). Diagnosed
before changing anything:

- The suspicion (CI's older FFmpeg does not convert the matrix) was **ruled out
  for this failure**. Frames produced by 6.1.1 and read in their native pixel
  format were 1.1-3.1 codes from truth, the same as 8.1's.
- The failing piece was the test's `_ycbcr_planes`, which claimed to read
  "samples as stored" but asked FFmpeg for `yuv444p`. 6.1 decodes a JPEG as
  `yuvj420p` and treats `yuvj420p -> yuv444p` as full-to-limited range, so
  every JPEG, including one made by 8.1, read 16 codes toward grey. Grey itself
  decoded exactly, which is the signature of range, not matrix. Fixed: read in
  the stream's own pixel format, no scaler involved.

### What checking the suspicion found: proxy v2 depended on the FFmpeg version
The claim quoted in the brief ("FFmpeg 8 converts the matrix when output
requests bt709") was about the **proxy**, and it was true, which was the
defect. v2 had no colour filter for SDR and relied on the CLI converting toward
`-colorspace bt709 -color_range tv`. 7.1+ converts; 6.1 only relabels. No test
covered it, so CI stayed green on it. BT.601 bars, one argv:

| Proxy source | 6.1.1 before | 8.1 before | 6.1.1 after | 8.1 after |
|---|---|---|---|---|
| bt709 / tv | 0.6 | 0.6 | 0.6 | 0.6 |
| smpte170m / tv | **30.0** | 3.8 | 1.8 | 1.8 |
| bt709 / pc | 2.0 | 1.5 | 2.0 | 2.0 |

Fixed in the code, not by pinning CI's FFmpeg (amendment 013, PROPOSED):
`ffmpeg_builder.convert_colour` is one `zscale` stating matrix, range,
primaries and transfer for input and output. Every proxy, every frame and every
stage of the HDR chain goes through it; tags reach the graph only through an
allow-list (ffprobe output is input, `security.md`). The JFIF stage moved from
swscale to zimg too: frames are now 1.1-1.8 from truth on both versions (were
2.4-3.8), and zimg gives identical numbers across versions where swscale did
not.

**STOP A's look is unchanged.** On 8.1 the new proxies are byte-identical to
v2's for the real HLG clip, the synthetic HLG fixture and a tagged bt709 clip.
Frames differ by a mean of 0.56-0.77 codes (max 15, at chroma edges).

**A bug I introduced and caught before commit:** the first version read an
untagged range as the *target's* range, which for the JPEG is full: untagged
video came back 16.4 codes off. Untagged range is now always limited, and the
allow-list test pins it.

New tests, each checked against the defect it guards:
- `test_the_proxy_decodes_to_the_sources_colours_as_its_tags_say`. Negative
  control, v2's behaviour emulated: the BT.601 case fails on 6.1.1 and passes
  on 8.1, which is exactly the version dependence.
- `test_a_tag_reaches_the_graph_only_through_the_allow_list`, including an
  injection-shaped tag.

### FFmpeg versions each run used
| Where | FFmpeg | Engine suite |
|---|---|---|
| CI (Ubuntu 24.04 apt) | 6.1.1-3ubuntu5 | before: 4 failed, 471 passed. After (run 36417773937, first run on the new commits): **488 passed, 2 skipped** (the Windows-only loop tests), all 7 PR checks green |
| This laptop | 8.1 (gyan.dev full) | **490 passed** |
| This laptop, CI's release | 6.1.1 (gyan.dev essentials) | **490 passed** |
| This laptop | 5.1 | 487 passed, 3 failed: every rotation test (fixture) |
| This laptop | 5.0.1 | builder subset: 6 failed, `Unrecognized option 'fps_mode'` |

**Minimum FFmpeg: 6.1**, in `scripts/check_env.py`, as the oldest release the
whole suite has passed on. I first wrote 5.1, from the release notes
(`-fps_mode`). Running the suite on 5.1 proved that wrong: the rotation
fixture's `-display_rotation` arrived in 6.0, so rotation was never verified
below it. check_env also requires the `zscale` and `tonemap` filters, because
every proxy, SDR included, now needs libzimg. Colour no longer sets the floor.
Amendment 013 carries a correction section rather than an edit.

One more version-dependent instrument turned up: the loudness-fixture test
trimmed with output-side `-ss/-to`, which the Windows 6.1.1 build applies
differently (quiet window -26.7 dB vs -35.6 dB from 8.1 on the same file).
Ubuntu's 6.1.1 did not show it. It now trims input-side, as
`build_audio_energy_probe` does.

### Version arithmetic
| | shipped by Prompt 03 | `7f972f4` | `a3b9ae9` | now |
|---|---|---|---|---|
| `PARAMS_VERSION[proxy]` | 1 | 2 | 2 | **3** |
| `SCENE_PARAMS_VERSION` | 1 | 2 | 2 | **3** |
| `FRAME_PARAMS_VERSION` | 1 | 1 | 2 | **3** |
| `GEMINI_PROMPT_VERSION` | 2 | 2 | 3 | **4** |

- The real library was never regenerated past proxy 1 / scene 1 (held at v1
  until STOP A names the operator). Versions 2 and 3 exist only in test
  fixtures, so these bumps cost the library **nothing extra**: one
  regeneration and one Gemini re-send per scene (13 scenes today, plus the 3
  HDR clips never analysed), whenever it happens.
- An operator other than hable at STOP A moves all four again (4, 4, 4, 5).
  It still costs one regeneration, because the library skips from 1 straight
  to whatever is current.
- Pins in `repcut/fingerprints.py` for proxy 3, scene 3, frame 3 and Gemini 4
  digest four colour branches. Earlier pins digested two, so they are history
  and cannot be recomputed; the module says so.
- Every gate check derives neighbouring versions from the live constant
  (`now - 1`, `now + 1`), never a literal, so none of them needed editing.

### Swallowed-exception audit of the gate scripts
Scope: every `except`, every `suppress()`, and the shell equivalents (`|| true`,
`2>/dev/null`) in `scripts/`. There is no bare `except:` and no
`except Exception`. Named catches were audited by what their body does. Fixed,
each with a test or a negative control:

1. **verify-03/04: exit 2 without a reason counted as SKIP, and the gate still
   exited 0.** Python exits 2 when it cannot open the checks script, and the
   checks modules returned 2 for an unknown criterion name. Negative control
   on HEAD: `[SKIP] (no reason reported)`; now `[FAIL]`. Usage errors return 1.
2. **Plan guards (verify-01 criteria 13 and 22), `UnicodeDecodeError -> skip`.**
   One cp1252 byte made a transcription read as clean. Now decoded with
   replacement.
3. **Plan guards, `OSError -> skip`.** A file the guard could not open was
   certified. Now `CANNOT CERTIFY <file>` and exit 1.
4. **Title guard (criterion 22) skipped files over 2 MB**, the hole the leak
   guard had closed for itself. Removed.
   (2-4: five new tests in `test_plan_leak_guard.py`; all five fail on HEAD's
   guards.)
5. **verify-01 #11 and verify-02 #15, "nothing forbidden tracked": `git
   ls-files 2>/dev/null | grep`.** A git that refuses the repo (safe.directory
   on a synced folder) listed nothing, which read as "no footage, no .env
   tracked". Now checked, and a failure is a FAIL.
6. **verify-00 #6 credential scan: `grep … || true`** merged "no match" with
   "could not scan". Only grep's exit 1 counts as clean now.
7. **verify-00 CRLF check and verify-01 print check**: a crash printed nothing,
   which read as clean. Unreadable or unparseable files were skipped. Both now
   check the exit status and list unreadable files as failures.
8. **verify-01 #8 (`any` in UI)**: grep exit 2 read as 0 hits. Fixed as in 6.
9. **`make secrets`: `gitleaks protect --staged … || true`**, found at
   checkpoint. A staged secret could never fail the target (`detect` scans
   history, not the index). The `|| true` is removed, which strengthens the
   gate. The standalone `gitleaks` is not on this shell's PATH, so the
   checkpoint scan used pre-commit's cached binary: `detect` found 142 commits
   and no leaks; `protect --staged`, no leaks.

Left as they are, and why: poll-loop retries (`cdp_browser`, `dev_stack`,
`posix_shell`), whose timeouts are enforced by the loop; psutil
`NoSuchProcess` (a gone process is not a survivor); the checkbox counts
(`${unticked:-1}` makes an error a FAIL); the axe loop (an error counts as
uncovered).

**Not fixed; a decision for you:** a gate exits 0 when criteria SKIP
(`PASSED: 5 of 5 (3 skipped)`). `testing.md` says gates are binary, and
`/gate` needs every criterion PASS, so a green exit code with skips is weaker
than it looks. Making a SKIP fail would make verify-03 red wherever criterion
16 has no console, and so verify-04 criterion 7 too. Recommendation: exit 1 on
any SKIP, with an explicit `ALLOW_SKIP=16` for the one known-unrunnable case.

### Exit-code evidence for verify-04
`make verify-04` from Git Bash (GNU Make 3.81, FFmpeg 8.1), 2026-09-28, nothing
else running:
- the script printed `FAILED: 2 of 8 criteria`, and make printed
  `make: *** [verify-04] Error 1`. The recipe's status is **1, not 130**;
- `make`'s own exit status was **2**. That is GNU Make's convention for a
  failed recipe, not a pass-through;
- criterion 7 ran all of verify-03 inside it: `PASSED: 19 of 19 (1 skipped)`,
  exit 0. verify-04 does not print its sub-gate's skip reason. It is presumably
  criterion 16 (no real console from this shell), but this run did not show it.
A green verify-04 cannot be shown yet: criteria 1 and 8 are STOP A's.

### Gate status after this session (verify-04, Phase A)
| # | Criterion | Result |
|---|---|---|
| 1 | proxy colour from the file | FAIL under hable only. Identical to 2026-09-27: v1 74.2, v2 33.4, ratio 0.45, luma 101.6 (the HDR proxy is byte-identical) |
| 2 | one normalisation | PASS. Calls [tv, pc]; an operator change moves both argvs |
| 3 | short-side cap | PASS. 720x1280, 1280x720, 640x360 |
| 4 | versions move together | PASS. proxy 1→3, scene 1→3; negative controls 2 and 1 problems |
| 5 | stale regeneration | PASS. Jobs per open 2, 0, 0; scenes [2, 3]; duplicate +0 |
| 6 | a browser sees it | PASS. HDR sat 244.7 / black 16 vs SDR 251.1 / 18; both played |
| 7 | verify-03 green | **PASS**, 19 of 19 (1 skipped). NOT RUN yesterday |
| 8 | [HUMAN] Phase A boxes | FAIL. 0 of 6 ticked |

CI on the PR after this session: all 7 checks green, on the first run of the
new commits. `make test-gpu`: not applicable (no GPU code).

## Real library, before any regeneration
9 clips; 13 scenes under detector v1 will be re-detected and re-sent on first
open; 3 HDR clips (96 s) were never analysed. `GEMINI_DAILY_LIMIT` 1400, RPM 10.
Nothing has been regenerated.

## Risks / known gaps
- A clip opened while the engine runs an older branch regenerates under that
  branch's versions; harmless (new keys), but spends a re-send.
- The v1 proxy renderer survives only in the gate, as criterion 1's yardstick.
