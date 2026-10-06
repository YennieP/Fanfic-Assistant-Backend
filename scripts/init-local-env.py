"""Create a local-only Django .env without exposing generated secrets."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def main() -> None:
    if ENV_PATH.exists():
        raise SystemExit(f"Refusing to overwrite existing {ENV_PATH}")

    secret_key = secrets.token_urlsafe(50)
    encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    content = "\n".join(
        [
            f"SECRET_KEY={secret_key}",
            "DEBUG=True",
            "ALLOWED_HOSTS=localhost,127.0.0.1",
            "DATABASE_URL=sqlite:///db.sqlite3",
            "CORS_ALLOWED_ORIGINS=http://localhost:5173",
            "CSRF_TRUSTED_ORIGINS=http://localhost:5173",
            f"ENCRYPTION_KEY={encryption_key}",
            "LOG_LEVEL=INFO",
            "",
        ]
    )

    descriptor = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as env_file:
        env_file.write(content)

    print(f"Created {ENV_PATH} with mode 0600")


if __name__ == "__main__":
    main()
