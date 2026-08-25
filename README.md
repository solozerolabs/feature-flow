# feature-flow

A portable, harness-agnostic feature workflow for coding agents: **spec → plan → plan review →
implement → verify → learn**, with a verify step that produces a cryptographic receipt an agent
cannot talk its way past.

Extracted from the development workflow behind [Syndai](https://syndai.ai). This is the actual
tooling we use daily, not a demo.

## Hosted: Syndai Coding

feature-flow is the workflow. Syndai Coding is the hosted coding agents that run it for you.

They take a feature from spec to a verified receipt inside your repo, on this same flow. You
get the checked result and the flow doc, committed alongside your code.

The hosted agents are at [syndai.ai](https://syndai.ai).

## Why

Coding agents drift: they skip planning, claim "done" without running anything, and lose all
state on context compaction. feature-flow fixes that with three mechanisms:

1. **A committed contract.** Every non-trivial feature gets a flow doc (`docs/flows/<slug>.md`)
   with `## Spec`, `## Plan`, and `## Harness` — where Harness is a fenced block of commands that
   must each exit 0. The doc is committed, so recovery after a crash or compaction needs no
   host support at all: the agent reads the doc.
2. **A verify receipt.** `harness` executes the Harness block verbatim and records the four git
   OIDs (head commit, head tree, index tree, worktree tree) plus a digest of the Harness text.
   `complete verify` recomputes everything and **refuses** if anything moved — a new commit, an
   unstaged edit, an edited Harness block, or a non-zero exit code. "It passes" becomes a claim
   the tooling checks, not one the agent asserts.
3. **State outside the repo.** Tracking state (lane, stages) lives in
   `~/.local/state/feature-flow/<uuid>/`, keyed per git worktree, so parallel worktrees never
   share a flow and a stale flow never leaks into an unrelated session.

Two lanes keep the overhead honest: **express** (one file, ~30 lines, no UI/API surface) skips
the ceremony; **flow** (everything else) runs the full loop.

## Install

```bash
git clone https://github.com/solozerolabs/feature-flow ~/plugins/feature-flow
bash ~/plugins/feature-flow/install.sh v0.1.0            # dry run: reports what would change
bash ~/plugins/feature-flow/install.sh v0.1.0 --apply    # writes
```

The installer takes a tag (or any ref) and installs an immutable copy under
`~/.local/share/feature-flow/`, so no host ever executes the mutable checkout. It never edits
`~/.claude/settings.json` — the install run prints the exact hook JSON to paste into your
settings if you want the Claude Code hooks wired, and `python3 scripts/doctor.py --json`
verifies the wiring afterwards.

The Flow lane's stage steps reference brainstorming/TDD/debugging skills from the public
[superpowers](https://github.com/obra/superpowers) plugin — substitute your own equivalents if
you don't use it.

The CLI is plain Python 3 with no dependencies:

```bash
python3 ~/.local/share/feature-flow/current/scripts/feature-flow-state.py <command>
```

Commands: `init <express|flow> "<feature>"`, `set-plan <path>`, `complete <stage>`, `harness`,
`get`, `clear`, `prune [--apply]`.

## Harness support

The mechanism is a CLI plus a committed document, so it works identically on every agent
harness. On hosts with hook support (Claude Code, Codex) the included hooks add earlier
enforcement — `SessionStart` re-injects flow state after compaction, `PreToolUse` denies source
edits before the plan is reviewed, `Stop` blocks completion claims with no verify receipt. The
hooks are an accelerator, never the mechanism: everything they enforce has a portable substitute
the skill instructs the agent to perform itself.

The agent-facing instructions live in [`skills/feature-flow/SKILL.md`](skills/feature-flow/SKILL.md) —
that file is the product; read it to understand the whole design.

## License

MIT
