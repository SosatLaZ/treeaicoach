"""Static checks for the WoW addon's Lua files (luacheck-like, stdlib + ``luac5.1`` only).

For every ``.lua`` under the given roots (default: ``wow_addon/`` and ``tests/wow/``, minus
``tests/wow/fixtures/bad``):
  * syntax: ``luac5.1 -p``;
  * global writes: ``SETGLOBAL`` in ``luac5.1 -l`` bytecode listings, plus ``_G.x = …`` /
    ``_G["x"] = …`` in the source. Allowed: ``TreeAI``, ``TreeAIDB``, ``SLASH_*``, ``BINDING_*``, the
    addon's own name and the TOC's SavedVariables (TOC found next to / above the file);
  * Blizzard ToS: no protected/automation call (CastSpellByName, RunMacroText, UseAction…) from
    addon code; a file that calls CreateMacro/EditMacro/DeleteMacro/SetBinding* must also test
    ``InCombatLockdown``.
The harness files themselves (tests/wow/*.lua) are only syntax-checked.

CLI: ``python tests/wow/luacheck.py [roots…]`` → prints findings, exit 1 if any, 0 if clean,
0 with a SKIP note when no ``luac5.1`` / ``luac`` 5.1 is installed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HARNESS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOTS = [REPO / "wow_addon", HARNESS_DIR]
EXCLUDE = [HARNESS_DIR / "fixtures" / "bad"]

ALWAYS_ALLOWED = {"TreeAI", "TreeAIDB"}
ALLOWED_PATTERNS = [re.compile(r"^SLASH_\w+\d+$"), re.compile(r"^BINDING_(HEADER|NAME)_\w+$")]
FORBIDDEN_CALLS = {
    "CastSpellByName", "CastSpellByID", "UseAction", "RunMacroText", "RunMacro", "TargetUnit",
    "AttackTarget", "CastPetAction", "PetAttack", "UseInventoryItem", "SpellStopCasting",
    "TargetNearestEnemy", "InteractUnit", "MoveForwardStart", "JumpOrAscendStart", "TurnLeftStart",
    "CameraOrSelectOrMoveStart", "CastShapeshiftForm", "UseItemByName",
}
MACRO_WRITES = re.compile(r"\b(CreateMacro|EditMacro|DeleteMacro|SetBinding\w*|SaveBindings)\b")
SETGLOBAL = re.compile(r"\bSETGLOBAL\b.*;\s*(\S+)")
GETGLOBAL = re.compile(r"\bGETGLOBAL\b.*;\s*(\S+)")
G_ASSIGN = re.compile(r"\b_G\s*(?:\.\s*([A-Za-z_]\w*)|\[\s*[\"']([A-Za-z_]\w*)[\"']\s*\])\s*=(?!=)")
G_FORBIDDEN = re.compile(r"\b_G\s*(?:\.\s*|\[\s*[\"'])(" + "|".join(sorted(FORBIDDEN_CALLS)) + r")\b")


def find_luac() -> str | None:
    for name in ("luac5.1", "luac"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if "5.1" in (out.stdout + out.stderr):
            return exe
    return None


def lua_files(roots) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        root = Path(root)
        if root.is_file() and root.suffix == ".lua":
            files.append(root)
            continue
        if not root.is_dir():
            continue
        rroot = root.resolve()
        explicit = any(ex == rroot or ex in rroot.parents for ex in EXCLUDE)
        for p in sorted(root.rglob("*.lua")):
            if not explicit and any(ex in p.resolve().parents for ex in EXCLUDE):
                continue
            files.append(p)
    return files


def _strip_comments(src: str) -> str:
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    return re.sub(r"--[^\n]*", "", src)


def toc_allowed(lua: Path) -> set[str]:
    """Addon name + SavedVariables of the nearest TOC at or above the file's folder."""
    allowed: set[str] = set()
    for d in [lua.parent, *lua.parents]:
        tocs = sorted(d.glob("*.toc")) if d.is_dir() else []
        if tocs:
            allowed.add(d.name)
            for toc in tocs:
                allowed.add(toc.stem.split("_")[0])
                try:
                    text = toc.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for m in re.finditer(r"^##\s*SavedVariables(?:PerCharacter)?\s*:\s*(.+)$", text, re.M):
                    allowed.update(re.findall(r"[A-Za-z_]\w*", m.group(1)))
            break
        if d == REPO:
            break
    return allowed


def is_allowed(name: str, extra: set[str]) -> bool:
    return name in ALWAYS_ALLOWED or name in extra or any(p.match(name) for p in ALLOWED_PATTERNS)


def check_file(path: Path, luac: str) -> list[str]:
    rel = os.path.relpath(path.resolve(), REPO)
    res = subprocess.run([luac, "-p", str(path)], capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        return [f"{rel}: SYNTAX {(res.stderr or res.stdout).strip()}"]
    if path.resolve().parent == HARNESS_DIR:
        return []  # mock / harness: syntax only (they define the WoW globals on purpose)
    findings: list[str] = []
    listing = subprocess.run([luac, "-p", "-l", str(path)], capture_output=True, text=True, timeout=30).stdout
    extra = toc_allowed(path)
    for line in listing.splitlines():
        m = SETGLOBAL.search(line)
        if m and not is_allowed(m.group(1), extra):
            ln = re.search(r"\[(\d+)\]", line)
            findings.append(f"{rel}:{ln.group(1) if ln else '?'}: LEAK global '{m.group(1)}'")
        m = GETGLOBAL.search(line)
        if m and m.group(1) in FORBIDDEN_CALLS:
            ln = re.search(r"\[(\d+)\]", line)
            findings.append(f"{rel}:{ln.group(1) if ln else '?'}: TOS protected/automation API '{m.group(1)}'")
    src = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    for m in G_ASSIGN.finditer(src):
        name = m.group(1) or m.group(2)
        if not is_allowed(name, extra):
            findings.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1}: LEAK global '_G.{name}'")
    for m in G_FORBIDDEN.finditer(src):
        findings.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1}: TOS protected/automation API '{m.group(1)}'")
    if MACRO_WRITES.search(src) and "InCombatLockdown" not in src:
        findings.append(f"{rel}: TOS macro/binding writes without an InCombatLockdown() test in the file")
    return sorted(set(findings))


def check_paths(roots=None, luac: str | None = None) -> tuple[list[Path], list[str]]:
    luac = luac or find_luac()
    if not luac:
        raise FileNotFoundError("luac5.1 not found")
    files = lua_files(roots or DEFAULT_ROOTS)
    findings: list[str] = []
    for f in files:
        findings.extend(check_file(f, luac))
    return files, findings


def main(argv: list[str]) -> int:
    luac = find_luac()
    if not luac:
        print("SKIP luacheck: no luac5.1 (apt-get install -y lua5.1)")
        return 0
    files, findings = check_paths([Path(a) for a in argv] or None, luac)
    for f in findings:
        print(f)
    print(f"luacheck: {len(files)} files, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
