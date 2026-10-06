# Repository Working Agreement

These rules apply to the whole backend repository.

## Documentation ownership

- `README.md` is the backend developer entry point. Keep setup, verification, deployment boundaries, and an API overview here; do not maintain a second copy of the product design or full implementation ledger.
- `EXPERIMENT.md` is the preserved Phase 1 experiment report. Change measured results only when rerunning the experiment with recorded evidence.
- `docs/README.md` explains how backend documents relate to the cross-system documentation in `YennieP/Fanfic-Assistant`.
- Cross-system design, default-branch implementation status, open work, and append-only audit history live in the frontend repository under `docs/`. Update the appropriate document there when a backend change alters those facts.

## Required documentation updates

- A new or changed endpoint must update the API overview in `README.md` and the cross-system `IMPLEMENTATION.md`.
- A newly discovered gap belongs in the cross-system `ToDo.md`; completed work must be removed from ToDo and recorded in `IMPLEMENTATION.md`.
- Do not rewrite product design to match an implementation shortcut. Record the deviation and track the remaining gap.
- Do not duplicate detailed status tables in this repository. Link to the cross-system source of truth.

## Verification

- Run `python scripts/verify.py` with the repository's Python 3.12 environment before handoff. On the isolated Mac setup, use `.venv/bin/python scripts/verify.py`; on Windows use `.venv\\Scripts\\python.exe scripts\\verify.py`.
- The command runs documentation checks, Django system checks, migration-state checks, and the full pytest suite with coverage—the same checks used by GitHub Actions.
- Do not generate or apply an unreviewed production migration merely to silence model-state drift. Inspect every generated migration and keep schema-changing migrations separate from model-state-only fixes.
- If CI is unavailable, run the unified command on the exact commit being reviewed, record the SHA, and require a clean worktree. Any later commit invalidates that result.

## Environment and production safety

- Use Python 3.12 in a repository-local environment. Never modify the system Python, global PATH, shell profiles, or another repository's environment.
- Never commit `.env`, `.runtime/`, `.venv/`, local databases, credentials, caches, or production data.
- Never point local Django commands or tests at the Railway production `DATABASE_URL`.
- Keep migrations, deployment configuration, dependency/environment changes, and business behavior in separate commits or PRs when their risks are independent.
- Production checks should be read-only unless the user explicitly authorizes a data-changing smoke test.
