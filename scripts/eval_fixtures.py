#!/usr/bin/env python3
"""The eval's real-footage fixtures: cut from Ashwin's own clips, kept local.

``engine/tests/fixtures/eval_fixtures.json`` (plus ``rep_labels.json``) names
each fixture by the source clip's sha256 and a span. This script finds those
clips by hash in a footage folder and cuts the spans into
``$DATA_DIR/fixtures/`` - outside the repo's tracked files, never uploaded
(P4), never committed (``.claude/rules/secrets.md``). Synthetic media is not a
substitute: the eval measures real footage or reports the gate as BOUND.

    python scripts/eval_fixtures.py status
    python scripts/eval_fixtures.py propose --footage <folder>
    python scripts/eval_fixtures.py build --footage <folder>

``propose`` prints every video's sha256, length, frame rate and VFR flag so
the manifest can be filled in by hand; it writes nothing. ``build`` re-encodes
each span frame-accurately with its timestamps passed through (a VFR source
stays VFR) and refuses a corpus over 100 MB. Nothing printed carries a path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "engine" / "tests" / "fixtures"
MANIFEST = FIXTURES / "eval_fixtures.json"
REP_LABELS = FIXTURES / "rep_labels.json"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv"}
MAX_TOTAL_BYTES = 100 * 1024 * 1024
ROLES = ("session", "known_audio", "barbell_artifact", "walking_subject", "skin", "reps")
# The spans' expected counts: the gates need at least this many.
MINIMUM = {
    "session": 3,
    "known_audio": 1,
    "barbell_artifact": 1,
    "walking_subject": 1,
    "skin": 2,
    "reps": 3,
}


@dataclass(frozen=True)
class Fixture:
    role: str
    index: int
    sha256: str
    start: float
    end: float
    label: dict[str, object]

    @property
    def name(self) -> str:
        return f"{self.role}-{self.index}-{self.sha256[:12]}.mp4"


def fixtures_dir() -> Path:
    """``$REPCUT_FIXTURES_DIR``, else ``$DATA_DIR/fixtures``, else ``data/fixtures``."""
    override = os.environ.get("REPCUT_FIXTURES_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "data")) / "fixtures"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def manifest_fixtures() -> list[Fixture]:
    """Every span the manifest and the rep labels name, in a stable order."""
    data = _load(MANIFEST)
    out: list[Fixture] = []
    for role in ROLES[:-1]:
        for i, entry in enumerate(data.get(role, []) or []):  # type: ignore[union-attr]
            e = dict(entry)
            if role == "skin":
                t = float(e["time"])
                start, end = max(0.0, t - 0.5), t + 0.5
            else:
                start, end = float(e["start"]), float(e["end"])
            out.append(Fixture(role, i, str(e["sha256"]), start, end, e))
    for i, entry in enumerate(_load(REP_LABELS).get("segments", []) or []):  # type: ignore[union-attr]
        e = dict(entry)
        out.append(Fixture("reps", i, str(e["sha256"]), float(e["start"]), float(e["end"]), e))
    return out


def by_role(role: str) -> list[tuple[Fixture, Path]]:
    """The built fixtures of one role that exist on disk, with their files."""
    root = fixtures_dir()
    return [
        (f, root / f.name)
        for f in manifest_fixtures()
        if f.role == role and (root / f.name).is_file()
    ]


def missing_roles() -> dict[str, str]:
    """role -> why it cannot be measured here; empty when every role is ready."""
    named = manifest_fixtures()
    why: dict[str, str] = {}
    for role in ROLES:
        n_named = sum(1 for f in named if f.role == role)
        n_built = len(by_role(role))
        if n_named < MINIMUM[role]:
            why[role] = f"{n_named} of {MINIMUM[role]} spans labelled"
        elif n_built < n_named:
            why[role] = f"{n_built} of {n_named} built (run eval_fixtures.py build)"
    return why


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _index(footage: Path) -> dict[str, Path]:
    """sha256 -> file for every video under ``footage``, cached by size+mtime."""
    cache_file = fixtures_dir() / ".hash-cache.json"
    cache = _load(cache_file)
    found: dict[str, Path] = {}
    fresh: dict[str, str] = {}
    for path in sorted(footage.rglob("*")):
        if path.suffix.lower() not in VIDEO_SUFFIXES or not path.is_file():
            continue
        stat = path.stat()
        key = hashlib.sha256(
            f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()
        ).hexdigest()
        sha = str(cache.get(key) or _sha256(path))
        fresh[key] = sha
        found[sha] = path
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(fresh), encoding="utf-8")
    return found


def _probe(path: Path) -> dict[str, object]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate,width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    doc = json.loads(out.stdout or "{}")
    stream = (doc.get("streams") or [{}])[0]

    def rate(text: str) -> float:
        num, _, den = (text or "0/1").partition("/")
        return float(num) / float(den or 1) if float(den or 1) else 0.0

    r, avg = rate(stream.get("r_frame_rate", "")), rate(stream.get("avg_frame_rate", ""))
    return {
        "seconds": round(float(doc.get("format", {}).get("duration", 0.0)), 2),
        "size": f"{stream.get('width')}x{stream.get('height')}",
        "fps": round(avg, 3),
        "vfr": bool(r and avg and abs(r - avg) > 0.01 * r),
    }


def _colour_tags(path: Path) -> list[str]:
    """The source's own colour tags, carried onto the fixture so the engine's
    proxy normalises an HDR fixture exactly as it would the original clip."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_primaries,color_transfer,color_space,color_range",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    stream = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
    flags = {
        "color_primaries": "-color_primaries",
        "color_transfer": "-color_trc",
        "color_space": "-colorspace",
        "color_range": "-color_range",
    }
    args: list[str] = []
    for key, flag in flags.items():
        value = str(stream.get(key) or "")
        if value and value != "unknown" and value.replace("-", "").replace("_", "").isalnum():
            args += [flag, value]
    return args


