from datetime import date

import pytest

from parsing import is_bar_or_snack, parse_best_before, parse_count

# Real strings seen on the four stores, plus common variants.
DATES = [
    # SupplementSource variant titles
    ("Caramel Crunch / 30-Nov-26", date(2026, 11, 30)),
    ("21-Sep-26", date(2026, 9, 21)),
    ("Fluff n Butter / Bars have EXTRA crunch see Note in description / No Refunds / 30-Nov-26", date(2026, 11, 30)),
    # Vita-Plus
    ("Built Puff Protein Bars (12 Bars) - Best Before 09/2026", date(2026, 9, 30)),
    ("Warrior Crunch Protein Bars (12 Bars) - Best Before 09/25", date(2025, 9, 30)),
    ("Pumpkin Spice / 12 Bars (Best Before 09/2026", date(2026, 9, 30)),
    ("Chocolate Mint / 2lbs (Best Before End of 11/2026)", date(2026, 11, 30)),
    ("Pink Lemonade / 30 Servings (Best Beore 05/2026)", date(2026, 5, 31)),
    ("Best Before: 10/24", date(2024, 10, 31)),
    # Top Nutrition
    ("Chocolate Peanut Butter Cup BB April 2, 2026 FINAL SALE", date(2026, 4, 2)),
    ("Cookies & Cream BB 05/2026 Final Sale", date(2026, 5, 31)),
    ("Bucked Up RUT (90 Capsules) BB 03/31/26", date(2026, 3, 31)),
    # Other common forms
    ("Best Before 2026-11-15", date(2026, 11, 15)),
    ("BBD: 2026/11", date(2026, 11, 30)),
    ("Exp. Nov 2026", date(2026, 11, 30)),
    ("EXP 11-26", date(2026, 11, 30)),
    ("Best by Sept '26", date(2026, 9, 30)),
    ("Expiry Date: November 30th, 2026", date(2026, 11, 30)),
    ("best before 30 November 2026", date(2026, 11, 30)),
    ("BB 15.11.2026", date(2026, 11, 15)),       # day > 12 -> DMY
    ("Use by 11.2026", date(2026, 11, 30)),
    ("Short dated - Dec 15 2026", date(2026, 12, 15)),
    ("B.B. 31/12/26", date(2026, 12, 31)),
    ("Good until Jan-27", date(2027, 1, 31)),
    ("Expires 2027-01-05", date(2027, 1, 5)),
    ("12 Bars (Best Before 05/206)", None),         # typo on vita-plus: unparseable
    # Things that must NOT be read as dates
    ("Grenade GRENADE BAR, 60g x 12 Bars/Box", None),
    ("Marshmallow Rocky Road / 12 Bars", None),
    ("Mrs. Taste Zero Calories Mayonnaise (355g)", None),
    ("Red 5-50lbs (1/2\")", None),
    ("Sour Lemonade / 30 Servings", None),
]


@pytest.mark.parametrize("text,expected", DATES)
def test_parse_best_before(text, expected):
    assert parse_best_before(text) == expected


def test_numeric_order_dmy():
    assert parse_best_before("BB 03/04/26", "DMY") == date(2026, 4, 3)
    assert parse_best_before("BB 03/04/26", "MDY") == date(2026, 3, 4)


def test_keyword_only_ignores_bare_dates():
    assert parse_best_before("Founded November 2019", keyword_only=True) is None
    assert parse_best_before("This lot has a best-before date of 03/31/26", keyword_only=True) == date(2026, 3, 31)


COUNTS = [
    ("Alani Nu PROTEIN BARS, 12 Bars/Box", 12),
    ("Grenade GRENADE BAR, 60g x 12 Bars/Box", 12),
    ("5 RANDOM PROTEIN BARS * Limit 3", 5),
    ("Pure Protein PURE PROTEIN BARS, 6 Bars/Box *Limit 6", 6),
    ("SINGLE BAR Final Boss Performance ANABAR, 65g", 1),
    ("Wispy PROTEIN BAR, 55g x 10 Bars/Box", 10),
    ("Cream Egg / 18 Bars (Best Before 12/2026)", 18),
    ("Strawberry Shortcake / 7 Bar (Best Before 08/2026)", 7),
    ("TRUBAR GF Vegan Protein Bar (1 box of 12 bars)", 12),
    ("TRUBAR GF Vegan Protein Bar (1 bar)", 1),
    ("Cinnamon / 6 Pastry (Best Before 03/2026)", 6),
    ("Birthday Cake / 12 Cookie (Best Before 07/2026)", 12),
    ("ChocZero Jolies GF Keto Cookies (21 cookies)", 21),
    ("Mixed Berry (12-pack)", 12),
    ("12 x 45g", 12),
    ("Quest Protein Chips (8 x 32g)", 8),
    ("Wilde Protein Chips (12 Bags)", 12),
    ("Protein Crisps (1 bag)", 1),
    ("Keto Cheese Crisps (6 pouches)", 6),
    ("Prozis High Protein Waffles (1 box of 6 waffles)", 6),
    ("SANA GF Crunchy Protein Bites (100 g)", None),
    ("Barebells Protein Bar (10 Flavors)", None),
]


@pytest.mark.parametrize("text,expected", COUNTS)
def test_parse_count(text, expected):
    assert parse_count(text) == expected


INCLUDE = ["bar", "bars", "snack", "snacks", "cookie", "cookies", "pastry", "bites", "puff"]
EXCLUDE = ["drink", "sauce", "syrup", "spices", "band", "bands", "mayonnaise"]


