"""Versions that key derived output move with every recipe that feeds them.

Each test that expects a failure is the negative control for the one before it:
a guard that passes the current recipes and would also pass a changed one has
proved nothing.
"""

from dataclasses import replace
from datetime import timedelta

from repcut.analysis.params import (
    FRAME_PARAMS_VERSION,
    FRAME_RECIPE,
    SCENE_DETECTOR_RECIPE,
    SCENE_PARAMS_VERSION,
)
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION
from repcut.fingerprints import (
    check_pins,
    frame_fingerprint,
    proxy_fingerprint,
    scene_fingerprint,
)
from repcut.media.artifacts import NORMALISATION, PARAMS_VERSION, PROXY_RECIPE, ArtifactKind

PROXY_VERSION = PARAMS_VERSION[ArtifactKind.PROXY]


def test_every_version_matches_the_recipe_it_keys() -> None:
    assert check_pins() == []


def test_an_unbumped_proxy_change_names_the_proxy_and_the_scenes() -> None:
    changed = replace(PROXY_RECIPE, crf=PROXY_RECIPE.crf + 1)

    problems = check_pins(proxy=changed)

    assert any("PARAMS_VERSION[proxy]" in problem for problem in problems)
    assert any("SCENE_PARAMS_VERSION" in problem for problem in problems)


def test_bumping_the_proxy_without_the_scenes_still_fails() -> None:
    """Amendment 012 row 3: detection reads the proxy, so its version must move too."""
    changed = replace(PROXY_RECIPE, crf=PROXY_RECIPE.crf + 1)
    bumped = PROXY_VERSION + 1

    problems = check_pins(
        proxy=changed,
        proxy_version=bumped,
        proxy_pins={bumped: proxy_fingerprint(changed)},
    )

    assert len(problems) == 1
    assert "SCENE_PARAMS_VERSION" in problems[0]


def test_bumping_both_together_is_consistent() -> None:
    changed = replace(PROXY_RECIPE, crf=PROXY_RECIPE.crf + 1)
    proxy_bumped, scene_bumped = PROXY_VERSION + 1, SCENE_PARAMS_VERSION + 1

    assert (
        check_pins(
            proxy=changed,
            proxy_version=proxy_bumped,
            scene_version=scene_bumped,
            proxy_pins={proxy_bumped: proxy_fingerprint(changed)},
            scene_pins={scene_bumped: scene_fingerprint(SCENE_DETECTOR_RECIPE, changed)},
        )
        == []
    )


def test_a_detector_change_alone_moves_only_the_scenes() -> None:
    detector = replace(SCENE_DETECTOR_RECIPE, minimum_scene_length=timedelta(seconds=1))

    problems = check_pins(detector=detector)

    assert len(problems) == 1
    assert "SCENE_PARAMS_VERSION" in problems[0]


def test_bumping_the_frame_without_the_gemini_prompt_still_fails() -> None:
    """Open issue 12: the Gemini cache key carries no frame version."""
    changed = replace(FRAME_RECIPE, quality=FRAME_RECIPE.quality + 1)
    bumped = FRAME_PARAMS_VERSION + 1

    problems = check_pins(
        frame=changed,
        frame_version=bumped,
        frame_pins={bumped: frame_fingerprint(changed)},
    )

    assert len(problems) == 1
    assert "GEMINI_PROMPT_VERSION" in problems[0]


def test_a_different_operator_moves_all_four_versions() -> None:
    """The operator is upstream of the proxy, the scenes, the frame and the tags."""
    other = replace(NORMALISATION, operator="mobius")

    problems = check_pins(
        proxy=replace(PROXY_RECIPE, normalisation=other),
        frame=replace(FRAME_RECIPE, normalisation=other),
    )

    named = " ".join(problems)
    for version in (
        "PARAMS_VERSION[proxy]",
        "SCENE_PARAMS_VERSION",
        "FRAME_PARAMS_VERSION",
        "GEMINI_PROMPT_VERSION",
    ):
        assert version in named


def test_a_version_with_no_pin_is_reported_not_passed() -> None:
    problems = check_pins(gemini_version=GEMINI_PROMPT_VERSION + 1)

    assert problems == [
        f"GEMINI_PROMPT_VERSION {GEMINI_PROMPT_VERSION + 1} has no pinned fingerprint - "
        f"add {frame_fingerprint()!r} for it"
    ]
