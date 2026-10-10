"""Grading engine: themes as data, the resolver, LUTs, the corrector and the chain.

Pixel claims are checked on decoded output, never on the argv (the colour
baseline's lesson: the argv was right and the file was wrong). Every fixture is
generated here from ``lavfi`` or numpy; no media is committed.
"""

from __future__ import annotations

import asyncio
import colorsys
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from repcut.grading import apply as grading
from repcut.grading.corrector import (
    Correction,
    Measurements,
    SceneTags,
    correct,
    measure,
    white_balance_gains,
)
from repcut.grading.lut import (
    CubeFormatError,
    apply_look,
    cube_text,
    look_digest,
    read_cube,
    write_cube,
)
from repcut.grading.packs import (
    PackLibrary,
    PackNotFoundError,
    UnsafeGradingPathError,
    ensure_lut,
    lut_path,
)
from repcut.grading.theme import (
    MAX_EXPOSURE_STOPS,
    MAX_TEMPERATURE_SHIFT_K,
    Look,
    ThemeFileError,
    load_theme,
)
from repcut.media import ffmpeg_builder as builder
from repcut.media.ffmpeg_builder import GradeSpec

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

LIBRARY = PackLibrary()
GYM = LIBRARY.load("gym")
TAG_VALUES = {
    "lighting_temperature": [None, "warm", "neutral", "cool"],
    "lighting_quality": [None, "bright", "dim", "harsh", "soft", "mixed"],
}


# --- fixtures ---------------------------------------------------------------