@pytest.mark.parametrize("title,expected", [
    ("Barebells Protein Bar (12 bars)", True),
    ("Unreal Chocolate Snacks (1 Pack)", True),
    ("HangryBoy Protein Cookie (1 cookie)", True),
    ("Bum Energy Drink (12 Cans)", False),
    ("Mrs. Taste Zero Calories Mayonnaise (355g)", False),
    ("Flavor God Spices (188 Servings) - Best Before 07/24", False),
    ("Ultimate Resistance Bands Bundle", False),
    ("Allmax Hexapro Protein (2lbs)", False),
])
def test_is_bar_or_snack(title, expected):
    assert is_bar_or_snack(title, INCLUDE, EXCLUDE) is expected


# --- Regression: real catalog titles, classified with the real config.toml -----------
import tomllib
from pathlib import Path

from parsing import is_snack

_F = tomllib.loads((Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8"))["filter"]


def _snack(title, ptype=""):
    return is_snack(title, ptype, _F["include"], _F["exclude"], _F["snack_types"])


@pytest.mark.parametrize("title,ptype", [
    # Caught by title keywords
    ("Barebells Protein Bar (12 bars)", ""),
    ("Quest Frosted Cookies (8 Cookies)", ""),
    ("Legendary Foods Tasty Pastry (10 Packs)", ""),
    ("Legendary Foods GF Keto Protein Pastry (Box of 10 \"poptarts\")", ""),
    ("Quest Peanut Butter Cups (12 Pack)", ""),
    ("Legendary Foods Protein Sweet Roll (8 Packs)", ""),
    ("Bowmar Apex Protein Meat Sticks (1 stick)", ""),
    ("MuscleSport GF Protein Whoopies (1 whoopie)", ""),
    ("Go Nutrition GF Go Gummy Protein Candy (1 Bag)", ""),
    ("Quest Protein Chips (8 Bags)", ""),
    ("Omaha Protein Popcorn 124g-224g", ""),
    ("Crisp Power High Protein Keto Vegan Pretzels (BOX of 6)", ""),
    ("Tide Treats GF Crispy Rice Protein Bars (1 Box of 8)", ""),
    ("Go Nutrition Go Beanz Roasted Edamame (20 Pack)", ""),
    ("Better Bovine Extra Lean Beef Jerky (1 bag)", ""),
    ("AP Prime Bites Keto GF Mini Protein Muffins (1 pack of 3 mini muffins)", ""),
    ("Smarter Snacks GF Protein Waffle (Box of 12)", ""),
    # Caught only because the store filed it as a snack
    ("Nutraphase Clean Beans (3 servings)", "Protein Snacks"),
    ("Bucked Up Protein Bites", "Protein Snacks"),
    ("SINGLE PACK Quest PEANUT BUTTER CUPS, 2 pack", "Protein Bars"),
])
def test_snacks_are_tracked(title, ptype):
    assert _snack(title, ptype)


@pytest.mark.parametrize("title,ptype", [
    ("Barebells Protein Drink (12 Cans)", "Protein Bars & Snacks"),
    ("Mrs. Taste Zero Calories Cookies & Cream Syrup (335g)", "Protein Bars & Snacks"),
    ("The Flavor Gang Sweet Papi Sauce (473ml)", "Protein Bars & Snacks"),
    ("Protein 2O Infused Protein Water (12 Bottles)", "Protein Bars & Snacks"),
    ("Musclesport Cream Of Rice Gourmet Hot Cereal (25 Servs)", "Protein Bars & Snacks"),
    ("PB2 GF Powdered Peanut Butter (1 LB)", "Protein Snacks"),
    ("Mission Fajita Size Carb Balance Flour Tortillas - 8oz/8ct", "Protein Snacks"),
    ("Youtheory BIOTIN+ ZERO SUGAR GUMMIES, 60 Gummies", ""),
    ("Nova Pharma Dodo (60 Gummies)", ""),
    ("Betty Crocker Lower Sugar Chocolate Chip Cookie Mix (16 servings)", "Protein Snacks"),
    ("Grizzly 2\" SUPREME GRIP BAR COLLARS 8780-04", ""),
    ("Optimum Nutrition Gold Standard 100% Whey Protein (5lbs)", ""),
    ("Prozis Energy Gel (1 gel - 25G)", "Protein Snacks"),
])
def test_non_snacks_are_skipped(title, ptype):
    assert not _snack(title, ptype)


@pytest.mark.parametrize("text,expected", [
    ("ChocZero Peanut Stackers (12 packs of 2 cookies)", 12),
    ("NEW Quest Crispy Protein Chips (1 bag of 4 servings)", 1),
    ("Simply Protein Vegan Tortilla Chips (1 bag of 2.5 servings)", 1),
    ("Grenade Creme Egg Flavour Soft Core Protein Bar 18/bars", 18),
    ("Iron Vegan SPROUTED PROTEIN BARS, 12/box", 12),
    ("AP Prime Bites (BOX OF 12 PACKS of 3 mini muffins)", 12),
    ("Quest GF Peanut Butter Cups (1 x 2-cup package)", 1),
    ("Quest GF Peanut Butter Cups (Box of 12 2-cup packages)", 12),
    ("Legendary Foods Protein Sweet Roll (8 Packs)", 8),
    ("Bowmar Apex Protein Meat Sticks (1 stick)", 1),
])
def test_parse_count_snacks(text, expected):
    assert parse_count(text) == expected
