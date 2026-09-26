"""Pure text parsing: best-before dates, bar counts, and bar/snack classification.

Kept free of network/IO so it can be unit tested (see tests/test_parsing.py).
"""
from __future__ import annotations

import calendar
import re
from datetime import date

# --- Dates -------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Whole-word month names/abbreviations only, so "Marshmallow" or "Mayo" never match.
MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
       r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?![a-z])")

# Phrases that introduce a date: "Best Before", "Best Beore" (typo), "Best By", "BB", "BBD",
# "B.B.", "Exp", "Expiry", "Expires", "Expiration Date", "Use By", "Good Until", "Dated".
KEYWORD_RE = re.compile(
    r"(?<![a-z])(?:best[\s\-]*(?:before|beore|befor|b4|by)|b\.?\s?b\.?d?|"
    r"exp(?:iry|iration|ires|ired|\.)?|use[\s\-]*by|good[\s\-]*(?:until|till|thru|through)|dated?)"
    r"(?![a-z])\s*(?:date)?\s*(?:[:\-–—=]|is|of)?\s*(?:the\s+)?(?:end\s+of\s+)?",
    re.I,
)

_SEP = r"\s*[/\-.]\s*"
# (name, regex, needs_keyword). Order matters: more specific patterns first.
_DATE_PATTERNS = [
    # 2026-11-30, 2026/11/30
    ("ymd", rf"(?P<y>\d{{4}}){_SEP}(?P<m>\d{{1,2}}){_SEP}(?P<d>\d{{1,2}})(?!\d)", False),
    # 30-Nov-26, 30 Nov 2026, 30th November, 2026, 07.Nov.26
    ("d_mon_y", rf"(?<!\d)(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s\-./]*{MON}[\s\-./,']*(?P<y>\d{{4}}|\d{{2}})(?!\d)", False),
    # April 2, 2026 / Apr 2 2026 / Nov. 30th, 26 (2-digit year only after a keyword, see below)
    ("mon_d_y4", rf"{MON}[\s\-./]*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-./]+(?P<y>\d{{4}})(?!\d)", False),
    ("mon_d_y2", rf"{MON}[\s\-./]*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-./]+(?P<y>\d{{2}})(?!\d)", True),
    # 03/31/26, 31/03/2026, 3-31-2026  (order resolved in _numeric_dmy)
    ("n_n_y", rf"(?<!\d)(?P<a>\d{{1,2}}){_SEP}(?P<b>\d{{1,2}}){_SEP}(?P<y>\d{{4}}|\d{{2}})(?!\d)", False),
    # November 2026, Nov-2026, Sept '26, Nov/26
    ("mon_y4", rf"{MON}[\s\-./,']*(?P<y>\d{{4}})(?!\d)", False),
    ("mon_y2", rf"{MON}[\s\-./,']*(?P<y>\d{{2}})(?!\d)", True),
    # 2026-11, 2026/11
    ("y_m", rf"(?P<y>\d{{4}}){_SEP}(?P<m>\d{{1,2}})(?![\d/\-.])", False),
    # 11/2026, 11-2026, 11.2026
    ("m_y4", rf"(?<![\d/.\-])(?P<m>\d{{1,2}}){_SEP}(?P<y>\d{{4}})(?!\d)", False),
    # 11/26 -- ambiguous with fractions/sizes, so only trusted right after a keyword
    ("m_y2", rf"(?<![\d/.\-])(?P<m>\d{{1,2}}){_SEP}(?P<y>\d{{2}})(?![\d/.\-])", True),
]
_COMPILED = [(n, re.compile(p, re.I), kw) for n, p, kw in _DATE_PATTERNS]


def _year(y: str) -> int:
    v = int(y)
    return 2000 + v if v < 100 else v