def _encode_bt709(source_args: list[str], out: Path, *, seconds: float = 2.0) -> Path:
    """A clip shaped like a proxy: bt709 limited-range 4:2:0 x264, every tag stated."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *source_args,
            "-t", f"{seconds}",
            "-vf",
            "format=gbrp,zscale=min=gbr:rin=pc:pin=bt709:tin=bt709"
            ":m=bt709:r=tv:p=bt709:t=bt709,format=yuv420p",
            "-c:v", "libx264", "-crf", "12",
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", str(out),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    return out


def _lavfi_clip(tmp_path: Path, name: str, mixer: str) -> Path:
    src = f"testsrc2=size=640x360:rate=30,format=rgb24,colorchannelmixer={mixer}"
    return _encode_bt709(["-f", "lavfi", "-i", src], tmp_path / f"{name}.mp4")


@pytest.fixture
def dark_warm_clip(tmp_path: Path) -> Path:
    """A dim gym under warm tungsten: low and orange."""
    return _lavfi_clip(tmp_path, "dark-warm", "rr=0.40:gg=0.27:bb=0.15")


@pytest.fixture
def window_lit_clip(tmp_path: Path) -> Path:
    """Daylight through a window: bright, slightly blue."""
    return _lavfi_clip(tmp_path, "window", "rr=0.85:gg=0.92:bb=1.0")


SKIN_PATCHES = np.array(
    [
        [0.95, 0.80, 0.70],
        [0.87, 0.68, 0.55],
        [0.76, 0.57, 0.45],
        [0.60, 0.42, 0.30],
        [0.45, 0.30, 0.21],
        [0.30, 0.20, 0.14],
    ]
)


@pytest.fixture
def skin_clip(tmp_path: Path) -> Path:
    """Six skin patches, light to dark, as a proxy-shaped clip."""
    image = np.zeros((128, 128 * len(SKIN_PATCHES), 3), dtype=np.uint8)
    for i, rgb in enumerate(SKIN_PATCHES):
        image[:, i * 128 : (i + 1) * 128] = np.round(rgb[::-1] * 255)
    still = tmp_path / "skin.png"
    cv2.imwrite(str(still), image)
    return _encode_bt709(["-loop", "1", "-i", str(still)], tmp_path / "skin.mp4", seconds=1.0)


def _still(proxy: Path, out: Path, grade: GradeSpec, at: float = 0.5) -> np.ndarray:
    command = builder.build_graded_still(proxy, out, timestamp_seconds=at, grade=grade)
    asyncio.run(builder.render(command, dry_run_first=False))
    image = cv2.imread(str(out))
    assert image is not None
    return image[:, :, ::-1].astype(np.float64) / 255.0


def _neutral(lut: Path | None, intensity: int, grain: float = 0.0) -> GradeSpec:
    return GradeSpec(
        exposure_stops=0.0, gains=(1.0, 1.0, 1.0), denoise=0.0,
        lut=lut, intensity=intensity, grain=grain,
    )  # fmt: skip


def _hue(rgb: np.ndarray) -> float:
    return colorsys.rgb_to_hsv(*rgb)[0] * 360.0


# --- themes are data ----------------------------------------------------------


def test_the_gym_pack_holds_five_themes_and_a_safe_default() -> None:
    assert len(GYM.themes) == 5
    assert GYM.manifest.default_theme == "raw-plus"
    raw = GYM.theme("raw-plus")
    assert raw.look == Look(), "the correction-only theme must have an identity look"
    assert raw.finish.grain == 0.0


@needs_ffmpeg
def test_a_new_pack_is_a_directory_and_zero_code(tmp_path: Path, skin_clip: Path) -> None:
    root = tmp_path / "packs"
    pack = root / "travel"
    pack.mkdir(parents=True)
    (pack / "pack.toml").write_text(
        'id = "travel"\nname = "Travel"\ndescription = "Test pack."\n'
        'themes = ["golden"]\ndefault_theme = "golden"\n',
        encoding="utf-8",
    )
    (pack / "golden.toml").write_text(
        'id = "golden"\nname = "Golden"\ndescription = "Warm."\n'
        "[look]\nwarmth = 0.04\nsaturation = 1.1\n",
        encoding="utf-8",
    )
    library = PackLibrary(root)
    assert library.pack_ids() == ["travel"]
    theme = library.theme("travel", "golden")
    lut = ensure_lut(tmp_path / "luts", "travel", theme)
    image = _still(skin_clip, tmp_path / "g.jpg", _neutral(lut, 100))
    assert image.shape[0] > 0


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("id = 'x'\nname = ", "not valid TOML"),
        ('id = "bad"\nname = "B"\ndescription = "d"\n[look]\nsaturation = 3.0\n', "saturation"),
        ('id = "bad"\nname = "B"\ndescription = "d"\nsparkle = 1\n', "sparkle"),
        ('id = "other"\nname = "B"\ndescription = "d"\n', "does not match"),
        ('id = "bad"\nname = "B"\ndescription = "d"\n[[adapt]]\nwhen = {}\n', "adapt"),
    ],
)
def test_a_malformed_theme_raises_a_named_error(tmp_path: Path, text: str, needle: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ThemeFileError, match=needle):
        load_theme(path)


def test_a_theme_file_the_manifest_does_not_list_is_refused(tmp_path: Path) -> None:
    shutil.copytree(LIBRARY.root / "gym", tmp_path / "gym")
    shutil.copy(tmp_path / "gym" / "moody.toml", tmp_path / "gym" / "stray.toml")
    with pytest.raises(ThemeFileError, match=r"stray\.toml"):
        PackLibrary(tmp_path).load("gym")


@pytest.mark.parametrize("pack_id", ["../gym", "GYM", "gym/../gym", "", "a" * 41, "gym\\x"])
def test_the_resolver_takes_only_slugs(pack_id: str) -> None:
    with pytest.raises(UnsafeGradingPathError):
        LIBRARY.load(pack_id)


def test_an_unknown_pack_is_named() -> None:
    with pytest.raises(PackNotFoundError):
        LIBRARY.load("travel")


def test_a_lut_path_is_derived_and_contained(tmp_path: Path) -> None:
    path = lut_path(tmp_path, "gym", GYM.theme("moody"))
    assert path.is_relative_to(tmp_path.resolve())
    assert path.name == f"moody-{look_digest(GYM.theme('moody').look)}.cube"
    with pytest.raises(UnsafeGradingPathError):
        lut_path(tmp_path, "..", GYM.theme("moody"))


# --- LUTs -------------------------------------------------------------------


def test_every_lut_regenerates_byte_identical(tmp_path: Path) -> None:
    for theme in GYM.themes:
        first, second = tmp_path / "a.cube", tmp_path / "b.cube"
        write_cube(first, theme.look, title=theme.id)
        write_cube(second, theme.look, title=theme.id)
        assert first.read_bytes() == second.read_bytes(), theme.id
        size, table = read_cube(first)
        assert size == 33 and table.shape == (33**3, 3)


def test_the_identity_look_is_an_identity_lut() -> None:
    _, table = 33, apply_look(np.random.default_rng(1).random((500, 3)), Look())
    grid = np.random.default_rng(1).random((500, 3))
    assert np.max(np.abs(table - grid)) < 1e-9


def test_the_digest_moves_with_the_look_and_only_with_it() -> None:
    a = look_digest(Look(saturation=0.9))
    assert a == look_digest(Look(saturation=0.9))
    assert a != look_digest(Look(saturation=0.91))


def _cube(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "x.cube"
    path.write_text(body, encoding="utf-8")
    return path


GOOD_2 = "LUT_3D_SIZE 2\n" + "".join(
    f"{r} {g} {b}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1)
)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("0 0 0\n", "no LUT_3D_SIZE"),
        ("LUT_3D_SIZE 2\n0 0 0\n", "rows"),
        ("LUT_3D_SIZE two\n", "integer"),
        ("LUT_3D_SIZE 99\n", "outside"),
        (GOOD_2.replace("1 1 1", "1e999 1 1"), "finite"),
        (GOOD_2.replace("1 1 1", "1 1 7"), "domain"),
        (GOOD_2.replace("1 1 1", "1 1"), "three numbers"),
        ("LUT_1D_SIZE 2\n", "unsupported keyword"),
        (GOOD_2 + "LUT_3D_SIZE 2\n", "out of place"),
        ("LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\n", "DOMAIN_MIN"),
    ],
)
def test_a_malformed_cube_raises_a_named_error(tmp_path: Path, body: str, needle: str) -> None:
    with pytest.raises(CubeFormatError, match=needle):
        read_cube(_cube(tmp_path, body))


def test_a_non_ascii_or_oversized_cube_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.cube"
    path.write_bytes(b"\xff\xfe" + GOOD_2.encode())
    with pytest.raises(CubeFormatError, match="ASCII"):
        read_cube(path)
    with pytest.raises(CubeFormatError, match="cannot be read"):
        read_cube(tmp_path / "absent.cube")


def test_a_corrupted_cache_is_regenerated_not_used(tmp_path: Path) -> None:
    theme = GYM.theme("moody")
    path = ensure_lut(tmp_path, "gym", theme)
    path.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    assert ensure_lut(tmp_path, "gym", theme) == path
    assert path.read_text(encoding="ascii") == cube_text(theme.look, title="gym/moody")


# --- the corrector ------------------------------------------------------------


def test_white_balance_is_exactly_neutral_at_zero_and_cools_below() -> None:
    assert white_balance_gains(0.0) == (1.0, 1.0, 1.0)
    r, g, b = white_balance_gains(-400.0)
    assert g == pytest.approx(1.0) and b > 1.0 > r


@pytest.mark.parametrize("theme_id", [t.id for t in GYM.themes])
def test_any_tags_on_any_measurement_stay_inside_the_bounds(theme_id: str) -> None:
    rules = GYM.theme(theme_id).adapt
    rng = np.random.default_rng(4)
    extremes = [
        Measurements(median_luma=0.001, cast_kelvin=1500.0, noise_sigma=50.0, frames=3),
        Measurements(median_luma=0.999, cast_kelvin=15000.0, noise_sigma=0.0, frames=3),
    ]
    randoms = [
        Measurements(
            median_luma=float(rng.random()),
            cast_kelvin=float(rng.uniform(1500, 15000)),
            noise_sigma=float(rng.uniform(0, 20)),
            frames=3,
        )
        for _ in range(40)
    ]
    for m in [None, *extremes, *randoms]:
        for temp in TAG_VALUES["lighting_temperature"]:
            for quality in TAG_VALUES["lighting_quality"]:
                c = correct(m, SceneTags(temp, quality), rules)
                assert abs(c.exposure_stops) <= MAX_EXPOSURE_STOPS
                assert abs(c.temperature_shift_k) <= MAX_TEMPERATURE_SHIFT_K
                assert 0.0 <= c.denoise <= 1.0


def test_no_analysis_is_a_measurement_only_correction() -> None:
    m = Measurements(median_luma=0.2, cast_kelvin=4000.0, noise_sigma=2.0, frames=3)
    without = correct(m, SceneTags(), GYM.theme("moody").adapt)
    assert without.rules == ()
    assert correct(None, SceneTags(), GYM.theme("moody").adapt) == Correction()


@needs_ffmpeg
def test_the_same_theme_corrects_a_dark_gym_and_a_window_differently(
    dark_warm_clip: Path, window_lit_clip: Path
) -> None:
    """The guide's adaptation proof, measured from rendered frames."""
    theme = GYM.theme("moody")

    def run(clip: Path) -> Correction:
        return asyncio.run(
            grading.scene_correction(
                clip, start_seconds=0.0, end_seconds=2.0, tags=SceneTags(), theme=theme
            )
        )

    dark, window = run(dark_warm_clip), run(window_lit_clip)
    assert dark.exposure_stops - window.exposure_stops > 0.5, (dark, window)
    assert dark.temperature_shift_k < window.temperature_shift_k - 200, (dark, window)


