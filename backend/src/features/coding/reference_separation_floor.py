"""Deterministic brand-separation check for reference-product runs.

SPEC-build-your-own.md §12: when Syndai builds a customer "their own version" of a
product, the built tree must not reproduce the *source* product's brand name, its
domain, or verbatim text. This module is the teeth behind that rule.

Three signals, deliberately different strengths:

* **domain** — a source domain matched on a host boundary (not a bare substring,
  so source "cal.com" does not fire on an unrelated "local.com"). This is the
  highest-severity, high-precision tooth.
* **shingle** — any verbatim 12-token run of the scraped prose, matched via
  ``blake2b`` hashes. A 12-token match is essentially never accidental; this is
  the other real tooth, and it survives paraphrase-at-the-edges because the
  windows overlap.
* **brand name** — a whole-word match on the product name. This is a *soft*
  signal (a name can be an ordinary word: "Cal", "Monday", "Notion"), so a short
  common-word brand term is only reported when it sits in a file that also has a
  domain or shingle hit.

The check never raises and never hard-blocks: it returns a list of findings in the
floors' ``{check, target, detail, fix}`` shape (plus a ``kind`` discriminator) so
the existing validation->repair round consumes them unchanged. Wiring the scan
into the backend validation stage lives outside this open-source checkout; the
deterministic scanner and fingerprint helpers land here and are unit-tested.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
from pathlib import Path

SHINGLE_N = 12
_DIGEST_SIZE = 8

# A soft brand term counts only when it sits within this many lines of a domain
# or shingle hit (§12 "adjacent to a domains hit or a shingle hit").
_ADJACENCY = 2

# Directories / files excluded from the scan (§12). ``.syndai`` holds the
# reference scratch by design; ``PRODUCT.md`` is Syndai's own summary and is
# allowed to name the source.
_EXCLUDED_DIRS = frozenset(
    {"node_modules", ".syndai", ".git", "dist", "build", ".next", ".turbo", "vendor"}
)
_EXCLUDED_FILES = frozenset(
    {
        "product.md",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "poetry.lock",
        "cargo.lock",
    }
)

# ponytail: a 30-word stoplist, not an NLP model; widen only if a real brand name
# keeps slipping through. Soft brand terms that are a common word AND <=4 chars
# are reported only when adjacent to a domain or shingle hit.
_COMMON_SHORT_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "you",
        "are",
        "all",
        "new",
        "get",
        "now",
        "our",
        "one",
        "day",
        "way",
        "use",
        "see",
        "top",
        "any",
        "can",
        "not",
        "but",
        "out",
        "add",
        "set",
        "up",
        "go",
        "to",
        "of",
        "in",
        "on",
        "cal",
    }
)

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _tokens(text: str) -> list[str]:
    """Normalized word tokens: NFKC, lowercase, punctuation stripped to spaces."""
    return _TOKEN.findall(_normalize(text))


def _shingle_hash(window: list[str]) -> bytes:
    return hashlib.blake2b(
        " ".join(window).encode("utf-8"), digest_size=_DIGEST_SIZE
    ).digest()


def compute_shingles(text: str, n: int = SHINGLE_N) -> set[bytes]:
    """Set of ``blake2b`` hashes of every ``n``-token window of ``text``."""
    tokens = _tokens(text)
    if len(tokens) < n:
        return set()
    return {_shingle_hash(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def build_fingerprint(
    pages: list[str],
    brand_terms: list[str],
    domains: list[str],
    n: int = SHINGLE_N,
) -> dict:
    """Assemble the ``reference_fingerprint`` persisted at dossier time (§12)."""
    shingles: set[bytes] = set()
    for page in pages:
        shingles |= compute_shingles(page, n)
    return {
        "brand_terms": list(brand_terms),
        "domains": list(domains),
        "shingles": shingles,
        "shingle_n": n,
    }


def _fix_hint(name: str, kind: str, match: str, file: str) -> str:
    noun = {"domain": "domain", "brand": "name", "shingle": "wording"}[kind]
    return (
        f"reference-leak = this ships the source product's {noun}: "
        f'"{match}" in {file}. Make it the customer\'s own — their name, your '
        "words, your dummy data. Do not paraphrase the source; write the "
        "customer's."
    )


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _is_excluded(rel: Path) -> bool:
    if any(part in _EXCLUDED_DIRS for part in rel.parts):
        return True
    return rel.name.lower() in _EXCLUDED_FILES


def _is_adjacent(lineno: int, hard_lines: set[int]) -> bool:
    return any(abs(lineno - h) <= _ADJACENCY for h in hard_lines)


def _scan_text(
    text: str,
    rel_name: str,
    fingerprint: dict,
    source_name: str,
) -> list[dict]:
    findings: list[dict] = []
    lines = text.splitlines()
    lower_lines = [ln.lower() for ln in lines]
    domains = [d.lower() for d in fingerprint.get("domains", [])]
    brand_terms = fingerprint.get("brand_terms", [])
    shingle_set: set[bytes] = fingerprint.get("shingles", set())
    n = fingerprint.get("shingle_n", SHINGLE_N)

    # 1. domain hits (host-boundary match, high precision). A bare substring test
    # false-positives (source "cal.com" inside an unrelated "local.com"), so the
    # domain must sit on a host boundary: not preceded or followed by another
    # label/subdomain character ([a-z0-9-]). A leading "." (subdomain) or "/" is a
    # boundary, so "app.cal.com" and "https://cal.com/x" still match.
    domain_patterns = [
        (domain, re.compile(r"(?<![a-z0-9-])" + re.escape(domain) + r"(?![a-z0-9-])"))
        for domain in domains
        if domain
    ]
    domain_lines: set[int] = set()
    for lineno, low in enumerate(lower_lines, start=1):
        for domain, pattern in domain_patterns:
            if pattern.search(low):
                domain_lines.add(lineno)
                findings.append(
                    {
                        "check": "reference_separation",
                        "kind": "domain",
                        "target": f"{rel_name}:{lineno}",
                        "detail": f"source domain {domain!r} appears in {rel_name}",
                        "fix": _fix_hint(source_name, "domain", domain, rel_name),
                    }
                )

    # 3. shingle hits (verbatim 12-token runs). Tokens are tracked with their
    # source line so a match reports where it begins; windows cross lines, which
    # is what collapsing whitespace to a single stream yields.
    shingle_lines: set[int] = set()
    if shingle_set:
        toks: list[str] = []
        tok_lines: list[int] = []
        for lineno, line in enumerate(lines, start=1):
            for tok in _tokens(line):
                toks.append(tok)
                tok_lines.append(lineno)
        for i in range(len(toks) - n + 1):
            window = toks[i : i + n]
            if _shingle_hash(window) in shingle_set:
                start_line = tok_lines[i]
                shingle_lines.add(start_line)
                phrase = " ".join(window)
                findings.append(
                    {
                        "check": "reference_separation",
                        "kind": "shingle",
                        "target": f"{rel_name}:{start_line}",
                        "detail": f'verbatim source phrase: "{phrase}"',
                        "fix": _fix_hint(source_name, "shingle", phrase, rel_name),
                    }
                )

    # 2. brand-name hits (whole word, soft). A short common-word term is only
    # reported when it sits ADJACENT to a domain or shingle hit (within
    # _ADJACENCY lines), so an unrelated `cal` variable far from a real leak in
    # the same file is not flagged.
    hard_hit_lines = domain_lines | shingle_lines
    for term in brand_terms:
        term_norm = term.lower()
        if not term_norm:
            continue
        pattern = re.compile(r"\b" + re.escape(term_norm) + r"\b")
        soft = len(term_norm) <= 4 and term_norm in _COMMON_SHORT_WORDS
        for lineno, low in enumerate(lower_lines, start=1):
            if not pattern.search(low):
                continue
            if soft and not _is_adjacent(lineno, hard_hit_lines):
                continue
            findings.append(
                {
                    "check": "reference_separation",
                    "kind": "brand",
                    "target": f"{rel_name}:{lineno}",
                    "detail": f"source brand name {term!r} appears in {rel_name}",
                    "fix": _fix_hint(source_name, "brand", term, rel_name),
                }
            )
    return findings


def _git_tracked_files(root_path: Path) -> list[Path] | None:
    """Tracked files under ``root_path`` when it is a git worktree, else ``None``.

    ``git ls-files`` honours the repo's ignore rules for free, so an ignored
    reference dump (e.g. anything under a gitignored path) never reaches the scan
    even if it is not in the hardcoded exclusion list (§12: "tracked text files
    the build ships … use the repo's own ignore rules").
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root_path), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # Git permits tracked filenames with non-UTF-8 bytes; decode through the
    # filesystem codec (surrogateescape) so such a name round-trips back to a
    # readable path instead of raising UnicodeDecodeError.
    return [
        root_path / os.fsdecode(name)
        for name in proc.stdout.split(b"\x00")
        if name
    ]


