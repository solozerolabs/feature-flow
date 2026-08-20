"""Shared library for the feature-flow hooks and CLI.

Two mechanisms live here that the rest of the plugin depends on:

* **Incarnation-keyed state.** A flow's state lives at
  ``$FEATURE_FLOW_STATE_HOME/<incarnation>/`` where ``<incarnation>`` is a uuid4
  minted at ``init`` into ``$(git rev-parse --git-path feature-flow-incarnation)``.
  That file is per-worktree and travels with the worktree, so ``git worktree move``
  and quarantine/restore need no sidecar handling. A worktree with no incarnation
  file has no active flow.
* **Receipts.** ``snapshot_oids`` + ``harness_block_sha256`` are the four
  tree/commit OIDs and the harness-text digest that ``complete verify`` re-derives
  and compares. Comparisons are tree-to-tree only.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any


LANES = {"pending", "express", "flow"}
INIT_LANES = ("express", "flow", "pending")
# Read-time migration only: an in-flight session's state file may still say
# standard/heavy. Never written back as a legacy value; `init` refuses them.
LEGACY_LANES = {"standard": "flow", "heavy": "flow"}
PLAN_STAGE = "plan"
REVIEW_STAGE = "review-plan"
VERIFY_STAGE = "verify"
INCARNATION_FILE = "feature-flow-incarnation"
STATE_FILE = "state.json"
RECEIPT_FILE = "receipt.json"

# A feature request is an INSTRUCTION, so its verb starts a clause. Two filters,
# and both are needed — each alone lets real noise through:
#
#   proximity: the verb and noun must sit within one sentence on one line. The
#   original was `.+` under re.S, so they could be paragraphs apart, and any long
#   enough document matched.
#
#   imperative position: the verb must open a line or follow a comma, optionally
#   after a polite lead-in. This is what separates "add a settings toggle to the billing page"
#   from "Suggest things they could build or create, referencing any page" and
#   from "it has a support page" — both of which are proximate AND descriptive.
#
# Deliberately biased toward MISSING requests rather than inventing them. A false
# negative costs a nudge the user can trigger themselves; a false positive opens a
# feature-flow, records the prompt as the feature name, and then nags at every
# Stop until someone clears it. That happened: Codex's own project-suggestions
# prompt opened a flow named after itself.
_LEAD_IN = (
    r"(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+need\s+you\s+to\s+|"
    r"i'?d\s+like\s+you\s+to\s+|let'?s\s+|now\s+|next\s+|then\s+)?"
)
FEATURE_RE = re.compile(
    r"(?:^|\n|,)\s*(?:[-*+]\s+|\d+[.)]\s+)?" + _LEAD_IN +
    r"(add|build|implement|create|ship|make|wire|integrate|support)\b"
    r"[^.\n]{0,80}?"
    r"\b(feature|flow|ui|api|endpoint|screen|page|toggle|setting|mode|integration|support)\b",
    re.I,
)
SKIP_RE = re.compile(r"\b(skip feature-flow|no feature-flow|do not use feature-flow|critique only|review only|explain|investigate bug)\b", re.I)
DONE_RE = re.compile(r"\b(done|completed|implemented|fixed|verified|shipped|installed|ready)\b", re.I)
PLAN_PATH_MARKERS = (
    "/docs/flows/",
    "/docs/superpowers/plans/",
    "/docs/superpowers/specs/",
)
SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".swift", ".dart", ".sql", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp",
    ".css", ".scss", ".html", ".vue", ".svelte",
}
HARNESS_HEADING_RE = re.compile(r"^##\s+Harness\b")
FENCE_RE = re.compile(r"^```(sh|bash)\s*$")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- git


def git(args: list[str], cwd: str | Path | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=str(cwd or os.getcwd()),
        env=env,
        stderr=subprocess.PIPE,
    ).decode().strip()


def repo_root(cwd: str | Path | None = None) -> Path:
    base = Path(cwd or os.getcwd())
    try:
        return Path(git(["rev-parse", "--show-toplevel"], base))
    except Exception:
        return base


def incarnation_path(cwd: str | Path | None = None) -> Path | None:
    """Absolute path of this worktree's incarnation file, or None outside a repo.

    ``--git-path`` returns the per-worktree admin dir for an unknown filename, so
    this resolves inside ``.git/worktrees/<name>/`` in a linked worktree (where
    ``.git`` is a gitfile) and inside ``.git/`` in the main checkout. The output
    is relative to the invocation cwd when the git dir is reachable relatively.
    """
    base = Path(cwd or os.getcwd())
    try:
        raw = git(["rev-parse", "--git-path", INCARNATION_FILE], base)
    except Exception:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (base / path).resolve()


def incarnation(cwd: str | Path | None = None, create: bool = False) -> str | None:
    path = incarnation_path(cwd)
    if path is None:
        return None
    if path.is_file():
        value = path.read_text().strip()
        if value:
            return value
    if not create:
        return None
    value = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    return value


# ------------------------------------------------------------------------- state


def state_home() -> Path:
    override = os.environ.get("FEATURE_FLOW_STATE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "feature-flow"


def state_dir(cwd: str | Path | None = None, flow: str | None = None, create: bool = False) -> Path | None:
    """State dir for the active flow, or None when this worktree has no flow."""
    ident = flow or incarnation(cwd, create=create)
    if not ident:
        return None
    return state_home() / ident


def state_path(cwd: str | Path | None = None, flow: str | None = None, create: bool = False) -> Path | None:
    directory = state_dir(cwd, flow, create=create)
    return None if directory is None else directory / STATE_FILE


def receipt_path(cwd: str | Path | None = None, flow: str | None = None) -> Path | None:
    directory = state_dir(cwd, flow)
    return None if directory is None else directory / RECEIPT_FILE


def migrate_lane(state: dict[str, Any]) -> dict[str, Any]:
    state["lane"] = LEGACY_LANES.get(state.get("lane"), state.get("lane"))
    return state


def load_state(payload: dict[str, Any] | None = None, cwd: str | Path | None = None, flow: str | None = None) -> dict[str, Any]:
    path = state_path(cwd or (normalize_event(payload)["cwd"] if payload else None), flow)
    if path is None or not path.is_file():
        return {}
    try:
        return migrate_lane(json.loads(path.read_text()))
    except Exception:
        return {}


def save_state(
    state: dict[str, Any],
    payload: dict[str, Any] | None = None,
    cwd: str | Path | None = None,
    flow: str | None = None,
    create: bool = False,
) -> Path:
    path = state_path(cwd or (normalize_event(payload)["cwd"] if payload else None), flow, create=create)
    if path is None:
        raise SystemExit("feature-flow: no incarnation for this worktree (run init first)")
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return path


#: Escape hatch for the repo that genuinely tracks work in its primary checkout.
#: Named rather than silent so the override shows up in a shell history or a hook
#: definition instead of being a thing someone remembers.
ALLOW_PRIMARY_ENV = "FEATURE_FLOW_ALLOW_PRIMARY_CHECKOUT"


def primary_checkout_with_worktrees(cwd: str | Path | None = None) -> bool:
    """True when this is a repo's PRIMARY checkout and linked worktrees exist.

    That combination is the shared-checkout hazard. In a repo whose work happens
    in linked worktrees, the primary checkout is where several sessions coexist —
    so a feature-flow opened there is not one session's flow, it is a flow every
    session in that directory inherits.

    Observed: a session working in `myrepo-feature-x` ran commands whose cwd
    resolved to the shared checkout, so a second flow was opened there. An
    unrelated session, whose shell also sat in that directory, then had its Stop
    hook block on the first session's missing verify receipt.

    Detected with plain git, no repo-specific knowledge: in a linked worktree
    `--git-dir` and `--git-common-dir` differ; in the primary they are the same.
    Fails OPEN (returns False) when git cannot answer — a detector that guesses
    "primary" on an unreadable repo would refuse inits it has no business
    refusing.
    """
    try:
        git_dir = git(["rev-parse", "--absolute-git-dir"], cwd)
        common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    except Exception:
        return False
    if Path(git_dir).resolve() != Path(common).resolve():
        return False  # a linked worktree: exactly where a flow belongs
    try:
        listing = git(["worktree", "list", "--porcelain"], cwd)
    except Exception:
        return False
    return sum(1 for line in listing.splitlines() if line.startswith("worktree ")) > 1


def init_state(lane: str, feature: str, payload: dict[str, Any] | None = None, cwd: str | Path | None = None) -> Path:
    if lane not in INIT_LANES:
        raise SystemExit(f"invalid lane: {lane} (expected one of {', '.join(INIT_LANES)})")
    if primary_checkout_with_worktrees(cwd) and not os.environ.get(ALLOW_PRIMARY_ENV):
        raise SystemExit(
            f"refusing to open a feature-flow in {repo_root(cwd)}: this is the repo's "
            "PRIMARY checkout and linked worktrees exist, so several sessions share "
            "this directory. A flow opened here is inherited by all of them — one "
            "session's missing evidence then blocks the others.\n"
            "Run from the worktree the work belongs to (`git worktree list`), or set "
            f"{ALLOW_PRIMARY_ENV}=1 if this repo really does track work here."
        )
    return save_state(
        {
            "lane": lane,
            "feature": feature.strip() or "(unspecified)",
            "plan_path": None,
            "stages_completed": [],
            "started_at": now(),
            # Recorded so `doctor` can tell an orphaned state dir (its worktree is
            # gone, or was re-incarnated) from a live one, with no repo scanning.
            "worktree": str(repo_root(cwd)),
        },
        payload,
        cwd,
        create=True,
    )


def complete_stage(state: dict[str, Any], stage: str) -> None:
    done = state.setdefault("stages_completed", [])
    if stage not in done:
        done.append(stage)


def has_stage(state: dict[str, Any], stage: str) -> bool:
    return stage in (state.get("stages_completed") or [])


def summary(state: dict[str, Any]) -> str:
    stages = ", ".join(state.get("stages_completed") or []) or "none"
    return (
        f"feature-flow state: lane={state.get('lane')}, feature={state.get('feature')}, "
        f"stages={stages}"
    )


# ------------------------------------------------------------------- oids/receipt


def snapshot_oids(cwd: str | Path | None = None) -> dict[str, str]:
    """The four OIDs a receipt pins. Never mutates the real index or worktree.

    ``index_tree_oid`` is a plain ``git write-tree`` (the real index is read, not
    changed). ``worktree_tree_oid`` is computed against a throwaway
    ``GIT_INDEX_FILE`` so ``git add -A`` cannot touch the real index.
    """
    base = repo_root(cwd)
    oids = {
        "head_commit_oid": git(["rev-parse", "HEAD"], base),
        "head_tree_oid": git(["rev-parse", "HEAD^{tree}"], base),
        "index_tree_oid": git(["write-tree"], base),
    }
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(Path(tmp) / "index")
        git(["read-tree", "HEAD"], base, env)
        git(["add", "-A"], base, env)
        oids["worktree_tree_oid"] = git(["write-tree"], base, env)
    return oids


OID_KEYS = ("head_commit_oid", "head_tree_oid", "index_tree_oid", "worktree_tree_oid")


def harness_block(flow_doc_path: str | Path) -> str:
    """Exact text of the first fenced sh block under the ``## Harness`` heading."""
    path = Path(flow_doc_path)
    if not path.is_file():
        raise SystemExit(f"feature-flow: flow doc not found: {path}")
    lines = path.read_text().splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if HARNESS_HEADING_RE.match(line)), None)
    if index is None:
        raise SystemExit(f"feature-flow: no '## Harness' heading in {path}")
    open_at = next((i for i in range(index + 1, len(lines)) if FENCE_RE.match(lines[i].rstrip("\n"))), None)
    if open_at is None:
        raise SystemExit(f"feature-flow: no fenced sh block under '## Harness' in {path}")
    close_at = next((i for i in range(open_at + 1, len(lines)) if lines[i].rstrip("\n") == "```"), None)
    if close_at is None:
        raise SystemExit(f"feature-flow: unterminated Harness fence in {path}")
    block = "".join(lines[open_at + 1:close_at])
    if not block.strip():
        raise SystemExit(f"feature-flow: Harness block is empty in {path}")
    return block