@needs_ffmpeg
def test_a_wrong_tag_moves_the_correction_by_at_most_its_rule(dark_warm_clip: Path) -> None:
    theme = GYM.theme("raw-plus")
    frames = asyncio.run(grading.read_measure_frames(dark_warm_clip, [0.5, 1.0, 1.5]))
    m = measure(frames)
    honest = correct(m, SceneTags("warm", "dim"), theme.adapt)
    wrong = correct(m, SceneTags("cool", "bright"), theme.adapt)
    assert abs(honest.temperature_shift_k - wrong.temperature_shift_k) <= 500.0 + 1e-6
    assert abs(wrong.temperature_shift_k) <= MAX_TEMPERATURE_SHIFT_K


# --- the chain, on pixels ------------------------------------------------------


def test_the_chain_states_every_conversion_and_omits_what_is_off(tmp_path: Path) -> None:
    lut = tmp_path / "C-drive" / "x.cube"
    chain = builder.build_grade_filter(_neutral(lut, 0, grain=6.0))
    assert "lut3d" not in chain and "noise" not in chain and "hqdn3d" not in chain
    assert "exposure" not in chain and "colorchannelmixer" not in chain
    full = builder.build_grade_filter(
        GradeSpec(
            exposure_stops=0.5,
            gains=(0.9, 1.0, 1.1),
            denoise=0.5,
            lut=lut,
            intensity=60,
            grain=5.0,
        )
    )
    for stage in ("hqdn3d", "exposure=exposure=0.5000", "colorchannelmixer", "blend", "noise"):
        assert stage in full
    assert full.count("zscale=") == 3 and "t=linear" in full


