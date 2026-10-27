"""Text shared by every catalog implementation: what the agent reads and how queries match."""

import re
import unicodedata
from collections.abc import Mapping

from vitrina_core.money import amounts_cop, format_cop

# Function words that would otherwise match every document.
STOPWORDS = frozenset(
    [
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "es",
        "la",
        "las",
        "lo",
        "los",
        "me",
        "mi",
        "o",
        "para",
        "por",
        "que",
        "se",
        "su",
        "un",
        "una",
        "y",
    ]
)


def describe_product(
    name: str, description: str | None, price_cop: int, attributes: Mapping[str, str]
) -> str:
    """The product text the agent sees and cites; the price appears exactly once."""
    parts = [name.strip()]
    if description:
        parts.append(description.strip())
    parts.append(f"Precio: {format_cop(price_cop)}")
    if attributes:
        parts.append("; ".join(f"{key}: {value}" for key, value in sorted(attributes.items())))
    return ". ".join(parts)


def fold(text: str) -> str:
    """Lowercase and accent-free, with single spaces: how names compare."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(plain.split())


def tokens(text: str) -> set[str]:
    """Lowercase, accent-free word set without function words."""
    return {word for word in re.findall(r"[a-z0-9ñ]+", fold(text)) if word not in STOPWORDS}


def product_amounts(
    price_cop: int, description: str | None, attributes: Mapping[str, str] | None
) -> tuple[int, ...]:
    """Every amount the agent may quote for a product: its price plus any in its text (R2)."""
    found = {price_cop}
    for text in (description or "", *(attributes or {}).values()):
        found.update(amounts_cop(text))
    return tuple(sorted(found))
