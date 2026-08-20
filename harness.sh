#!/usr/bin/env bash
# feature-flow tag gate. Plain bash + asserts. Fully isolated: every case runs in
# a throwaway `git init` repo under a mktemp dir with FEATURE_FLOW_STATE_HOME
# redirected, so no real worktree and no real state is ever read or written.
#
# The Codex resume canary is deliberately NOT part of this gate (deliberately
# non-gating). It requires a live Codex session, so it is a separately invoked
# check that gates only the "Codex resume supported" doc claim, never this exit
# code. No canary script ships here yet.
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$REPO/scripts"
STATE_CLI="$SCRIPTS/feature-flow-state.py"
GUARD="$SCRIPTS/feature-flow-guard.py"

SANDBOX="$(mktemp -d)"
export FEATURE_FLOW_STATE_HOME="$SANDBOX/state"
export PYTHONPATH="$SCRIPTS"
export GIT_CONFIG_GLOBAL="$SANDBOX/gitconfig"
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=harness GIT_AUTHOR_EMAIL=harness@example.com
export GIT_COMMITTER_NAME=harness GIT_COMMITTER_EMAIL=harness@example.com
unset FEATURE_FLOW_BYPASS
trap 'rm -rf "$SANDBOX"' EXIT

PASS=0
FAIL=0
CASE=""

start() { CASE="$1"; }
ok()   { echo "PASS  $CASE — $1"; PASS=$((PASS + 1)); }
bad()  { echo "FAIL  $CASE — $1"; FAIL=$((FAIL + 1)); }
assert_eq()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }
assert_ne()  { if [ "$2" != "$3" ]; then ok "$1"; else bad "$1 (expected a change from '$3')"; fi; }
assert_contains() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 (output lacked '$3': $2)" ;; esac; }

# ---------------------------------------------------------------- helpers

newrepo() { # $1 = name -> echoes the repo path
  local dir="$SANDBOX/$1"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  printf 'print("one")\n' > "$dir/app.py"
  mkdir -p "$dir/docs/flows"
  git -C "$dir" add -A
  git -C "$dir" commit -qm init
  echo "$dir"
}

write_flowdoc() { # $1 = repo, $2 = doc relpath, $3.. = command lines
  local repo="$1" rel="$2"; shift 2
  { echo "# Flow"; echo; echo "## Spec"; echo; echo "do the thing"; echo;
    echo "## Plan"; echo; echo "1. do it"; echo;
    echo "## Harness"; echo; echo '```sh'
    for line in "$@"; do echo "$line"; done
    printf '```\n'
  } > "$repo/$rel"
}

oid() { # $1 = repo, $2 = key
  ( cd "$1" && python3 -c "import sys,json;from feature_flow_common import snapshot_oids;print(snapshot_oids('.')['$2'])" )
}

ff() { # $1 = repo, rest = state CLI args
  local repo="$1"; shift
  ( cd "$repo" && python3 "$STATE_CLI" "$@" )
}

guard() { # $1 = repo, $2 = json payload -> echoes hook stdout
  local repo="$1" payload="$2"
  ( cd "$repo" && printf '%s' "$payload" | python3 "$GUARD" )
}

# =================================================================== snapshot

start "snapshot: clean tree has index == worktree == head tree"
R="$(newrepo clean)"
HT="$(oid "$R" head_tree_oid)"; IT="$(oid "$R" index_tree_oid)"; WT="$(oid "$R" worktree_tree_oid)"
assert_eq "index tree equals head tree" "$IT" "$HT"
assert_eq "worktree tree equals head tree" "$WT" "$HT"

start "snapshot: staged-only edit moves the index tree, not the head tree"
R="$(newrepo staged)"
HT0="$(oid "$R" head_tree_oid)"; IT0="$(oid "$R" index_tree_oid)"
printf 'print("two")\n' > "$R/app.py"; git -C "$R" add app.py
assert_eq "head tree unchanged" "$(oid "$R" head_tree_oid)" "$HT0"
assert_ne "index tree moved" "$(oid "$R" index_tree_oid)" "$IT0"

