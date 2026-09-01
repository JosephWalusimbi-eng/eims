"""Static checks on the manuscript that a LaTeX compile would not catch early.

Verifies cross-reference and citation integrity, absence of unfilled
placeholders, ASCII cleanliness, and gives a length estimate. Run before every
submission; it is faster than a compile and catches the errors that survive one
(a dangling \\ref compiles to "??" without failing).
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

def _default_manuscript():
    """The manuscript is not part of the code artifact, so search a few likely
    locations and ask for an explicit path rather than guessing wrongly."""
    here = Path(__file__).resolve()
    for candidate in [here.parents[1], *here.parents[1:4]]:
        for rel in ("EIMS_IEEE_NCC/name.tex", "name.tex"):
            hit = candidate / rel
            if hit.exists():
                return hit
    return None


DEFAULT = _default_manuscript()


def check(path: Path) -> int:
    s = io.open(path, encoding="utf-8").read()
    problems = 0

    labels = set(re.findall(r"\\label\{([^}]+)\}", s))
    refs = set(re.findall(r"\\ref\{([^}]+)\}", s))
    dangling = sorted(refs - labels)
    unused = sorted(labels - refs)

    cites: set[str] = set()
    for group in re.findall(r"\\cite\{([^}]+)\}", s):
        cites |= {c.strip() for c in group.split(",")}
    keys = set(re.findall(r"\\bibitem\{([^}]+)\}", s))
    bad_cites = sorted(cites - keys)
    uncited = sorted(keys - cites)

    placeholders = re.findall(r"\\ph\{[^}]*\}", s)
    nonascii = sorted({c for c in s if ord(c) > 127})

    # A bare "ef{" is a \ref that lost its backslash; it compiles silently.
    broken_refs = re.findall(r"(?<![a-zA-Z\\])ef\{", s)

    body = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    body = re.sub(r"[{}$&\\%~^_]", " ", body)
    words = len(body.split())

    print("labels defined     : %d" % len(labels))
    print("references used    : %d" % len(refs))
    print("citation keys      : %d" % len(keys))
    print("approx. body words : %d  (~%.1f IEEE two-column pages)" % (words, words / 700.0))

    for name, items in (
        ("DANGLING \\ref", dangling),
        ("DANGLING \\cite", bad_cites),
        ("UNFILLED PLACEHOLDERS", placeholders),
        ("NON-ASCII CHARACTERS", nonascii),
        ("BROKEN 'ef{' (missing backslash)", broken_refs),
    ):
        if items:
            problems += len(items)
            print("  FAIL %-34s %s" % (name, items))
        else:
            print("  ok   %s" % name)

    for name, items in (("unused labels", unused), ("uncited bibitems", uncited)):
        if items:
            print("  note %-34s %s" % (name, items))

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT) if DEFAULT else None)
    args = ap.parse_args()
    if not args.path:
        raise SystemExit(
            "No manuscript found nearby. Pass the path to name.tex explicitly:\n"
            "    python analysis/check_manuscript.py path/to/name.tex"
        )
    problems = check(Path(args.path))
    print("\n%d problem(s)" % problems)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