def harness_block_sha256(flow_doc_path: str | Path) -> str:
    return hashlib.sha256(harness_block(flow_doc_path).encode()).hexdigest()


def harness_commands(flow_doc_path: str | Path) -> list[str]:
    """One command per non-blank line; each must exit 0 independently."""
    return [line.strip() for line in harness_block(flow_doc_path).splitlines() if line.strip()]


def receipt_failures(cwd: str | Path | None = None, flow: str | None = None) -> list[str]:
    """Named reasons the receipt does not authorize `complete verify`; [] = ok."""
    path = receipt_path(cwd, flow)
    if path is None:
        return ["no active flow: this worktree has no incarnation file"]
    if not path.is_file():
        return ["no receipt: run `feature-flow-state.py harness` before `complete verify`"]
    try:
        receipt = json.loads(path.read_text())
    except Exception as exc:
        return [f"receipt unreadable: {exc}"]

    failures: list[str] = []
    fresh = snapshot_oids(cwd)
    for key in OID_KEYS:
        if receipt.get(key) != fresh[key]:
            failures.append(
                f"stale receipt: {key} changed since the harness ran "
                f"(receipt={receipt.get(key)}, now={fresh[key]})"
            )

    state = load_state(cwd=cwd, flow=flow)
    plan_path = state.get("plan_path")
    if not plan_path:
        failures.append("no plan_path recorded: run `set-plan docs/flows/<slug>.md`")
    else:
        doc = Path(plan_path)
        if not doc.is_absolute():
            doc = repo_root(cwd) / doc
        try:
            current = harness_block_sha256(doc)
        except SystemExit as exc:
            failures.append(f"flow doc Harness unreadable: {exc}")
        else:
            if receipt.get("harness_block_sha256") != current:
                failures.append(
                    "stale receipt: the flow doc's Harness block changed since the harness ran"
                )

    for entry in receipt.get("commands") or []:
        if entry.get("exit_code") != 0:
            failures.append(
                f"harness command failed: exit {entry.get('exit_code')} for {entry.get('command')!r}"
            )
    return failures