start "snapshot: unstaged edit moves the worktree tree only"
R="$(newrepo unstaged)"
IT0="$(oid "$R" index_tree_oid)"; WT0="$(oid "$R" worktree_tree_oid)"
printf 'print("three")\n' > "$R/app.py"
assert_eq "index tree unchanged" "$(oid "$R" index_tree_oid)" "$IT0"
assert_ne "worktree tree moved" "$(oid "$R" worktree_tree_oid)" "$WT0"

start "snapshot: new untracked file moves the worktree tree only"
R="$(newrepo untracked)"
IT0="$(oid "$R" index_tree_oid)"; WT0="$(oid "$R" worktree_tree_oid)"
printf 'x\n' > "$R/brand-new.py"
assert_eq "index tree unchanged" "$(oid "$R" index_tree_oid)" "$IT0"
assert_ne "worktree tree moved" "$(oid "$R" worktree_tree_oid)" "$WT0"

start "snapshot: a new commit moves both head oids"
R="$(newrepo committed)"
HC0="$(oid "$R" head_commit_oid)"; HT0="$(oid "$R" head_tree_oid)"
printf 'print("four")\n' > "$R/app.py"; git -C "$R" add -A; git -C "$R" commit -qm second
assert_ne "head commit oid moved" "$(oid "$R" head_commit_oid)" "$HC0"
assert_ne "head tree oid moved" "$(oid "$R" head_tree_oid)" "$HT0"

start "snapshot: computing index_tree_oid does not mutate the real index"
R="$(newrepo nomutate)"
printf 'print("staged")\n' > "$R/app.py"; git -C "$R" add app.py
printf 'y\n' > "$R/loose.txt"
BEFORE="$(git -C "$R" status --porcelain)"
oid "$R" index_tree_oid >/dev/null; oid "$R" worktree_tree_oid >/dev/null
assert_eq "git status --porcelain identical" "$(git -C "$R" status --porcelain)" "$BEFORE"
assert_contains "the loose file is still untracked" "$(git -C "$R" status --porcelain)" "?? loose.txt"

# ============================================================ linked worktree

start "linked worktree: incarnation path lands in the worktree admin dir"
R="$(newrepo linked)"
WORK="$SANDBOX/linked-wt"
git -C "$R" worktree add -q -b side "$WORK" >/dev/null 2>&1
assert_eq ".git is a gitfile there" "$([ -f "$WORK/.git" ] && echo file || echo dir)" "file"
IP="$( cd "$WORK" && python3 -c "from feature_flow_common import incarnation_path;print(incarnation_path('.'))" )"
assert_contains "path is under .git/worktrees/linked-wt" "$IP" "/worktrees/linked-wt/feature-flow-incarnation"
assert_eq "path is absolute" "$(case "$IP" in /*) echo yes ;; *) echo no ;; esac)" "yes"
WHT="$(oid "$WORK" head_tree_oid)"
assert_eq "snapshot_oids works in the linked worktree" "${#WHT}" "40"
INC="$( cd "$WORK" && python3 -c "from feature_flow_common import incarnation;print(incarnation('.',create=True))" )"
assert_eq "incarnation minted only in the linked worktree" "$([ -f "$R/.git/feature-flow-incarnation" ] && echo leaked || echo clean)" "clean"
assert_eq "state dir is keyed by that uuid" "$([ -n "$INC" ] && echo yes || echo no)" "yes"

# ================================================================== no flow

start "no incarnation: worktree has no active flow"
R="$(newrepo noflow)"
OUT="$( cd "$R" && python3 -c "from feature_flow_common import load_state;print(load_state(cwd='.'))" )"
assert_eq "load_state returns {}" "$OUT" "{}"
( cd "$R" && python3 "$STATE_CLI" complete plan ) >/dev/null 2>&1
RC=$?
assert_eq "a mutation without init exits 2" "$RC" "2"
assert_eq "no incarnation file was created" \
  "$([ -f "$R/.git/feature-flow-incarnation" ] && echo created || echo absent)" "absent"

# =========================================================== lane migration

