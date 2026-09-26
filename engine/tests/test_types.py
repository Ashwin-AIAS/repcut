"""The two shared analysis dataclasses, and the invariants they enforce at construction."""

import pytest

from repcut.analysis.types import EnergyMeasurement, SceneBoundary


def test_scene_boundary_holds_its_own_fields() -> None:
    boundary = SceneBoundary(
        sequence_index=0,
        start_seconds=1.0,
        end_seconds=3.5,
        start_frame_source=30,
        end_frame_source=105,
    )

    assert boundary.sequence_index == 0
    assert boundary.end_seconds - boundary.start_seconds == pytest.approx(2.5)


def test_scene_boundary_rejects_a_non_positive_duration() -> None:
    with pytest.raises(ValueError, match="positive duration"):
        SceneBoundary(
            sequence_index=0,
            start_seconds=2.0,
            end_seconds=2.0,
            start_frame_source=60,
            end_frame_source=60,
        )
    with pytest.raises(ValueError, match="positive duration"):
        SceneBoundary(
            sequence_index=0,
            start_seconds=3.0,
            end_seconds=1.0,
            start_frame_source=90,
            end_frame_source=30,
        )


def test_scene_boundary_rejects_a_negative_sequence_index() -> None:
    with pytest.raises(ValueError, match="sequence_index"):
        SceneBoundary(
            sequence_index=-1,
            start_seconds=0.0,
            end_seconds=1.0,
            start_frame_source=0,
            end_frame_source=30,
        )


def test_scene_boundary_is_frozen() -> None:
    boundary = SceneBoundary(
        sequence_index=0,
        start_seconds=0.0,
        end_seconds=1.0,
        start_frame_source=0,
        end_frame_source=30,
    )
    with pytest.raises(AttributeError):
        boundary.sequence_index = 1  # type: ignore[misc]


def test_energy_measurement_holds_its_own_fields() -> None:
    measurement = EnergyMeasurement(motion_energy=1.2, audio_energy=0.05, energy_score=42.0)

    assert measurement.motion_energy == pytest.approx(1.2)
    assert measurement.energy_score == pytest.approx(42.0)


@pytest.mark.parametrize(
    ("motion_energy", "audio_energy", "energy_score", "match"),
    [
        (-0.1, 0.0, 0.0, "motion_energy"),
        (0.0, -0.1, 0.0, "audio_energy"),
        (0.0, 0.0, -0.1, "energy_score"),
        (0.0, 0.0, 100.1, "energy_score"),
    ],
)
def test_energy_measurement_rejects_out_of_bounds_values(
    motion_energy: float, audio_energy: float, energy_score: float, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        EnergyMeasurement(
            motion_energy=motion_energy, audio_energy=audio_energy, energy_score=energy_score
        )


def test_energy_measurement_accepts_its_own_documented_boundaries() -> None:
    """0 and 100 are valid, not off-by-one excluded."""
    EnergyMeasurement(motion_energy=0.0, audio_energy=0.0, energy_score=0.0)
    EnergyMeasurement(motion_energy=0.0, audio_energy=0.0, energy_score=100.0)