# ------------------------------------------------------------------- state census


def worktree_incarnation(worktree: str | Path) -> str | None:
    """Read a worktree's incarnation file without invoking git.

    Deliberately git-free: the worktree may be gone entirely, and `git -C` on a
    missing path would just error. Handles the gitfile indirection a linked
    worktree uses.
    """
    gitpath = Path(worktree) / ".git"
    if gitpath.is_file():
        try:
            gitdir = gitpath.read_text().split("gitdir:", 1)[1].strip()
        except Exception:
            return None
        candidate = Path(gitdir) / INCARNATION_FILE
    else:
        candidate = gitpath / INCARNATION_FILE
    try:
        return candidate.read_text().strip() or None
    except Exception:
        return None


def classify_state_dirs() -> tuple[list[dict], list[dict]]:
    """(live, orphaned) state dirs. The single source both doctor and prune use.

    Orphan classes: no recorded worktree, the worktree is gone/unregistered, or
    the worktree has been re-incarnated to a different uuid.
    """
    home = state_home()
    live: list[dict] = []
    orphaned: list[dict] = []
    if not home.is_dir():
        return live, orphaned
    for directory in sorted(p for p in home.iterdir() if p.is_dir()):
        worktree = None
        state_file = directory / STATE_FILE
        if state_file.is_file():
            try:
                worktree = json.loads(state_file.read_text()).get("worktree")
            except Exception:
                worktree = None
        if not worktree:
            reason = "no recorded worktree"
        else:
            found = worktree_incarnation(worktree)
            if found is None:
                reason = f"worktree gone or unregistered: {worktree}"
            elif found != directory.name:
                reason = f"worktree re-incarnated to {found[:8]}...: {worktree}"
            else:
                reason = None
        entry = {"incarnation": directory.name, "path": str(directory), "worktree": worktree}
        if reason:
            orphaned.append({**entry, "reason": reason})
        else:
            live.append(entry)
    return live, orphaned