start "lane read-migration: a lane=heavy state file loads as flow"
R="$(newrepo legacy)"
ff "$R" init flow "legacy" >/dev/null
SD="$( cd "$R" && python3 -c "from feature_flow_common import state_dir;print(state_dir('.'))" )"
python3 - "$SD/state.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p)); d["lane"] = "heavy"; json.dump(d, open(p, "w"))
PY
assert_eq "lane reads as flow" "$( cd "$R" && python3 -c "from feature_flow_common import load_state;print(load_state(cwd='.')['lane'])" )" "flow"
INIT_ERR="$( ff "$R" init heavy x 2>&1 )"
assert_contains "init refuses the legacy lane" "$INIT_ERR" "invalid choice: 'heavy'"

# ==================================================================== guard

start "guard: source edit blocked before plan, allowed after plan+review-plan"
R="$(newrepo guarded)"
ff "$R" init flow "guarded feature" >/dev/null
PAYLOAD="$(python3 - "$R" <<'PY'
import json, sys
cmd = '*** Begin Patch\n*** Update File: app.py\n*** End Patch'
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "tool_name": "apply_patch", "tool_input": {"command": cmd}}))
PY
)"
assert_contains "blocked before the plan stage" "$(guard "$R" "$PAYLOAD")" "complete the plan stage"
write_flowdoc "$R" docs/flows/guarded.md "true"
ff "$R" set-plan docs/flows/guarded.md >/dev/null
ff "$R" complete plan >/dev/null
assert_contains "still blocked before plan review" "$(guard "$R" "$PAYLOAD")" "complete plan review"
ff "$R" complete review-plan >/dev/null
assert_eq "allowed after plan + review-plan" "$(guard "$R" "$PAYLOAD")" ""

start "guard: the flow doc itself is editable while source is blocked"
R2="$(newrepo guarded-plan)"
ff "$R2" init flow "plan only" >/dev/null
PLAN_PAYLOAD="$(python3 - "$R2" <<'PY'
import json, sys
cmd = '*** Begin Patch\n*** Add File: docs/flows/x.py\n*** End Patch'
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "tool_name": "apply_patch", "tool_input": {"command": cmd}}))
PY
)"
assert_eq "plan-path edit allowed" "$(guard "$R2" "$PLAN_PAYLOAD")" ""

start "guard: git commit is NEVER blocked (commit authorization deleted)"
R3="$(newrepo commits)"
ff "$R3" init flow "no commit gate" >/dev/null
COMMIT_PAYLOAD="$(python3 - "$R3" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "tool_name": "Bash", "tool_input": {"command": "git commit -m wip"}}))
PY
)"
assert_eq "no deny for git commit, pre-plan, in the flow lane" "$(guard "$R3" "$COMMIT_PAYLOAD")" ""
assert_eq "the commit-blocking branch is gone from the source" \
  "$(grep -c 'git commit' "$GUARD")" "0"

start "guard: bypass escapes work"
R4="$(newrepo bypass)"
ff "$R4" init flow "bypass" >/dev/null
SRC_PAYLOAD="$(python3 - "$R4" <<'PY'
import json, sys
cmd = '*** Begin Patch\n*** Update File: app.py\n*** End Patch'
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "tool_name": "apply_patch", "tool_input": {"command": cmd}}))
PY
)"
assert_contains "blocked without a bypass" "$(guard "$R4" "$SRC_PAYLOAD")" "Source edit blocked"
assert_eq "FEATURE_FLOW_BYPASS=1 escapes" \
  "$(cd "$R4" && printf '%s' "$SRC_PAYLOAD" | FEATURE_FLOW_BYPASS=1 python3 "$GUARD")" ""
INLINE_PAYLOAD="$(python3 - "$R4" <<'PY'
import json, sys
cmd = '# feature-flow: bypass\n*** Begin Patch\n*** Update File: app.py\n*** End Patch'
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "tool_name": "apply_patch", "tool_input": {"command": cmd}}))
PY
)"
assert_eq "in-command 'feature-flow: bypass' escapes" "$(guard "$R4" "$INLINE_PAYLOAD")" ""

# ================================================================= receipts

