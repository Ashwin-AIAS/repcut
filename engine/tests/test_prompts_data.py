"""The build plan is read from the private guide, never carried in the repo.

The fixture below is INVENTED content in the guide's real *layout*. That split is
the point of the whole module: the parser is ours to publish and test, the plan
is not. Nothing here may be copied from the actual guide.
"""

from pathlib import Path

import pytest

from repcut.prompt_tracker import get_all_prompts_status
from repcut.prompts_data import (
    GuideUnavailableError,
    load_prompts_metadata,
    parse_guide,
)

# Two prompts, two waves, invented names. Deliberately small: three or more
# plan-shaped table rows in a tracked file is what scripts/check_plan_leak.py
# fails the build for, and a test fixture must not be an exception to that.
FAKE_GUIDE = """
# Some Guide

| Wave | Features | Prompts |
|---|---|---|
| 0 — Groundwork | doing the first thing | 01 |
| 1 — Middlework | doing the second thing | 02 |

## 8. Build Plan

| # | Name | What gets built | Human review? | Key tech |
|---|---|---|---|---|
| 01 | Alpha Widget | A widget that does the alpha thing well | report only | make · git |
| 02 | Beta Widget | A widget that does the beta thing well | **YES — taste** | ffmpeg |

## PROMPT 01 — Alpha Widget

### Deliverables
1. The first alpha deliverable
2. The second alpha deliverable

### Constraints
- Not a deliverable

## PROMPT 02 — Beta Widget

### Deliverables
1. The only beta deliverable

## 22. Timeline

| Phase | Prompts | Your realistic calendar time |
|---|---|---|
| Wave 0 | 01 | 3 fortnights |
| Wave 1 | 02 | 9 fortnights |
"""


def test_parses_every_prompt_the_guide_defines() -> None:
    prompts = parse_guide(FAKE_GUIDE)

    assert [p.id for p in prompts] == ["01", "02"]
    assert prompts[0].name == "Alpha Widget"
    assert prompts[1].summary == "A widget that does the beta thing well"


def test_derives_gate_command_and_report_path_from_the_id() -> None:
    first, second = parse_guide(FAKE_GUIDE)

    assert first.gate_command == "make verify-01"
    assert second.report_file == "docs/reports/prompt-02.md"


def test_assigns_each_prompt_to_its_wave_with_that_waves_timeline() -> None:
    first, second = parse_guide(FAKE_GUIDE)

    assert (first.wave_number, first.estimated_timeline) == (0, "3 fortnights")
    assert (second.wave_number, second.estimated_timeline) == (1, "9 fortnights")
    assert first.wave.endswith("Groundwork")


def test_reads_the_human_review_flag_out_of_the_table() -> None:
    first, second = parse_guide(FAKE_GUIDE)

    assert first.human_review is False
    assert second.human_review is True


def test_collects_deliverables_and_stops_at_the_next_heading() -> None:
    first, second = parse_guide(FAKE_GUIDE)

    assert first.deliverables == [
        "The first alpha deliverable",
        "The second alpha deliverable",
    ]
    # "Not a deliverable" lives under ### Constraints and must not be swept in.
    assert second.deliverables == ["The only beta deliverable"]


def test_splits_key_tech_on_the_guides_separator() -> None:
    first, _ = parse_guide(FAKE_GUIDE)

    assert first.key_tech == ["make", "git"]


def test_a_document_that_is_not_the_plan_is_a_named_failure() -> None:
    """An unrecognised file must not read as a plan with zero prompts."""
    with pytest.raises(GuideUnavailableError) as caught:
        parse_guide("# Shopping list\n\n- milk\n")

    assert "no prompt entries" in str(caught.value)


def test_unset_guide_path_degrades_with_a_named_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clone without the guide is the expected state, not a crash."""

    class _NoGuide:
        repcut_guide_path = None

    monkeypatch.setattr("repcut.prompts_data.get_settings", lambda: _NoGuide())

    with pytest.raises(GuideUnavailableError) as caught:
        load_prompts_metadata()

    assert "not available on this machine" in caught.value.reason


def test_missing_guide_file_degrades_with_a_named_reason(tmp_path: Path) -> None:
    with pytest.raises(GuideUnavailableError) as caught:
        load_prompts_metadata(tmp_path / "nothing-here.md")

    assert "no file exists there" in caught.value.reason


def test_a_binary_guide_degrades_rather_than_raising_unicode(tmp_path: Path) -> None:
    """REPCUT_GUIDE_PATH pointing at the PDF export must not crash the engine."""
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"%PDF-1.7\n\x80\x81\x82 binary payload")

    with pytest.raises(GuideUnavailableError) as caught:
        load_prompts_metadata(pdf)

    assert "UTF-8" in caught.value.reason


def test_no_guide_path_never_leaks_the_path_into_the_reason(tmp_path: Path) -> None:
    """A path on this machine carries the OS username (secrets.md)."""
    secret = tmp_path / "not-a-guide.md"

    with pytest.raises(GuideUnavailableError) as caught:
        load_prompts_metadata(secret)

    assert str(tmp_path) not in caught.value.reason
    assert "not-a-guide" not in caught.value.reason


def test_tracker_reports_the_plan_as_unavailable_instead_of_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dashboard must be able to tell 'no plan here' from 'nothing done'."""
    monkeypatch.setattr(
        "repcut.prompt_tracker.load_prompts_metadata",
        lambda: (_ for _ in ()).throw(GuideUnavailableError("no plan on this machine")),
    )

    report = get_all_prompts_status()

    assert report.guide_available is False
    assert report.unavailable_reason == "no plan on this machine"
    assert report.total_prompts == 0
    assert report.prompts == []
    assert report.waves == []
