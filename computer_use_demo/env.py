from __future__ import annotations

import os
from pathlib import Path


def load_env() -> None:
    """
    Best-effort `.env` loader.

    This project commonly runs under docker-compose where environment variables are
    already injected. When running the backend directly (or when a subprocess is
    spawned), we also want to pick up values from a local `.env` file.
    """

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    # Load from CWD first, then from the repo root if this file is imported from elsewhere.
    load_dotenv(override=False)
    repo_root = Path(__file__).resolve().parents[1]
    candidate = repo_root / ".env"
    if candidate.exists():
        load_dotenv(dotenv_path=os.fspath(candidate), override=False)