setup_receipt_repo() { # $1 = name, $2.. = harness lines -> echoes repo path
  local name="$1"; shift
  local repo; repo="$(newrepo "$name")"
  write_flowdoc "$repo" docs/flows/f.md "$@"
  git -C "$repo" add -A; git -C "$repo" commit -qm flowdoc
  ff "$repo" init flow "$name" >/dev/null
  ff "$repo" set-plan docs/flows/f.md >/dev/null
  ff "$repo" harness >/dev/null 2>&1
  echo "$repo"
}

start "receipt: verify passes on a clean matching receipt"
R="$(setup_receipt_repo receipt-clean "true")"
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_eq "complete verify exits 0" "$RC" "0"
assert_contains "verify stage recorded" "$OUT" "verify"

start "receipt: refuses with no receipt at all"
R="$(newrepo receipt-none)"
write_flowdoc "$R" docs/flows/f.md "true"
git -C "$R" add -A; git -C "$R" commit -qm flowdoc
ff "$R" init flow none >/dev/null; ff "$R" set-plan docs/flows/f.md >/dev/null
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_ne "exits non-zero" "$RC" "0"
assert_contains "names the missing receipt" "$OUT" "no receipt"

start "receipt: refuses after a new commit"
R="$(setup_receipt_repo receipt-commit "true")"
printf 'print("five")\n' > "$R/app.py"; git -C "$R" add -A; git -C "$R" commit -qm later
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_ne "exits non-zero" "$RC" "0"
assert_contains "names head_commit_oid" "$OUT" "head_commit_oid"

start "receipt: refuses after an unstaged edit"
R="$(setup_receipt_repo receipt-dirty "true")"
printf 'print("six")\n' > "$R/app.py"
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_ne "exits non-zero" "$RC" "0"
assert_contains "names worktree_tree_oid" "$OUT" "worktree_tree_oid"

start "receipt: refuses after the Harness block text changes"
R="$(setup_receipt_repo receipt-harness "true")"
write_flowdoc "$R" docs/flows/f.md "true" "true"
git -C "$R" add -A; git -C "$R" commit -qm reharness
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_ne "exits non-zero" "$RC" "0"
assert_contains "names the Harness change" "$OUT" "Harness block changed"

start "receipt: refuses when a recorded exit_code is non-zero"
R="$(setup_receipt_repo receipt-failed "false")"
OUT="$(ff "$R" complete verify 2>&1)"; RC=$?
assert_ne "exits non-zero" "$RC" "0"
assert_contains "names the failing command" "$OUT" "harness command failed"

start "receipt: harness itself exits non-zero when a command fails"
R="$(newrepo harness-rc)"
write_flowdoc "$R" docs/flows/f.md "true" "false"
git -C "$R" add -A; git -C "$R" commit -qm flowdoc
ff "$R" init flow rc >/dev/null; ff "$R" set-plan docs/flows/f.md >/dev/null
ff "$R" harness >/dev/null 2>&1; RC=$?
assert_ne "harness exits non-zero" "$RC" "0"
RCP="$( cd "$R" && python3 -c "from feature_flow_common import receipt_path;print(receipt_path('.'))" )"
assert_eq "the receipt is still written, with the failure recorded" "$([ -f "$RCP" ] && echo yes || echo no)" "yes"

start "receipt: lives outside the worktree so it cannot self-stale"
R="$(setup_receipt_repo receipt-outside "true")"
assert_eq "no receipt file inside the worktree" "$(cd "$R" && git status --porcelain | grep -c receipt || true)" "0"
assert_contains "receipt path is under the state home" \
  "$( cd "$R" && python3 -c "from feature_flow_common import receipt_path;print(receipt_path('.'))" )" "$FEATURE_FLOW_STATE_HOME"

# =============================================================== session start

SESSION="$SCRIPTS/feature-flow-session-start.py"

session_start() { # $1 = repo, $2 = trigger -> echoes hook stdout
  local repo="$1" trigger="$2"
  ( cd "$repo" && python3 - "$repo" "$trigger" <<'PY' | python3 "$SESSION"
import json, sys
print(json.dumps({"hook_event_name": "SessionStart", "cwd": sys.argv[1], "source": sys.argv[2]}))
PY
  )
}