def cut(source: Path, fixture: Fixture, target: Path) -> None:
    """Frame-accurate span, timestamps passed through (VFR stays VFR), 10-bit
    with the source's colour tags (HDR stays HDR), long side <= 1920."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{fixture.start:.3f}",
            "-to",
            f"{fixture.end:.3f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-fps_mode",
            "passthrough",
            "-vf",
            "scale='if(gt(iw,ih),min(1920,iw),-2)':'if(gt(iw,ih),-2,min(1920,ih))'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p10le",
            *_colour_tags(source),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(partial),
        ],
        check=True,
        timeout=600,
    )
    partial.replace(target)


def status() -> int:
    root = fixtures_dir()
    total = sum(p.stat().st_size for p in root.glob("*.mp4")) if root.is_dir() else 0
    for role in ROLES:
        named = sum(1 for f in manifest_fixtures() if f.role == role)
        print(f"{role:17s} labelled {named} (need {MINIMUM[role]}), built {len(by_role(role))}")
    vfr = [p for _, p in by_role("session") if _probe(p)["vfr"]]
    print(f"session VFR clips built: {len(vfr)} (need 1)")
    print(f"total {total / 2**20:.1f} MiB of {MAX_TOTAL_BYTES / 2**20:.0f} MiB")
    return 0 if not missing_roles() and vfr and total <= MAX_TOTAL_BYTES else 3


def propose(footage: Path) -> int:
    for sha, path in sorted(_index(footage).items(), key=lambda kv: kv[0]):
        info = _probe(path)
        print(
            f"{sha}  {info['seconds']:>7}s  {info['size']:>10}  {info['fps']:>7} fps  "
            f"{'VFR' if info['vfr'] else 'CFR'}"
        )
    return 0


def build(footage: Path) -> int:
    index = _index(footage)
    root = fixtures_dir()
    problems: list[str] = []
    for fixture in manifest_fixtures():
        target = root / fixture.name
        if target.is_file():
            continue
        source = index.get(fixture.sha256)
        if source is None:
            problems.append(f"{fixture.role} {fixture.index}: clip {fixture.sha256[:8]} not found")
            continue
        cut(source, fixture, target)
        print(f"built {fixture.name}")
    total = sum(p.stat().st_size for p in root.glob("*.mp4")) if root.is_dir() else 0
    if total > MAX_TOTAL_BYTES:
        problems.append(
            f"corpus is {total / 2**20:.0f} MiB, over {MAX_TOTAL_BYTES / 2**20:.0f} MiB"
        )
    for problem in problems:
        print(f"FAILED: {problem}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("propose", "build"):
        sub.add_parser(name).add_argument("--footage", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "status":
        return status()
    if not args.footage.is_dir():
        print("FAILED: --footage is not a folder")
        return 1
    return propose(args.footage) if args.command == "propose" else build(args.footage)


if __name__ == "__main__":
    sys.exit(main())
