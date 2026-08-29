#!/usr/bin/env python3
"""Every hook must emit ONLY top-level keys Codex accepts.

Codex validates hook OUTPUT with `deny_unknown_fields` / `additionalProperties:
false`. An unrecognised top-level key makes it discard the WHOLE output and let the
turn proceed — so a hook that emits one extra field stops guarding on Codex,
silently, with a single word in a log as the only signal.

Verified live on codex-cli 0.145.0-alpha.24 with an isolated CODEX_HOME:

    {"hookSpecificOutput": {...}}                 -> hook: UserPromptSubmit Completed
    {"decision": "block", "reason": "..."}        -> hook: Stop Blocked
    {..., "totallyBogusFieldThatCannotExist": 1}  -> hook: UserPromptSubmit Failed

and in the Failed case `codex exec` still exited 0 and the model still answered.
That is the fail-open this guards against.

ALLOWED holds only keys proven accepted by that live check. Widening it needs the
same live check against a real Codex, not a guess about what it probably takes.

Run: python3 tools/check_hook_output_shapes.py <plugin-root>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOWED = {"decision", "reason", "hookSpecificOutput"}

#: Payloads chosen to make each hook actually EMIT something — a hook that stays
#: silent proves nothing about its output shape.
PROBES: tuple[tuple[str, dict], ...] = (
    (
        "feature-flow-user-prompt.py",
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "add a settings toggle to the billing page",
        },
    ),
    ("feature-flow-session-start.py", {"hook_event_name": "SessionStart"}),
    (
        "feature-flow-stop.py",
        {"hook_event_name": "Stop", "last_assistant_message": "done"},
    ),
    (
        "feature-flow-guard.py",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
    ),
)


def _fixture_repo() -> str:
    """A throwaway repo, so the probes never read the caller's real state."""
    tmp = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", tmp], check=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", tmp, "config", key, value], check=True)
    Path(tmp, "f").write_text("x")
    subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
    subprocess.run(["git", "-C", tmp, "commit", "-qm", "init"], check=True)
    return tmp


def main() -> int:
    """Probe every hook in `PROBES` and fail if any emits a disallowed top-level key.

    Reads the plugin root from ``sys.argv[1]`` (defaulting to ``.``), runs each
    probed ``scripts/<script>`` against a throwaway fixture repo so no hook reads
    real state, and prints one ``FAIL:`` line per offending hook. Returns a
    nonzero exit code if any hook emitted an unaccepted key or none emitted JSON
    at all.
    """
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    repo = _fixture_repo()
    env = {**os.environ, "FEATURE_FLOW_ALLOW_PRIMARY_CHECKOUT": "1"}
    offenders: list[str] = []
    emitted = 0

    for script, payload in PROBES:
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / script)],
            input=json.dumps({**payload, "cwd": repo}),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                keys = set(json.loads(line))
            except json.JSONDecodeError:
                offenders.append(f"{script}: emitted unparseable JSON")
                continue
            emitted += 1
            extra = keys - ALLOWED
            if extra:
                offenders.append(f"{script}: unaccepted top-level key(s) {sorted(extra)}")

    if not emitted:
        print("FAIL: no hook emitted JSON, so nothing was actually checked")
        return 1
    if offenders:
        for line in offenders:
            print(f"FAIL: {line}")
        print(
            "Codex discards output with unknown top-level keys and lets the turn "
            "proceed, so these hooks would stop guarding there silently."
        )
        return 1
    print(f"ok: {emitted} hook output(s), all within {sorted(ALLOWED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
