#!/usr/bin/env python3
"""Stop gate: a completion claim needs the flow lane's recorded evidence."""
from __future__ import annotations

import os
import sys

from feature_flow_common import (
    PLAN_STAGE,
    ALLOW_PRIMARY_ENV,
    REVIEW_STAGE,
    VERIFY_STAGE,
    block,
    claims_done,
    has_stage,
    hook_input,
    load_state,
    normalize_event,
    primary_checkout_with_worktrees,
    summary,
)


def main() -> None:
    payload = hook_input()
    event = normalize_event(payload)
    state = load_state(cwd=event["cwd"])
    if not state:
        return
    # A flow in the repo's PRIMARY checkout is shared by every session whose shell
    # sits there, so blocking on it stops sessions that never opened it. `init`
    # refuses to create one now, but states predating that guard still exist —
    # advise instead of blocking, and say which flow it is so the owner can move it.
    #
    # Not silently ignored: the flow may be real, and the person reading this may
    # be its owner. What is wrong is holding OTHER sessions to it.
    if primary_checkout_with_worktrees(event["cwd"]) and not os.environ.get(ALLOW_PRIMARY_ENV):
        print(
            f"feature-flow: {summary(state)} — this flow lives in the repo's PRIMARY "
            "checkout, which several sessions share, so it is advisory here rather "
            "than blocking. If it is yours, re-open it from its worktree; if it is "
            "stale, clear it with feature-flow-state.py clear.",
            file=sys.stderr,
        )
        return
    message = str(payload.get("last_assistant_message") or payload.get("lastAssistantMessage") or "")
    if not claims_done(message):
        return
    lane = state.get("lane")
    if lane == "pending":
        block(f"{summary(state)}. Continue: choose a feature-flow lane before claiming completion.")
    if lane != "flow":
        return
    missing = [
        stage
        for stage in (PLAN_STAGE, REVIEW_STAGE, VERIFY_STAGE)
        if not has_stage(state, stage)
    ]
    if missing:
        block(
            f"{summary(state)}. Continue: completion claim is blocked until missing feature-flow "
            f"evidence exists: {', '.join(missing)} (verify requires a matching harness receipt)."
        )


if __name__ == "__main__":
    main()