start "session start: an active flow injects the flow doc path and stage"
R="$(newrepo session-active)"
write_flowdoc "$R" docs/flows/s.md "make check" "bash scripts/x.sh"
git -C "$R" add -A; git -C "$R" commit -qm flowdoc
ff "$R" init flow "session feature" >/dev/null
ff "$R" set-plan docs/flows/s.md >/dev/null
ff "$R" complete plan >/dev/null
OUT="$(session_start "$R" startup)"
assert_contains "names the flow doc path" "$OUT" "docs/flows/s.md"
assert_contains "names the completed stage" "$OUT" "stages completed: plan"
assert_contains "names the lane" "$OUT" "lane=flow"
assert_contains "carries the Harness verbatim" "$OUT" "make check"
assert_contains "emits additionalContext" "$OUT" "additionalContext"

start "session start: no incarnation file injects NOTHING and exits 0"
R="$(newrepo session-noflow)"
OUT="$(session_start "$R" startup)"; RC=$?
assert_eq "stdout is empty" "$OUT" ""
assert_eq "exit code is 0" "$RC" "0"

start "session start: the compact trigger reads differently from startup"
R="$(newrepo session-triggers)"
write_flowdoc "$R" docs/flows/s.md "true"
git -C "$R" add -A; git -C "$R" commit -qm flowdoc
ff "$R" init flow "triggers" >/dev/null; ff "$R" set-plan docs/flows/s.md >/dev/null
STARTUP="$(session_start "$R" startup)"
COMPACT="$(session_start "$R" compact)"
RESUME="$(session_start "$R" resume)"
assert_ne "compact differs from startup" "$COMPACT" "$STARTUP"
assert_ne "resume differs from startup" "$RESUME" "$STARTUP"
assert_contains "compact says the transcript was lost" "$COMPACT" "COMPACTED"
assert_contains "resume says resumed" "$RESUME" "resumed"
assert_eq "an unknown trigger (Codex has none) still injects" \
  "$(session_start "$R" "" | grep -c additionalContext)" "1"

start "session start: an oversized Harness block is truncated with a notice"
R="$(newrepo session-big)"
BIG=()
for i in $(seq 1 400); do BIG+=("echo command-number-$i-with-padding-to-exceed-four-kilobytes"); done
write_flowdoc "$R" docs/flows/s.md "${BIG[@]}"
git -C "$R" add -A; git -C "$R" commit -qm flowdoc
ff "$R" init flow "big harness" >/dev/null; ff "$R" set-plan docs/flows/s.md >/dev/null
OUT="$(session_start "$R" compact)"
assert_contains "says it was truncated" "$OUT" "Harness TRUNCATED"
assert_contains "points at the full block" "$OUT" "for the full block"
BYTES="$(printf '%s' "$OUT" | wc -c | tr -d ' ')"
assert_eq "payload stays bounded under 6KB" "$([ "$BYTES" -lt 6144 ] && echo yes || echo no)" "yes"
assert_eq "a small block is NOT truncated" \
  "$(session_start "$(setup_receipt_repo session-small "true")" compact | grep -c TRUNCATED)" "0"

# ===================================================================== prune

start "prune: dry run lists an orphan and deletes nothing"
R="$(newrepo prune-orphan)"
ff "$R" init flow "doomed" >/dev/null
DOOMED="$( cd "$R" && python3 -c "from feature_flow_common import state_dir;print(state_dir('.'))" )"
rm "$R/.git/feature-flow-incarnation"
LIVE="$(newrepo prune-live)"
ff "$LIVE" init flow "keeper" >/dev/null
KEEP="$( cd "$LIVE" && python3 -c "from feature_flow_common import state_dir;print(state_dir('.'))" )"
OUT="$(ff "$LIVE" prune)"
assert_contains "names the orphan" "$OUT" "WOULD REMOVE $DOOMED"
assert_contains "names the condemning class" "$OUT" "worktree gone or unregistered"
assert_contains "names the live dir as kept" "$OUT" "keep    $KEEP"
assert_eq "the orphan still exists after a dry run" "$([ -d "$DOOMED" ] && echo yes || echo no)" "yes"

