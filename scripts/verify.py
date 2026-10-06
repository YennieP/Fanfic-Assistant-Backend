from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(*arguments: str) -> None:
    print(f"+ {' '.join(arguments)}", flush=True)
    subprocess.run(arguments, cwd=ROOT, check=True, env=os.environ.copy())


def main() -> int:
    docs_command = [sys.executable, "scripts/check_docs.py"]
    if "--base" in sys.argv[1:]:
        index = sys.argv.index("--base")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--base requires a git revision")
        docs_command.extend(("--base", sys.argv[index + 1]))

    run(*docs_command)
    run(sys.executable, "manage.py", "check")
    run(sys.executable, "-m", "pytest", "--cov", "--cov-report=term-missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
