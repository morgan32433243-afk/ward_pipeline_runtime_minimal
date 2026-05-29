from __future__ import annotations


def default_policy() -> dict[str, bool]:
    return {
        "external_llm_allowed": False,
        "discord_allowed": False,
        "requires_local_only": True,
    }


def default_review_reasons() -> list[str]:
    return [
        "initial_review_required",
        "contains_possible_phi",
    ]


def deidentify_text(text: str) -> str:
    replacements = {
        "王小明": "[NAME]",
        "陳小華": "[NAME]",
        "0912345678": "[PHONE]",
        "A123456789": "[ID]",
    }
    sanitized = text
    for source, replacement in replacements.items():
        sanitized = sanitized.replace(source, replacement)
    return sanitized
