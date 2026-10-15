"""Themes the person saved from a reference ("save this look as a theme"): data on disk.

A saved theme is one TOML file under ``$DATA_DIR/grading/saved/``, in exactly
the shape of a built-in theme, validated by the same ``load_theme`` and so
bound by the same caps. Saved themes join the built-in pack at the end of its
list (``with_saved``), so the theme selector, the grade, the edit's pace and
the export all resolve them like any other theme, and they survive a restart
because they are files. Ids are ``ref-<slug>``: they can never shadow a
built-in theme, and they are slugs, so they are safe path components.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from repcut.grading.packs import LoadedPack, UnsafeGradingPathError
from repcut.grading.theme import SLUG_PATTERN, Theme, ThemeFileError, load_theme
from repcut.logging import get_logger

logger = get_logger(__name__)

SAVED_PREFIX = "ref-"
MAX_SAVED_THEMES = 64
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def saved_root(data_dir: Path) -> Path:
    return data_dir / "grading" / "saved"


def slug_for(name: str, taken: set[str]) -> str:
    """``ref-<words of the name>``, unique among ``taken``, always a valid theme id."""
    words = _NON_SLUG.sub("-", name.casefold()).strip("-")[:28].strip("-") or "look"
    base = f"{SAVED_PREFIX}{words}"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base[:36]}-{n}"
        n += 1
    if not SLUG_PATTERN.match(candidate):
        raise UnsafeGradingPathError("saved theme id is not a slug")
    return candidate


def _path(data_dir: Path, theme_id: str) -> Path:
    if not SLUG_PATTERN.match(theme_id) or not theme_id.startswith(SAVED_PREFIX):
        raise UnsafeGradingPathError("not a saved theme id")
    root = saved_root(data_dir).resolve()
    candidate = (root / f"{theme_id}.toml").resolve()
    if not candidate.is_relative_to(root):
        raise UnsafeGradingPathError("resolves outside the saved themes")
    return candidate


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, dict):
        inner = ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items() if v is not None)
        return "{ " + inner + " }"
    raise TypeError(f"no TOML form for {type(value).__name__}")


def theme_toml(theme: Theme) -> str:
    """The theme as a theme file. Round-trips through ``load_theme`` to an equal Theme."""
    data = theme.model_dump(mode="python")
    lines = ["# Saved from a reference clip by Repcut. A theme file like any other."]
    for key in ("id", "name", "description", "default_intensity"):
        lines.append(f"{key} = {_toml_value(data[key])}")
    for table in ("look", "finish", "recommend", "pace"):
        lines.append("")
        lines.append(f"[{table}]")
        for key, value in data[table].items():
            if value is None or value == {}:
                continue
            lines.append(f"{key} = {_toml_value(value)}")
    for rule in data["adapt"]:
        lines.append("")
        lines.append("[[adapt]]")
        lines.append(f"when = {_toml_value(rule['when'])}")
        for key in ("exposure_stops", "temperature_shift_k", "denoise"):
            lines.append(f"{key} = {_toml_value(rule[key])}")
    return "\n".join(lines) + "\n"


def list_saved(data_dir: Path) -> list[Theme]:
    """Every valid saved theme, oldest first. A file that no longer validates is skipped, logged."""
    root = saved_root(data_dir)
    if not root.is_dir():
        return []
    themes: list[tuple[float, Theme]] = []
    for path in root.glob(f"{SAVED_PREFIX}*.toml"):
        try:
            themes.append((path.stat().st_mtime, load_theme(path)))
        except (ThemeFileError, OSError) as exc:
            logger.warning("saved_theme_invalid", file=path.name, error=str(exc)[:160])
    return [theme for _, theme in sorted(themes, key=lambda pair: (pair[0], pair[1].id))]


def save(data_dir: Path, theme: Theme) -> Theme:
    """Write a saved theme atomically, then read it back through ``load_theme``."""
    path = _path(data_dir, theme.id)
    if not path.exists() and len(list_saved(data_dir)) >= MAX_SAVED_THEMES:
        raise ThemeFileError("too many saved themes; delete one first")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".toml.part")
    partial.write_text(theme_toml(theme), encoding="utf-8")
    os.replace(partial, path)
    loaded = load_theme(path)
    if loaded != theme:
        path.unlink(missing_ok=True)
        raise ThemeFileError("the saved theme did not read back as written")
    return loaded


def delete(data_dir: Path, theme_id: str) -> bool:
    path = _path(data_dir, theme_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def with_saved(pack: LoadedPack, data_dir: Path | None) -> LoadedPack:
    """The pack plus the person's saved themes, after its own (built-in order wins ties)."""
    if data_dir is None:
        return pack
    extra = [t for t in list_saved(data_dir) if t.id not in pack.manifest.themes]
    if not extra:
        return pack
    manifest = pack.manifest.model_copy(
        update={"themes": [*pack.manifest.themes, *(t.id for t in extra)]}
    )
    return LoadedPack(manifest=manifest, themes=(*pack.themes, *extra))


__all__ = [
    "MAX_SAVED_THEMES",
    "SAVED_PREFIX",
    "delete",
    "list_saved",
    "save",
    "saved_root",
    "slug_for",
    "theme_toml",
    "with_saved",
]