# ------------------------------------------------------------- session context


HARNESS_CONTEXT_LIMIT = 4096
TRIGGER_PHRASES = {
    "compact": "The transcript was just COMPACTED — the earlier turns are gone from your context. Re-anchor on this flow before doing anything else.",
    "clear": "The context was cleared. Re-anchor on this flow before doing anything else.",
    "resume": "This session was resumed. The flow below is still open.",
    "startup": "A feature flow is already open in this worktree.",
    # Codex emits session_start with no discriminator: it has no compact event, so
    # its resume path is indistinguishable from a cold start. Generic phrasing.
    "": "A feature flow is open in this worktree.",
}


def session_context(state: dict[str, Any], trigger: str, cwd: str | Path | None = None) -> str | None:
    """The additionalContext for a SessionStart, or None when there is no flow."""
    if not state:
        return None
    plan_path = state.get("plan_path")
    stages = ", ".join(state.get("stages_completed") or []) or "none"
    lines = [
        TRIGGER_PHRASES.get(trigger, TRIGGER_PHRASES[""]),
        f"feature-flow: lane={state.get('lane')} feature={state.get('feature')}",
        f"stages completed: {stages}",
        f"flow doc: {plan_path or '(none recorded — write docs/flows/<slug>.md and run set-plan)'}",
    ]
    if plan_path:
        doc = Path(plan_path)
        if not doc.is_absolute():
            doc = repo_root(cwd) / doc
        try:
            block = harness_block(doc)
        except SystemExit as exc:
            lines.append(f"Harness: UNREADABLE — {exc}")
        else:
            if len(block.encode()) <= HARNESS_CONTEXT_LIMIT:
                lines.append("Harness (verbatim — every line must exit 0):")
                lines.append(block.rstrip("\n"))
            else:
                commands = harness_commands(doc)
                kept: list[str] = []
                size = 0
                for command in commands:
                    if size + len(command) + 1 > HARNESS_CONTEXT_LIMIT:
                        break
                    kept.append(command)
                    size += len(command) + 1
                lines.append(
                    f"Harness TRUNCATED ({len(block.encode())} bytes): showing {len(kept)} of "
                    f"{len(commands)} command lines; read {plan_path} for the full block."
                )
                lines.extend(kept)
    return "\n".join(lines)


