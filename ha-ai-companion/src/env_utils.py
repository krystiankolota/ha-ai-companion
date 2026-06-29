"""Small, dependency-free env-parsing helpers.

Kept at the ``src/`` top level (outside the ``agents`` package, whose
``__init__`` imports agent_system and its heavy deps) so it is unit-testable in
isolation.
"""
import os


def env_positive_int(key: str, default: int) -> int:
    """Return a positive int from env var ``key``; ``0``/blank/non-digit -> ``default``.

    Guards the config-default-0 trap: add-on options like ``max_iterations`` default
    to ``0`` and ``run.sh`` exports ``MAX_ITERATIONS=0``, so a bare
    ``int(os.getenv(key, '25'))`` reads ``0`` (the env var IS set, so the ``'25'``
    fallback never applies) and the agent loop aborts every turn. Only a positive
    value overrides the default.
    """
    raw = (os.getenv(key, "") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default
