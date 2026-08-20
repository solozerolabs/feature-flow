#!/usr/bin/env bash
# Install a TAGGED feature-flow into a versioned dir and point every host adapter
# at it, so no host ever executes the mutable source checkout.
#
#   ./install.sh <tag-or-ref>            # dry run (default): reports, writes nothing
#   ./install.sh <tag-or-ref> --apply    # writes
#
# Hosts touched: the Claude skill copy, the Codex skill copy, the Codex hooks
# command paths, and the Claude ~/.claude/hooks/ scripts. ~/.claude/settings.json
# is NEVER modified: the Claude-side filenames it already references are installed
# as shims that exec the versioned scripts.
set -euo pipefail

REF=""
APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) REF="$arg" ;;
  esac
done
[ -n "$REF" ] || { echo "usage: $0 <tag-or-ref> [--apply]" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")" && pwd)"
git -C "$REPO" rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
  || { echo "not a ref in $REPO: $REF" >&2; exit 2; }
COMMIT="$(git -C "$REPO" rev-parse "$REF^{commit}")"

SHARE="$HOME/.local/share/feature-flow"
# The install dir must be immutable. A tag names itself; any other ref (HEAD, a
# branch) installs under its commit, so "reinstall HEAD" can never quietly change
# what an existing dir contains.
if git -C "$REPO" rev-parse --verify --quiet "refs/tags/$REF" >/dev/null; then
  DIRNAME="$REF"
else
  DIRNAME="${COMMIT:0:12}"
fi
DEST="$SHARE/$DIRNAME"
CURRENT="$SHARE/current"
MANIFEST="$HOME/.local/state/feature-flow/install-manifest.json"

STAGE="$(mktemp -d)"
RENDER="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$RENDER"' EXIT
git -C "$REPO" archive --format=tar "$REF" | tar -x -C "$STAGE"

# --- desired content for every host file ------------------------------------

shim() { # $1 = versioned script basename
  printf '#!/usr/bin/env python3\n# Installed by feature-flow install.sh (%s). Do not edit; execs the tagged bytes.\nimport os, sys\nos.execv(sys.executable, [sys.executable, "%s/scripts/%s", *sys.argv[1:]])\n' \
    "$REF" "$DEST" "$1"
}

mkdir -p "$RENDER"
cp "$STAGE/skills/feature-flow/SKILL.md" "$RENDER/claude-SKILL.md"
cp "$STAGE/skills/feature-flow/SKILL.md" "$RENDER/codex-SKILL.md"
shim feature-flow-guard.py       > "$RENDER/feature-flow-guard.py"
shim feature-flow-user-prompt.py > "$RENDER/feature-flow-nudge.py"
shim feature-flow-state.py       > "$RENDER/feature-flow-state.py"
shim feature-flow-stop.py        > "$RENDER/feature-flow-stop.py"
shim feature-flow-session-start.py > "$RENDER/feature-flow-session-start.py"
# PostToolUse test inference is retired. If a host settings.json still names this file,
# and settings.json is not ours to edit, so install a harmless no-op.
printf '#!/usr/bin/env python3\n# feature-flow: PostToolUse test inference is retired. `complete verify` owns\n# verification evidence via the harness receipt. Intentional no-op.\n' \
  > "$RENDER/feature-flow-tests-detect.py"

CODEX_HOOKS="$HOME/.codex/hooks.json"
if [ -f "$CODEX_HOOKS" ]; then
  DEST="$DEST" python3 - "$CODEX_HOOKS" "$RENDER/codex-hooks.json" "$STAGE/hooks/hooks.json" <<'PY'
import json, os, re, sys
src, out, plugin = sys.argv[1], sys.argv[2], sys.argv[3]
dest = os.environ["DEST"]
doc = json.loads(open(src).read())
plugin_doc = json.loads(open(plugin).read())
# Matchers rot too: take them from the tagged plugin's own hooks.json.
matchers = {
    event: group.get("matcher")
    for event, groups in (plugin_doc.get("hooks") or {}).items()
    for group in groups
    if group.get("matcher")
}
# Match only the unquoted path token, so surrounding quotes survive verbatim.
pat = re.compile(r'[^"\'\s]*/feature-flow-([a-z-]+)\.py')
retired = {"feature-flow-tests"}
for event, groups in list((doc.get("hooks") or {}).items()):
    kept_groups = []
    for group in groups:
        hooks = []
        for hook in group.get("hooks", []):
            cmd = str(hook.get("command", ""))
            match = pat.search(cmd)
            if match and f"feature-flow-{match.group(1)}" in retired:
                continue
            hook["command"] = pat.sub(lambda m: f'{dest}/scripts/feature-flow-{m.group(1)}.py', cmd)
            hooks.append(hook)
        if hooks:
            group["hooks"] = hooks
            if event in matchers:
                group["matcher"] = matchers[event]
            kept_groups.append(group)
    if kept_groups:
        doc["hooks"][event] = kept_groups
    else:
        del doc["hooks"][event]
# A NEW plugin event (SessionStart) exists in no host file yet, so patching paths
# alone would leave Codex without it forever. Add what the host is missing.
for event, groups in (plugin_doc.get("hooks") or {}).items():
    if event in (doc.get("hooks") or {}):
        continue
    doc.setdefault("hooks", {})[event] = json.loads(
        json.dumps(groups).replace("${PLUGIN_ROOT}", dest)
    )
open(out, "w").write(json.dumps(doc, indent=2) + "\n")
PY
fi

# --- report / apply ----------------------------------------------------------

