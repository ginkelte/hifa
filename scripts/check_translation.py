"""Verify that a translated spec keeps all of its technical structure.

Compares a file against its previous version (git show HEAD:path) and reports:
- REQ-* IDs, D-* IDs, A#N rules
- count of code fences (```), tables, headings
- leftover Spanish (accented/n-tilde words outside code fences)

The Spanish word list below is the detection corpus; it is written with unicode escapes
so that the pattern matches Spanish text while the source itself contains no accented
character.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/administrator/hifa")
# Specs where Spanish text IS test data (diacritic stripping in FTS, slugify, ambiguous
# assignee). There the accents are the case under test, not untranslated prose.
DATA_FILES = {"090-search-fts.md", "100-config-bootstrap.md", "080-transfer-csv-json.md"}
ES_WORDS = re.compile(
    r"\b(el|la|los|las|de|del|que|con|para|una|un|por|como|m\u00e1s|tambi\u00e9n"
    r"|seg\u00fan|estado|debe|puede|s\u00f3lo|sin|entre|cada|cuando|desde|hasta"
    r"|sobre|tiene|devuelve|error|tabla|columna|campo|siempre|nunca|nivel|comando"
    r"|salida)\b",
    re.I,
)
ACCENTS = re.compile(
    r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00c1\u00c9\u00cd\u00d3\u00da\u00d1\u00bf\u00a1]"
)


def metrics(text: str) -> dict:
    outside = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            outside.append(line)
    prose = "\n".join(outside)
    return {
        "req": len(set(re.findall(r"REQ-[A-Z]\d{3}(?:-\d{2})?", text))),
        "decisions": len(set(re.findall(r"D-\d{3}", text))),
        "rules": len(set(re.findall(r"\bA[1-8]\b", text))),
        "fences": text.count("```"),
        "headings": len(re.findall(r"(?m)^#{1,4} ", text)),
        "table_rows": len(re.findall(r"(?m)^\|", text)),
        "scenarios": len(re.findall(r"(?m)^\s*(Scenario|Scenario Outline):", text)),
        "accents_in_prose": len(ACCENTS.findall(prose)),
        "es_words_in_prose": len(ES_WORDS.findall(prose)),
        "lines": text.count("\n") + 1,
    }


def main(paths: list[str]) -> int:
    bad = 0
    print(f"{'file':<42} {'before -> after':<28} verdict")
    for rel in paths:
        before = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, text=True
        ).stdout
        after = (ROOT / rel).read_text(encoding="utf-8")
        b, a = metrics(before), metrics(after)
        problems = []
        for key in ("req", "decisions", "rules", "fences"):
            if a[key] < b[key]:
                problems.append(f"{key} {b[key]}->{a[key]}")
        if a["scenarios"] < b["scenarios"]:
            problems.append(f"scenarios {b['scenarios']}->{a['scenarios']}")
        if a["accents_in_prose"] > b["accents_in_prose"] * 0.25 and a["accents_in_prose"] > 5:
            problems.append(f"prose with accents ({a['accents_in_prose']})")
        verdict = "OK" if not problems else "REVIEW: " + ", ".join(problems)
        if problems:
            bad += 1
        print(
            f"{rel:<42} REQ {b['req']}->{a['req']} fences {b['fences']}->{a['fences']} "
            f"lines {b['lines']}->{a['lines']}  {verdict}"
        )
    print("\nfiles with problems:", bad, "/", len(paths))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
