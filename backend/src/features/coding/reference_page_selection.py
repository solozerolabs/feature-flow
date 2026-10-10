"""Deterministic Firecrawl page selection for the reference-product dossier.

This is the pure function behind SPEC-build-your-own.md §13 step 2: given the
candidate URLs a Firecrawl ``map`` returns for a product origin, pick at most 15
pages, ranked by *page intent* (home, pricing, features, docs, …) rather than by
crawl order, so one slow synthesis call sees the richest free spec a product has.

The function is network-free and total: the same candidate list always yields the
same ordered subset, which is what makes it unit-testable (see
``tests/test_reference_page_selection.py``). The surrounding dossier lane that
calls ``map_urls`` / ``scrape_url`` lives in the Syndai backend and is out of
scope for this open-source checkout; only the deterministic picker lands here.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

MAX_PAGES = 15

# A locale path prefix: a 2-letter language, optionally with a region
# (``/fr/``, ``/en-us/``, ``/pt-br/``). Used to collapse locale duplicates.
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")

# Asset / API file extensions that are never spec pages.
_ASSET_SUFFIXES = (
    ".js",
    ".mjs",
    ".css",
    ".map",
    ".json",
    ".xml",
    ".txt",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
    ".zip",
)

# Path prefixes dropped up front: never carry product-feature signal.
_DROP_PREFIXES = (
    "/blog",
    "/news",
    "/careers",
    "/jobs",
    "/privacy",
    "/terms",
    "/legal",
    "/cookies",
    "/login",
    "/signin",
    "/sign-in",
    "/signup",
    "/sign-up",
    "/register",
    "/auth",
    "/logout",
    "/api",
    "/assets",
    "/static",
    "/cdn",
    "/_next",
)

# Exact index paths (priority 4). Children of these become priority-5 articles.
_DOCS_INDEX = ("/docs", "/help", "/guide", "/support", "/getting-started")

# Each intent: (priority, slots, matcher). Lower priority number wins; a URL is
# assigned to the first (highest-priority) intent it matches. ``None`` slots on
# priority 5 is special-cased (children of a selected docs index).
_PRICING = ("/pricing", "/plans")
_FEATURES = ("/features", "/product", "/solutions", "/how-it-works")
_CHANGELOG = ("/changelog", "/releases", "/whats-new", "/updates")
_ABOUT = ("/about", "/use-cases", "/customers")

# priority -> max slots
_SLOTS = {1: 1, 2: 1, 3: 3, 4: 1, 5: 5, 6: 1, 7: 2}


def _homepage(origin: str) -> str | None:
    """The ``/`` URL for ``origin`` (``https://x.com/path`` -> ``https://x.com/``)."""
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def _path_of(url: str) -> str:
    """Normalized path for ``url``: lowercased, no trailing slash (except root)."""
    path = urlsplit(url).path or "/"
    path = path.lower()
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def _strip_locale(path: str) -> str:
    """Canonical path with a leading locale segment removed (``/fr/x`` -> ``/x``)."""
    if path == "/":
        return path
    segments = path.strip("/").split("/")
    if segments and _LOCALE_SEGMENT.match(segments[0]):
        rest = "/".join(segments[1:])
        return "/" + rest if rest else "/"
    return path


def _should_drop(path: str) -> bool:
    if path == "/":
        return False
    if path.endswith(_ASSET_SUFFIXES):
        return True
    for prefix in _DROP_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        if path == pat or path.startswith(pat + "/"):
            return True
    return False


def _intent(path: str) -> int | None:
    """Highest-priority intent for ``path``, or ``None`` if it has no spec value."""
    if path == "/":
        return 1
    if _matches(path, _PRICING):
        return 2
    if _matches(path, _FEATURES):
        return 3
    if path in _DOCS_INDEX:  # exact: the index itself, not its children
        return 4
    for index in _DOCS_INDEX:
        if path.startswith(index + "/"):
            return 5
    if _matches(path, _CHANGELOG):
        return 6
    if _matches(path, _ABOUT):
        return 7
    return None


def select_reference_pages(candidates: list[str], origin: str) -> list[str]:
    """Pick <=15 intent-ranked pages from Firecrawl ``map`` candidates.

    ``candidates`` is the discovered URL list; ``origin`` is the product origin,
    used to guarantee the homepage (``/``) is always selected even when ``map``
    omits it (§13: "always including /"). Matching is otherwise path-based, so
    off-origin URLs simply rank by their own path. Returns the selected URLs in
    intent-priority order. Blog/careers/legal/auth/asset/API paths are dropped;
    locale duplicates collapse to the first locale seen; ties within an intent
    break by shortest path then alphabetically. Empty input returns ``[]``.
    """
    if not candidates:
        return []

    # Collapse locale duplicates, keeping the first-seen URL per canonical path,
    # and de-duplicate identical normalized paths.
    seen_canonical: dict[str, str] = {}
    for url in candidates:
        path = _path_of(url)
        if _should_drop(path):
            continue
        canonical = _strip_locale(path)
        if canonical not in seen_canonical:
            seen_canonical[canonical] = url

    # §13: the homepage is always included. If no candidate resolved to "/", seed
    # it from the origin so a map result that omits the root still yields it.
    homepage = _homepage(origin)
    if homepage is not None:
        seen_canonical.setdefault("/", homepage)

    # Bucket surviving URLs by intent.
    buckets: dict[int, list[tuple[int, str, str]]] = {p: [] for p in _SLOTS}
    for canonical, url in seen_canonical.items():
        intent = _intent(canonical)
        if intent is None:
            continue
        buckets[intent].append((len(canonical), canonical, url))

    selected: list[str] = []
    for priority in sorted(_SLOTS):
        bucket = sorted(buckets[priority])  # (len, path, url): shortest then alpha
        for _, _, url in bucket[: _SLOTS[priority]]:
            if url not in selected:
                selected.append(url)
            if len(selected) >= MAX_PAGES:
                return selected
    return selected


__all__ = ["select_reference_pages", "MAX_PAGES"]
