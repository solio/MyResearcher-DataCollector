#!/usr/bin/env python3
"""Cross-platform source/package sanity checks; NOT an ArkTS compiler."""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def check():
    problems = []
    for path in (ROOT / "harmony").rglob("*"):
        if any(part in {"oh_modules", ".hvigor", "build", ".idea"} for part in path.parts):
            continue
        if path.suffix in {".json", ".json5"}:
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{path.relative_to(ROOT)}: {exc}")
    module = ROOT / "harmony/entry/src/main"
    for path in module.rglob("*.ets"):
        for relative in re.findall(r"from ['\"](\.[^'\"]+)['\"]", path.read_text(encoding="utf-8")):
            if not (path.parent / (relative + ".ets")).is_file():
                problems.append(f"Missing ArkTS import: {path.name}: {relative}")
    required = ["SPEC.md", "runs.md", "harmony/hvigorfile.ts", "harmony/build-profile.json5",
                "harmony/entry/src/main/ets/pages/Index.ets", "deploy/waygate.service", "deploy/nginx.conf.example"]
    for relative in required:
        if not (ROOT / relative).is_file():
            problems.append("Missing: " + relative)
    print("\n".join(problems) if problems else "Project metadata/import paths OK. ArkTS compilation and device acceptance are separate gates.")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(check())
