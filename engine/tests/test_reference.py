"""Reference matching, pure parts: look extraction, rhythm, captions, saved themes."""

from pathlib import Path

import numpy as np
import pytest

from repcut.captions.styles import styles
from repcut.edit.planner import SLOWMO_EVERY_SECONDS
from repcut.grading import saved
from repcut.grading.lut import apply_look
from repcut.grading.packs import PackLibrary, UnsafeGradingPathError
from repcut.grading.theme import Look, PaceSpec, load_theme
from repcut.reference import captions as cap
from repcut.reference import look as lk
from repcut.reference import rhythm as rh
from repcut.reference.service import theme_for

# What "within tolerance of the theme" means for each look parameter. Wide
# enough for sampling noise between two different pixel sets, narrow enough
# that a neighbouring theme (Hardstyle vs Moody, say) is outside it.
TOLERANCE = {
    "contrast": 0.1,
    "black_lift": 0.03,
    "black_crush": 0.025,
    "highlight_rolloff": 0.06,
    "saturation": 0.06,
    "warmth": 0.008,
}
SPLIT_HUE_DEGREES = 25.0
SPLIT_STRENGTH = 0.02


def _within(fit: Look, want: Look) -> list[str]:
    misses = [
        name
        for name, tol in TOLERANCE.items()
        if abs(getattr(fit, name) - getattr(want, name)) > tol
    ]
    for name in ("shadows", "highlights"):
        a, b = getattr(fit, name), getattr(want, name)
        sa = a.strength if a else 0.0
        sb = b.strength if b else 0.0
        if abs(sa - sb) > SPLIT_STRENGTH:
            misses.append(f"{name}.strength")
        elif a and b and sb >= SPLIT_STRENGTH:
            if abs((a.hue - b.hue + 180) % 360 - 180) > SPLIT_HUE_DEGREES:
                misses.append(f"{name}.hue")
    return misses


@pytest.mark.parametrize("theme_id", ["moody", "hardstyle", "aesthetic", "old-school", "raw-plus"])
def test_a_built_in_look_round_trips_through_extraction(theme_id: str) -> None:
    want = PackLibrary().theme("gym", theme_id).look
    graded = apply_look(lk.neutral_baseline(seed=11), want)
    fit = lk.extract_look(graded, lk.neutral_baseline(seed=3))
    assert _within(fit.look, want) == []
    assert fit.capped == ()
    assert lk.look_distance(fit.look, want) < 0.01


def test_moody_is_told_apart_from_hardstyle() -> None:
    pack = PackLibrary().load("gym")
    moody, hard = pack.theme("moody").look, pack.theme("hardstyle").look
    fit = lk.extract_look(apply_look(lk.neutral_baseline(seed=5), moody), lk.neutral_baseline(seed=9))
    assert _within(fit.look, moody) == []
    assert _within(fit.look, hard) != []


def test_a_garish_reference_degrades_to_a_capped_look_and_says_so() -> None:
    base = lk.neutral_baseline(seed=2)
    y = base @ np.array([0.2126, 0.7152, 0.0722])
    neon = np.clip(y[:, None] + 3.0 * (base - y[:, None]) + np.array([0.08, 0.0, -0.08]), 0, 1)
    fit = lk.extract_look(neon, lk.neutral_baseline(seed=4))
    assert "saturation" in fit.capped
    assert fit.look.saturation <= 1.4
    assert fit.look.skin_protection >= Look().skin_protection
    # Still a valid theme look: the same validation every theme file gets.
    Look.model_validate(fit.look.model_dump())


def test_extraction_refuses_too_few_pixels() -> None:
    with pytest.raises(ValueError):
        lk.extract_look(np.zeros((10, 3)), lk.neutral_baseline())


# --- rhythm -----------------------------------------------------------------------


def _cuts(every: float, duration: float) -> list[float]:
    return [round(t, 3) for t in np.arange(every, duration - 1e-6, every)]


def test_fast_and_slow_references_give_different_planner_numbers() -> None:
    fast = rh.profile(_cuts(0.8, 30.0), 30.0, bpm=128.0)
    slow = rh.profile(_cuts(4.0, 30.0), 30.0, bpm=128.0)
    pf, ps = rh.to_pace(fast), rh.to_pace(slow)
    assert fast.cuts_per_10s > 3 * slow.cuts_per_10s
    assert pf.energy > ps.energy + 0.4
    assert pf.calm_beats < ps.calm_beats and pf.peak_beats < ps.peak_beats
    # 0.8 s at 128 bpm is about two beats; 4 s about eight.
    assert pf.peak_beats == 2 and ps.calm_beats == 9


def test_without_a_beat_shot_lengths_use_the_pacing_tempo() -> None:
    p = rh.profile(_cuts(2.0, 20.0), 20.0)
    assert p.bpm is None and p.beat_alignment is None
    pace = rh.to_pace(p)
    assert 1 <= pace.peak_beats <= pace.calm_beats <= 32


def test_detector_flashes_merge_and_alignment_counts_cuts_on_beats() -> None:
    assert rh.shot_lengths([1.0, 1.05, 3.0], 4.0) == pytest.approx([1.0, 2.0, 1.0])
    beats = [i * 0.5 for i in range(20)]
    assert rh.beat_alignment([1.0, 2.02, 3.3], beats) == pytest.approx(2 / 3)


