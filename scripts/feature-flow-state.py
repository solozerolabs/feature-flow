#!/usr/bin/env python3
"""feature-flow state CLI.

One active flow per worktree, keyed by the worktree's incarnation UUID. The
`harness` subcommand runs the flow doc's `## Harness` block and records a receipt;
`complete verify` refuses unless that receipt still matches the tree.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from feature_flow_common import (
    INIT_LANES,
    VERIFY_STAGE,
    classify_state_dirs,
    complete_stage,
    harness_block_sha256,
    harness_commands,
    incarnation,
    init_state,
    load_state,
    now,
    receipt_failures,
    receipt_path,
    repo_root,
    save_state,
    snapshot_oids,
    state_dir,
    state_path,
    summary,
)

# `pending` is initable (that is what a feature-shaped prompt records) but is not a
# lane you can switch INTO once a real one is chosen.
LANE_CHOICES = list(INIT_LANES)
SET_LANE_CHOICES = [lane for lane in INIT_LANES if lane != "pending"]


def flow_doc(state: dict, cwd: Path) -> Path:
    plan_path = state.get("plan_path")
    if not plan_path:
        raise SystemExit("feature-flow: no plan_path recorded. Run `set-plan docs/flows/<slug>.md` first.")
    doc = Path(plan_path)
    return doc if doc.is_absolute() else cwd / doc


def run_harness(state: dict, flow: str | None) -> int:
    root = repo_root()
    doc = flow_doc(state, root)
    commands = harness_commands(doc)
    digest = harness_block_sha256(doc)

    results = []
    failed = 0
    for command in commands:
        print(f"feature-flow harness: {command}", flush=True)
        code = subprocess.call(["bash", "-c", command], cwd=str(root))
        results.append({"command": command, "exit_code": code})
        if code != 0:
            failed += 1

    # Snapshot AFTER the commands run: harness commands legitimately rewrite the
    # tree (formatters, generated files), so OIDs taken before would self-stale.
    receipt = {
        **snapshot_oids(root),
        "harness_block_sha256": digest,
        "commands": results,
        "recorded_at": now(),
    }
    path = receipt_path(root, flow)
    if path is None:
        raise SystemExit("feature-flow: no active flow for this worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"feature-flow: receipt written to {path}")
    if failed:
        print(f"feature-flow: {failed} of {len(commands)} harness commands failed", file=sys.stderr)
    return 1 if failed else 0


def run_prune(apply: bool) -> int:
    """Remove orphaned state dirs. Live worktrees' state is never a candidate."""
    live, orphaned = classify_state_dirs()
    for entry in live:
        print(f"keep    {entry['path']}  (live: {entry['worktree']})")
    if not orphaned:
        print("feature-flow: no orphaned state dirs.")
        return 0
    for entry in orphaned:
        print(f"{'remove ' if apply else 'WOULD REMOVE'} {entry['path']}  ({entry['reason']})")
        if apply:
            shutil.rmtree(entry["path"], ignore_errors=True)
    if apply:
        print(f"feature-flow: removed {len(orphaned)} orphaned state dir(s); kept {len(live)} live.")
    else:
        print(
            f"feature-flow: dry run — {len(orphaned)} orphaned state dir(s) would be removed, "
            f"{len(live)} live kept. Re-run with --apply. Run `git worktree prune` first."
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", default=None, help="operate on this flow UUID instead of the worktree's incarnation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("lane", choices=LANE_CHOICES)
    p.add_argument("feature", nargs="?", default="(unspecified)")
    p = sub.add_parser("set-lane")
    p.add_argument("lane", choices=SET_LANE_CHOICES)
    p = sub.add_parser("set-plan")
    p.add_argument("path")
    p = sub.add_parser("complete")
    p.add_argument("stage")
    sub.add_parser("harness")
    sub.add_parser("get")
    sub.add_parser("clear")
    p = sub.add_parser("prune")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()
    flow = args.flow

    if args.cmd == "init":
        print(init_state(args.lane, args.feature))
        return

    # prune is a global operation: it must work from a worktree with no flow.
    if args.cmd == "prune":
        raise SystemExit(run_prune(args.apply))

    state = load_state(cwd=None, flow=flow)

    if args.cmd == "get":
        payload = {
            "incarnation": flow or incarnation(),
            "state_dir": str(state_dir(flow=flow) or ""),
            "state": state,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.cmd == "clear":
        path = state_path(flow=flow)
        if path and path.exists():
            path.unlink()
        return

    if not state:
        print("feature-flow: no active flow for this worktree. Run init first.", file=sys.stderr)
        raise SystemExit(2)

    if args.cmd == "harness":
        raise SystemExit(run_harness(state, flow))

    if args.cmd == "set-lane":
        state["lane"] = args.lane
    elif args.cmd == "set-plan":
        state["plan_path"] = args.path
    elif args.cmd == "complete":
        if args.stage == VERIFY_STAGE:
            failures = receipt_failures(flow=flow)
            if failures:
                print("feature-flow: `complete verify` refused:", file=sys.stderr)
                for failure in failures:
                    print(f"  - {failure}", file=sys.stderr)
                raise SystemExit(1)
        complete_stage(state, args.stage)
    save_state(state, flow=flow)
    print(summary(state))


if __name__ == "__main__":
    main()
