"""Phase B measurements behind `make verify-04`, criteria 9-17 (grading).

Registered into ``verify_04_checks.CHECKS``; same contract: exactly one
``MEASURED:`` line, ``FAILED:`` then exit 1, exit 0 only when the criterion
holds. Every colour claim is read from a rendered file (ffprobe for tags,
decoded pixels for values). Every fixture is generated here, at test time, and
deleted with its scratch directory; nothing touches real footage.

Tolerances are stated here, before any of them was measured on a fixture:
- artifacts: a theme may raise the fraction of clipped pixels (any channel at
  0 or 255) by at most 5 points over the corrected-only render
- skin: hue drift at most 4 degrees per patch (the unit test's bound)
- adaptation: the same theme corrects the dark warm fixture and the window-lit
  one apart by more than 0.5 stop and more than 200 K
- intensity 0: max decoded difference 0 against corrected-only (PSNR infinite)
- preview budget: the guide's 2 s, per graded still and to the first graded frame
- someone can use it: the current graded still's mean pixel difference after
  picking a non-default theme is at least 2 codes (0-255)
"""

from __future__ import annotations

import asyncio
import colorsys
import itertools
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_03_checks as v3  # noqa: E402 - after the sys.path insert it depends on
from verify_03_checks import failed, measured  # noqa: E402 - same

ARTIFACT_CLIP_INCREASE_MAX = 0.05
SKIN_HUE_TOLERANCE_DEGREES = 4.0
ADAPT_MIN_STOPS = 0.5
ADAPT_MIN_KELVIN = 200.0
PREVIEW_BUDGET_S = 2.0
UI_MIN_PIXEL_CHANGE = 2.0

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

# --- fixtures ------------------------------------------------------------------


