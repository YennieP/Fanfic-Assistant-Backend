from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "AGENTS.md",
    "README.md",
    "EXPERIMENT.md",
    "docs/README.md",
)
IGNORED_DIRS = {".git", ".runtime", ".venv", "htmlcov", "staticfiles"}
LINK_PATTERN = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def has_exact_case(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return False

    current = ROOT
    for part in relative.parts:
        if part not in {entry.name for entry in current.iterdir()}:
            return False
        current /= part
    return True


def markdown_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for child in directory.iterdir():
        if child.is_dir():
            if child.name not in IGNORED_DIRS:
                files.extend(markdown_files(child))
        elif child.suffix == ".md":
            files.append(child)
    return files


def base_ref(arguments: list[str]) -> str | None:
    if "--base" in arguments:
        index = arguments.index("--base")
        if index + 1 >= len(arguments):
            raise SystemExit("--base requires a git revision")
        value = arguments[index + 1]
    else:
        value = os.environ.get("DOCS_BASE_REF") or None
    if value and set(value) == {"0"}:
        return None
    return value


def main() -> int:
    errors: list[str] = []
    for name in REQUIRED:
        path = ROOT / name
        if not path.exists() or not has_exact_case(path):
            errors.append(f"required file missing or wrong case: {name}")

    files = markdown_files(ROOT)
    for file in files:
        content = file.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(content):
            target = match.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split("#", 1)[0]
            if not target or re.match(r"^[a-z]+:", target, re.IGNORECASE):
                continue
            destination = (file.parent / unquote(target)).resolve()
            if not destination.exists():
                errors.append(f"{file.relative_to(ROOT)}: missing link target {target}")
            elif not has_exact_case(destination):
                errors.append(
                    f"{file.relative_to(ROOT)}: link target has wrong filename case: {target}"
                )

    base = base_ref(sys.argv[1:])
    diff_commands = [["git", "diff", "--check"]]
    if base:
        diff_commands.append(["git", "diff", "--check", f"{base}...HEAD"])
    for command in diff_commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            errors.append(f"git whitespace check failed: {result.stdout or result.stderr}")

    if errors:
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1

    print(f"Documentation checks passed ({len(files)} Markdown files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