start "prune: --apply removes exactly the orphan and leaves live state intact"
OUT="$(ff "$LIVE" prune --apply)"
assert_contains "reports the removal" "$OUT" "remove  $DOOMED"
assert_eq "the orphan is gone" "$([ -d "$DOOMED" ] && echo yes || echo no)" "no"
assert_eq "the live state dir survives" "$([ -f "$KEEP/state.json" ] && echo yes || echo no)" "yes"
assert_contains "a second run finds nothing left" "$(ff "$LIVE" prune --apply)" "no orphaned state dirs"
assert_contains "the live flow still loads" "$(ff "$LIVE" get)" "keeper"

# ============================================================ doctor wiring

start "doctor: reports session_start_hook unwired, and stops once it is wired"
FAKE="$SANDBOX/fakehome"; mkdir -p "$FAKE/.claude"
printf '{"hooks":{}}\n' > "$FAKE/.claude/settings.json"
probe_wiring() { HOME="$FAKE" python3 "$SCRIPTS/doctor.py" --json 2>/dev/null \
  | python3 -c 'import json,sys;d=json.load(sys.stdin)["claude_hooks"];print(d["session_start_hook"],d["stop_hook"],d["ok"])'; }
assert_eq "both unwired against an empty settings.json" "$(probe_wiring)" "unwired unwired False"
printf '{"hooks":{"SessionStart":[{"hooks":[{"command":"$HOME/.claude/hooks/feature-flow-session-start.py"}]}]}}\n' \
  > "$FAKE/.claude/settings.json"
assert_eq "session_start wired, stop still unwired" "$(probe_wiring)" "wired unwired False"
printf '{"hooks":{"SessionStart":[{"hooks":[{"command":"$HOME/.claude/hooks/feature-flow-session-start.py"}]}],"Stop":[{"hooks":[{"command":"$HOME/.claude/hooks/feature-flow-stop.py"}]}]}}\n' \
  > "$FAKE/.claude/settings.json"
assert_eq "both wired reports ok" "$(probe_wiring)" "wired wired True"

start "doctor: a machine with no Claude host is not a wiring failure"
BARE="$SANDBOX/barehome"; mkdir -p "$BARE"
assert_eq "absent settings.json reports absent and ok" \
  "$(HOME="$BARE" python3 "$SCRIPTS/doctor.py" --json 2>/dev/null \
     | python3 -c 'import json,sys;d=json.load(sys.stdin)["claude_hooks"];print(d["status"],d["unwired"],d["ok"])')" \
  "absent [] True"

# ============================================================== skill honesty
# Content assertions on the SHIPPED instruction. Hookless hosts are first-class,
# so a hook-derived guarantee stated unconditionally is a false promise in
# the one file agents actually read. These cases exist to stop a future edit from
# quietly re-universalizing one.

SKILL="$REPO/skills/feature-flow/SKILL.md"

start "skill: hook guarantees are labeled conditional"
HOOKS_SECTION="$(awk '/^## Host hooks/{f=1} f' "$SKILL")"
assert_ne "a hooks section exists" "$HOOKS_SECTION" ""
assert_eq "the section names hookless hosts explicitly" \
  "$(printf '%s' "$HOOKS_SECTION" | grep -ow 'hookless' | head -1)" "hookless"
assert_contains "it says hooks fire ONLY on hook-supporting hosts" "$HOOKS_SECTION" "ONLY on hosts that support hooks"
assert_contains "it says nothing fires on the hookless host" "$HOOKS_SECTION" "none of them fire"
assert_contains "the heading itself marks the conditionality" "$(grep '^## Host hooks' "$SKILL")" "never the mechanism"

