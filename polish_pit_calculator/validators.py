"""Shared input validators used across reporters and UI prompts."""

from collections.abc import Callable

PromptValidator = Callable[[str], bool | str]


def validate_year(raw: str) -> bool | str:
    """Validate non-empty integer year input."""
    if not (text := raw.strip()):
        return "Year is required."
    if not text.isdigit():
        return "Year must be an integer."
    return True


def validate_amount(raw: str) -> bool | str:
    """Validate non-empty numeric amount input."""
    if not (text := raw.strip()):
        return "Amount is required."
    try:
        float(text)
    except ValueError:
        return "Amount must be a number."
    return True


def validate_query_id(query_id: int | str) -> bool | str:
    """Validate reporter-specific query identifier input."""
    if not str(query_id).strip():
        return "Query ID is required."
    return True


def validate_token(token: str) -> bool | str:
    """Validate reporter-specific token input."""
    if not token.strip():
        return "Token is required."
    return True
