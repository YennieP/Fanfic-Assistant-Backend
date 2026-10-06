from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERIFY_ENV = os.environ.copy()
VERIFY_ENV.setdefault("SECRET_KEY", "verification-only-secret-key")
VERIFY_ENV.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())


def run(*arguments: str) -> None:
    print(f"+ {' '.join(arguments)}", flush=True)
    subprocess.run(arguments, cwd=ROOT, check=True, env=VERIFY_ENV)


def main() -> int:
    docs_command = [sys.executable, "scripts/check_docs.py"]
    if "--base" in sys.argv[1:]:
        index = sys.argv.index("--base")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--base requires a git revision")
        docs_command.extend(("--base", sys.argv[index + 1]))

    run(*docs_command)
    run(sys.executable, "manage.py", "check")
    run(sys.executable, "manage.py", "makemigrations", "--check", "--dry-run")
    run(sys.executable, "-m", "pytest", "--cov", "--cov-report=term-missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
