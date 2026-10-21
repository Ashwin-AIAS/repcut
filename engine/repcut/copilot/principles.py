"""Natural only (P1), checked before the model is asked anything.

The copilot's system prompt states the principle, but a principle that only a
model enforces is enforced only as often as the model agrees. So requests that
plainly ask to generate, replace or reshape what the camera captured are
refused here, by pattern, with no request sent. The model remains the second
line (``refuse_request``) for phrasings no pattern names.

Patterns are deliberately about *what is asked for* (a new background, a
changed body or face, inserted or generated content), not about words alone:
"remove the second cut" and "a darker look" must pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PRINCIPLE = "natural only"

REFUSAL = (
    "I can't do that one: Repcut is natural only. It enhances what you filmed "
    "(grade, cuts, timing, captions, up to 2x slow motion) and never generates, "
    "replaces or reshapes footage, bodies or faces."
)

_BODY = (
    r"(me|my|him|her|them|body|arms?|legs?|chest|shoulders?|waist|abs|muscles?|physique"
    r"|biceps?|glutes?|face|jaw|skin)"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "background replacement",
            # "background music" / "background noise" are sound, not the picture.
            r"\b(replace|swap|change|new|different|fake|remove)\b[^.?!]{0,30}\bbackground\b"
            r"(?!\s+(music|noise|sound|track|audio|song|hum))"
            r"|\bbackground\b(?!\s+(music|noise|sound|track|audio|song|hum))"
            r"[^.?!]{0,20}\b(replace|swap|beach|studio|green ?screen)",
        ),
        (
            "body or face editing",
            rf"\b(make|look|appear|edit|reshape|slim|enlarge)\b[^.?!]{{0,25}}\b{_BODY}\b[^.?!]{{0,25}}"
            r"\b(bigger|larger|leaner|slimmer|thinner|wider"
            r"|more (muscular|ripped|defined|jacked|toned)|"
            r"jacked|ripped|swole|taller|shredded)\b"
            r"|\b(bigger|larger|leaner|slimmer|thinner|wider|more muscular|jacked|ripped|swole|"
            r"taller|shredded)\b[^.?!]{0,20}\b(arms?|legs?|chest|shoulders?|waist|abs|muscles?|"
            r"physique|biceps?|glutes?|me)\b"
            r"|\b(smooth|retouch|airbrush|beautify)\b[^.?!]{0,20}\b(skin|face)\b"
            r"|\b(face ?swap|change my face|whiten my teeth)\b",
        ),
        (
            "inserted or generated content",
            r"\b(add|insert|put|generate|create|paint in)\b[^.?!]{0,30}"
            r"\b(crowd|people|person|logo|object|weights?|plates?|dumbbells?|equipment"
            r"|fake reps?|reps? that|explosion|fire|lightning|sparks)\b"
            r"|\b(ai|generative)\b[^.?!]{0,15}\b(fill|extend|expand|generate|outpaint)"
            r"|\boutpaint|\bgenerative fill\b"
            r"|\b(add|insert)\b[^.?!]{0,15}\b(extra|more)\s+(reps?|plates?|weight)\b",
        ),
        (
            "slow motion beyond 2x",
            r"\b([3-9]|[1-9][0-9]+)\s*x\b[^.?!]{0,20}\bslow"
            r"|\bslow[^.?!]{0,25}\b([3-9]|[1-9][0-9]+)\s*(x|times)\b"
            r"|\b(super|ultra|extreme)[ -]?slow",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    rule: str | None = None


def check(text: str) -> Verdict:
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return Verdict(allowed=False, rule=name)
    return Verdict(allowed=True)


__all__ = ["PRINCIPLE", "REFUSAL", "Verdict", "check"]
