"""Shared helpers for stage strategies."""
from __future__ import annotations

from pathlib import Path

from poc2.models import Account

REPO_ROOT = Path(__file__).resolve().parents[2]

# Prompt files may carry an optional instruction section after a lone `---`
# line: everything above is the system prompt, everything below the
# per-invocation instruction (used by the generators).
_SEPARATOR = "\n---\n"


def load_prompt(rel_path: str) -> str:
    """Full prompt file text (system prompt only, for single-section files)."""
    return load_prompt_sections(rel_path)[0]


def load_prompt_sections(rel_path: str) -> tuple[str, str]:
    """(system, instruction) — instruction is '' for single-section files."""
    text = (REPO_ROOT / rel_path).read_text()
    if _SEPARATOR in text:
        system, instruction = text.split(_SEPARATOR, 1)
        return system.strip(), instruction.strip()
    return text.strip(), ""


def account_from(payload: dict) -> Account:
    return Account.model_validate(payload["account"])


def artifact_prefix(ctx) -> str:
    """S3/local key prefix — poc1 §11.2 layout, kept identical."""
    account = account_from(ctx.payload)
    return f"{ctx.batch_id}/{account.bdr_id}/{account.account_id}"
