---
name: feature-flow
description: Use when the user asks to add, build, implement, create, wire, or ship a feature in an existing codebase, or to resume an active feature flow. Invoke before writing code, planning, or asking clarifying questions.
---

# Feature Flow

Two lanes. Pick the lighter one unless a stage can change a decision.

- **Express:** one file, ~30 changed lines or fewer, no UI, no external interface, no new concept.
- **Flow:** everything else — more than one file or subsystem, a new concept, a UI or API surface, or a data-model change.

Everything below is something **you** do. This skill is a CLI plus a committed document, so it works
identically on every harness. Some hosts additionally have hooks that catch mistakes earlier; those are
an accelerator, never the mechanism (see the last section).

## Invocation

```bash
python3 ~/.local/share/feature-flow/current/scripts/feature-flow-state.py <command>
```

If that path does not exist the install was never run on this host — invoke the script straight from the
plugin checkout instead (`python3 ~/plugins/feature-flow/scripts/feature-flow-state.py <command>`).
Use whichever exists; the commands are the same.

Commands: `init <express|flow> "<feature>"`, `set-plan <path>`, `complete <stage>`, `harness`, `get`,
`clear`, `prune [--apply]`. Add `--flow <uuid>` to act on a flow other than this worktree's.

`prune` deletes state dirs whose worktree is gone or re-incarnated; it is a dry run unless you pass
`--apply`, it never touches a live worktree's state, and it belongs after `git worktree prune`.

## Recovering state — do this first, and after any context loss

Two different things live in two different places, and that split is what makes recovery portable:

- **Tracking state** (lane, stages, receipt) is outside the repo, in `~/.local/state/feature-flow/<uuid>/`,
  keyed by `$(git rev-parse --git-path feature-flow-incarnation)`. One active flow per worktree; a
  worktree without that file has no flow. Never look for tracking state in the repo.
- **The contract** — the flow doc — is COMMITTED in the repo at `docs/flows/<slug>.md`. That is
  deliberate: it means recovery needs no host support at all.

So whenever you do not know where you are — a fresh session, a resumed one, or right after a
compaction:

1. Run `get`. It prints the lane, the stages completed, and the flow doc path.
2. If `get` returns no active flow, run `ls docs/flows/` and read the flow doc for this work.
3. Re-read the `## Harness` block before claiming anything is verified.

Do that unprompted. On most harnesses nothing will remind you.

## Isolation

Isolation policy is the repo's, not this skill's: read the nearest `AGENTS.md` and follow it. In a repo
that mandates a worktree, cut the worktree — there is no "skip the worktree" option and no same-branch
default.

## The flow doc

The Flow lane's plan file IS the deliverable contract: `docs/flows/<slug>.md`, committed, with three
headings.

- `## Spec` — the outcome, the non-goals, and the priority order for trade-offs.
- `## Plan` — the ordered work, small enough to land in batches.
- `## Harness` — one fenced `sh` block. **Every line is a command that must exit 0 independently.**
  No heredocs, no multi-line constructs, no prose. This is the executable definition of done.

Flow docs are **living regression contracts**, maintained forever. When a referenced file is renamed
or deleted, the doc's own gate goes red until the flow doc is updated. That is the design, not a bug.

## Express

1. Say in one sentence what will change and why.
2. `init express "<feature>"`.
3. Make the narrow edit.
4. Run the narrowest relevant check; report the command and its result.
5. Report `Non-obvious findings: NONE` or up to two findings; on findings, record them in
   your project's learnings file (or whatever learning-capture mechanism your setup has).

## Flow

1. Recover state as above, then `init flow "<feature>"` if no flow is open.
2. **Understand:** `superpowers:brainstorming` if requirements are fuzzy, else a short intent
   paragraph. `complete understand`.
3. **Plan:** write `docs/flows/<slug>.md` with all three headings, Harness included. `set-plan
   docs/flows/<slug>.md`, then `complete plan`.
4. **Review plan:** review the plan with fresh eyes — a plan-review skill, a second model, or a
   fresh-context subagent prompted as an engineering reviewer; add a design review for UI and a
   devex review for developer-facing changes. `complete review-plan`.
5. **Implement:** **do not edit source until `plan` and `review-plan` are complete.** Then
   `superpowers:test-driven-development`, one plan item at a time. Commit incrementally — commits are
   never gated. On unexpected behavior, `superpowers:systematic-debugging`. `complete implement`.
6. **Verify:** run `harness`. It executes the Harness block verbatim, records every exit code, and
   writes a receipt of the four git OIDs (head commit, head tree, index tree, worktree tree) plus the
   Harness text digest. Then `complete verify` — it recomputes all of that and **refuses** if anything
   moved: a new commit, a staged or unstaged edit, an edited Harness block, or a non-zero exit code.
   Re-run `harness` after any change. Report `Non-obvious findings: NONE` or up to three findings.
7. **Learn:** if findings were not `NONE`, record up to three lessons in your project's learnings
   file. `complete learn`.

## Where the flow ends

In a repo with its own ship pipeline (e.g. [preflight](https://github.com/solozerolabs/preflight)), **the flow ends at verify + learn.** That
pipeline owns review, simplification, and shipping — do not duplicate its phases here.

In a repo without one, stop after verify + learn unless the user asks to commit, ship, deploy, or
document.

## After a push

If your repo has a CI watch, clean up the worktree only after that watch **ends** — never at push
time; the fix-push cycles still need the worktree. The pusher owns every red on the ref they pushed.

## Host hooks — an accelerator on two hosts, never the mechanism

**These hooks fire ONLY on hosts that support hooks (Claude Code, Codex). On hookless hosts, none of them fire, and nothing below happens for you.** Every guarantee
here has a portable substitute that you perform yourself; that substitute is the real mechanism.

| hook (hooked hosts only) | what actually holds everywhere |
| --- | --- |
| `SessionStart` re-injects the flow doc path, stage, and Harness after startup/resume/clear/compact | the committed-doc recovery step above — `get`, else `ls docs/flows/` |
| `PreToolUse` denies source edits before `plan` + `review-plan` | the step-5 instruction, backed for real by the repo's own gates (pre-push hook, CI) rather than by your honesty |
| `Stop` continues the turn on a completion claim missing `plan`, `review-plan`, or `verify` | the verify receipt — `complete verify` refuses on stale evidence, and it is a CLI, so any harness can produce it |
| `UserPromptSubmit` records pending state for feature-shaped prompts | `init` |

Never assume a hook ran. If you did not see its output, act as though there is none.

Bypass the hooked-host gates only on explicit user instruction: `FEATURE_FLOW_BYPASS=1`, or
`feature-flow: bypass` inside the command.
