from __future__ import annotations

import re
import unicodedata


def slugify(value: str, max_length: int = 100) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return (value or "item")[:max_length].strip("-")


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards in user input (the value itself is still bound as a parameter)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
