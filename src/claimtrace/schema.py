"""Fixed claim schema, value normalization, and grounded extraction patterns.

The pattern table drives the deterministic extractor. Each claim type pairs a
keyword test with a value pattern, both of which must hit inside the same
sentence of a real chunk. Values are normalized so the cross-checker compares
like with like ($1.75 trillion vs 1.75T vs 1,750 billion would all collide).
"""

from __future__ import annotations

import re

CLAIM_TYPES = [
    "valuation",
    "raise_size",
    "shares_offered",
    "offer_price",
    "listing_date",
    "ticker",
    "exchange",
    "free_float",
    "retail_allocation",
    "annual_revenue",
    "net_loss",
]

LABELS = {
    "valuation": "Valuation",
    "raise_size": "Raise size",
    "shares_offered": "Shares offered",
    "offer_price": "Offer price",
    "listing_date": "Listing date",
    "ticker": "Ticker",
    "exchange": "Exchange",
    "free_float": "Free float",
    "retail_allocation": "Retail allocation",
    "annual_revenue": "Annual revenue",
    "net_loss": "Net loss",
}

# Query text per claim type, embedded and compared against the chunk embedding
# to produce a real retrieval score for the audit trail.
QUERIES = {
    "valuation": "company valuation valued at trillion dollars",
    "raise_size": "raise gross proceeds billion dollars offering",
    "shares_offered": "million shares offered in the offering",
    "offer_price": "offer price dollars per share",
    "listing_date": "shares begin trading listing date",
    "ticker": "ticker symbol",
    "exchange": "listed on stock exchange Nasdaq NYSE",
    "free_float": "free float percent of shares",
    "retail_allocation": "retail investor allocation percent",
    "annual_revenue": "annual revenue billion dollars fiscal year",
    "net_loss": "net loss billion dollars fiscal year",
}

MONEY = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s*(trillion|billion|million)", re.IGNORECASE)
SHARES = re.compile(r"(\d+(?:\.\d+)?)\s*million\s+shares", re.IGNORECASE)
PER_SHARE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s*(?:per share|a share|apiece)", re.IGNORECASE)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:percent|%)", re.IGNORECASE)
DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})"
)
TICKER = re.compile(r"(?:ticker(?:\s+symbol)?|symbol)\s+[\"“']?([A-Z]{2,5})[\"”']?")
EXCHANGE = re.compile(r"(Nasdaq(?:\s+Global\s+Select\s+Market)?|New York Stock Exchange|NYSE)")

KEYWORDS = {
    "valuation": re.compile(r"\b(valuation|valuing|valued|worth)\b", re.IGNORECASE),
    "raise_size": re.compile(r"\b(raise|raising|raised|proceeds|seeking)\b", re.IGNORECASE),
    "shares_offered": re.compile(r"\b(offer|offering|offered|sell|selling)\b", re.IGNORECASE),
    "offer_price": re.compile(r"\b(price|priced|pricing)\b", re.IGNORECASE),
    "listing_date": re.compile(r"\b(list|listing|listed|debut|trading|trade)\b", re.IGNORECASE),
    "ticker": re.compile(r"\b(ticker|symbol)\b", re.IGNORECASE),
    "exchange": re.compile(r"\b(list|listed|listing|exchange|trade|trading|debut)\b", re.IGNORECASE),
    "free_float": re.compile(r"\bfree\s+float\b|\bfloat\b", re.IGNORECASE),
    "retail_allocation": re.compile(r"\bretail\b", re.IGNORECASE),
    "annual_revenue": re.compile(r"\brevenue\b", re.IGNORECASE),
    "net_loss": re.compile(r"\bnet\s+loss\b", re.IGNORECASE),
}

VALUE_PATTERNS = {
    "valuation": MONEY,
    "raise_size": MONEY,
    "shares_offered": SHARES,
    "offer_price": PER_SHARE,
    "listing_date": DATE,
    "ticker": TICKER,
    "exchange": EXCHANGE,
    "free_float": PERCENT,
    "retail_allocation": PERCENT,
    "annual_revenue": MONEY,
    "net_loss": MONEY,
}

_SCALE = {"trillion": 1e12, "billion": 1e9, "million": 1e6}
_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Return (offset, sentence) pairs with offsets into the given text."""
    out = []
    pos = 0
    for sent in SENTENCE_SPLIT.split(text):
        if not sent:
            continue
        start = text.index(sent, pos)
        out.append((start, sent))
        pos = start + len(sent)
    return out


def normalize(claim_type: str, match: re.Match) -> tuple[str, object]:
    """Return (value_text, normalized_value) for a value pattern match."""
    value_text = match.group(0)
    if claim_type in ("valuation", "raise_size", "annual_revenue", "net_loss"):
        amount = float(match.group(1)) * _SCALE[match.group(2).lower()]
        return value_text, amount
    if claim_type == "shares_offered":
        return value_text, float(match.group(1)) * 1e6
    if claim_type == "offer_price":
        return value_text, float(match.group(1))
    if claim_type in ("free_float", "retail_allocation"):
        return value_text, float(match.group(1))
    if claim_type == "listing_date":
        month, day, year = match.group(1), int(match.group(2)), int(match.group(3))
        return value_text, f"{year:04d}-{_MONTHS[month]:02d}-{day:02d}"
    if claim_type == "ticker":
        return value_text, match.group(1).upper()
    if claim_type == "exchange":
        raw = match.group(1)
        if raw.upper().startswith("NASDAQ"):
            return value_text, "Nasdaq"
        return value_text, "NYSE"
    return value_text, value_text


def values_equal(a: object, b: object) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        biggest = max(abs(a), abs(b), 1e-12)
        return abs(a - b) / biggest < 1e-6
    return a == b


def format_value(claim_type: str, normalized: object) -> str:
    if claim_type in ("valuation", "raise_size", "annual_revenue", "net_loss"):
        v = float(normalized)
        for unit, scale in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6)):
            if v >= scale:
                return f"${v / scale:g} {unit}"
        return f"${v:g}"
    if claim_type == "shares_offered":
        return f"{float(normalized) / 1e6:g} million shares"
    if claim_type == "offer_price":
        return f"${float(normalized):g} per share"
    if claim_type in ("free_float", "retail_allocation"):
        return f"{float(normalized):g} percent"
    return str(normalized)
