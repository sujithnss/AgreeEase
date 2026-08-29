"""
Malayalam number-to-words spelling, dependency-free (matches the project's
existing preference for stdlib-only helpers, see dateutils.py).

Used to reproduce the vendor specimen's convention of writing amounts and
dates with both digits and their Malayalam word-form side by side (e.g.
"5,500-ക (അയ്യായിരത്തിഅഞ്ഞൂറ് ഉറുപ്പിക)"). The composition rules below
(the "drop trailing ം, add ത്തി" pattern for tens/hundreds/thousands/lakhs,
and "drop trailing ് (chandrakkala), add ാം" for ordinals) are standard
Malayalam numeral morphology, verified against the real specimen's own
wording for 26, 500, 5000, 5500, 2026, and 18(th).

Supports 0 to 99,99,99,999 (crores) — comfortably past any rent, deposit,
or year this business will ever need to spell out.
"""

_UNITS = [
    "പൂജ്യം", "ഒന്ന്", "രണ്ട്", "മൂന്ന്", "നാല്", "അഞ്ച്",
    "ആറ്", "ഏഴ്", "എട്ട്", "ഒൻപത്",
]

_TEENS = [
    "പത്ത്", "പതിനൊന്ന്", "പന്ത്രണ്ട്", "പതിമൂന്ന്", "പതിനാല്",
    "പതിനഞ്ച്", "പതിനാറ്", "പതിനേഴ്", "പതിനെട്ട്", "പത്തൊൻപത്",
]

# Index 2-9 for 20/30/.../90; standalone form and "-തി" combining form
# (used when a units digit follows, e.g. ഇരുപത് + ത്തി + ആറ് = ഇരുപത്തിആറ്).
_TENS = {
    2: ("ഇരുപത്", "ഇരുപത്തി"),
    3: ("മുപ്പത്", "മുപ്പത്തി"),
    4: ("നാല്പത്", "നാല്പത്തി"),
    5: ("അമ്പത്", "അമ്പത്തി"),
    6: ("അറുപത്", "അറുപത്തി"),
    7: ("എഴുപത്", "എഴുപത്തി"),
    8: ("എൺപത്", "എൺപത്തി"),
    9: ("തൊണ്ണൂറ്", "തൊണ്ണൂറ്റി"),
}

# Standalone and combining ("followed by more digits") forms for 100-900.
_HUNDREDS = {
    1: ("നൂറ്", "നൂറ്റി"),
    2: ("ഇരുനൂറ്", "ഇരുനൂറ്റി"),
    3: ("മുന്നൂറ്", "മുന്നൂറ്റി"),
    4: ("നാനൂറ്", "നാനൂറ്റി"),
    5: ("അഞ്ഞൂറ്", "അഞ്ഞൂറ്റി"),
    6: ("അറുനൂറ്", "അറുനൂറ്റി"),
    7: ("എഴുനൂറ്", "എഴുനൂറ്റി"),
    8: ("എണ്ണൂറ്", "എണ്ണൂറ്റി"),
    9: ("തൊള്ളായിരം", "തൊള്ളായിരത്തി"),
}

# Standalone form for N x 1000 (N = 1-9). Combining form is derived by the
# general "trailing ം -> ത്തി" rule below rather than listed separately.
_THOUSAND_MULTIPLES = {
    1: "ആയിരം", 2: "രണ്ടായിരം", 3: "മൂവായിരം", 4: "നാലായിരം",
    5: "അയ്യായിരം", 6: "ആറായിരം", 7: "ഏഴായിരം", 8: "എട്ടായിരം",
    9: "ഒമ്പതിനായിരം",
}

# Standalone form for N x 10,000 (N = 1-9), i.e. exact multiples of ten
# thousand with no units-of-thousand remainder.
_TEN_THOUSAND_MULTIPLES = {
    1: "പതിനായിരം", 2: "ഇരുപതിനായിരം", 3: "മുപ്പതിനായിരം",
    4: "നാല്പതിനായിരം", 5: "അമ്പതിനായിരം", 6: "അറുപതിനായിരം",
    7: "എഴുപതിനായിരം", 8: "എൺപതിനായിരം", 9: "തൊണ്ണൂറായിരം",
}


def _combining(standalone: str) -> str:
    """The recurring Malayalam agglutination rule: a word ending in ം
    (anusvara) becomes ...ത്തി when another number word follows it."""
    if standalone.endswith("ം"):
        return standalone[:-1] + "ത്തി"
    return standalone + "-"  # shouldn't normally hit this fallback