TARGETS=(
  "$RENDER/claude-SKILL.md|$HOME/.claude/skills/feature-flow/SKILL.md|644"
  "$RENDER/codex-SKILL.md|$HOME/.codex/skills/feature-flow/SKILL.md|644"
  "$RENDER/feature-flow-guard.py|$HOME/.claude/hooks/feature-flow-guard.py|755"
  "$RENDER/feature-flow-nudge.py|$HOME/.claude/hooks/feature-flow-nudge.py|755"
  "$RENDER/feature-flow-state.py|$HOME/.claude/hooks/feature-flow-state.py|755"
  "$RENDER/feature-flow-stop.py|$HOME/.claude/hooks/feature-flow-stop.py|755"
  "$RENDER/feature-flow-session-start.py|$HOME/.claude/hooks/feature-flow-session-start.py|755"
  "$RENDER/feature-flow-tests-detect.py|$HOME/.claude/hooks/feature-flow-tests-detect.py|755"
)
[ -f "$RENDER/codex-hooks.json" ] && TARGETS+=("$RENDER/codex-hooks.json|$CODEX_HOOKS|644")
# The Claude-side divergent library is superseded by the shims above.
OBSOLETE=("$HOME/.claude/hooks/feature_flow_lib.py")

echo "feature-flow install: ref=$REF commit=${COMMIT:0:12} install_dir=$DEST"
[ "$APPLY" = 1 ] && echo "mode: APPLY" || echo "mode: DRY-RUN (no writes; pass --apply to write)"
echo "host files that would change:"
CHANGES=0
for entry in "${TARGETS[@]}"; do
  IFS='|' read -r src dst _mode <<<"$entry"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    echo "  unchanged  $dst"
  elif [ -f "$dst" ]; then
    echo "  UPDATE     $dst"
    CHANGES=$((CHANGES + 1))
  else
    echo "  CREATE     $dst"
    CHANGES=$((CHANGES + 1))
  fi
done
for dst in "${OBSOLETE[@]}"; do
  if [ -e "$dst" ]; then
    echo "  DELETE     $dst  (superseded by the versioned shims)"
    CHANGES=$((CHANGES + 1))
  fi
done
if [ ! -d "$DEST" ]; then echo "  CREATE     $DEST/  (tagged bytes)"; CHANGES=$((CHANGES + 1)); fi
echo "  SYMLINK    $CURRENT -> $DEST"
echo "  MANIFEST   $MANIFEST"
echo "total host files to change: $CHANGES"

# ~/.claude/settings.json is the USER's file and is never edited here. The two
# hooks it does not already invoke would ship inert, so print the exact fragment.
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
echo
echo "~/.claude/settings.json wiring (NOT edited by this script):"
MISSING=0
for entry in "SessionStart:startup|resume|clear|compact:feature-flow-session-start.py:10" "Stop::feature-flow-stop.py:5"; do
  IFS=':' read -r event matcher script timeout <<<"$entry"
  if [ -f "$CLAUDE_SETTINGS" ] && grep -q "$script" "$CLAUDE_SETTINGS"; then
    echo "  already wired: $event -> $script"
    continue
  fi
  MISSING=$((MISSING + 1))
  echo "  UNWIRED: $event. Paste this into the \"hooks\" object of $CLAUDE_SETTINGS:"
  echo "    \"$event\": ["
  echo "      {"
  [ -n "$matcher" ] && echo "        \"matcher\": \"$matcher\","
  echo "        \"hooks\": ["
  echo "          { \"type\": \"command\", \"command\": \"\$HOME/.claude/hooks/$script\", \"timeout\": $timeout }"
  echo "        ]"
  echo "      }"
  echo "    ]"
done
[ "$MISSING" = 0 ] || echo "  until pasted, doctor.py reports these as unwired and exits non-zero."

if [ "$APPLY" != 1 ]; then
  echo "dry run: nothing written."
  exit 0
fi

rm -rf "$DEST"
mkdir -p "$DEST"
git -C "$REPO" archive --format=tar "$REF" | tar -x -C "$DEST"
ln -sfn "$DEST" "$CURRENT"

for entry in "${TARGETS[@]}"; do
  IFS='|' read -r src dst mode <<<"$entry"
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  chmod "$mode" "$dst"
done
for dst in "${OBSOLETE[@]}"; do rm -f "$dst"; done

mkdir -p "$(dirname "$MANIFEST")"
{
  printf '{\n  "tag": "%s",\n  "commit": "%s",\n  "install_dir": "%s",\n' "$REF" "$COMMIT" "$DEST"
  printf '  "installed_at": "%s",\n  "files": {\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  first=1
  for entry in "${TARGETS[@]}"; do
    IFS='|' read -r _src dst _mode <<<"$entry"
    sha="$(shasum -a 256 "$dst" | awk '{print $1}')"
    [ "$first" = 1 ] || printf ',\n'
    first=0
    printf '    "%s": "%s"' "$dst" "$sha"
  done
  printf '\n  }\n}\n'
} > "$MANIFEST"
echo "wrote $MANIFEST"
if [ -f "$CODEX_HOOKS" ]; then
  echo "NOTE: Codex trusts hooks by hashing the hook ENTRY. Rewriting hooks.json"
  echo "      invalidates every trusted_hash in ~/.codex/config.toml — Codex will"
  echo "      re-prompt on next launch and SILENTLY SKIP these hooks until you accept."
  echo "      Verify with: python3 $DEST/scripts/doctor.py --json"
fi
