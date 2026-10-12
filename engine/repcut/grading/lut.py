"""3D LUTs, generated here from a theme's ``[look]`` numbers (amendment 012 row 7).

No LUT is downloaded and none is committed (``*.cube`` is gitignored): a
theme's LUT is a pure function of its ``Look``, this module's
``LUT_GENERATOR_VERSION`` and ``LUT_SIZE``, written once to the LUT cache under
``$DATA_DIR`` and named by the digest of those inputs. Regenerating it is
byte-identical, which makes "the LUT on disk is the one the parameters
describe" a checkable claim (verify-04 criterion 13).

``read_cube`` is the only way a ``.cube`` reaches FFmpeg: a cached file is
parsed and checked before it is used, and anything malformed raises
``CubeFormatError`` by name instead of reaching a C parser.

Skin protection lives in the generator (the guide's constraint: the LUT
preserves the skin-tone region within a tolerance). An input colour in the
skin-hue band keeps its hue through the look, weighted by
``Look.skin_protection``; lightness and saturation still follow the theme, so
a dark theme still darkens a face, it just never turns it orange or grey-green.
This holds hue only - it never smooths, softens or retouches (P1).
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from repcut.grading.theme import Look, SplitTone

LUT_SIZE = 33
# Bump with any change to apply_look or the file format: the digest, and so
# every cached LUT's name, moves with it.
LUT_GENERATOR_VERSION = 2
# Where gamut compression starts, as a fraction of the way to the ceiling
# (see ``_compress_gamut``), and the ceiling: a look leaves this much headroom
# below full scale, unless the source already used it.
GAMUT_KNEE = 0.85
GAMUT_CEILING = 0.985
# A real .cube from a colourist tool is well under this; ours is ~1 MB at 33^3.
MAX_CUBE_BYTES = 16 * 1024 * 1024
MAX_CUBE_SIZE = 65

# Skin band in HSV hue degrees, centred on the hue of skin across light to dark
# complexions under neutral light (roughly 10-40 degrees), with soft edges.
SKIN_HUE_CENTRE = 24.0
SKIN_HUE_SIGMA = 14.0

Floats = NDArray[np.float64]


class CubeFormatError(ValueError):
    """A ``.cube`` file this module refuses to hand to FFmpeg."""


# --- the look ---------------------------------------------------------------


def _luma(rgb: Floats) -> Floats:
    return np.asarray(rgb @ np.array([0.2126, 0.7152, 0.0722]), dtype=np.float64)


def _tone(y: Floats, look: Look) -> Floats:
    t = np.clip(y, 0.0, 1.0)
    if look.contrast != 0.0:
        p, k = look.pivot, 1.0 + look.contrast
        low = p * np.power(np.clip(t / p, 0.0, None), k)
        high = 1.0 - (1.0 - p) * np.power(np.clip((1.0 - t) / (1.0 - p), 0.0, None), k)
        t = np.where(t < p, low, high)
    if look.black_crush > 0.0:
        t = np.clip((t - look.black_crush) / (1.0 - look.black_crush), 0.0, 1.0)
    if look.highlight_rolloff > 0.0:
        r = look.highlight_rolloff
        knee = 1.0 - 2.0 * r
        over = np.clip(t - knee, 0.0, None)
        t = np.where(t > knee, knee + over * (1.0 - r * over / (1.0 - knee)), t)
    if look.black_lift > 0.0:
        t = look.black_lift + t * (1.0 - look.black_lift)
    return np.asarray(t, dtype=np.float64)


def _hue_vector(hue_degrees: float) -> Floats:
    """A zero-luma tint pointing at ``hue_degrees``."""
    rgb = _hsv_to_rgb(np.array([hue_degrees / 360.0]), np.array([1.0]), np.array([1.0])).reshape(3)
    return np.asarray(rgb - _luma(rgb.reshape(1, 3))[0], dtype=np.float64)


def _split(rgb: Floats, y: Floats, tone: SplitTone | None, weight: Floats) -> Floats:
    if tone is None or tone.strength == 0.0:
        return rgb
    return rgb + np.outer(weight * tone.strength, _hue_vector(tone.hue))


def _rgb_to_hsv(rgb: Floats) -> tuple[Floats, Floats, Floats]:
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    v = rgb.max(axis=1)
    c = v - rgb.min(axis=1)
    safe_c = np.where(c > 0, c, 1.0)
    h = np.where(
        v == r,
        ((g - b) / safe_c) % 6.0,
        np.where(v == g, (b - r) / safe_c + 2.0, (r - g) / safe_c + 4.0),
    )
    h = np.where(c > 0, h / 6.0, 0.0)
    s = np.where(v > 0, c / np.where(v > 0, v, 1.0), 0.0)
    return h, s, v


def _hsv_to_rgb(h: Floats, s: Floats, v: Floats) -> Floats:
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1.0 - s), v * (1.0 - f * s), v * (1.0 - (1.0 - f) * s)
    i = i.astype(int) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


def _smoothstep(edge0: float, edge1: float, x: Floats) -> Floats:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return np.asarray(t * t * (3.0 - 2.0 * t), dtype=np.float64)


def skin_weight(rgb: Floats) -> Floats:
    """0-1: how skin-like each input colour is (hue band, plausible saturation, not black)."""
    h, s, v = _rgb_to_hsv(np.clip(rgb, 0.0, 1.0))
    hue = h * 360.0
    distance = np.abs((hue - SKIN_HUE_CENTRE + 180.0) % 360.0 - 180.0)
    band = np.exp(-0.5 * (distance / SKIN_HUE_SIGMA) ** 2)
    plausible = _smoothstep(0.08, 0.2, s) * (1.0 - _smoothstep(0.7, 0.85, s))
    lit = _smoothstep(0.06, 0.16, v)
    return np.asarray(band * plausible * lit, dtype=np.float64)


def _gamut_reach(rgb: Floats, ceiling: Floats) -> Floats:
    """How far each colour's chroma reaches toward the box [0, ceiling]^3, about its own luma.

    1.0 is on the surface (a channel at exactly 0 or at the ceiling), above 1
    is outside it. A grey (no chroma) reaches 0.
    """
    y = np.clip(_luma(rgb), 0.0, 1.0)
    d = rgb - y[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        up = np.where(d > 1e-9, d / np.maximum(ceiling - y, 1e-9)[:, None], 0.0)
        down = np.where(d < -1e-9, -d / np.maximum(y, 1e-9)[:, None], 0.0)
    return np.asarray(np.maximum(up.max(axis=1), down.max(axis=1)), dtype=np.float64)


def _compress_gamut(source: Floats, out: Floats) -> Floats:
    """Pull chroma a look pushed out of gamut back inside, softly, keeping luma and hue.

    Without this, a contrast or saturation boost drives a bright, saturated
    colour (a red shirt under a window) through the cube's surface, and the
    final clip flattens it into a patch of one value with a shifted hue: the
    artifact verify-04 criterion 9 measured on Hardstyle. Only what the look
    pushed further out than the source is touched, so an identity look stays
    an identity; the knee keeps the transition continuous. The ceiling sits a
    little under full scale so a pushed colour keeps its gradations instead of
    landing on the last code value.
    """
    ceiling = np.maximum(source.max(axis=1), GAMUT_CEILING)
    reach_out = _gamut_reach(out, ceiling)
    floor = np.maximum(_gamut_reach(source, ceiling), GAMUT_KNEE)
    over = reach_out > floor + 1e-9
    if not np.any(over):
        return out
    # A source already on the surface may not be pushed past it: map to just inside.
    floor = np.minimum(floor, 0.999)
    span = 1.0 - floor
    target = floor + span * np.tanh((reach_out - floor) / span)
    scale = np.where(over, target / np.where(over, reach_out, 1.0), 1.0)
    y = np.clip(_luma(out), 0.0, 1.0)
    return np.asarray(y[:, None] + scale[:, None] * (out - y[:, None]), dtype=np.float64)


def apply_look(rgb: Floats, look: Look) -> Floats:
    """The theme's look on display-referred bt709 RGB in [0, 1], shape (N, 3)."""
    source = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    y = _luma(source)
    toned = _tone(y, look)
    ratio = np.where(y > 1e-6, toned / np.where(y > 1e-6, y, 1.0), 0.0)
    out = np.where((y > 1e-6)[:, None], source * ratio[:, None], toned[:, None])

    y2 = _luma(out)
    out = y2[:, None] + look.saturation * (out - y2[:, None])
    if look.warmth != 0.0:
        mid = 4.0 * y2 * (1.0 - y2)
        out = out + np.outer(mid * look.warmth, np.array([1.0, 0.0, -1.0]))
    out = _split(out, y2, look.shadows, (1.0 - y2) ** 2)
    out = _split(out, y2, look.highlights, y2**2)
    out = np.clip(_compress_gamut(source, out), 0.0, 1.0)

    if look.skin_protection > 0.0:
        weight = skin_weight(source) * look.skin_protection
        h_in, _, _ = _rgb_to_hsv(source)
        h_out, s_out, v_out = _rgb_to_hsv(out)
        delta = (h_in - h_out + 0.5) % 1.0 - 0.5
        held = _hsv_to_rgb((h_out + weight * delta) % 1.0, s_out, v_out)
        out = np.where((weight > 0)[:, None], held, out)
    return np.clip(out, 0.0, 1.0)