# -------------------------------------------------------------------- hook plumbing


def hook_input() -> dict[str, Any]:
    try:
        return json.loads(open(0).read() or "{}")
    except Exception:
        return {}


def normalize_event(payload: dict[str, Any] | None) -> dict[str, Any]:
    """One shape for both hosts, so hook scripts never read host-specific keys."""
    payload = payload or {}
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    return {
        "event": str(payload.get("hook_event_name") or payload.get("hookEventName") or payload.get("event") or ""),
        "cwd": str(payload.get("cwd") or payload.get("workdir") or os.getcwd()),
        "tool_name": str(payload.get("tool_name") or payload.get("toolName") or ""),
        "tool_input": tool_input if isinstance(tool_input, dict) else {},
        "prompt": str(payload.get("prompt") or payload.get("user_prompt") or ""),
        # SessionStart discriminator. Claude sends source=startup|resume|clear|compact.
        # Codex has NO compact event and no discriminator at all — its resume path is
        # plain session_start — so "" is the honest value there, not a guessed "resume".
        "trigger": str(payload.get("source") or payload.get("trigger") or ""),
        "session_id": str(
            payload.get("session_id")
            or payload.get("sessionId")
            or os.environ.get("CODEX_SESSION_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or ""
        ),
    }


def deny(event: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    raise SystemExit(0)


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    raise SystemExit(0)


def context(event: str, message: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}))


def feature_prompt(prompt: str) -> bool:
    return bool(prompt and not SKIP_RE.search(prompt) and FEATURE_RE.search(prompt))


def claims_done(message: str) -> bool:
    return bool(message and DONE_RE.search(message))


# ------------------------------------------------------------------------- paths


def is_plan_path(path: str) -> bool:
    normalized = "/" + path.lstrip("/")
    return any(marker in normalized for marker in PLAN_PATH_MARKERS)


def patch_paths(command: str) -> list[str]:
    paths: list[str] = []
    for line in command.splitlines():
        for marker in ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "):
            if line.startswith(marker):
                paths.append(line.removeprefix(marker).strip())
    return paths


def edited_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Paths a write tool is about to touch, for either host's tool shape."""
    if tool_name == "apply_patch":
        return patch_paths(str(tool_input.get("command") or tool_input.get("input") or ""))
    single = tool_input.get("file_path") or tool_input.get("path")
    return [str(single)] if single else []


def source_like(paths: list[str]) -> list[str]:
    return [p for p in paths if Path(p).suffix in SOURCE_EXTS and not is_plan_path(p)]
