"""The eval's arithmetic, proven on known answers, and its honesty rules.

The gates themselves measure real footage (``make eval``); what is tested here
is everything that decides a verdict: the metrics, the exit codes, that a
missing fixture is BOUND and never PASS, that a crash is a FAIL with its name,
and that the committed scorecard and manifest carry no path or file name.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

metrics = importlib.import_module("eval_metrics")
fixtures = importlib.import_module("eval_fixtures")
report = importlib.import_module("eval_report")
pipeline = importlib.import_module("eval_pipeline")

FPS = 30.0


def _result(status: str, gate: str = "g") -> object:
    return metrics.GateResult(gate, "claim", status, "metric", "v", "b")


def test_exit_codes_never_turn_bound_or_nothing_into_a_pass() -> None:
    P, F, B = metrics.PASS, metrics.FAIL, metrics.BOUND
    assert metrics.exit_code([_result(P), _result(P)]) == 0
    assert metrics.exit_code([_result(P), _result(F), _result(B)]) == 1
    assert metrics.exit_code([_result(P), _result(B)]) == 3
    assert metrics.exit_code([_result(P), _result(B)], strict=True) == 1
    assert metrics.exit_code([]) == 1  # measured nothing: not a pass


def test_cuts_are_located_in_the_frames_and_a_flat_window_is_no_cut() -> None:
    frames = np.zeros((90, 8, 8), dtype=np.uint8)
    frames[45:] = 200  # a hard cut into frame 45 (1.5 s)
    diffs = metrics.frame_differences(frames)
    assert diffs[45] > 0 and diffs[44] == 0
    located = metrics.locate_cuts(diffs, FPS, [1.48, 0.5])
    assert located[0] == pytest.approx(1.5)
    assert located[1] is None  # nothing changes near 0.5 s


def test_beat_errors_and_the_40ms_bound() -> None:
    beats = [0.5 * k for k in range(10)]
    errors = metrics.beat_errors([1.0, 2.03, 3.3], beats)
    assert errors == pytest.approx([0.0, 0.03, 0.2])
    assert [e <= pipeline.BEAT_TOLERANCE_S for e in errors] == [True, True, False]
    assert metrics.beat_errors([1.0], []) == [float("inf")]


def test_audio_lag_finds_a_known_delay_within_half_a_period() -> None:
    rate = 22050
    clicks = np.zeros(rate * 4, dtype=np.float32)
    for k in range(8):  # 120 bpm clicks, with an accent so the grid is not uniform
        start = int((0.25 + 0.5 * k) * rate)
        clicks[start : start + 200] = 1.0 if k % 4 else 0.3
    late = np.concatenate([np.zeros(int(0.1 * rate), dtype=np.float32), clicks])[: len(clicks)]
    env_ref = metrics.onset_envelope(clicks, rate, 256)
    env_late = metrics.onset_envelope(late, rate, 256)
    lag = metrics.audio_lag(env_late, env_ref, rate / 256, max_lag=0.45 * 0.5)
    assert lag == pytest.approx(0.1, abs=0.015)
    assert metrics.audio_lag(env_ref, env_ref, rate / 256, max_lag=0.2) == pytest.approx(0.0)


def test_hue_drift_revert_rate_reps_and_middle_third() -> None:
    assert metrics.hue_drift_degrees((200, 150, 120), (200, 150, 120)) == 0.0
    assert metrics.hue_drift_degrees((255, 0, 0), (255, 0, 10)) == pytest.approx(2.35, abs=0.1)
    assert metrics.hue_drift_degrees((255, 0, 10), (255, 10, 0)) < 5  # wraps through 0
    assert metrics.revert_rate([False, False]) == 1.0
    assert metrics.revert_rate([False, True]) == 0.5
    assert metrics.revert_rate([]) == 0.0  # nothing refused is not 100 %
    assert metrics.within_one([9, 11, None, 7], [10, 10, 10, 10]) == [True, True, False, False]
    crop = [0.0] * 4
    assert metrics.middle_third_fraction([40, 50, None, 10], crop, 90.0) == pytest.approx(2 / 3)
    assert metrics.middle_third_fraction([None], [0.0], 90.0) == 0.0


def test_one_word_error_rate_for_the_repo() -> None:
    speech = importlib.import_module("speech_fixture")
    assert metrics.word_error_rate is speech.word_error_rate
    assert metrics.word_error_rate("lift the bar", "lift a bar") == pytest.approx(1 / 3)


def test_a_missing_fixture_is_bound_and_a_crash_is_a_named_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REPCUT_FIXTURES_DIR", str(tmp_path / "none"))
    results = pipeline.run_gates(["caption-wer", "skin-tone"])
    assert {r.status for r in results} == {metrics.BOUND}
    assert all("labelled" in r.detail or "built" in r.detail for r in results)

    def boom(_folder: Path) -> list[object]:
        raise ValueError("decoder died")

    monkeypatch.setitem(pipeline.GATES, "skin-tone", boom)
    crashed = pipeline.run_gates(["skin-tone"])
    assert crashed[0].status == metrics.FAIL and "decoder died" in crashed[0].detail


def test_every_claim_has_a_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REPCUT_FIXTURES_DIR", str(tmp_path))  # never the real corpus here
    names = {r.gate for name in pipeline.GATES for r in pipeline.run_gates([name])}
    assert names >= {
        "wave1-time",
        "beat-accuracy",
        "caption-wer",
        "slowmo-revert",
        "rep-count",
        "skin-tone",
        "reframe",
    }


def test_the_scorecard_is_scrubbed_and_says_bound_is_not_a_pass(tmp_path: Path) -> None:
    rows = [
        metrics.GateResult(
            "skin-tone",
            "c",
            metrics.BOUND,
            "m",
            "not measured",
            "b",
            "missing under /home/someone/footage/x.mp4",
        ),
        metrics.GateResult(
            "rep-count",
            "c",
            metrics.FAIL,
            "m",
            "1 of 3",
            "3 of 3",
            r"C:\Users\someone\clip.mov | odd",
        ),
    ]
    results = tmp_path / "r.json"
    results.write_text(metrics.to_json(rows, {"when": "now"}), encoding="utf-8")
    out = tmp_path / "card.md"
    assert report.main(["--results", str(results), "--out", str(out)]) == 1
    text = out.read_text(encoding="utf-8")
    assert "someone" not in text and "<path>" in text
    assert "BOUND (not measured)" in text and "**FAIL**" in text
    assert report.main(["--results", str(tmp_path / "absent.json"), "--out", str(out)]) == 1


def test_the_manifest_names_clips_by_hash_only() -> None:
    raw = fixtures.MANIFEST.read_text(encoding="utf-8")
    data = json.loads(raw)
    for role in fixtures.ROLES[:-1]:
        for entry in data.get(role, []):
            assert set(entry) <= {
                "sha256",
                "start",
                "end",
                "time",
                "box",
                "language",
                "transcript",
            }, role
            assert len(entry["sha256"]) == 64
    for suffix in (".mp4", ".mov", ".MOV", ":\\", "/Users/", "/home/"):
        assert suffix not in raw


def test_a_built_fixture_is_found_by_role(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    span = fixtures.Fixture("session", 0, "a" * 64, 1.0, 3.0, {})
    monkeypatch.setattr(fixtures, "manifest_fixtures", lambda: [span])
    monkeypatch.setenv("REPCUT_FIXTURES_DIR", str(tmp_path))
    assert fixtures.by_role("session") == []
    (tmp_path / span.name).write_bytes(b"x")
    assert fixtures.by_role("session") == [(span, tmp_path / span.name)]
    assert "1 of 3 spans labelled" in fixtures.missing_roles()["session"]


def test_the_live_gemini_gate_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free tier is not a test resource: without the flag the live gate does
    not run and no gate sees a key; with it, it is the only gate that may."""
    monkeypatch.delenv(pipeline.LIVE_FLAG, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    assert pipeline.run_gates(["gemini-live"]) == []
    assert os.environ["GEMINI_API_KEY"] == ""  # blanked for every gate and child