def _end_of_month(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _numeric_dmy(a: int, b: int, order: str) -> tuple[int, int]:
    """Return (month, day) for an a/b/year date. Unambiguous if one part > 12."""
    if a > 12 and b <= 12:
        return b, a
    if b > 12 and a <= 12:
        return a, b
    return (a, b) if order.upper() == "MDY" else (b, a)


def _build(name: str, g: dict, numeric_order: str) -> date | None:
    try:
        y = _year(g["y"])
        if not 2000 <= y <= 2100:
            return None
        if name == "ymd":
            return date(y, int(g["m"]), int(g["d"]))
        if name in ("d_mon_y", "mon_d_y4", "mon_d_y2"):
            return date(y, _MONTHS[g["mon"].lower()[:3]], int(g["d"]))
        if name == "n_n_y":
            m, d = _numeric_dmy(int(g["a"]), int(g["b"]), numeric_order)
            return date(y, m, d)
        if name in ("mon_y4", "mon_y2"):
            return _end_of_month(y, _MONTHS[g["mon"].lower()[:3]])
        if name in ("y_m", "m_y4", "m_y2"):
            return _end_of_month(y, int(g["m"]))
    except (ValueError, KeyError):
        return None
    return None


def parse_best_before(text: str, numeric_order: str = "MDY", keyword_only: bool = False) -> date | None:
    """Find a best-before date in free text.

    Month-only dates ("09/2026", "Sep 2026") resolve to the last day of that month.
    Dates introduced by a keyword ("Best Before", "BB", "Exp", ...) win; otherwise a
    standalone date in an unambiguous format is used (earliest one, to be safe),
    unless keyword_only is set (used for long product descriptions).
    """
    if not text:
        return None
    # Keyword-anchored: look just after each keyword.
    for kw in KEYWORD_RE.finditer(text):
        window = text[kw.end(): kw.end() + 40]
        for name, rx, _ in _COMPILED:
            m = rx.match(window)
            if m:
                d = _build(name, m.groupdict(), numeric_order)
                if d:
                    return d
    if keyword_only:
        return None
    # Standalone: only patterns that can't be confused with sizes/fractions.
    found: list[tuple[int, date]] = []
    taken: list[tuple[int, int]] = []
    for name, rx, needs_kw in _COMPILED:
        if needs_kw:
            continue
        for m in rx.finditer(text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            d = _build(name, m.groupdict(), numeric_order)
            if d:
                found.append((m.start(), d))
                taken.append((m.start(), m.end()))
    return min(d for _, d in found) if found else None


# --- Bar counts --------------------------------------------------------------

_UNIT = (r"(?:bars?|cookies?|pastr(?:y|ies)|wafers?|brownies?|bites?|puffs?|"
         r"snacks?|pieces?|pcs|count|ct|packs?|pk|bags?|pouch(?:es)?|waffles?|tarts?)")
_COUNT_PATTERNS = [
    re.compile(r"\b(?:box|pack|case|carton|bundle|packs)\s+of\s+(\d{1,3})\b", re.I),   # box of 12
    re.compile(r"\b\d+(?:\.\d+)?\s*g\s*[x×]\s*(\d{1,3})\b", re.I),                    # 60g x 12
    re.compile(r"\b(\d{1,3})\s*[x×]\s*\d+(?:\.\d+)?\s*g\b", re.I),                    # 12 x 60g
    re.compile(rf"\b(\d{{1,3}})\s*[-\s]?\s*(?:random\s+|mini\s+|protein\s+|snack\s+)*{_UNIT}\b", re.I),  # 12 Bars
]
_SINGLE_RE = re.compile(r"\bsingle\b", re.I)


def parse_count(text: str) -> int | None:
    """Units in the package: '12 Bars/Box' -> 12, '60g x 12' -> 12, 'SINGLE BAR' -> 1."""
    if not text:
        return None
    for rx in _COUNT_PATTERNS:
        m = rx.search(text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 200:
                return n
    if _SINGLE_RE.search(text):
        return 1
    return None


# --- Classification ----------------------------------------------------------

def _any_word(words: list[str], text: str) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text, re.I) for w in words)


def is_bar_or_snack(title: str, include: list[str], exclude: list[str]) -> bool:
    return _any_word(include, title) and not _any_word(exclude, title)
