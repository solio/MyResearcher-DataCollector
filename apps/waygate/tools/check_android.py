"""Structural checks for the Android port that run without the Android SDK.

This machine has no Android toolchain, so the Kotlin sources cannot be compiled here.
These checks cover the classes of breakage that a compiler would otherwise catch
first and that a reviewer would otherwise have to eyeball:

  1. every @string/@color/@style/@layout/@drawable/@xml/@mipmap/@id reference used by
     the manifest and the layouts resolves to something the project defines;
  2. every R.id.<name> used from Kotlin exists as an @+id in some layout;
  3. each Kotlin file's package matches its directory, and its top-level classes match
     the file name;
  4. the Android channel.json carries exactly the same node set, pinned host keys and
     credential file names as the HarmonyOS rawfile copy;
  5. no credential material is tracked by git and the credential path stays ignored.

Usage: python apps/waygate/tools/check_android.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ANDROID = ROOT / "apps" / "waygate" / "android"
APP = ANDROID / "app" / "src" / "main"
HARMONY_CHANNEL = (ROOT / "apps" / "waygate" / "harmony" / "entry" / "src" / "main"
                   / "resources" / "rawfile" / "channel.json")
ANDROID_CHANNEL = APP / "assets" / "channel.json"
SECRET_PATH = Path("apps/waygate/android/app/src/main/assets/channel/phone-ed25519.pem")

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
REFERENCE = re.compile(r"@(?:\+)?(string|color|style|layout|drawable|xml|mipmap|id)/([A-Za-z0-9_.]+)")
R_ID = re.compile(r"\bR\.id\.([A-Za-z0-9_]+)")
PACKAGE = re.compile(r"^package\s+([A-Za-z0-9_.]+)", re.MULTILINE)
TOP_LEVEL = re.compile(r"^(?:internal |private |open |abstract |sealed )*(?:class|object|interface|enum class)\s+([A-Za-z0-9_]+)", re.MULTILINE)
# A real key block has base64 material after the header; prose that merely names the
# header (runs.md does) does not.
PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*\r?\n[A-Za-z0-9+/=]{40,}")

problems: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def defined_resources() -> dict[str, set[str]]:
        found: dict[str, set[str]] = {kind: set() for kind in
                                      ("string", "color", "style", "layout", "drawable", "xml", "mipmap", "id")}
        for path in sorted((APP / "res" / "values").glob("*.xml")):
            try:
                tree = ElementTree.parse(path)
            except ElementTree.ParseError as err:
                fail(f"{path.relative_to(ROOT)}: not valid XML ({err})")
                continue
            for element in tree.getroot():
                name = element.attrib.get("name")
                if not name:
                    continue
                tag = element.tag
                if tag == "string":
                    found["string"].add(name)
                elif tag == "color":
                    found["color"].add(name)
                elif tag == "style":
                    found["style"].add(name)
                elif tag == "item" and element.attrib.get("type") in ("string", "color", "style"):
                    found[element.attrib["type"]].add(name)
        for path in (APP / "res").rglob("*"):
            if not path.is_file():
                continue
            parts = path.relative_to(APP / "res").parts
            folder = parts[0].split("-")[0]
            stem = path.name.split(".")[0]
            if folder == "layout":
                found["layout"].add(stem)
            elif folder == "drawable":
                found["drawable"].add(stem)
            elif folder == "xml":
                found["xml"].add(stem)
            elif folder == "mipmap":
                found["mipmap"].add(stem)
        for path in sorted((APP / "res" / "layout").glob("*.xml")):
            text = path.read_text(encoding="utf-8")
            for name in re.findall(r'@\+id/([A-Za-z0-9_]+)', text):
                found["id"].add(name)
        return found


def check_references(resources: dict[str, set[str]]) -> None:
    files = [APP / "AndroidManifest.xml"] + sorted((APP / "res" / "layout").glob("*.xml"))
    for path in sorted((APP / "res" / "values").glob("*.xml")):
        files.append(path)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for kind, name in REFERENCE.findall(text):
            if kind == "style" and name.startswith("Theme.Material"):
                continue
            if name not in resources[kind]:
                fail(f"{path.relative_to(ROOT)}: @{kind}/{name} is not defined by the project")


def check_kotlin_ids(resources: dict[str, set[str]]) -> None:
    for path in sorted((APP / "java").rglob("*.kt")):
        text = path.read_text(encoding="utf-8")
        for name in set(R_ID.findall(text)):
            if name not in resources["id"]:
                fail(f"{path.relative_to(ROOT)}: R.id.{name} does not exist in any layout")


def check_kotlin_layout(resources: dict[str, set[str]]) -> None:
    for path in sorted((APP / "java").rglob("*.kt")):
        text = path.read_text(encoding="utf-8")
        for name in set(re.findall(r"\bR\.layout\.([A-Za-z0-9_]+)", text)):
            if name not in resources["layout"]:
                fail(f"{path.relative_to(ROOT)}: R.layout.{name} does not exist")
        for name in set(re.findall(r"\bR\.string\.([A-Za-z0-9_]+)", text)):
            if name not in resources["string"]:
                fail(f"{path.relative_to(ROOT)}: R.string.{name} does not exist")


def check_packages() -> None:
    base = APP / "java"
    for path in sorted(base.rglob("*.kt")):
        text = path.read_text(encoding="utf-8")
        declared = PACKAGE.search(text)
        expected = ".".join(path.relative_to(base).parent.parts)
        if not declared:
            fail(f"{path.relative_to(ROOT)}: no package declaration")
        elif declared.group(1) != expected:
            fail(f"{path.relative_to(ROOT)}: package {declared.group(1)} does not match directory {expected}")
        names = set(TOP_LEVEL.findall(text))
        if names and path.stem not in names:
            fail(f"{path.relative_to(ROOT)}: declares {sorted(names)} but the file is named {path.stem}.kt")


def check_channel_parity() -> None:
    if not ANDROID_CHANNEL.is_file() or not HARMONY_CHANNEL.is_file():
        fail("channel.json missing on one of the platforms")
        return
    android = json.loads(ANDROID_CHANNEL.read_text(encoding="utf-8"))["nodes"]
    harmony = json.loads(HARMONY_CHANNEL.read_text(encoding="utf-8"))["nodes"]
    android_sorted = sorted(android, key=lambda node: node["id"])
    harmony_sorted = sorted(harmony, key=lambda node: node["id"])
    if android_sorted != harmony_sorted:
        fail("android channel.json differs from the harmony rawfile copy")
    else:
        notes.append(f"channel.json nodes identical on both platforms ({len(android)} nodes)")

    for node in android:
        if not node.get("hostKeys"):
            fail(f"channel node {node.get('id')} has no pinned host keys")
        secret_file = node.get("secretFile", "")
        if not secret_file.startswith("channel/"):
            fail(f"channel node {node.get('id')} references an unexpected credential path {secret_file}")
        if secret_file and not (APP / "assets" / secret_file).exists():
            notes.append(f"credential {secret_file} is absent from this build (manual form stays in place)")


def check_secrets() -> None:
    ignored = subprocess.run(["git", "check-ignore", "-q", str(SECRET_PATH)], cwd=ROOT)
    if ignored.returncode != 0:
        fail(f"{SECRET_PATH} is not covered by .gitignore")
    else:
        notes.append(f"{SECRET_PATH} is git-ignored")

    listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    for relative in listed.stdout.splitlines():
        path = ROOT / relative
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if PEM_BLOCK.search(text):
            fail(f"private key material is tracked in {relative}")


def main() -> int:
    resources = defined_resources()
    check_references(resources)
    check_kotlin_ids(resources)
    check_kotlin_layout(resources)
    check_packages()
    check_channel_parity()
    check_secrets()

    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        print(f"\n{len(problems)} problem(s) found")
        return 1
    print("\nAndroid project structure OK (not compiled: no Android SDK on this machine)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