# --- the file ---------------------------------------------------------------


def look_digest(look: Look) -> str:
    """Names the LUT: changes when, and only when, its bytes would."""
    payload = f"{LUT_GENERATOR_VERSION}|{LUT_SIZE}|{look.model_dump_json()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def lut_grid(size: int = LUT_SIZE) -> Floats:
    """Every grid point, red fastest - the .cube order."""
    axis = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)


def cube_text(look: Look, *, title: str, size: int = LUT_SIZE) -> str:
    table = apply_look(lut_grid(size), look)
    # Rounded to 9 places first so a last-bit difference in a transcendental
    # function cannot flip the 6th decimal of an otherwise identical value.
    table = np.round(table, 9)
    lines = [
        "# Repcut LUT, generated by repcut.grading.lut from a theme's [look]. Do not edit.",
        f"# generator={LUT_GENERATOR_VERSION} digest={look_digest(look)}",
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table)
    return "\n".join(lines) + "\n"


def write_cube(path: Path, look: Look, *, title: str) -> None:
    """Atomic: a reader never sees half a LUT."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temp.write_bytes(cube_text(look, title=title).encode("ascii"))
    os.replace(temp, path)


def read_cube(path: Path) -> tuple[int, Floats]:
    """Parse and check a .cube; raise ``CubeFormatError`` naming what is wrong."""
    try:
        size_on_disk = path.stat().st_size
    except OSError as exc:
        raise CubeFormatError(f"{path.name}: cannot be read ({exc.strerror})") from exc
    if size_on_disk > MAX_CUBE_BYTES:
        raise CubeFormatError(f"{path.name}: {size_on_disk} bytes, over {MAX_CUBE_BYTES}")
    try:
        text = path.read_bytes().decode("ascii")
    except UnicodeDecodeError as exc:
        raise CubeFormatError(f"{path.name}: not ASCII text") from exc

    size: int | None = None
    domain_min, domain_max = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    rows: list[tuple[float, float, float]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword = line.split(maxsplit=1)[0]
        if keyword == "TITLE":
            continue
        if keyword == "LUT_3D_SIZE":
            if size is not None or rows:
                raise CubeFormatError(f"{path.name}:{number}: LUT_3D_SIZE out of place")
            size = _parse_size(path, number, line)
            continue
        if keyword in {"DOMAIN_MIN", "DOMAIN_MAX"}:
            values = _parse_triple(path, number, line.split()[1:])
            if keyword == "DOMAIN_MIN":
                domain_min = values
            else:
                domain_max = values
            continue
        if keyword[0].isalpha():
            raise CubeFormatError(f"{path.name}:{number}: unsupported keyword {keyword[:20]!r}")
        rows.append(_parse_triple(path, number, line.split()))

    if size is None:
        raise CubeFormatError(f"{path.name}: no LUT_3D_SIZE")
    if any(lo >= hi for lo, hi in zip(domain_min, domain_max, strict=True)):
        raise CubeFormatError(f"{path.name}: DOMAIN_MIN is not below DOMAIN_MAX")
    if len(rows) != size**3:
        raise CubeFormatError(f"{path.name}: {len(rows)} rows, expected {size**3}")
    table = np.array(rows, dtype=np.float64)
    low, high = np.array(domain_min), np.array(domain_max)
    if np.any(table < low - 1e-6) or np.any(table > high + 1e-6):
        raise CubeFormatError(f"{path.name}: a value lies outside the domain")
    return size, table


def _parse_size(path: Path, number: int, line: str) -> int:
    parts = line.split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise CubeFormatError(f"{path.name}:{number}: LUT_3D_SIZE is not an integer")
    size = int(parts[1])
    if not 2 <= size <= MAX_CUBE_SIZE:
        raise CubeFormatError(f"{path.name}:{number}: LUT_3D_SIZE {size} outside 2-65")
    return size


def _parse_triple(path: Path, number: int, parts: list[str]) -> tuple[float, float, float]:
    if len(parts) != 3:
        raise CubeFormatError(f"{path.name}:{number}: expected three numbers")
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise CubeFormatError(f"{path.name}:{number}: not a number") from exc
    if not all(math.isfinite(value) for value in values):
        raise CubeFormatError(f"{path.name}:{number}: not a finite number")
    return values[0], values[1], values[2]


__all__ = [
    "LUT_GENERATOR_VERSION",
    "LUT_SIZE",
    "CubeFormatError",
    "apply_look",
    "cube_text",
    "look_digest",
    "lut_grid",
    "read_cube",
    "skin_weight",
    "write_cube",
]