def encode_bt709(source_args: list[str], out: Path, *, seconds: float = 2.0) -> Path:
    """Proxy-shaped: bt709 limited-range 4:2:0 x264, every tag stated."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *source_args,
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


def lavfi_clip(root: Path, name: str, mixer: str, size: str = "640x360") -> Path:
    src = f"testsrc2=size={size}:rate=30,format=rgb24,colorchannelmixer={mixer}"
    return encode_bt709(["-f", "lavfi", "-i", src], root / f"{name}.mp4")


def dark_warm(root: Path) -> Path:
    return lavfi_clip(root, "dark-warm", "rr=0.40:gg=0.27:bb=0.15")


def window_lit(root: Path) -> Path:
    return lavfi_clip(root, "window", "rr=0.85:gg=0.92:bb=1.0")


def portrait(root: Path) -> Path:
    return lavfi_clip(root, "portrait", "rr=0.7:gg=0.65:bb=0.6", size="720x1280")


def skin_clip(root: Path) -> Path:
    import cv2

    image = np.zeros((128, 128 * len(SKIN_PATCHES), 3), dtype=np.uint8)
    for i, rgb in enumerate(SKIN_PATCHES):
        image[:, i * 128 : (i + 1) * 128] = np.round(rgb[::-1] * 255)
    still = root / "skin.png"
    cv2.imwrite(str(still), image)
    return encode_bt709(["-loop", "1", "-i", str(still)], root / "skin.mp4", seconds=1.0)


def two_shot_clip(root: Path) -> Path:
    """Two shots with a hard cut and a tone, so detection finds two scenes."""
    out = root / "two-shots.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
            "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "20",
            "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    return out


# --- render helpers --------------------------------------------------------------


def decode_still(path: Path) -> np.ndarray:
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError("a graded still did not decode")
    return image[:, :, ::-1].astype(np.float64)


def still(proxy: Path, out: Path, grade: object, at: float = 0.5) -> np.ndarray:
    from repcut.media import ffmpeg_builder as builder

    command = builder.build_graded_still(proxy, out, timestamp_seconds=at, grade=grade)  # type: ignore[arg-type]
    asyncio.run(builder.render(command, dry_run_first=False))
    return decode_still(out)


def neutral(lut: Path | None, intensity: int) -> object:
    from repcut.media.ffmpeg_builder import GradeSpec

    return GradeSpec(
        exposure_stops=0.0, gains=(1.0, 1.0, 1.0), denoise=0.0,
        lut=lut, intensity=intensity, grain=0.0,
    )  # fmt: skip


def probe_stream(path: Path) -> dict[str, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_space,color_primaries,color_transfer,color_range,pix_fmt,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    stream: dict[str, str] = json.loads(result.stdout)["streams"][0]
    return stream


def clipped_fraction(rgb: np.ndarray) -> float:
    return float(np.mean(np.any((rgb <= 1.0) | (rgb >= 254.0), axis=2)))


def hue(rgb: np.ndarray) -> float:
    return colorsys.rgb_to_hsv(*(rgb / 255.0))[0] * 360.0


def gym() -> object:
    from repcut.grading.packs import PackLibrary

    return PackLibrary().load("gym")


# --- 9. the guide's automated criteria ----------------------------------------------


def check_themes_render() -> int:
    """9a. All five themes render on three clips without artifacts; skin hue holds."""
    from repcut.grading import apply as grading
    from repcut.grading.corrector import SceneTags
    from repcut.grading.packs import ensure_lut

    pack = gym()
    worst_clip = 0.0
    worst_skin = 0.0
    problems: list[str] = []
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        clips = [dark_warm(root), window_lit(root), portrait(root)]
        for clip in clips:
            for theme in pack.themes:  # type: ignore[attr-defined]
                correction = asyncio.run(
                    grading.scene_correction(
                        clip, start_seconds=0.0, end_seconds=2.0, tags=SceneTags(), theme=theme
                    )
                )
                lut = ensure_lut(root / "luts", "gym", theme)
                only = still(
                    clip,
                    root / "c.jpg",
                    grading.grade_spec(correction, theme, intensity=0, lut=None),
                )
                graded = still(
                    clip, root / "g.jpg",
                    grading.grade_spec(correction, theme, intensity=100, lut=lut),
                )  # fmt: skip
                if graded.shape != only.shape or not np.isfinite(graded).all():
                    problems.append(f"{clip.stem}/{theme.id}: shape or values wrong")
                    continue
                rise = clipped_fraction(graded) - clipped_fraction(only)
                worst_clip = max(worst_clip, rise)
                if rise > ARTIFACT_CLIP_INCREASE_MAX:
                    problems.append(f"{clip.stem}/{theme.id}: clipped +{rise:.3f}")
        skin = skin_clip(root)
        before = still(skin, root / "sb.jpg", neutral(None, 0))
        for theme in pack.themes:  # type: ignore[attr-defined]
            lut = ensure_lut(root / "luts", "gym", theme)
            after = still(skin, root / "sa.jpg", neutral(lut, 100))
            for i in range(len(SKIN_PATCHES)):
                centre = (slice(40, 88), slice(i * 128 + 40, i * 128 + 88))
                h0 = hue(before[centre].reshape(-1, 3).mean(axis=0))
                h1 = hue(after[centre].reshape(-1, 3).mean(axis=0))
                drift = abs((h1 - h0 + 180.0) % 360.0 - 180.0)
                worst_skin = max(worst_skin, drift)
                if drift > SKIN_HUE_TOLERANCE_DEGREES:
                    problems.append(f"skin patch {i} under {theme.id}: {drift:.1f} deg")
    measured(
        f"5 themes x 3 clips (640x360 dark-warm, window-lit, 720x1280 portrait): "
        f"worst clipped rise {worst_clip:.3f} (max {ARTIFACT_CLIP_INCREASE_MAX}); "
        f"6 skin patches x 5 themes: worst hue drift {worst_skin:.2f} deg "
        f"(max {SKIN_HUE_TOLERANCE_DEGREES})"
    )
    if problems:
        failed("; ".join(problems[:4]))
        return 1
    return 0


def _adaptation(root: Path) -> tuple[object, object]:
    from repcut.grading import apply as grading
    from repcut.grading.corrector import SceneTags

    theme = gym().theme("moody")  # type: ignore[attr-defined]

    def run(clip: Path) -> object:
        return asyncio.run(
            grading.scene_correction(
                clip, start_seconds=0.0, end_seconds=2.0, tags=SceneTags(), theme=theme
            )
        )

    return run(dark_warm(root)), run(window_lit(root))


def check_adaptation() -> int:
    """9b. The same theme on a dark gym and a window-lit scene corrects measurably differently."""
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        dark, window = _adaptation(Path(scratch))
    stops = dark.exposure_stops - window.exposure_stops  # type: ignore[attr-defined]
    kelvin = window.temperature_shift_k - dark.temperature_shift_k  # type: ignore[attr-defined]
    measured(
        f"moody: dark-warm {dark.exposure_stops:+.2f} stop {dark.temperature_shift_k:+.0f} K | "  # type: ignore[attr-defined]
        f"window {window.exposure_stops:+.2f} stop {window.temperature_shift_k:+.0f} K | "  # type: ignore[attr-defined]
        f"apart {stops:.2f} stop (min {ADAPT_MIN_STOPS}), "
        f"{kelvin:.0f} K (min {ADAPT_MIN_KELVIN:.0f})"
    )
    if stops <= ADAPT_MIN_STOPS or kelvin <= ADAPT_MIN_KELVIN:
        failed("the corrections are not apart by the stated margins")
        return 1
    return 0


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    return math.inf if mse == 0 else 10 * math.log10(255.0**2 / mse)


def check_intensity_zero() -> int:
    """9c. Intensity 0 is the corrected-only picture, for every theme, on decoded pixels."""
    from repcut.grading import apply as grading
    from repcut.grading.corrector import Correction
    from repcut.grading.packs import ensure_lut

    pack = gym()
    correction = Correction(exposure_stops=0.6, temperature_shift_k=-500.0, denoise=0.3)
    worst = 0.0
    psnrs: list[float] = []
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        clip = dark_warm(root)
        raw = pack.theme("raw-plus")  # type: ignore[attr-defined]
        only = still(
            clip, root / "c.jpg", grading.grade_spec(correction, raw, intensity=0, lut=None)
        )
        for theme in pack.themes:  # type: ignore[attr-defined]
            lut = ensure_lut(root / "luts", "gym", theme)
            zero = still(
                clip, root / "z.jpg", grading.grade_spec(correction, theme, intensity=0, lut=lut)
            )
            worst = max(worst, float(np.max(np.abs(zero - only))))
            psnrs.append(_psnr(zero, only))
    shown = "inf" if all(math.isinf(p) for p in psnrs) else f"{min(psnrs):.1f} dB"
    measured(f"5 themes at intensity 0 vs corrected-only: max diff {worst:.0f}, min PSNR {shown}")
    if worst != 0.0:
        failed("intensity 0 is not pixel-identical to the corrected-only render")
        return 1
    return 0


def check_grading_security() -> int:
    """9d. LUT paths resolve through the pack resolver; a malformed .cube is a named error."""
    from repcut.grading.lut import CubeFormatError, read_cube
    from repcut.grading.packs import PackLibrary, UnsafeGradingPathError, lut_path

    refused = 0
    attempts = ["../gym", "gym/../../etc", "/abs", "Gym", "gym\\x", ""]
    library = PackLibrary()
    for bad in attempts:
        try:
            library.load(bad)
        except (UnsafeGradingPathError, ValueError, LookupError):
            refused += 1
    bodies = {
        "no size": "0 0 0\n",
        "short row": "LUT_3D_SIZE 2\n" + "0 0\n" * 8,
        "wrong count": "LUT_3D_SIZE 2\n" + "0 0 0\n" * 7,
        "not a number": "LUT_3D_SIZE 2\n" + "a b c\n" * 8,
        "out of range": "LUT_3D_SIZE 2\n" + "0 0 9\n" * 8,
        "huge size": "LUT_3D_SIZE 100000\n",
    }
    named = 0
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        for name, body in bodies.items():
            path = root / f"{name.replace(' ', '-')}.cube"
            path.write_text(body, encoding="ascii")
            try:
                read_cube(path)
            except CubeFormatError:
                named += 1
            except Exception as error:  # the point is to see which class
                failed(f"{name}: raised {type(error).__name__}, not CubeFormatError")
        theme = gym().theme("moody")  # type: ignore[attr-defined]
        derived = lut_path(root / "luts", "gym", theme)
        contained = derived.resolve().is_relative_to((root / "luts").resolve())
    measured(
        f"traversal/odd pack ids refused {refused}/{len(attempts)}; malformed cubes named "
        f"{named}/{len(bodies)}; derived LUT path contained: {contained}"
    )
    if refused != len(attempts) or named != len(bodies) or not contained:
        failed("a path or a malformed .cube got through")
        return 1
    return 0


# --- 10. graded colour, read from the file -------------------------------------------


def check_graded_colour() -> int:
    """10. Graded still and preview tags under ffprobe; intensity 0 on decoded pixels, preview."""
    from repcut.grading import apply as grading
    from repcut.grading.corrector import Correction
    from repcut.grading.packs import ensure_lut

    pack = gym()
    theme = pack.theme("hardstyle")  # type: ignore[attr-defined]
    raw = pack.theme("raw-plus")  # type: ignore[attr-defined]
    correction = Correction(exposure_stops=0.4, temperature_shift_k=-300.0, denoise=0.2)
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        clip = dark_warm(root)
        lut = ensure_lut(root / "luts", "gym", theme)

        def preview(name: str, t: object, intensity: int) -> Path:
            return asyncio.run(
                grading.render_preview(
                    clip, root / name, start_seconds=0.0, end_seconds=1.0,
                    correction=correction, theme=t, pack_id="gym", intensity=intensity,
                    luts_root=root / "luts",
                )
            )  # fmt: skip

        graded = preview("graded.mp4", theme, 80)
        zero = preview("zero.mp4", theme, 0)
        only = preview("only.mp4", raw, 0)
        still(
            clip, root / "still.jpg", grading.grade_spec(correction, theme, intensity=80, lut=lut)
        )
        p_tags = probe_stream(graded)
        s_tags = probe_stream(root / "still.jpg")

        def frames(path: Path) -> np.ndarray:
            raw_bytes = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                 "zscale=m=bt709:min=bt709:rin=tv:r=pc,format=rgb24",
                 "-f", "rawvideo", "-"],
                capture_output=True, check=True, timeout=60,
            ).stdout  # fmt: skip
            return np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float64)

        a, b = frames(zero), frames(only)
    diff = float(np.max(np.abs(a - b))) if a.size == b.size else math.inf
    psnr = _psnr(a, b) if a.size == b.size else 0.0
    preview_ok = (
        p_tags.get("color_space"), p_tags.get("color_primaries"),
        p_tags.get("color_transfer"), p_tags.get("color_range"), p_tags.get("pix_fmt"),
    ) == ("bt709", "bt709", "bt709", "tv", "yuv420p")  # fmt: skip
    # A JPEG carries the JFIF convention (bt601 matrix, full range) and no
    # primaries or transfer of its own; the picture inside is bt709 RGB
    # (amendment 013). Checked as such.
    still_ok = s_tags.get("color_space") == "bt470bg" and s_tags.get("color_range") == "pc"
    measured(
        f"preview {p_tags.get('color_space')}/{p_tags.get('color_primaries')}/"
        f"{p_tags.get('color_transfer')} {p_tags.get('color_range')} {p_tags.get('pix_fmt')}; "
        f"still {s_tags.get('color_space')} {s_tags.get('color_range')} (JFIF); "
        f"preview at intensity 0 vs corrected-only: max diff {diff:.0f}, "
        f"PSNR {'inf' if math.isinf(psnr) else f'{psnr:.1f} dB'}"
    )
    if not (preview_ok and still_ok) or diff != 0.0:
        failed("a tag is wrong or intensity 0 is not the corrected-only preview")
        return 1
    return 0


# --- 11. bounded under wrong analysis ---------------------------------------------------


def check_bounded() -> int:
    """11. Wrong tags and extreme exposure stay inside +-1 stop and +-800 K; vlm null renders."""
    from repcut.grading import apply as grading
    from repcut.grading.corrector import SceneTags, correct, measure
    from repcut.grading.theme import MAX_EXPOSURE_STOPS, MAX_TEMPERATURE_SHIFT_K

    pack = gym()
    applied: list[str] = []
    worst_stops = worst_k = 0.0
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        cases = {
            "cool tag on warm light": (dark_warm(root), SceneTags("cool", "bright")),
            "warm tag on window light": (window_lit(root), SceneTags("warm", "dim")),
            "near-black": (
                lavfi_clip(root, "black", "rr=0.03:gg=0.03:bb=0.03"), SceneTags("warm", "dim")
            ),
            "near-white": (
                lavfi_clip(root, "white", "rr=1.0:gg=1.0:bb=1.0"), SceneTags("cool", "harsh")
            ),
        }  # fmt: skip
        for label, (clip, tags) in cases.items():
            frames = asyncio.run(grading.read_measure_frames(clip, [0.5, 1.0, 1.5]))
            m = measure(frames)
            for theme in pack.themes:  # type: ignore[attr-defined]
                c = correct(m, tags, theme.adapt)
                worst_stops = max(worst_stops, abs(c.exposure_stops))
                worst_k = max(worst_k, abs(c.temperature_shift_k))
            c = correct(m, tags, pack.theme("moody").adapt)  # type: ignore[attr-defined]
            applied.append(f"{label}: {c.exposure_stops:+.2f} stop {c.temperature_shift_k:+.0f} K")
        clip = cases["cool tag on warm light"][0]
        theme = pack.theme("aesthetic")  # type: ignore[attr-defined]
        correction = asyncio.run(
            grading.scene_correction(
                clip, start_seconds=0.0, end_seconds=2.0, tags=SceneTags(), theme=theme
            )
        )
        out = asyncio.run(
            grading.render_still(
                clip, root / "null.jpg", timestamp_seconds=0.5, correction=correction,
                theme=theme, pack_id="gym", intensity=80, luts_root=root / "luts",
            )
        )  # fmt: skip
        null_ok = out.is_file() and out.stat().st_size > 0
    measured(
        "; ".join(applied)
        + f" | worst over 5 themes: {worst_stops:.2f} stop (max {MAX_EXPOSURE_STOPS}), "
        f"{worst_k:.0f} K (max {MAX_TEMPERATURE_SHIFT_K:.0f}) | vlm null renders: {null_ok}"
    )
    if worst_stops > MAX_EXPOSURE_STOPS + 1e-9 or worst_k > MAX_TEMPERATURE_SHIFT_K + 1e-9:
        failed("a correction left the guide's bounds")
        return 1
    if not null_ok:
        failed("a scene with no tags did not render")
        return 1
    return 0


# --- 12. themes are data ----------------------------------------------------------------


def check_themes_are_data() -> int:
    """12. A new pack directory loads and renders with zero code change; a bad file is named."""
    from repcut.grading import apply as grading
    from repcut.grading.corrector import Correction
    from repcut.grading.packs import PackLibrary
    from repcut.grading.theme import ThemeFileError

    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        pack_dir = root / "packs" / "travel"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack.toml").write_text(
            'id = "travel"\nname = "Travel"\ndescription = "Gate fixture."\n'
            'themes = ["golden"]\ndefault_theme = "golden"\n',
            encoding="utf-8",
        )
        (pack_dir / "golden.toml").write_text(
            'id = "golden"\nname = "Golden"\ndescription = "Warm and soft."\n'
            "[look]\nwarmth = 0.04\nsaturation = 1.05\n",
            encoding="utf-8",
        )
        library = PackLibrary(root / "packs")
        pack = library.load("travel")
        theme = pack.theme("golden")
        clip = window_lit(root)
        out = asyncio.run(
            grading.render_still(
                clip, root / "golden.jpg", timestamp_seconds=0.5, correction=Correction(),
                theme=theme, pack_id="travel", intensity=100, luts_root=root / "luts",
            )
        )  # fmt: skip
        rendered = out.is_file() and decode_still(out).size > 0
        (pack_dir / "golden.toml").write_text(
            'id = "golden"\nname = "Golden"\ndescription = "x"\n[look]\nsaturation = 9.0\n',
            encoding="utf-8",
        )
        try:
            library.load("travel")
            named = "no error"
        except ThemeFileError as error:
            named = type(error).__name__
    measured(
        f"temp pack 'travel' ids {library.pack_ids()}: rendered {rendered}; bad theme -> {named}"
    )
    if not rendered or named != "ThemeFileError":
        failed("a pack directory did not render, or a bad theme was not named")
        return 1
    return 0


# --- 13. LUTs are reproducible -----------------------------------------------------------


def check_luts_reproducible() -> int:
    """13. Regenerating every LUT is byte-identical; no .cube exists without a generator entry."""
    import hashlib

    from repcut.grading.packs import PackLibrary, ensure_lut, lut_path

    library = PackLibrary()
    with TemporaryDirectory(prefix="repcut-gate04b-") as scratch:
        root = Path(scratch)
        digests: dict[str, list[str]] = {}
        expected: set[Path] = set()
        for run in ("a", "b"):
            for pack_id in library.pack_ids():
                for theme in library.load(pack_id).themes:
                    path = ensure_lut(root / run, pack_id, theme)
                    digests.setdefault(f"{pack_id}/{theme.id}", []).append(
                        hashlib.sha256(path.read_bytes()).hexdigest()
                    )
                    if run == "a":
                        expected.add(lut_path(root / "a", pack_id, theme).resolve())
        on_disk = {p.resolve() for p in (root / "a").rglob("*.cube")}
    differing = [k for k, v in digests.items() if len(set(v)) != 1]
    strays = sorted(p.name for p in on_disk - expected)
    tracked = subprocess.run(
        ["git", "ls-files", "*.cube", "**/*.cube"],
        capture_output=True, text=True, check=True, cwd=REPO_ROOT, timeout=30,
    ).stdout.split()  # fmt: skip
    measured(
        f"{len(digests)} LUTs generated twice: {len(digests) - len(differing)} byte-identical; "
        f"cubes without a generator entry: {len(strays)}; tracked .cube files: {len(tracked)}"
    )
    if differing or strays or tracked:
        failed(f"differing {differing} strays {strays} tracked {tracked}")
        return 1
    return 0


# --- 14 and 15: through the real engine ----------------------------------------------------


async def _upload(engine: object, name: str, clip: Path) -> tuple[str, str]:
    project_id = await v3.new_project(engine, name)  # type: ignore[arg-type]
    body = await v3.upload_clip(engine, project_id, clip)  # type: ignore[arg-type]
    await engine.queue.drain()  # type: ignore[attr-defined]
    return str(body["media_file_id"]), str(body["sha256"])


async def _budget(root: Path) -> dict[str, object]:
    from repcut.db.models import JobStatus

    clip = portrait(root)
    async with v3.in_process_engine(root / "data", gemini_api_key=None) as engine:
        media_file_id, _ = await _upload(engine, "gate-04 budget", clip)
        # Time to first graded frame, from a cold cache: the grade request the UI
        # makes first (it measures the scenes on the proxy) plus the still of the
        # current grade (LUT generated on first use).
        started = time.perf_counter()
        first = await engine.client.get(f"/media/{media_file_id}/grade")
        scene = first.json()["scenes"][0]["key"]
        response = await engine.client.get(
            f"/media/{media_file_id}/grade/still", params={"scene": scene}
        )
        first_s = time.perf_counter() - started
        per_theme: dict[str, float] = {}
        for theme in ["raw-plus", "moody", "hardstyle", "aesthetic", "old-school"]:
            t0 = time.perf_counter()
            r = await engine.client.get(
                f"/media/{media_file_id}/grade/still",
                params={"scene": scene, "theme": theme, "intensity": 63},
            )
            per_theme[theme] = time.perf_counter() - t0
            if r.status_code != 200:
                per_theme[theme] = math.inf
        events: list[float] = []
        with engine.queue.subscribe() as stream:
            job = (await engine.client.post(f"/media/{media_file_id}/grade/preview")).json()
            while True:
                event = await asyncio.wait_for(stream.get(), timeout=120)
                if event.job_id != job["job_id"]:
                    continue
                if event.status == JobStatus.RUNNING:
                    events.append(event.progress)
                if event.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                    final = event.status
                    break
        return {
            "first_s": first_s,
            "first_ok": first.status_code == 200 and response.status_code == 200,
            "per_theme": per_theme,
            "progress": events,
            "final": str(final),
        }


def check_preview_budget() -> int:
    """14. Graded still per theme and first graded frame under 2 s on 720x1280; job progress."""
    with TemporaryDirectory(prefix="repcut-gate04b-", ignore_cleanup_errors=True) as scratch:
        result = asyncio.run(_budget(Path(scratch)))
    per_theme: dict[str, float] = result["per_theme"]  # type: ignore[assignment]
    progress: list[float] = result["progress"]  # type: ignore[assignment]
    rising = len(progress) >= 2 and all(b >= a for a, b in itertools.pairwise(progress))
    measured(
        f"720x1280: first graded frame {result['first_s']:.2f} s; per theme "
        + ", ".join(f"{k} {v:.2f}" for k, v in per_theme.items())
        + f" s (budget {PREVIEW_BUDGET_S}); preview job {result['final']} with "
        f"{len(progress)} progress events over the socket, "
        f"last {progress[-1] if progress else 0:.2f}"
    )
    problems = []
    if not result["first_ok"] or float(result["first_s"]) > PREVIEW_BUDGET_S:  # type: ignore[arg-type]
        problems.append("first graded frame over budget")
    slow = [k for k, v in per_theme.items() if v > PREVIEW_BUDGET_S]
    if slow:
        problems.append(f"over budget: {slow}")
    if not rising or result["final"] != "succeeded":
        problems.append("the preview job did not report rising progress to success")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


async def _signals(root: Path) -> dict[str, object]:
    from repcut.db.models import GradeChoice, MediaFile, TasteEvent
    from repcut.grading import service
    from sqlalchemy import func, select

    clip = two_shot_clip(root)
    async with v3.in_process_engine(root / "data", gemini_api_key=None) as engine:
        media_file_id, _ = await _upload(engine, "gate-04 signals", clip)
        url = f"/media/{media_file_id}/grade"
        before = (await engine.client.get(url)).json()
        scenes = [s["key"] for s in before["scenes"]]
        if len(scenes) < 2:
            return {"scenes": len(scenes)}

        async def events() -> list[TasteEvent]:
            async with engine.session_factory() as session:
                rows = await session.execute(select(TasteEvent).order_by(TasteEvent.created_at))
                return list(rows.scalars().all())

        counts: dict[str, int] = {}
        await engine.client.put(f"{url}/scenes/{scenes[0]}", json={"theme_id": "hardstyle"})
        counts["scene theme"] = len(await events())
        await engine.client.put(url, json={"intensity": 55})
        counts["intensity"] = len(await events()) - counts["scene theme"]
        await engine.client.post(f"{url}/preview")
        await engine.queue.drain()
        await engine.client.put(f"{url}/scenes/{scenes[1]}", json={"lighting_temperature": "warm"})
        rows = await events()
        counts["lighting"] = len(rows) - counts["scene theme"] - counts["intensity"]
        complete = all(
            json.loads(r.recommended_json) is not None
            and "value" in json.loads(r.chosen_json)
            and json.loads(r.features_json)
            for r in rows
        )
        async with engine.session_factory() as session:
            media_file = await session.get(MediaFile, media_file_id)
            grade = await service.clip_grade(session, engine.data_dir, media_file)  # type: ignore[arg-type]
        rendered = await service.ensure_previews(engine.data_dir, grade)
        reset = (await engine.client.post(f"{url}/reset", json={"everything": True})).json()
        async with engine.session_factory() as session:
            leftover = int(
                (await session.execute(select(func.count()).select_from(GradeChoice))).scalar_one()
            )
        return {
            "scenes": len(scenes),
            "counts": counts,
            "complete": complete,
            "rendered": rendered,
            "lighting_scene": scenes[1],
            "restored": reset["suggested"] and reset["theme_id"] == before["theme_id"],
            "leftover": leftover,
            "kinds": [r.kind for r in rows],
        }


def _migration_round_trip(root: Path) -> bool:
    from alembic import command
    from repcut.config import Settings
    from repcut.db.migrations import alembic_config

    db = root / "rt.db"
    settings = Settings(
        data_dir=root / "rt-data",
        database_url=f"sqlite+aiosqlite:///{db.as_posix()}",
        gemini_api_key=None,
    )
    config = alembic_config(settings)
    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    import sqlite3

    with sqlite3.connect(db) as connection:
        tables = {r[0] for r in connection.execute("select name from sqlite_master")}
    return {"grade_choices", "taste_events"} <= tables


def check_overrides_are_signals() -> int:
    """15. Each override writes exactly one taste event; lighting re-grades one scene; reset."""
    with TemporaryDirectory(prefix="repcut-gate04b-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        result = asyncio.run(_signals(root))
        round_trip = _migration_round_trip(root)
    if result.get("scenes", 0) < 2:  # type: ignore[operator]
        measured(f"scenes detected: {result.get('scenes')}")
        failed("the two-shot fixture needs two scenes to show a one-scene re-grade")
        return 1
    counts: dict[str, int] = result["counts"]  # type: ignore[assignment]
    measured(
        f"events per override {counts}; rows carry recommended/chosen/features: "
        f"{result['complete']}; re-rendered after lighting override: "
        f"{len(result['rendered'])} of {result['scenes']} scenes "  # type: ignore[arg-type]
        f"(the overridden one: {result['rendered'] == [result['lighting_scene']]}); "
        f"reset restores suggestion: {result['restored']}, choices left {result['leftover']}; "
        f"migration 0003 round-trips: {round_trip}"
    )
    ok = (
        set(counts.values()) == {1}
        and result["complete"]
        and result["rendered"] == [result["lighting_scene"]]
        and result["restored"]
        and result["leftover"] == 0
        and round_trip
    )
    if not ok:
        failed("an override did not write exactly one complete event, or the re-grade spread")
        return 1
    return 0


# --- 16. someone can use it -------------------------------------------------------------

_USE_THE_SELECTOR = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const v = fn(); if (v) return v; await sleep(200); }
    return null;
  };
  const current = await until(() => {
    const img = document.querySelector('img[data-graded-current="true"]');
    return img && img.complete && img.naturalWidth > 0 ? img : null;
  }, 30000);
  if (!current) return {error: "no graded still rendered"};
  const pixels = (img) => {
    const c = document.createElement("canvas");
    c.width = 160; c.height = Math.max(1, Math.round(160 * img.naturalHeight / img.naturalWidth));
    const g = c.getContext("2d"); g.drawImage(img, 0, 0, c.width, c.height);
    return g.getImageData(0, 0, c.width, c.height).data;
  };
  const before = pixels(current);
  const suggestedBefore = document.body.innerText.includes("AI suggested");
  const pick = [...document.querySelectorAll('[role="radio"]')].find(
    (r) => r.getAttribute("aria-checked") !== "true");
  if (!pick) return {error: "no other theme to pick"};
  const src = current.src;
  pick.click();
  const after = await until(() => {
    const img = document.querySelector('img[data-graded-current="true"]');
    return img && img.src !== src && img.complete && img.naturalWidth > 0 ? img : null;
  }, 30000);
  if (!after) return {error: "the graded still did not change"};
  const now = pixels(after);
  let sum = 0; for (let i = 0; i < Math.min(before.length, now.length); i += 4) {
    sum += Math.abs(before[i] - now[i]) + Math.abs(before[i + 1] - now[i + 1])
      + Math.abs(before[i + 2] - now[i + 2]);
  }
  const meanDiff = sum / (3 * Math.min(before.length, now.length) / 4);
  return {
    picked: pick.innerText.trim(),
    meanDiff,
    suggestedBefore,
    yourChoice: document.body.innerText.includes("Your choice"),
    reset: !!document.querySelector('button[aria-label*="to the AI suggestion"]'),
    perScene: !!document.querySelector('select[aria-label="Scene theme"]'),
  };
})()
"""