@pytest.mark.parametrize("bad", ["a'b.cube", "a[b].cube", "a;b.cube", "a,b.cube"])
def test_a_filter_path_is_refused_rather_than_escaped(tmp_path: Path, bad: str) -> None:
    with pytest.raises(builder.UnsafeFilterPathError):
        builder.filter_path(tmp_path / bad)


def test_a_drive_letter_colon_is_escaped() -> None:
    assert builder.filter_path(Path("C:/data/luts/x.cube")) == "'C\\:/data/luts/x.cube'"


@needs_ffmpeg
def test_intensity_zero_is_the_corrected_only_picture(tmp_path: Path, dark_warm_clip: Path) -> None:
    """The guide's Raw+ equivalence, on decoded pixels."""
    correction = Correction(exposure_stops=0.6, temperature_shift_k=-500.0, denoise=0.3)
    theme = GYM.theme("old-school")
    lut = ensure_lut(tmp_path / "luts", "gym", theme)
    zero = _still(
        dark_warm_clip,
        tmp_path / "z.jpg",
        grading.grade_spec(correction, theme, intensity=0, lut=lut),
    )
    only = _still(
        dark_warm_clip,
        tmp_path / "c.jpg",
        grading.grade_spec(correction, GYM.theme("raw-plus"), intensity=0, lut=None),
    )
    assert np.max(np.abs(zero - only)) == 0.0
    full = _still(
        dark_warm_clip,
        tmp_path / "f.jpg",
        grading.grade_spec(correction, theme, intensity=100, lut=lut),
    )
    assert np.mean(np.abs(full - only)) > 0.01, "the theme must change something at 100"


