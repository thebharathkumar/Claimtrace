"""Environment-driven configuration. Every knob has a working default."""

from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def db_path() -> str:
    return env("CLAIMTRACE_DB", "claimtrace.db")


def audit_key() -> bytes:
    return env("CLAIMTRACE_AUDIT_KEY", "claimtrace-dev-key").encode("utf-8")


def model_name() -> str:
    return env("CLAIMTRACE_MODEL", "claude-opus-4-8")


def offline_mode() -> bool:
    if env("CLAIMTRACE_OFFLINE", "0") in ("1", "true", "yes"):
        return True
    return not env("ANTHROPIC_API_KEY")


def triage_endpoint() -> str:
    return env("AGENT_TRIAGE_ENDPOINT", "http://127.0.0.1:8765/otlp/v1/traces")


def triage_dashboard() -> str:
    return env("AGENT_TRIAGE_DASHBOARD", "http://127.0.0.1:8765/")


def demo_enabled() -> bool:
    return env("CLAIMTRACE_DEMO", "1") in ("1", "true", "yes")


def seed_dir() -> Path:
    override = env("CLAIMTRACE_SEED_DIR")
    if override:
        return Path(override)
    # repo layout: data/seed next to the package when installed editable
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "seed"
        if candidate.is_dir():
            return candidate
    return Path("data/seed")
