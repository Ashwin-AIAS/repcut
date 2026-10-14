"""Hero moments: what the detector ranks first, and why it says so."""

from repcut.edit.signals import SIGNALS_VERSION, ClipSignals
from repcut.edit.types import Segment
from repcut.highlights.detector import TOP_N, SceneNote, detect, score_segment

SHA = "a" * 64
WIDTH = 0.5


def _signals(motion: list[float], audio: list[float]) -> ClipSignals:
    return ClipSignals(
        version=SIGNALS_VERSION,
        duration_seconds=len(motion) * WIDTH,
        window_seconds=WIDTH,
        audio_db=tuple(audio),
        motion=tuple(motion),
        shake=tuple(0.0 for _ in motion),
        speech=None,
    )


def _segment(start: float, end: float, sha: str = SHA) -> Segment:
    return Segment(Segment.make_id(sha, start, end), sha, start, end, 0.7, 0.5)


def _session() -> tuple[ClipSignals, list[Segment]]:
    """Three sets, each followed by a rest. The third ends on the hardest rep and a slam."""
    motion: list[float] = []
    audio: list[float] = []
    for peak in (4.0, 5.0, 9.0):
        motion += [peak * 0.6] * 8 + [peak] * 2 + [0.2] * 6  # 4 s of reps, 1 s top, 3 s rest
        audio += [-30.0] * 9 + [-30.0] + [-38.0] * 6
    audio[16 * 2 + 9] = -10.0  # the slam, at the end of the third set
    segments = [_segment(i * 8.0, i * 8.0 + 5.0) for i in range(3)]
    return _signals(motion, audio), segments


def test_the_last_reps_of_the_hardest_set_rank_first() -> None:
    signals, segments = _session()
    moments = detect(segments, {SHA: signals})
    assert len(moments) == TOP_N
    assert moments[0].segment_id == segments[2].id
    assert [m.rank for m in moments] == [1, 2, 3]
    assert moments[0].score > moments[1].score >= moments[2].score


def test_the_peak_is_the_busiest_moment_not_the_middle() -> None:
    signals, segments = _session()
    best = detect(segments, {SHA: signals})[0]
    assert 16.0 + 4.0 <= best.peak <= 16.0 + 5.0


def test_every_moment_says_why() -> None:
    signals, segments = _session()
    best = detect(segments, {SHA: signals})[0]
    assert best.reasons
    assert "a sharp impact sound (a plate or bar landing)" in best.reasons
    assert "the last reps before a rest" in best.reasons


def test_scene_analysis_counts_but_never_invents() -> None:
    signals, segments = _session()
    _, _, neutral, _ = score_segment(segments[0], signals, ())
    assert neutral["context"] == 0.5
    rest = [SceneNote(SHA, 0.0, 8.0, content_type="rest", energy_level="low")]
    lift = [
        SceneNote(SHA, 0.0, 8.0, content_type="exercise", exercise_guess="squat", energy_level="high")
    ]
    _, _, resting, _ = score_segment(segments[0], signals, rest)
    _, _, lifting, reasons = score_segment(segments[0], signals, lift)
    assert resting["context"] < neutral["context"] < lifting["context"]
    assert "scene analysis saw squat" in reasons


def test_detection_is_deterministic_and_skips_unmeasured_clips() -> None:
    signals, segments = _session()
    other = _segment(0.0, 4.0, sha="b" * 64)
    first = detect([*segments, other], {SHA: signals})
    again = detect([other, *reversed(segments)], {SHA: signals})
    assert [m.to_json() for m in first] == [m.to_json() for m in again]
    assert all(m.sha256 == SHA for m in first)