start "skill: the harness-agnostic recovery step is present and unconditional"
# Scoped to the recovery SECTION: the hooks table also mentions `ls docs/flows/`, so a
# whole-file grep would still pass with the primary recovery step deleted.
RECOVERY_SECTION="$(awk '/^## Recovering state/{f=1;next} f&&/^## /{exit} f' "$SKILL")"
assert_contains "tells the agent to list the committed flow docs" "$RECOVERY_SECTION" "ls docs/flows/"
assert_contains "names the flow doc as COMMITTED" "$RECOVERY_SECTION" "COMMITTED in the repo"
assert_contains "says no host support is required" "$RECOVERY_SECTION" "no host support"
assert_contains "recovery is ordered before everything else" "$(grep -c '^## Recovering state' "$SKILL")" "1"
assert_contains "step 1 of the Flow lane recovers state first" \
  "$(awk '/^## Flow$/{f=1;next} f&&/^1\./{print;exit}' "$SKILL")" "Recover state"
assert_contains "an uninstalled host has a stated fallback" "$(cat "$SKILL")" "plugin checkout"

start "skill: no unconditional claim that a hook blocks source edits"
assert_eq "the old universal 'Source edits stay blocked' claim is gone" \
  "$(grep -c 'Source edits stay blocked' "$SKILL" || true)" "0"
assert_eq "no bare 'blocks source edits' outside the conditional table" \
  "$(grep -n 'blocks source edits' "$SKILL" | grep -vc '|' || true)" "0"
assert_contains "the plan gate is stated as an instruction to the agent" "$(cat "$SKILL")" "do not edit source until"
assert_contains "and its real enforcement is named" "$(cat "$SKILL")" "pre-push hook, CI"

# ==================================================================== summary
# ------------------------------------------------------- shared-checkout guard
# A flow opened in a repo's PRIMARY checkout is inherited by every session whose
# shell sits there. Observed: a session working in a linked worktree ran commands
# from the shared checkout, opening a second flow; an unrelated session then had
# its Stop hook block on the first session's missing verify receipt.
start "shared checkout: a flow there is everyone's flow"
FF_GUARD_TMP="$(mktemp -d)"
(
  cd "$FF_GUARD_TMP"
  git init -q primary && cd primary
  git config user.email t@t; git config user.name t
  echo x > f && git add -A && git commit -qm init
) >/dev/null 2>&1
FF_PRIMARY="$FF_GUARD_TMP/primary"

# No linked worktrees yet: a lone checkout is NOT the hazard, so init must work.
FF_LONE="$(cd "$FF_PRIMARY" && python3 -c "
import sys; sys.path.insert(0, '$REPO/scripts')
from feature_flow_common import primary_checkout_with_worktrees as p
print(p('.'))
")"
assert_eq "a repo with no linked worktrees is not flagged" "$FF_LONE" "False"

(cd "$FF_PRIMARY" && git worktree add -q ../linked -b linked) >/dev/null 2>&1
FF_NOW="$(cd "$FF_PRIMARY" && python3 -c "
import sys; sys.path.insert(0, '$REPO/scripts')
from feature_flow_common import primary_checkout_with_worktrees as p
print(p('.'))
")"
assert_eq "the primary checkout IS flagged once a worktree exists" "$FF_NOW" "True"

FF_LINKED="$(cd "$FF_GUARD_TMP/linked" && python3 -c "
import sys; sys.path.insert(0, '$REPO/scripts')
from feature_flow_common import primary_checkout_with_worktrees as p
print(p('.'))
")"
assert_eq "a linked worktree is never flagged" "$FF_LINKED" "False"

FF_REFUSED="$(cd "$FF_PRIMARY" && python3 -c "
import sys; sys.path.insert(0, '$REPO/scripts')
from feature_flow_common import init_state
try:
    init_state('flow', 'x', cwd='.')
    print('CREATED')
except SystemExit as e:
    print('REFUSED' if 'PRIMARY checkout' in str(e) else f'OTHER:{e}')
" 2>&1 | tail -1)"
assert_eq "init refuses in the shared checkout" "$FF_REFUSED" "REFUSED"

FF_OVERRIDE="$(cd "$FF_PRIMARY" && FEATURE_FLOW_ALLOW_PRIMARY_CHECKOUT=1 python3 -c "
import sys; sys.path.insert(0, '$REPO/scripts')
from feature_flow_common import init_state
try:
    init_state('flow', 'x', cwd='.'); print('CREATED')
except SystemExit as e:
    print(f'REFUSED:{e}')
