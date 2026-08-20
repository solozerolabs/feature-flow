#!/usr/bin/env python3
"""SessionStart: re-anchor a session on this worktree's open flow.

This is the compaction-recovery path. After a compact the model has LOST the
transcript, so the flow doc path, the stage, and the Harness are re-injected — not
a reminder to go look for them.

Emits NOTHING when there is no active flow (no incarnation file, or no state):
a hook that injects noise into every unrelated session gets removed by the user.
"""
from __future__ import annotations

from feature_flow_common import context, hook_input, load_state, normalize_event, session_context


def main() -> None:
    event = normalize_event(hook_input())
    state = load_state(cwd=event["cwd"])
    message = session_context(state, event["trigger"], cwd=event["cwd"])
    if not message:
        return
    context("SessionStart", message)


if __name__ == "__main__":
    main()
