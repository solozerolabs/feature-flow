"""SPEC §13 step 2: deterministic Firecrawl page selection.

Pure function, no network. A fixed candidate list must yield exactly the expected
<=15 intent-ranked pages, with blog/login/privacy/locale-dupe dropped.
"""

from reference_page_selection import MAX_PAGES, select_reference_pages

ORIGIN = "https://cal.com"

CANDIDATES = [
    "https://cal.com/",
    "https://cal.com/pricing",
    "https://cal.com/features",
    "https://cal.com/product",
    "https://cal.com/docs",
    "https://cal.com/docs/intro",
    "https://cal.com/docs/scheduling",
    "https://cal.com/docs/teams",
    "https://cal.com/docs/webhooks",
    "https://cal.com/docs/availability",
    "https://cal.com/docs/routing",
    "https://cal.com/changelog",
    "https://cal.com/blog/why-scheduling",
    "https://cal.com/login",
    "https://cal.com/privacy",
    "https://cal.com/fr/pricing",
]

EXPECTED = [
    "https://cal.com/",
    "https://cal.com/pricing",
    # features tie-break is shortest-path-first: /product (8) before /features (9)
    "https://cal.com/product",
    "https://cal.com/features",
    "https://cal.com/docs",
    # five doc children by shortest-path-then-alpha; /docs/availability drops
    "https://cal.com/docs/intro",
    "https://cal.com/docs/teams",
    "https://cal.com/docs/routing",
    "https://cal.com/docs/webhooks",
    "https://cal.com/docs/scheduling",
    "https://cal.com/changelog",
]


def test_selection_matches_expected_order_and_drops():
    assert select_reference_pages(CANDIDATES, ORIGIN) == EXPECTED


def test_cap_at_15():
    many = ["https://x.com/"] + [f"https://x.com/docs/a{i}" for i in range(40)]
    result = select_reference_pages(many, "https://x.com")
    assert len(result) <= MAX_PAGES


def test_blog_login_privacy_dropped():
    result = select_reference_pages(CANDIDATES, ORIGIN)
    assert "https://cal.com/blog/why-scheduling" not in result
    assert "https://cal.com/login" not in result
    assert "https://cal.com/privacy" not in result


def test_locale_duplicate_collapses_to_first_seen():
    result = select_reference_pages(CANDIDATES, ORIGIN)
    assert "https://cal.com/fr/pricing" not in result
    assert "https://cal.com/pricing" in result


def test_home_always_included_and_first():
    result = select_reference_pages(CANDIDATES, ORIGIN)
    assert result[0] == "https://cal.com/"


def test_feature_slots_capped_at_three():
    cands = [
        "https://y.com/",
        "https://y.com/features",
        "https://y.com/product",
        "https://y.com/solutions",
        "https://y.com/how-it-works",
    ]
    result = select_reference_pages(cands, "https://y.com")
    feature_paths = [u for u in result if u != "https://y.com/"]
    assert len(feature_paths) == 3


def test_homepage_injected_when_map_omits_it():
    # §13: "always including /". A map result without the root must still yield it,
    # derived from origin, and it must come first.
    cands = ["https://cal.com/pricing", "https://cal.com/features"]
    result = select_reference_pages(cands, ORIGIN)
    assert result[0] == "https://cal.com/"
    assert "https://cal.com/pricing" in result


def test_empty_input_returns_empty():
    assert select_reference_pages([], ORIGIN) == []
