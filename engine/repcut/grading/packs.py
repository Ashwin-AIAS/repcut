"""Theme packs on disk, and the LUT cache, behind one resolver (amendment 012 row 6).

The guide asks that LUT paths "resolve through the store". ``store.absolute()``
rightly refuses anything outside ``$DATA_DIR``, and built-in packs ship inside
the package, so this module gives pack and LUT paths the store's three
guarantees instead of bending that function:

1. every id that becomes a path component is a slug (``SLUG_PATTERN``);
2. both sides are ``resolve()``d and the result must sit inside its root;
3. no caller ever supplies a path - only ids, from which the path is derived.

Themes are data: a new pack is a new directory holding ``pack.toml`` and one
TOML file per theme, with zero engine changes (verify-04 criterion 12).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from repcut.grading.lut import CubeFormatError, look_digest, read_cube, write_cube
from repcut.grading.theme import (
    SLUG_PATTERN,
    Pack,
    Theme,
    ThemeFileError,
    load_pack_manifest,
    load_theme,
)

log = structlog.get_logger(__name__)

BUILTIN_PACKS_ROOT = Path(__file__).resolve().parent / "packs"
MANIFEST = "pack.toml"


class PackNotFoundError(LookupError):
    """No pack with that id under the pack root."""


class ThemeNotFoundError(LookupError):
    """The pack has no theme with that id."""


class UnsafeGradingPathError(ValueError):
    """An id that is not a slug, or a path that resolves outside its root."""


def _slug(kind: str, value: str) -> str:
    if not SLUG_PATTERN.match(value):
        raise UnsafeGradingPathError(f"{kind} id is not a slug")
    return value


def _contained(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root) or candidate == resolved_root:
        raise UnsafeGradingPathError("resolves outside its root")
    return candidate


@dataclass(frozen=True, slots=True)
class LoadedPack:
    manifest: Pack
    themes: tuple[Theme, ...]

    def theme(self, theme_id: str) -> Theme:
        for theme in self.themes:
            if theme.id == theme_id:
                return theme
        raise ThemeNotFoundError(f"pack {self.manifest.id!r} has no theme {theme_id!r}")


class PackLibrary:
    """Every pack under one root. The built-in root by default; tests pass their own."""

    def __init__(self, root: Path = BUILTIN_PACKS_ROOT) -> None:
        self.root = root.resolve()

    def pack_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.root.iterdir()
            if entry.is_dir() and SLUG_PATTERN.match(entry.name) and (entry / MANIFEST).is_file()
        )

    def load(self, pack_id: str) -> LoadedPack:
        directory = _contained(self.root, _slug("pack", pack_id))
        manifest_path = directory / MANIFEST
        if not manifest_path.is_file():
            raise PackNotFoundError(f"no pack {pack_id!r}")
        manifest = load_pack_manifest(manifest_path)
        if manifest.id != pack_id:
            raise ThemeFileError(f"{MANIFEST}: id {manifest.id!r} is not its directory's name")
        listed = set(manifest.themes)
        strays = sorted(
            path.name
            for path in directory.glob("*.toml")
            if path.name != MANIFEST and path.stem not in listed
        )
        if strays:
            raise ThemeFileError(
                f"pack {pack_id!r}: theme files not listed in {MANIFEST}: {strays}"
            )
        themes = tuple(
            load_theme(_contained(directory, f"{_slug('theme', theme_id)}.toml"))
            for theme_id in manifest.themes
        )
        return LoadedPack(manifest=manifest, themes=themes)

    def theme(self, pack_id: str, theme_id: str) -> Theme:
        return self.load(pack_id).theme(_slug("theme", theme_id))


def lut_path(luts_root: Path, pack_id: str, theme: Theme) -> Path:
    """Where a theme's LUT lives: derived from ids and the look's digest, never passed in."""
    name = f"{_slug('theme', theme.id)}-{look_digest(theme.look)}.cube"
    return _contained(luts_root, _slug("pack", pack_id), name)


def ensure_lut(luts_root: Path, pack_id: str, theme: Theme) -> Path:
    """The theme's LUT, generated on first use and checked by ``read_cube`` every time.

    A cached file that no longer parses is regenerated (it is a cache, and its
    name says what it must contain); the regenerated file is parsed again, so a
    generator bug raises ``CubeFormatError`` instead of reaching FFmpeg.
    """
    path = lut_path(luts_root, pack_id, theme)
    if path.is_file():
        try:
            read_cube(path)
            return path
        except CubeFormatError as exc:
            log.warning("lut_cache_invalid", pack=pack_id, theme=theme.id, error=str(exc))
    write_cube(path, theme.look, title=f"{pack_id}/{theme.id}")
    read_cube(path)
    return path


__all__ = [
    "BUILTIN_PACKS_ROOT",
    "LoadedPack",
    "PackLibrary",
    "PackNotFoundError",
    "ThemeNotFoundError",
    "UnsafeGradingPathError",
    "ensure_lut",
    "lut_path",
]