" 2>&1 | tail -1)"
assert_eq "the named override still allows it" "$FF_OVERRIDE" "CREATED"

# That override CREATED a flow in the fixture, and the auto-path cases below read
# as "a flow is already active" if it is left standing — the fixture polluting
# itself, which is what the assertion would then be measuring.
(cd "$FF_PRIMARY" && python3 "$REPO/scripts/feature-flow-state.py" clear) >/dev/null 2>&1

# The AUTO path must not inherit the CLI's hard refusal. Every session in a
# worktree repo tends to sit in the primary checkout, so raising there would error
# the prompt hook on every feature-shaped message.
FF_HOOK_RC="$(echo "{\"prompt\":\"add a settings toggle to the billing page\",\"cwd\":\"$FF_PRIMARY\"}" \
  | python3 "$REPO/scripts/feature-flow-user-prompt.py" >/dev/null 2>&1; echo $?)"
assert_eq "the prompt hook exits 0 in the shared checkout" "$FF_HOOK_RC" "0"
FF_HOOK_OUT="$(echo "{\"prompt\":\"add a settings toggle to the billing page\",\"cwd\":\"$FF_PRIMARY\"}" \
  | python3 "$REPO/scripts/feature-flow-user-prompt.py" 2>/dev/null)"
assert_contains "and advises the worktree instead of opening a flow" "$FF_HOOK_OUT" "No flow was opened"
rm -rf "$FF_GUARD_TMP"

# ------------------------------------------------- codex strict output validation
# Codex validates hook OUTPUT with deny_unknown_fields. An unrecognised top-level
# key makes it discard the whole output and proceed — so a hook that emits one
# extra field stops guarding on Codex silently. Verified live on 0.145.0-alpha.24;
# the rationale and the observed log lines live in the tool's docstring.
start "codex output contract"
FF_SHAPES="$(python3 "$REPO/tools/check_hook_output_shapes.py" "$REPO" 2>&1 | tail -1)"
case "$FF_SHAPES" in
  ok:*) ok "every hook emits only Codex-accepted top-level keys" ;;
  *)    bad "hook output would be discarded by Codex ($FF_SHAPES)" ;;
esac

# ---------------------------------------------------------------- feature detection
start "prompt detection: an instruction, not a mention"
# Regression: the detector opened a feature-flow for Codex's own "generate
# suggestions for this project" prompt, recorded that prompt as the feature name,
# and then the Stop hook nagged about choosing a lane for it on every turn. Two
# defects — the verb and noun could sit paragraphs apart (`.+` under re.S), and a
# descriptive mention counted the same as an instruction.
FF_DETECT="$(python3 - "$REPO" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
from feature_flow_common import FEATURE_RE, SKIP_RE

def fires(text):
    return bool(FEATURE_RE.search(text) and not SKIP_RE.search(text))

CODEX_NOISE = (
    "# Overview\n\nGenerate 0 to 3 hyperpersonalized suggestions for what this user "
    "can do with Codex in this local project: /tmp/proj\n\nGet an understanding of "
    "the user's intent and goals by deeply viewing their connected apps. Suggest "
    "things they could build or create, referencing any page or integration they use."
)
cases = [
    ("request-imperative",  "add a settings toggle to the billing page", True),
    ("request-polite",      "Can you add a settings toggle to the billing page?", True),
    ("request-after-comma", "ok next, add a dark mode toggle", True),
    ("request-bulleted",    "- implement the export flow", True),
    ("noise-codex-suggest", CODEX_NOISE, False),
    ("noise-long-prose",    "Please summarize the report. " * 20 + "It has a support page.", False),
    ("noise-descriptive",   "The dashboard has a support page and an API integration.", False),
    ("skip-directive",      "add a settings toggle to the billing page, skip feature-flow", False),
]
print(" ".join(f"{n}={'1' if fires(t) == w else '0'}" for n, t, w in cases))
PY
)"
for pair in $FF_DETECT; do
  assert_eq "feature detection: ${pair%%=*}" "${pair##*=}" "1"
done


echo "------------------------------------------------------------"
echo "harness: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1

