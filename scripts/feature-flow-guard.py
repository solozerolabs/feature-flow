#!/usr/bin/env python3
"""PreToolUse gate: source edits wait for plan + review-plan in the flow lane.

Commits are NEVER blocked. Commit authorization was removed deliberately:
incremental commits are always permitted, and `complete verify` — not a
PreToolUse guess about which Bash line was a test — owns verification evidence.
"""
from __future__ import annotations

import os
from pathlib import Path

from feature_flow_common import (
    PLAN_STAGE,
    REVIEW_STAGE,
    deny,
    edited_paths,
    has_stage,
    hook_input,
    is_plan_path,
    load_state,
    normalize_event,
    source_like,
    summary,
)


def main() -> None:
    if os.environ.get("FEATURE_FLOW_BYPASS") == "1":
        return
    event = normalize_event(hook_input())
    state = load_state(cwd=event["cwd"])
    if not state:
        return

    tool_input = event["tool_input"]
    if "feature-flow: bypass" in str(tool_input.get("command") or ""):
        return

    paths = edited_paths(event["tool_name"], tool_input)
    if not source_like(paths):
        return

    lane = state.get("lane")
    if lane == "pending":
        deny("PreToolUse", f"{summary(state)}. Source edit blocked: choose a lane and run feature-flow-state.py init <express|flow> before editing.")
    if lane != "flow":
        return

    if all(is_plan_path(p) for p in paths):
        return
    if not has_stage(state, PLAN_STAGE):
        deny("PreToolUse", f"{summary(state)}. Source edit blocked: write the flow doc's Spec + Plan + Harness and complete the plan stage first.")
    if not has_stage(state, REVIEW_STAGE):
        deny("PreToolUse", f"{summary(state)}. Source edit blocked: complete plan review before editing source.")
    plan_path = state.get("plan_path")
    if not plan_path:
        deny("PreToolUse", f"{summary(state)}. Source edit blocked: plan stage is marked but no plan_path is recorded.")
    plan_file = Path(plan_path)
    if not plan_file.is_absolute():
        plan_file = Path(event["cwd"]) / plan_file
    if not plan_file.is_file() or plan_file.stat().st_size == 0:
        deny("PreToolUse", f"{summary(state)}. Source edit blocked: recorded flow doc is missing or empty: {plan_path}.")


if __name__ == "__main__":
    main()