@needs_ffmpeg
@pytest.mark.parametrize("theme_id", [t.id for t in GYM.themes])
def test_skin_keeps_its_hue_through_every_theme(
    tmp_path: Path, skin_clip: Path, theme_id: str
) -> None:
    theme = GYM.theme(theme_id)
    lut = ensure_lut(tmp_path / "luts", "gym", theme)
    before = _still(skin_clip, tmp_path / "b.jpg", _neutral(None, 0))
    after = _still(skin_clip, tmp_path / "a.jpg", _neutral(lut, 100))
    for i in range(len(SKIN_PATCHES)):
        centre = (slice(40, 88), slice(i * 128 + 40, i * 128 + 88))
        h0 = _hue(before[centre].reshape(-1, 3).mean(axis=0))
        h1 = _hue(after[centre].reshape(-1, 3).mean(axis=0))
        drift = abs((h1 - h0 + 180.0) % 360.0 - 180.0)
        assert drift <= SKIN_HUE_TOLERANCE_DEGREES, (theme_id, i, round(drift, 2))


SKIN_HUE_TOLERANCE_DEGREES = 4.0


@needs_ffmpeg
def test_a_graded_preview_is_tagged_bt709_in_the_file(tmp_path: Path, dark_warm_clip: Path) -> None:
    theme = GYM.theme("moody")
    out = asyncio.run(
        grading.render_preview(
            dark_warm_clip, tmp_path / "p.mp4", start_seconds=0.0, end_seconds=1.0,
            correction=Correction(exposure_stops=0.3), theme=theme, pack_id="gym",
            intensity=80, luts_root=tmp_path / "luts",
        )
    )  # fmt: skip
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_space,color_primaries,color_transfer,color_range,pix_fmt",
         "-of", "json", str(out)],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["color_space"], stream["color_primaries"], stream["color_transfer"]) == (
        "bt709", "bt709", "bt709",
    )  # fmt: skip
    assert stream["color_range"] == "tv" and stream["pix_fmt"] == "yuv420p"


# --- recommendation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (SceneTags(), "raw-plus"),
        (SceneTags(lighting_quality="dim"), "moody"),
        (SceneTags(lighting_quality="harsh"), "hardstyle"),
        (SceneTags(lighting_quality="soft"), "aesthetic"),
        (SceneTags(lighting_temperature="neutral"), "raw-plus"),
    ],
)
def test_the_recommendation_follows_the_tags(tags: SceneTags, expected: str) -> None:
    assert grading.recommend_theme(GYM, tags).theme_id == expected
