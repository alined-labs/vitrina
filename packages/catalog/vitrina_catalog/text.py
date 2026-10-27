"""Text shared by every catalog implementation: what the agent reads and how queries match."""

import re
import unicodedata
from collections.abc import Mapping

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


def format_cop(amount: int) -> str:
    """Colombian notation: 85000 -> $85.000."""
    return "$" + f"{amount:,}".replace(",", ".")


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


def tokens(text: str) -> set[str]:
    """Lowercase, accent-free word set without function words."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return {word for word in re.findall(r"[a-z0-9ñ]+", plain) if word not in STOPWORDS}