def test_slow_motion_share_moves_how_often_slow_shots_may_appear() -> None:
    base = rh.profile(_cuts(2.0, 20.0), 20.0)
    often = rh.to_pace(rh.PacingProfile(**{**_fields(base), "slowmo_share": 0.4}))
    rare = rh.to_pace(rh.PacingProfile(**{**_fields(base), "slowmo_share": 0.0}))
    assert often.slowmo_every_seconds < SLOWMO_EVERY_SECONDS < rare.slowmo_every_seconds
    assert rh.to_pace(base).slowmo_every_seconds == SLOWMO_EVERY_SECONDS


def _fields(p: rh.PacingProfile) -> dict[str, object]:
    return {name: getattr(p, name) for name in p.__slots__}  # type: ignore[attr-defined]


def test_the_gap_is_reported_never_filled() -> None:
    fast = rh.profile(_cuts(0.8, 30.0), 30.0, bpm=128.0)
    assert rh.pacing_gap(fast, [0.9, 0.8, 1.0]) is None
    sentence = rh.pacing_gap(fast, [2.4, 2.0, 2.2])
    assert sentence is not None and "slower" in sentence


def test_profile_json_round_trips() -> None:
    p = rh.profile(_cuts(1.5, 12.0), 12.0, beats=[0.5 * i for i in range(30)], bpm=120.0, slowmo_share=0.1)
    assert rh.PacingProfile.from_json(p.as_json()).as_json() == p.as_json()


# --- captions ---------------------------------------------------------------------


def _frame_with_text(centre: float, height: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = 640, 360
    frame = np.clip(rng.normal(110, 18, size=(h, w, 3)), 0, 255)
    top, bottom = int((centre - height / 2) * h), int((centre + height / 2) * h)
    for x in range(40, w - 40, 14):
        frame[top:bottom, x - 2 : x + 8] = 15  # outline
        frame[top + 2 : bottom - 2, x : x + 6] = 245  # stroke
    return frame.astype(np.uint8)


def test_local_detection_finds_where_the_captions_sit() -> None:
    found = cap.detect_text_band(_frame_with_text(0.75, 0.05))
    assert found.present
    assert found.centre == pytest.approx(0.75, abs=0.03)
    assert found.height == pytest.approx(0.05, abs=0.02)
    plain = np.clip(np.random.default_rng(1).normal(110, 18, size=(640, 360, 3)), 0, 255)
    assert not cap.detect_text_band(plain.astype(np.uint8)).present


def test_caption_profile_maps_to_the_closest_style() -> None:
    frames = [_frame_with_text(0.5, 0.06, seed=s) for s in range(4)]
    profile = cap.caption_profile(frames)
    assert profile.present and profile.position == "center" and profile.size == "medium"
    assert cap.closest_style(profile).style_id == "punch"
    low = cap.caption_profile([_frame_with_text(0.9, 0.035, seed=s) for s in range(4)])
    assert low.position == "bottom" and cap.closest_style(low).style_id == "classic"


def test_gemini_answers_refine_and_no_captions_turns_them_off() -> None:
    frames = [_frame_with_text(0.9, 0.035, seed=s) for s in range(3)]
    answers = [cap.GeminiCaptionAnswer(captions_present="yes", position="lower-third", size="small")] * 3
    profile = cap.caption_profile(frames, answers)
    assert profile.source == "local+gemini" and profile.position == "lower-third"
    assert cap.closest_style(profile).style_id == "minimal"
    none = cap.caption_profile(frames, [cap.GeminiCaptionAnswer(captions_present="no")])
    match = cap.closest_style(none)
    assert not none.present and match.enabled is False
    assert {s.id for s in styles()} >= {match.style_id}


# --- saved themes -----------------------------------------------------------------


def test_a_saved_look_is_a_theme_file_that_survives_a_restart(tmp_path: Path) -> None:
    fit = lk.extract_look(apply_look(lk.neutral_baseline(seed=1), PackLibrary().theme("gym", "moody").look), lk.neutral_baseline(seed=2))
    pace = PaceSpec(energy=0.9, calm_beats=4, peak_beats=2, slowmo_every_seconds=8.0)
    theme = saved.save(tmp_path, theme_for("Night session look", fit, pace, set()))
    assert theme.id == "ref-night-session-look"
    path = saved.saved_root(tmp_path) / f"{theme.id}.toml"
    assert load_theme(path) == theme  # the same validation every theme file gets
    # A fresh library, as after a restart: the saved theme joins the built-in pack.
    pack = saved.with_saved(PackLibrary().load("gym"), tmp_path)
    assert pack.manifest.themes[-1] == theme.id and pack.theme(theme.id).pace == pace
    assert pack.manifest.default_theme == "raw-plus"
    again = theme_for("Night session look", fit, pace, {theme.id})
    assert again.id == "ref-night-session-look-2"


def test_saved_theme_ids_cannot_escape_or_shadow(tmp_path: Path) -> None:
    for bad in ("moody", "ref-../x", "../ref-a"):
        with pytest.raises(UnsafeGradingPathError):
            saved.delete(tmp_path, bad)
    assert saved.slug_for("Ünïcode / ../ name", set()).startswith("ref-")
    (saved.saved_root(tmp_path)).mkdir(parents=True)
    (saved.saved_root(tmp_path) / "ref-broken.toml").write_text("id = 3", encoding="utf-8")
    assert saved.list_saved(tmp_path) == []