def _candidate_files(root_path: Path) -> list[Path]:
    tracked = _git_tracked_files(root_path)
    if tracked is not None:
        return tracked
    # Not a git worktree (e.g. a test tmp tree): fall back to a full walk and
    # lean on the hardcoded exclusions below.
    return sorted(root_path.rglob("*"))


def scan_tree(root, fingerprint: dict) -> list[dict]:
    """Scan the built worktree at ``root`` against ``fingerprint`` (§12).

    Returns a list of findings in the floors' ``{check, target, detail, fix}``
    shape (plus ``kind``). Never raises; an empty fingerprint or empty tree
    yields ``[]``. Only files the build actually ships are scanned: in a git
    worktree that is the set of tracked files (ignored paths are skipped via the
    repo's own ignore rules); otherwise a full walk minus the hardcoded
    exclusions (``node_modules``/``.syndai``/``.git``/build output, lockfiles,
    ``PRODUCT.md``). Symlinks are never followed and binary files (NUL-byte
    sniff) are skipped.
    """
    root_path = Path(root)
    if not root_path.exists():
        return []
    if not (
        fingerprint.get("domains")
        or fingerprint.get("brand_terms")
        or fingerprint.get("shingles")
    ):
        return []

    source_name = ""
    terms = fingerprint.get("brand_terms") or []
    if terms:
        source_name = terms[0]
    elif fingerprint.get("domains"):
        source_name = fingerprint["domains"][0]

    real_root = root_path.resolve()
    findings: list[dict] = []
    for path in _candidate_files(root_path):
        rel = path.relative_to(root_path)
        if _is_excluded(rel):
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        # Enforce containment: a tracked `src/page.md` whose `src` was replaced by
        # a symlink to an outside directory still shows up in `git ls-files`, and
        # its leaf is not itself a symlink. Rejecting anything whose resolved path
        # differs from the lexical path under the resolved root drops files
        # reached through a symlinked leaf OR ancestor, and anything escaping the
        # tree, without following symlinks (§12: symlinks are never followed).
        if resolved != real_root.joinpath(*rel.parts):
            continue
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if _looks_binary(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        findings.extend(
            _scan_text(text, rel.as_posix(), fingerprint, source_name)
        )
    return findings


__all__ = [
    "SHINGLE_N",
    "compute_shingles",
    "build_fingerprint",
    "scan_tree",
]
