"""SPEC §12: deterministic brand-separation check.

Pure functions over a temp tree + fingerprint. Also the §8 acceptance mutation
(paste a source paragraph -> red; a fully reworded one -> green).
"""

import subprocess

from reference_separation_floor import build_fingerprint, compute_shingles, scan_tree

# A 31-token source paragraph: long enough that changing the three middle tokens
# still leaves whole untouched 12-token windows at both ends.
SOURCE_PARAGRAPH = (
    "book a meeting by sharing one simple link where invited guests choose any "
    "open time slot on your calendar and both sides instantly receive a confirmed "
    "invite without trading many emails"
)

FINGERPRINT = build_fingerprint(
    pages=[SOURCE_PARAGRAPH],
    brand_terms=["Acme"],
    domains=["cal.com"],
)


def _kinds(findings, kind):
    return [f for f in findings if f["kind"] == kind]


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_domain_hit(tmp_path):
    _write(tmp_path, "src/page.md", "Learn more at https://cal.com/pricing today.")
    findings = scan_tree(tmp_path, FINGERPRINT)
    assert len(_kinds(findings, "domain")) == 1
    assert findings[0]["target"].startswith("src/page.md:")
    assert "reference-leak" in findings[0]["fix"]


def test_verbatim_shingle_hit_reports_phrase(tmp_path):
    _write(tmp_path, "src/copy.tsx", f"const hero = `{SOURCE_PARAGRAPH}`")
    findings = _kinds(scan_tree(tmp_path, FINGERPRINT), "shingle")
    assert len(findings) >= 1
    assert any("invited guests choose" in f["detail"] for f in findings)


def test_paraphrase_at_edges_still_hits(tmp_path):
    # Change the three middle tokens ("time slot on" -> "period window at").
    mutated = SOURCE_PARAGRAPH.replace("open time slot on", "open period window at")
    assert mutated != SOURCE_PARAGRAPH
    _write(tmp_path, "src/copy.tsx", mutated)
    findings = _kinds(scan_tree(tmp_path, FINGERPRINT), "shingle")
    assert len(findings) >= 1


def test_fully_reworded_has_no_shingle_hit(tmp_path):
    reworded = (
        "Pick a time that fits everyone and we send both of you the details "
        "right away so nobody has to chase replies."
    )
    _write(tmp_path, "src/copy.tsx", reworded)
    findings = _kinds(scan_tree(tmp_path, FINGERPRINT), "shingle")
    assert findings == []


def test_exclusions_hold(tmp_path):
    leak = f"https://cal.com {SOURCE_PARAGRAPH}"
    _write(tmp_path, "node_modules/dep/readme.md", leak)
    _write(tmp_path, ".syndai/reference/raw.md", leak)
    _write(tmp_path, "PRODUCT.md", leak)
    assert scan_tree(tmp_path, FINGERPRINT) == []


def test_binary_file_skipped(tmp_path):
    (tmp_path / "logo.bin").write_bytes(b"cal.com\x00" + SOURCE_PARAGRAPH.encode())
    assert scan_tree(tmp_path, FINGERPRINT) == []


def test_empty_fingerprint_returns_empty(tmp_path):
    _write(tmp_path, "src/page.md", "https://cal.com " + SOURCE_PARAGRAPH)
    empty = {"brand_terms": [], "domains": [], "shingles": set(), "shingle_n": 12}
    assert scan_tree(tmp_path, empty) == []


def test_soft_brand_far_from_hard_hit_not_flagged(tmp_path):
    # §12: a short common-word brand ("cal") is reported only when adjacent to a
    # domain/shingle hit. A 'cal' variable 60 lines from the only domain hit must
    # not generate a false repair instruction.
    fp = build_fingerprint(pages=[], brand_terms=["cal"], domains=["example.com"])
    body = "\n".join(
        ["visit https://example.com"] + ["filler line"] * 60 + ["const cal = 1"]
    )
    _write(tmp_path, "src/a.ts", body)
    assert _kinds(scan_tree(tmp_path, fp), "brand") == []


def test_soft_brand_adjacent_to_hard_hit_flagged(tmp_path):
    fp = build_fingerprint(pages=[], brand_terms=["cal"], domains=["example.com"])
    _write(tmp_path, "src/a.ts", "visit https://example.com for cal widgets")
    assert len(_kinds(scan_tree(tmp_path, fp), "brand")) == 1


def test_gitignored_file_not_scanned(tmp_path):
    # In a git worktree, only tracked files are scanned, so an ignored scratch
    # dump that never ships generates no findings.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    leak = f"https://cal.com {SOURCE_PARAGRAPH}"
    _write(tmp_path, ".gitignore", "scratch/\n")
    _write(tmp_path, "scratch/dump.md", leak)  # gitignored, untracked
    _write(tmp_path, "src/page.md", leak)  # tracked
    subprocess.run(
        ["git", "add", ".gitignore", "src/page.md"], cwd=tmp_path, check=True
    )
    targets = [f["target"] for f in scan_tree(tmp_path, FINGERPRINT)]
    assert any(t.startswith("src/page.md") for t in targets)
    assert not any(t.startswith("scratch/") for t in targets)


def test_symlink_not_followed(tmp_path):
    # A symlink to source-containing text outside the tree must not be read.
    outside = tmp_path.parent / "outside_leak.md"
    outside.write_text(f"https://cal.com {SOURCE_PARAGRAPH}", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link.md").symlink_to(outside)
    assert scan_tree(tree, FINGERPRINT) == []


def test_unrelated_domain_substring_not_flagged(tmp_path):
    # Source domain "cal.com" must not fire on an unrelated host that merely
    # contains it as a substring ("local.com", "cal.community").
    _write(
        tmp_path,
        "src/page.md",
        "Reach us at local.com or cal.community for support.",
    )
    assert _kinds(scan_tree(tmp_path, FINGERPRINT), "domain") == []


def test_subdomain_of_source_still_flagged(tmp_path):
    fp = build_fingerprint(pages=[], brand_terms=["Acme"], domains=["cal.com"])
    _write(tmp_path, "src/page.md", "open https://app.cal.com/book now")
    assert len(_kinds(scan_tree(tmp_path, fp), "domain")) == 1


def test_symlinked_ancestor_not_followed(tmp_path):
    # A tracked src/page.md whose `src` dir is later replaced by a symlink to an
    # outside directory still appears in `git ls-files`; its external content must
    # not be read.
    import shutil

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path, "src/page.md", "clean customer content, no leak here")
    subprocess.run(["git", "add", "src/page.md"], cwd=tmp_path, check=True)
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    (outside / "page.md").write_text(
        f"https://cal.com {SOURCE_PARAGRAPH}", encoding="utf-8"
    )
    shutil.rmtree(tmp_path / "src")
    (tmp_path / "src").symlink_to(outside)
    assert scan_tree(tmp_path, FINGERPRINT) == []


def test_non_utf8_tracked_filename_does_not_raise(tmp_path):
    # Git permits tracked filenames with non-UTF-8 bytes; scanning must return a
    # list, not raise UnicodeDecodeError.
    import os

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    raw_path = os.path.join(os.fsencode(tmp_path), b"src", b"p\xff.md")
    with open(raw_path, "wb") as fh:
        fh.write(b"clean customer content, no leak here")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    findings = scan_tree(tmp_path, FINGERPRINT)
    assert isinstance(findings, list)


def test_compute_shingles_short_text_empty():
    assert compute_shingles("too few words here") == set()