def check_someone_can_use_it() -> int:
    """16. In a real browser on `make dev`: pick a non-default theme; the graded picture changes."""
    import dev_stack
    import verify_02_checks as v2
    from cdp_browser import BrowserNotFoundError, inspect_page

    with (
        dev_stack.DevStack(gemini_enabled=False) as stack,
        TemporaryDirectory(prefix="repcut-gate04b-", ignore_cleanup_errors=True) as scratch,
    ):
        clip = window_lit(Path(scratch))
        stack.start()
        if not stack.wait_ready():
            measured("stack did not start")
            failed("`make dev` never reached both ports; see the launcher output")
            return 1
        status, project = stack.engine_request("POST", "/projects", {"name": "gate-04 use"})
        if status != 201 or not isinstance(project, dict):
            measured(f"POST /projects -> HTTP {status}")
            failed("could not create a project against the running stack")
            return 1
        project_id = str(project["id"])
        if not v2.upload(stack.engine_port, project_id, clip).get("sha256"):
            measured("upload failed")
            failed("the fixture did not upload")
            return 1
        deadline = time.monotonic() + v2.INGEST_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline and not ready:
            _, media = stack.engine_request("GET", f"/projects/{project_id}/media")
            ready = isinstance(media, list) and bool(media) and bool(media[0].get("has_proxy"))
            if not ready:
                time.sleep(1.0)
        if not ready:
            measured("no proxy")
            failed("the fixture's proxy never became available")
            return 1
        try:
            report = asyncio.run(
                inspect_page(
                    f"http://localhost:{stack.ui_port}/projects/{project_id}",
                    observe_seconds=6.0,
                    evaluate=_USE_THE_SELECTOR,
                    evaluate_budget_s=90.0,
                )
            )
        except BrowserNotFoundError as error:
            measured("no browser")
            failed(f"cannot use the selector without a browser: {error}")
            return 1
    result = report.evaluated if isinstance(report.evaluated, dict) else {"error": "no result"}
    measured(json.dumps(result))
    if "error" in result:
        failed(str(result["error"]))
        return 1
    ok = (
        result["meanDiff"] >= UI_MIN_PIXEL_CHANGE
        and result["suggestedBefore"]
        and result["yourChoice"]
        and result["reset"]
        and result["perScene"]
    )
    if not ok:
        failed("the picture did not change enough, or a P2 control is missing")
        return 1
    return 0


# --- 17. the plan stays out ---------------------------------------------------------------


def check_plan_stays_out() -> int:
    """17. scripts/check_plan_leak.py exits 0 over tracked files."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_plan_leak.py")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )  # fmt: skip
    last = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
    measured(f"check_plan_leak.py exit {result.returncode}: {last}")
    if result.returncode != 0:
        failed("the build plan's content is in a tracked file")
        return 1
    return 0


GRADING_CHECKS: dict[str, Callable[[], int]] = {
    "themes-render": check_themes_render,
    "adaptation": check_adaptation,
    "intensity-zero": check_intensity_zero,
    "grading-security": check_grading_security,
    "graded-colour": check_graded_colour,
    "bounded": check_bounded,
    "themes-are-data": check_themes_are_data,
    "luts-reproducible": check_luts_reproducible,
    "preview-budget": check_preview_budget,
    "overrides-are-signals": check_overrides_are_signals,
    "someone-can-use-it": check_someone_can_use_it,
    "plan-stays-out": check_plan_stays_out,
}
