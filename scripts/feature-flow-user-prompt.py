#!/usr/bin/env python3
"""UserPromptSubmit: surface the active flow, or flag feature-shaped prompts."""
from __future__ import annotations

import os

from feature_flow_common import (
    ALLOW_PRIMARY_ENV,
    context,
    feature_prompt,
    hook_input,
    init_state,
    load_state,
    normalize_event,
    primary_checkout_with_worktrees,
    summary,
)


def main() -> None:
    event = normalize_event(hook_input())
    state = load_state(cwd=event["cwd"])
    if state:
        context("UserPromptSubmit", f"Feature Flow is active. {summary(state)}. Continue the current lane and update state at stage boundaries.")
        return
    if not feature_prompt(event["prompt"]):
        return
    # The AUTO path degrades to advice where the explicit CLI refuses outright.
    # Every session in a worktree-based repo tends to sit in the primary checkout
    # (all four of this repo's did), so raising here would error the prompt hook on
    # every feature-shaped message. A nudge that cannot open the flow is still a
    # useful nudge; a hook that exits non-zero on every prompt is not.
    if primary_checkout_with_worktrees(event["cwd"]) and not os.environ.get(ALLOW_PRIMARY_ENV):
        context(
            "UserPromptSubmit",
            "This looks like feature work, and this shell is in the repo's PRIMARY checkout, "
            "which several sessions share — a flow opened here would be inherited by all of "
            "them. No flow was opened. Work from the worktree this belongs to (`git worktree "
            "list`) and run feature-flow-state.py init <lane> there.",
        )
        return
    init_state("pending", event["prompt"][:240], cwd=event["cwd"])
    context(
        "UserPromptSubmit",
        "This looks like feature work. Use the feature-flow skill before editing: choose Express or Flow, "
        "run feature-flow-state.py init <lane>, then follow that lane.",
    )


if __name__ == "__main__":
    main()