def _glue_ayiram(stem_with_virama: str) -> str:
    """Attaches ആയിരം (thousand) to a bare cardinal word, e.g. for the
    10-19 thousands range: പതിനൊന്ന് (11) -> പതിനൊന്നായിരം (11000).
    Malayalam orthography attaches the dependent vowel sign ാ rather than
    the independent letter ആ when a vowel-initial word follows a consonant
    whose virama has been dropped (bare consonant = inherent 'a')."""
    stem = stem_with_virama[:-1] if stem_with_virama.endswith("്") else stem_with_virama
    return stem + "ായിരം"


def _under_100(n: int, combining: bool) -> str:
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    tens_digit, units_digit = divmod(n, 10)
    standalone, combine_form = _TENS[tens_digit]
    if units_digit == 0:
        return standalone
    return combine_form + _UNITS[units_digit]


def _under_1000(n: int) -> str:
    if n < 100:
        return _under_100(n, combining=False)
    hundreds_digit, remainder = divmod(n, 100)
    standalone, combine_form = _HUNDREDS[hundreds_digit]
    if remainder == 0:
        return standalone
    return combine_form + _under_100(remainder, combining=True)


def _under_100000(n: int) -> str:
    """Handles 0-99,999 (the thousands place plus everything under it)."""
    if n < 1000:
        return _under_1000(n)
    thousands_count, remainder = divmod(n, 1000)
    if thousands_count < 10:
        word = _THOUSAND_MULTIPLES[thousands_count]
    else:
        tens_digit, units_digit = divmod(thousands_count, 10)
        if tens_digit == 1:
            # 10-19 thousand use the teen cardinal (പതിന്-prefixed) stem
            # rather than the 20/30/.../90 combining table, e.g.
            # 11000 -> പതിനൊന്ന് (11) + ആയിരം -> പതിനൊന്നായിരം.
            if units_digit == 0:
                word = _TEN_THOUSAND_MULTIPLES[1]  # പതിനായിരം
            else:
                word = _glue_ayiram(_TEENS[units_digit])
        elif units_digit == 0:
            word = _TEN_THOUSAND_MULTIPLES[tens_digit]
        else:
            _, tens_combine = _TENS[tens_digit]
            word = tens_combine + _THOUSAND_MULTIPLES[units_digit]
    if remainder == 0:
        return word
    return _combining(word) + _under_1000(remainder)


def number_to_malayalam_words(n: int) -> str:
    """Spells out a non-negative integer in Malayalam words, e.g.
    5500 -> 'അയ്യായിരത്തിഅഞ്ഞൂറ്', 2026 -> 'രണ്ടായിരത്തിഇരുപത്തിആറ്'."""
    if n < 0:
        raise ValueError("Malayalam word-form is only defined for n >= 0")
    if n == 0:
        return _UNITS[0]
    if n < 100000:
        return _under_100000(n)

    # Lakhs (10^5) and crores (10^7) are, unlike the fused thousand words
    # above, written as a separate count word + a space + ലക്ഷം/കോടി (e.g.
    # "രണ്ടു ലക്ഷം", not a single fused word) — and "one" takes its
    # attributive form ഒരു rather than the standalone cardinal ഒന്ന്
    # (e.g. "ഒരു ലക്ഷം", not "ഒന്ന് ലക്ഷം"). This range isn't covered by
    # the vendor specimen this module was validated against, so treat it
    # as a best-effort convention rather than a verified form.
    crore, remainder = divmod(n, 10_000_000)
    lakh, remainder = divmod(remainder, 100_000)
    parts = []
    if crore:
        count = "ഒരു" if crore == 1 else number_to_malayalam_words(crore)
        word = count + " കോടി"
        parts.append(_combining(word) if (lakh or remainder) else word)
    if lakh:
        count = "ഒരു" if lakh == 1 else number_to_malayalam_words(lakh)
        word = count + " ലക്ഷം"
        parts.append(_combining(word) if remainder else word)
    if remainder:
        parts.append(_under_100000(remainder))
    return "".join(parts)


def ordinal_malayalam_words(n: int) -> str:
    """Spells out an ordinal (1st, 2nd, ...), e.g. 18 -> 'പതിനെട്ടാം',
    matching how the vendor specimen writes the day-of-month in the
    agreement's opening date line."""
    cardinal = number_to_malayalam_words(n)
    if cardinal.endswith("്"):
        cardinal = cardinal[:-1]
    return cardinal + "ാം"


_MONTH_NAMES_ML = {
    1: "ജനുവരി", 2: "ഫെബ്രുവരി", 3: "മാർച്ച്", 4: "ഏപ്രിൽ",
    5: "മെയ്", 6: "ജൂൺ", 7: "ജൂലൈ", 8: "ആഗസ്റ്റ്",
    9: "സെപ്റ്റംബർ", 10: "ഒക്ടോബർ", 11: "നവംബർ", 12: "ഡിസംബർ",
}


def month_name_malayalam(month: int) -> str:
    return _MONTH_NAMES_ML[month]
