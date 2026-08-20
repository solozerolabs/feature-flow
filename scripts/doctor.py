#!/usr/bin/env python3
"""Read-only health report for the installed feature-flow.

Answers three questions a published-but-never-installed plugin gets wrong:
  1. Do each host's ACTIVE bytes match the install manifest's shas?
  2. Are the Codex hooks installed but UNTRUSTED (Codex silently skips those)?
  3. Which state dirs are orphaned (their worktree is gone or re-incarnated)?

Never writes, never deletes. Orphan removal is an explicit operator action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from feature_flow_common import classify_state_dirs, state_home

MANIFEST = Path.home() / ".local" / "state" / "feature-flow" / "install-manifest.json"
CODEX_HOOKS = Path.home() / ".codex" / "hooks.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
# Hooks install.sh ships to ~/.claude/hooks/ that settings.json must invoke itself.
CLAUDE_WIRING = {
    "session_start_hook": "feature-flow-session-start.py",
    "stop_hook": "feature-flow-stop.py",
}


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def install_report() -> dict:
    if not MANIFEST.is_file():
        return {"manifest": str(MANIFEST), "status": "missing", "drift": [], "ok": False}
    manifest = json.loads(MANIFEST.read_text())
    drift = []
    for target, expected in (manifest.get("files") or {}).items():
        actual = sha256_file(Path(target))
        if actual is None:
            drift.append({"path": target, "reason": "missing"})
        elif actual != expected:
            drift.append({"path": target, "reason": "sha_mismatch", "expected": expected, "actual": actual})
    return {
        "manifest": str(MANIFEST),
        "status": "installed",
        "tag": manifest.get("tag"),
        "commit": manifest.get("commit"),
        "install_dir": manifest.get("install_dir"),
        "drift": drift,
        "ok": not drift,
    }


def codex_trust_report() -> dict:
    """Codex records a trusted_hash per '<hooks-file>:<event>:<i>:<j>' key.

    A declared event with no such key has never been trusted, and Codex skips it
    silently — installed but inert.
    """
    if not CODEX_HOOKS.is_file():
        return {"hooks_file": str(CODEX_HOOKS), "status": "missing", "untrusted": [], "ok": True}
    declared = sorted((json.loads(CODEX_HOOKS.read_text()).get("hooks") or {}).keys())
    trusted: set[str] = set()
    if CODEX_CONFIG.is_file():
        prefix = re.escape(str(CODEX_HOOKS))
        for match in re.finditer(rf'^\[hooks\.state\."{prefix}:([a-z_]+):', CODEX_CONFIG.read_text(), re.M):
            trusted.add(match.group(1))
    # hooks.json uses CamelCase event names; config.toml uses snake_case.
    def snake(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    untrusted = [event for event in declared if snake(event) not in trusted]
    # Codex hashes the hook ENTRY, not the script it points at, so rewriting
    # hooks.json invalidates every recorded trusted_hash. We cannot recompute
    # Codex's hash, so report the ordering: a hooks.json newer than the config
    # that trusts it means Codex will re-prompt and, until then, skip the hooks.
    stale_trust = bool(
        trusted
        and CODEX_CONFIG.is_file()
        and CODEX_HOOKS.stat().st_mtime > CODEX_CONFIG.stat().st_mtime
    )
    return {
        "hooks_file": str(CODEX_HOOKS),
        "status": "present",
        "declared": declared,
        "trusted": sorted(trusted),
        "untrusted": untrusted,
        "trust_possibly_stale": stale_trust,
        "note": "trusted_hash covers the hook entry; a rewritten hooks.json must be re-trusted in Codex",
        "ok": not untrusted and not stale_trust,
    }


def claude_wiring_report() -> dict:
    """Which shipped hooks ~/.claude/settings.json actually invokes.

    install.sh cannot wire these: settings.json is the user's file. A hook that
    ships but is never invoked is indistinguishable from a working one unless it is
    reported, so an unwired hook is a doctor FAILURE, not a note.
    """
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.is_file():
        # No Claude host on this machine. Hooks are an accelerator on the two hosts
        # that have them, so "no host" is not a failure — on a hookless harness
        # the portable path in SKILL.md is the whole mechanism.
        return {
            "settings": str(settings),
            "status": "absent",
            "unwired": [],
            "note": "no Claude host here; hook wiring is not applicable",
            "ok": True,
        }
    text = settings.read_text()
    wiring = {name: ("wired" if basename in text else "unwired") for name, basename in CLAUDE_WIRING.items()}
    unwired = sorted(name for name, value in wiring.items() if value == "unwired")
    return {
        "settings": str(settings),
        "status": "present",
        **wiring,
        "unwired": unwired,
        "note": "paste the fragment printed by `install.sh --apply` into settings.json",
        "ok": not unwired,
    }


def orphan_report() -> dict:
    """State dirs whose recorded worktree no longer carries that incarnation."""
    live, orphaned = classify_state_dirs()
    return {
        "state_home": str(state_home()),
        "active": live,
        "orphaned": orphaned,
        "note": "orphans are REPORTED only; remove them with `feature-flow-state.py prune --apply`",
        "ok": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable report (the only format)")
    parser.parse_args()
    report = {
        "install": install_report(),
        "codex_hooks": codex_trust_report(),
        "claude_hooks": claude_wiring_report(),
        "state": orphan_report(),
    }
    report["ok"] = all(section["ok"] for section in report.values() if isinstance(section, dict))
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
