# SPEC: Full REA integration + a "learn from a product" run

Status: **v2 DRAFT** (2026-10-09). v1 feedback: use REA in full (not just two browser commands), cover every
build type we support — sites, web apps, CLIs, mobile, desktop. This is the spec only; no code until go.
Base: `Syndai/main` `298620b704`. Local checkout is 788 commits behind; every ref is to `Syndai/main`.
`C/` = `backend/src/features/coding/`.
Prior art: [morluto/rea](https://github.com/morluto/rea) v6.3.0 (MIT). I cloned it, read its code, docs, bundled
skill and trackers #32/#39/#48, and ran its published MCP server locally to measure it (§2).

## 1. What we're building

Two things, one shared foundation.

1. **REA available as a tool inside every coding run.** REA is a local, evidence-first analysis tool: give it a
   target (a running website, a JS/Electron app directory, a .NET assembly, an Android APK, a native binary,
   firmware, a HAR, a live process) and it returns structured facts — modules, routes, endpoints, call graphs,
   decompiled functions, network shapes — each tagged as observed / inferred / unknown. Our agents already build
   every kind of app; giving them REA means that when a build depends on understanding an existing artifact (the
   user's own legacy app, a dependency with no docs, a binary tool they want a wrapper for), the agent can read
   that artifact instead of guessing.

2. **A "learn from a product" affordance on Build home.** The user points at a product they want to emulate —
   typically a public website or web app, optionally with their own screenshots or an artifact they already have
   — and Syndai produces a feature dossier, then builds the user their own implementation under their own brand.

The foundation both share: REA installed in the sandbox template behind a small proxy, and a prompt block that
tells the agent when and how to use it.

## 2. REA, measured (not guessed)

I installed `rea-agents@6.2.0` and started its MCP server over stdio:

- **139 tools.** `tools/list` is **1.86 MB** of JSON ≈ **530k tokens**. Input schemas alone are ~567k chars.
  Median tool is ~9 KB; the heavy ones are huge (`compare_web_captures` 86 KB, `capture_browser_scenario` 45 KB,
  `analyze_javascript_application` 32 KB).
- **Conclusion: we cannot register REA's MCP server raw.** 530k tokens of schema on every turn is a non-starter
  across our five harnesses and would blow past context on its own. This is also why REA's own skill tells agents
  to read `tools/list` lazily, and why strict-provider schema bugs (#1212, #1282, #1329 — DeepSeek 400s every
  turn while connected) and V8 string-limit crashes on big outputs (#1331, #1371, #1380) matter to us: we route
  around both.
- Runtime needs: Node ≥22.19 (template has 24), bundled `playwright-core`, a Chromium we point it at (template
  has one), 6.5 MB unpacked. Native/Android/firmware tools need bring-your-own engines (Ghidra+JDK, JADX, binwalk)
  that the template does not have yet.

REA's own history is the design guide: PRs #555/#572 deleted its permission system and orchestration engine; its
roadmap admits new orchestration only for a demonstrated need. **REA is a tool, not an engine.** We treat it as
one: our run engine stays ours, REA is a toolbox the agent reaches into.

## 3. What Syndai has today (verified on `Syndai/main`)

| Fact | Evidence |
|---|---|
| The in-sandbox `browser` MCP (patchright) already gives agents `browse_external`, `snapshot`, `screenshot`, governed and SSRF-fenced; first external call per domain raises a session-approval card. | `C/browser_capability_definitions.py:153`, `C/capability_runtime_external.py:13-34`, `C/executors/patchright_bridge_server.py` |
| It has **no network capture and no multi-step scenario with per-step evidence** — REA's `capture-browser-scenario` does. | REA `docs/browser-scenario-contract.md` |
| A `site_clone` run kind already exists via API (`POST /external/coding/site-clones`, `source_urls` max 20, per-URL attestation, SSRF check, managed/existing repo). No portal UI, and its prompt points at an AGENTS.md workflow that doesn't exist. | `C/router_evidence.py:33`, `C/site_clone_dtos.py`, `C/site_clone_service.py:123,159,229` |
| New site/app first runs use the one-turn lane (no plan turn); shape from the enhancer's `project_shape`. | `C/one_turn_lane.py:176,266`, `C/coding_landing_spec.py:50-58` |
| The enhancer outputs `goal`, `acceptance_criteria`, `project_shape`, and can raise a multi-question clarify card (with options) that pauses before the first sandbox turn and auto-proceeds at 24 h. | `C/prompt_enhancer.py:125-143`, `C/intake_clarify.py:157-200` |
| Intake research (Tavily, Context7) feeds only the enhancer. Firecrawl client exists server-side. | `C/context_research_gatherer.py:188`, `knowledge/firecrawl.py` |
| Template has Node 24, Playwright+Chromium, patchright; pins in one module. Governed capabilities route through one `run_skill` MCP tool; customer MCP servers are proxied. | `services/e2b_base_template.py`, `services/coding_toolchain.py`, tool-delivery v5 (`docs/flows/caas-tool-delivery.md`) |
| Built output is checked by floors (`/` + 10 pages × 3 widths, FORM_PROBE), the app visitor pass, and acceptance criteria via criterion proofs. Nothing compares against an external source. | `C/syndai_floors.py`, `C/floors/*.mjs`, `C/sandbox_web_visitor.py:403`, `C/acceptance_binding.py` |
| Run-attached images already reach the agent as "a reference to match". New-project runs accept `new_repository.scaffold`/`project_shape`. | `C/coding_image_intake.py`, `C/dtos.py:627-631` |
| Pricing is metered usage × 1.20, no per-kind price; markup snapshotted per run. | `billing/tier_catalog.py:88`, `C/run_creation.py:629` |

## 4. Design decisions

### REA integration

- **D1. Install REA's full toolkit in the template, with every free provider.** Add to
  `e2b_base_template.py` / `coding_toolchain.py`, each pinned:
  - `rea-agents@<exact>` (`npm i -g`), pointed at the template's Chromium via `executable_path`.
  - Ghidra + a JDK → native analysis (ELF/PE/Mach-O/DOS, Swift/ObjC metadata, decompilation).
  - JADX → Android.
  - binwalk + unblob → firmware.
  - pwntools + GDB → ELF layout and recorded-crash inspection.
  - mitmproxy → network capture parsing.
  This is a heavy template path, so the whole workstream ships as **one CaaS run**, never a local push.
- **D2. The agent uses REA as a CLI through the shell — no MCP bridge, no proxy.** This is the laziest design
  that works, and it's also the correct one. The reasoning (full version in the drill-down note, §9):
  - REA ships a complete CLI (`rea <command> --json`); the MCP server is just a second face on the same
    workflows. The 530k-token schema problem (§2) is *only* a problem on the MCP path. Via the CLI there are no
    schemas in the prompt at all — the agent reads `rea <command> --help` on demand (cheap).
  - A bridge's only real justification would be governance, but it buys nothing here: the sandbox already has
    **open internet by default** (`coding_network_policy.py:38`) and `curl` installed (`coding_toolchain.py:159`),
    and the provider egress layer denies private/loopback/metadata ranges for *every* process regardless of
    transport (`run_egress.py`). So gating `rea` behind a session-approval bridge while `curl` runs ungated is
    theater, not containment. The `browse_external` card is a consent/UX surface, not a security boundary.
  - Output discipline is a shell convention, not code: the prompt tells the agent to write to
    `.syndai/reference/` (gitignored) and `rg`/`jq` slices out of it. Big-output crashes (#1331/#1380) live
    inside REA's own process and hit the MCP path identically, so a bridge would not have protected us there
    either — only a fix does (§9 fork question).
  - Audit is already covered: every shell command is in the run transcript.

  Net: the agent gets **all 139 tools** at **zero standing prompt cost and zero new backend code**. Compare the
  rejected bridge: a new ~4-tool sandbox server (`rea_catalog`/`rea_schema`/`rea_run`/`rea_read`) modelled on the
  patchright bridge — real code, real maintenance, for a gate that duplicates one `curl` already has.
- **D3. REA is default-available on every run, surfaced lazily in the prompt.** A ~15-line prompt block (inlined,
  not a gated skill — gated skills are skipped ~half the time) tells the agent: REA exists, it reads shipped
  artifacts and live targets, route by target type, run `rea --help` (then `rea <command> --help`) first. The block is cached after turn 1,
  so the standing cost is ~0. It does not fire REA on its own; the agent uses it when a task needs it, exactly
  like the existing reuse-before-build step.
- **D3b. Big outputs go to a file, never a pipe.** The prompt rule is `rea <command> --json > .syndai/reference/
  <name>.json`, then read slices with `rg`/`jq`. REA's own output-size crashes (#1331/#1380) can still abort a
  command on a pathological target (a 9 MB inline source map); the agent treats that as a REA limitation, records
  the unknown, and proceeds — it does not retry identically. This is the one place a fork would help (§9).
- **D4. Output discipline.** All REA output lands in `.syndai/reference/` (gitignored, never committed). The
  prompt states that REA findings are for *understanding* — the agent writes its own implementation and may not
  paste decompiled or bundle-extracted source into the repo. A deterministic check (D8) enforces the visible half
  of that.

### "Learn from a product" run

- **D5. It's a New-project run with `reference_product` metadata, not a new RunKind.** Server-owned
  `metadata.reference_product = {target, kind, origin, name}` where `kind ∈ {url, artifact, package}`. Routing,
  effort and the one-turn lane are identical to any New app, so no DB CHECK migration and no ~8 branch edits. The
  existing `site_clone` kind stays for its API callers and gets the shared prompt block (D10).
- **D6. Server-side dossier before any sandbox spend.** When `reference_product` is a URL, Firecrawl reads up to
  ~15 public pages (home, pricing, product/feature pages, docs/help-centre index + top articles, changelog) in
  place of the Tavily pass; the enhancer writes a feature dossier in our own words. The user sees it through the
  existing clarify card in ≈1 minute. Help centres are the richest free spec there is. For `artifact`/`package`
  targets the dossier is produced in-sandbox by REA (static analysis) because those can't be Firecrawled.
- **D7. The user picks first features and the name.** The clarify card asks: (a) which features to build first
  (multi-select, core loop pre-checked, ≤5); (b) the product name (enhancer-suggested default). This is the one
  real decision and it sets scope and cost. Each chosen feature becomes one acceptance criterion, proven by the
  existing `criterion_proofs` — no new comparison engine. The dossier is committed as `PRODUCT.md` (our summary,
  checkboxes, no source text) so later runs do "build the next 3 from PRODUCT.md".
- **D8. "Your own version" is the default and the better product.** We build what the product *does* (features,
  flows, data model, IA) under the user's own brand, data and look + Syndai's seed/design rules. A deterministic
  **brand-separation check** at validation flags the source's brand name (as a word), its domain, or any verbatim
  12-word shingle from the scraped pages appearing in the built tree (excluding `node_modules`/`.syndai`). The
  source's name/domain/shingle hashes are stored at dossier time (~600 KB bounded). Findings go to the normal
  repair round and never hard-block (coding-runs rule). This check is what keeps the output the user's own and
  not a lookalike of someone else's site.
- **D9. Origin pre-approval.** Naming the source at intake is the consent the session-approval card collects, so
  run creation writes one `coding_session_approvals` row for that origin and `browse_external`/REA don't re-ask
  for it. Other domains still raise the card.
- **D10. `site_clone` keeps working; its dead prompt is replaced** with the shared reference block. (Its
  self-declared attestations stay as the paper trail they already are.)
- **D11. Target coverage matches our build coverage.** URL → the dossier + browser/bundle analysis. User-provided
  artifact (their own app/binary) or a package name → REA static analysis produces the dossier directly. Mobile,
  desktop and CLI targets are in scope because the template now has the engines for them (D1); the build shape
  (`project_shape`) follows what the user is building, not what the source is.

## 5. User flow (URL case)

1. Build home: user pastes a product URL (or "my own Calendly" + URL). The composer recognises a non-GitHub
   product URL the way it already recognises GitHub URLs, and offers **"Build your own version of X."** Sending
   starts a New-project run with `reference_product`.
2. Intake (≤90 s): SSRF check → Firecrawl dossier → enhancer → clarify card (summary, feature checklist with 5
   pre-checked, name field). No sandbox spend yet.
3. Confirm (or 24 h auto-proceed) → one-turn lane on the app/site seed. Prompt carries the dossier, chosen
   features, name, brand rules, and the REA block. The agent captures public flows with REA
   (`capture-browser-scenario`, with network), inventories routes/endpoints (`analyze-web-bundle`), and builds
   with realistic dummy data (house rule).
4. Validation: floors + visitor pass + one criterion proof per feature + brand-separation check. Repairs as usual.
5. Delivery: normal receipt; `PRODUCT.md` lists remaining features.

## 6. Build plan (one CaaS run)

| # | Change | Files (`Syndai/main`) |
|---|---|---|
| 1 | Install REA + Ghidra/JDK/JADX/binwalk/unblob/pwntools/GDB/mitmproxy in the template, all pinned; point REA at the baked Chromium. Template canary: `rea capture-browser-scenario` on a local fixture, exit 0; `rea doctor` green. | `services/e2b_base_template.py`, `services/coding_toolchain.py` |
| 2 | REA prompt block (~15 lines, inlined) in the standing run preamble: REA exists, reads shipped artifacts and live targets, route by target type, `rea <cmd> --help` on demand, write `--json` to `.syndai/reference/`, findings are for understanding not copying. No bridge (D2). | `C/one_turn_lane.py:266` + the resume preamble |
| 3 | `.syndai/reference/` added to the seed `.gitignore`. | `C/scaffold_seed/{site,app}/.gitignore` |
| 4 | `reference_product` server-owned metadata: parse, SSRF-check, derive origin/name/kind; reject on non-New-project runs. | `C/external_controller_utils.py:90`, `C/dtos.py`, `C/run_creation.py` |
| 5 | Dossier source: Firecrawl map+scrape for URL targets (D6); REA static dossier for artifact/package targets; store brand/origin/shingle hashes for D8. Grep for the existing run-artifact store before adding one. | `C/context_research_gatherer.py`, `knowledge/firecrawl.py` |
| 6 | Enhancer reference suffix: dossier → one criterion per chosen feature, `project_shape`, always-escalate with the two D7 questions. | `C/prompt_enhancer.py`, `C/intake_clarify.py` |
| 7 | Brand-separation check wired into validation findings → repair. | new `C/reference_separation_floor.py` + call site |
| 8 | Origin pre-approval at run creation (D9). | `C/capability_runtime_external.py` seam, `C/run_creation.py` |
| 9 | `site_clone` prompt → shared reference block (D10). | `C/site_clone_service.py` |
| 10 | Build home: product-URL / artifact detection + "Build your own version of X" affordance posting `reference_product`; update `NAVIGATION.md` if the intent param changes. | `apps/portal-v2/src/lib/build-intent.ts`, `components/composer-page.tsx`, `sync/run-submit.ts` |

**Fork-vs-pin: see the drill-down in §9.** Short version: pin upstream by exact version, use the CLI, do not
fork; contribute any fix we need as an upstream PR.

## 7. Acceptance (prove before done)

REA integration:
1. `rea doctor` green in a fresh sandbox; the canary scenario exits 0.
2. A run can run `rea --help` → `rea <command> --help` → `rea <command> --json > .syndai/reference/<name>.json`
   (CLI only, no MCP bridge — D2) and read a scenario capture with network entries from
   `.syndai/reference/`, which is absent from git.
3. Standing prompt cost of the REA block is ≤ ~1 KB after turn 1 (measured).
4. One run each proving a native, a JS-app, and an Android target analysis returns structured findings.

"Learn from a product": run 3 live from portal-v2 in your-own-version mode — a booking app (cal.com), a form
builder (tally.so), an analytics product (plausible.io):
5. Dossier card appears ≤90 s, before any sandbox turn.
6. Every checked feature becomes a passing criterion proof.
7. Brand-separation check runs, 0 findings on the final tree. Mutation: paste one source paragraph in → red →
   green after repair.
8. `PRODUCT.md` committed with remaining features unchecked.

Cost to prove: ~3 app runs + Firecrawl cents + a one-off template rebuild.

## 9. Drill-downs

### 9a. Why no bridge (the delivery mechanism)

The patchright `browser` MCP bridge is the obvious template to copy, so I checked what it actually does and
whether REA needs the same. The bridge (`C/executors/patchright_bridge_runtime.py`) is a standalone stdlib-only
script uploaded into the sandbox; each tool call POSTs `run_skill` with a `capability_id` to a backend callback
URL + token (`_governance_callback`, :115), which runs the SSRF fence and the session-approval HITL card before
the fetch proceeds. That governance is the bridge's entire reason to exist.

For REA it collapses, because **the thing the bridge gates is already ungated for every other process:**

| Concern | Bridge handles it via… | But for REA via CLI… |
|---|---|---|
| SSRF to private/metadata ranges | backend re-check | Already enforced at the **provider egress layer** for all processes (`run_egress.py`, `coding_network_policy.py:38`), regardless of transport. `curl`, `rea`, and the bridge are equal here. |
| Reaching public internet at all | session-approval card | Open internet is the **default** (`:38`); `curl` is installed (`coding_toolchain.py:159`). The agent can already fetch anything. A card on `rea` only is theater. |
| Schema cost | bridge owns 4 small schemas | CLI puts **zero** schemas in the prompt; `rea <cmd> --help` is read on demand. |
| Big-output crashes | — | Bridge does **not** help: the crashes are inside REA's process (#1331/#1380) and hit both faces. `--json > file` is the mitigation on either path. |
| Audit | `run_skill` log | Shell transcript already records every command. |

So the bridge is ~5 new sandbox modules plus harness wiring (the patchright bridge is 9 files) to reproduce a
boundary `curl` already crosses freely. That fails rung 1 of the ladder — it doesn't need to exist. If we later
want to **meter** REA separately or force **every** REA external fetch through the consent card (a product
decision, not a security one), the thin 4-tool bridge in the git history of this spec is the way to add it; it is
not needed to ship.

One real consequence to state plainly: with CLI delivery, REA's Chromium fetches do **not** raise the
session-approval card. For the "learn from a product" run the origin is user-named at intake (consent already
given, D9). For general REA use the targets are overwhelmingly local artifacts (no egress). This matches how the
sandbox already treats `curl`. I'm choosing that consistency over a card that only looks like a gate.

### 9b. Fork vs. use-as-is

**Decision: use `rea-agents` as-is, pinned to an exact version, via the CLI. Do not fork. Upstream any fix we
need as a PR.**

Why not fork:
- The scary problems are MCP-path-only (530k-token schemas, strict-provider 400s in #1212/#1282/#1329). We're on
  the CLI, so they never touch us. A fork to fix them would fix bugs we don't have.
- REA ships a release every few days (1,886 commits in 6 months; npm `latest` was *behind* `main` in #1327). A
  standing fork of that is a permanent merge-conflict treadmill — the single most expensive way to consume a
  fast-moving MIT dep, for no feature we're missing.
- It's MIT and genuinely maintained, with an active external-PR flow (17 open PRs from non-maintainers right
  now, e.g. #1363 Go build metadata, #1362 MIPS, #1356 SQLite). Upstreaming is a live option.

What *would* justify a fork, and the response to each:
- A big-output crash (#1331/#1380) reliably aborts analysis of targets our users actually hand us (large JS
  bundles are plausible). → First try the `--json > file` path; if it still aborts, **file the issue and open a
  PR** (these are small, bounded parser fixes). Only if upstream stalls on a bug that blocks a shipping feature
  do we cut a fork — and then a **thin overlay** (patch + rebuild on top of a pinned upstream tag), never a
  long-lived divergent tree, with the patch sent upstream the same day.
- We need a capability upstream won't take. → Unlikely for our use (we use existing analysis, not new providers),
  but same overlay-plus-PR path.

Operational pin: exact version in `coding_toolchain.py`, a template canary (`rea doctor` + one real
`capture-browser-scenario` on a fixture, exit 0) so a bad bump is caught at image-build time, not mid-run. Bump
on purpose, read the changelog for the wire-format breaks REA is known for.

## 11. Sandbox compatibility (it runs server-side, not on a laptop)

"Local" in REA's docs means local to the machine REA runs on — for us, the **E2B sandbox**, where the coding
agent already lives. REA is a plain npm CLI; it installs beside `gh`/`rg`/`playwright`. Nothing touches a user
machine. The template is **Debian 13 x64, Node 24, Playwright + Chromium baked** (`e2b_base_template.py`), and
Linux x64 is REA's primary CI lane, so the fit is clean:

| REA capability | Needs | In our sandbox? |
|---|---|---|
| Websites / browser scenarios | a Chrome-family browser, headless | **Already there** (Chromium baked; `capture-browser-scenario` defaults `headless:true`) |
| JavaScript / Electron, .NET, EVM, HAR, offline ELF layout | Node + npm only | **Already there** |
| Native decompile (ELF/PE/Mach-O/DOS) | Ghidra + JDK, headless | **Add** (build-plan #1). Ghidra is the Linux path; Hopper is macOS-GUI and is skipped, IDA needs a licence and is skipped |
| Android APKs | headless JADX + JDK, Linux x64 | **Add** (#1). The template already layers the Android SDK/JDK separately, so this is a known pattern |
| Firmware | binwalk / unblob, Linux | **Add** (#1) |
| Crash / ELF diagnostics | pwntools, optional GDB | **Add** (#1) |
| Deep Apple-app analysis, native-UI observation | macOS + GUI | **Out of scope** — macOS-only; static Mach-O still works through Ghidra |

So every target type except the macOS-GUI-only ones runs headless, server-side, in the sandbox we already ship.

**Modal and the iOS/macOS runners (Sid's note):**
- **Modal** is the in-place resource-escalation lane (`resource_escalation.py:180,330`; E2B→Modal on resource
  exhaustion, or the heavier mobile image). This matters for REA: **Ghidra is memory-hungry**, and a native-analysis
  job that would OOM an E2B box now escalates to Modal in place instead of dying. So "add Ghidra" (build-plan #1)
  has real headroom behind it, not just the default E2B tier. Nothing REA-specific to build — it inherits the
  existing escalation.
- The **Namespace iOS lane** (`ios_build_descriptor.py`, `ios_namespace_provider.py`) is a **macOS build
  executor**: you POST a locked-down descriptor (`runner: "xcode"`, allowlisted fields, zero customer secrets)
  and it builds/validates. It is **not** an interactive agent sandbox with a shell, so we cannot drop the `rea`
  CLI into it and drive it — and Hopper (a licensed GUI app) could not be automated there anyway. The good news
  is we don't need it: REA's **static** Apple analysis (Mach-O, IPA/`.app` inventory, plists, asset catalogs,
  keyed archives, Swift/ObjC metadata) runs **headless through Ghidra on the Linux E2B/Modal box**. Only *live*
  macOS-GUI observation is out, which no build task needs. If a future task genuinely needs live iOS runtime
  observation, that's a new lane on the macOS host, out of scope here.

## 10. The prompt blocks (verbatim)

Two blocks. **Block A** (REA exists) is universal — it rides the standing preamble so every run kind can reach
REA. **Block B** (reference product) is added only when `reference_product` is set. Both match the lane voice:
terse, imperative, lowercase, `--json` to a file, no filler.

### Placement and cost

- Block A goes in the resume preamble that is already inlined on turn 0 and cached thereafter (the same place the
  reuse-before-build step lives), **not** in `_directive` (which is one-turn-lane only) — repair and feature runs
  must see it too. Measured budget: **≤120 tokens**, cached after turn 1, so standing cost ≈ 0.
- Block B is appended by `build_prompt`/`lane_first_prompt` when `spec_data["reference_product"]` is present, after
  the brief and before the seed guide. It interpolates the dossier path, the chosen features and the name.
- Neither block names a tool schema: the agent reads `rea <command> --help` on demand, so adding REA costs the
  prompt nothing per command.

### Block A — universal (always present)

> **Reading an existing artifact or a live product.** `rea` (Reverse Engineer Anything) is on the PATH. Reach
> for it only when a claim depends on something you cannot read from this repo's own source — a shipped bundle,
> a binary or package, an APK/IPA, a `.NET` assembly, firmware, a HAR, or a running site. For ordinary work in
> this repo, use the normal file and search tools; do not run `rea`.
>
> Route by what you were given, then read `rea <command> --help` for its flags:
> - a URL or running page → `rea capture-browser-scenario` (drives public flows, records DOM, a11y and network)
>   and `rea analyze-web-bundle` (routes and endpoint candidates from shipped JS);
> - a local app directory or ASAR → `rea analyze-javascript-application`;
> - an APK → `rea inspect-android-package`; a `.NET` PE → `rea inspect-managed-members`; a native binary →
>   `rea inspect` / `rea decompile`; firmware → `rea inspect-firmware-regions`.
>
> Always write output to a file: `rea <command> … --json > .syndai/reference/<name>.json`, then read slices with
> `rg`/`jq` — results can be large. `.syndai/reference/` is gitignored; it is scratch, never part of the build.
>
> REA findings tell you **how something behaves**. Build your own implementation from that understanding — never
> paste decompiled or bundle-extracted source into the repo. Every REA result separates observed, inferred and
> unknown; carry that distinction into what you write, and never claim static analysis observed runtime
> behaviour. If a `rea` command fails or aborts on a target (a huge or malformed input can), record it as an
> unknown and move on; do not retry it unchanged.

### Block B — reference product (only when `reference_product` is set)

> **You are building the customer their own version of {name}.** A feature dossier is at
> `.syndai/reference/PRODUCT.md` — Syndai's own summary of what {name} does, written from its public pages. Build
> these features first, each a checkbox in that file:
> {chosen_features, one per line}
>
> Work from the dossier, the customer's brief, and any screenshots they attached. Use the Block-A `rea` commands
> against {origin} to see real flows and data shapes when the dossier leaves a gap — {origin} is pre-approved, so
> `browse_external` and `rea` will not pause for it. Do **not** sign in to {name} or enter any credentials for it;
> public surfaces only.
>
> This is the customer's product, not a copy of {name}'s. Use the customer's name, your own copy, your own dummy
> data (realistic, never placeholder), and the project's design system. Do not reproduce {name}'s brand name, its
> domain, its logo or images, or its wording. A validation check flags the source's name, domain or any verbatim
> wording it finds in your tree and sends those back for repair (logos and images are binary, so that check can't
> see them — keeping them original is on you). The fix is to make it the customer's own, not to paraphrase.
>
> Keep `PRODUCT.md` current: check off what you ship, leave the rest for a later run.

### Why this wording (the non-obvious choices)

- **"Reach for it only when…"** mirrors the existing reuse-before-build gate. Without the negative clause the
  agent runs `rea` on this repo's own source, which is waste — REA is for artifacts you *can't* read as source.
- **`--help` on demand, not a flag catalogue.** Keeps the block tiny and dodges the 530k-token schema problem by
  construction.
- **"write output to a file … results can be large"** is the §3b crash mitigation stated as a habit, not a rule
  the agent has to be told the reason for.
- **"never paste decompiled or bundle-extracted source"** is the one line the brand-separation floor (D8) backs
  with teeth; the floor catches verbatim strings, the line catches the intent.
- **"record it as an unknown and move on"** follows the house rule that coding runs never dead-end; a flaky REA
  command must not stall the build.
- Block B says **pre-approved** out loud so the agent doesn't wait for a session card that will never come (D9),
  and says **no sign-in** because that is the one external action that is genuinely off-limits.

## 12. Brand-separation check (D8), in full

A deterministic static check that flags the build for repair when the output reproduces the **source product's**
brand name, domain, or verbatim text (never a hard block — see "How it reports" below) — the teeth behind "build
your own version, not a copy" (D8) and the Block-B "never paste source" rule. It sees text only: binary assets
(a copied logo or image) are excluded by the NUL-byte sniff and are the agent's responsibility, not this check's. It is a Python check in the validation stage, **not** a browser `.mjs` floor (those crawl
rendered pages; this scans the repo tree), and it emits findings in the **same shape the floors do** so the
existing repair round consumes them unchanged.

### What's stored at dossier time

When the dossier is built (build-plan #5), alongside `PRODUCT.md` we persist a small `reference_fingerprint` on
the run's spec_data (grep for the run-artifact store first; do not add a table):

```
reference_fingerprint = {
  "brand_terms":  ["Cal.com", "Cal"],          # source names, from the page <title>/og:site_name + the URL host label
  "domains":      ["cal.com", "app.cal.com"],   # the source origin host(s), registrable domain + observed subdomains
  "shingles":     [<8-byte hashes>],            # 12-word verbatim runs of the scraped prose (see below)
  "shingle_n":    12,
}
```

- **brand_terms:** the product name as a customer would write it, plus the bare host label. Derived, not
  hand-typed. Lowercased at compare time.
- **domains:** the registrable domain and any subdomain actually seen while scraping. Exact, no inference.
- **shingles:** every scraped page's visible text is normalized (NFKC, lowercase, strip punctuation to spaces,
  collapse whitespace), split to word tokens, and every sliding window of 12 tokens is hashed
  (`blake2b(…, digest_size=8)`). The set is stored. Bound: ≤15 pages × ~2k words ≈ 30k shingles × 8 B ≈ **240 KB**,
  well under the 600 KB ceiling. Pages with <12 words contribute nothing.

### What it scans

At validation, over the built worktree:

- **Include:** tracked text files the build ships — source, templates, markdown, JSON/YAML content, `.astro`,
  `.tsx`, CSS. Use the repo's own ignore rules.
- **Exclude:** `node_modules/`, `.syndai/` (the reference scratch lives here by design), `.git/`, lockfiles
  (`pnpm-lock.yaml` etc.), `dist/`/build output, and anything binary (NUL-byte sniff). `PRODUCT.md` is excluded
  too — it is Syndai's own summary and is *allowed* to name the source.

For each included file:
1. **domain hit** — any `domains` entry as a substring (hosts can't false-positive). Highest severity.
2. **brand hit** — any `brand_terms` entry as a whole word (`\b`, case-insensitive).
3. **shingle hit** — normalize the file's text the same way, window into 12-token shingles, intersect with the
   stored set. Any intersection is a verbatim-copy hit; report the matched phrase (reconstructed from the file,
   not stored).

### Precision (the part that matters)

Three signals, deliberately different strengths:

- **domain and shingle are high-precision.** A 12-token verbatim run matching is essentially never accidental, and
  a domain string is unambiguous. These are the real teeth.
- **brand name is a softer signal** because a name can be an ordinary word ("Linear", "Monday", "Cal", "Notion").
  We do **not** build a brand-disambiguation engine (that's the over-engineering trap). Instead the whole check is
  **non-blocking** (next section), so a rare brand-name false positive costs one repair-round glance, not a failed
  build. We state the source name in the finding so the agent can see at a glance whether it's a real leak or the
  English word.
  - One cheap guard worth having: skip a brand term that is a top-English common word *and* ≤4 chars unless it
    appears adjacent to a `domains` hit or a shingle hit. `ponytail: a 30-word stoplist, not an NLP model; widen
    only if a real brand name keeps slipping through.`

### How it reports and what happens

- Emits one finding per hit in the floors' finding shape: `{ check, target (file:line), detail, fix }`. The `fix`
  line, keyed into the same hints map the floors use:
  > `reference-leak = this ships the source product's {name|domain|wording}: "{match}" in {file}. Make it the
  > customer's own — their name, your words, your dummy data. Do not paraphrase the source; write the customer's.`
- Findings go into the **normal validation→repair round**, exactly like a floor failure. Following the
  coding-runs rule (`feedback_coding_runs_never_block_self_heal_or_hitl`), it **never hard-blocks** a run: a
  residual hit after the repair budget is surfaced on the receipt, not a dead end.
- It runs on the **final tree** each validation pass (like the other floors), so a repair that reintroduces a leak
  is caught.

### The one runnable check it leaves behind (ponytail rule)

`test_reference_separation.py`, assert-based, no fixtures:
- a tree containing the source domain → 1 domain finding;
- a tree with a 12-word source paragraph pasted in → ≥1 shingle finding, with the phrase in `detail`;
- the same paragraph with three words changed in the middle → the untouched 12-word windows still hit (shingles
  overlap), proving paraphrase-at-the-edges doesn't defeat it, while a fully reworded version → 0 (we are not
  trying to catch semantic copies);
- `node_modules/` and `.syndai/` and `PRODUCT.md` copies of the brand → 0 (exclusions hold).

This is also the §8 acceptance mutation test (paste a source paragraph → red → green after repair), so it's one
test doing double duty.

## 13. The dossier: Firecrawl page selection (D6), in full

The dossier is produced server-side, before any sandbox spend, as a **new lane inside `gather_context_research`**
that *replaces* the Tavily web lane when `reference_product.kind == "url"`. It reuses every existing seam: the
Firecrawl singleton client, the content-limit helper, the spec_data bucket the enhancer already reads
(`CONTEXT_RESEARCH_SPEC_KEY` + a sibling `reference_dossier` bucket), and the enhancer's own model for synthesis.

### One small client addition

The existing `knowledge/firecrawl.py` has `scrape_url` (URL → markdown, SSRF-checked, content-limited, 24 h
cache) but **no map/crawl**. Add one thin method, `map_urls(origin, *, search=None, limit=100)`, wrapping the
Firecrawl SDK's `map` endpoint (returns the site's discoverable URLs; the `search` param biases toward a term).
That's the whole new surface — everything else is `scrape_url` reuse. `ponytail: map is one SDK call and is
built for exactly this; reconstructing the link graph by parsing the homepage is more code and worse.`

### Step 1 — discover candidate URLs

1. SSRF-check the origin (`validate_external_read_url`, the same chokepoint the cloner uses).
2. `map_urls(origin)` → candidate URL list. Cap at 100.
3. If `map` returns < 3 URLs (a hard SPA or a login wall), fall back to **homepage-only**: scrape `/` and record
   `coverage: "thin"` so the dossier can say "most of this product is behind a login; here's what's public."

### Step 2 — pick the pages (deterministic, ≤15)

Rank candidates by **page intent**, not by crawl order, and take the top ≤15. Each intent is a set of
case-insensitive path/label patterns; a URL scores by the highest-priority intent it matches. One URL per intent
slot unless noted, always including `/`.

| Priority | Intent | Matches (path or nav label) | Slots |
|---|---|---|---|
| 1 | Home | `/` (exact) | 1 |
| 2 | Pricing | `/pricing`, `/plans`, "pricing", "plans" | 1 |
| 3 | Product/features | `/features`, `/product`, `/solutions`, `/how-it-works`, "features" | up to 3 |
| 4 | Docs / help index | `/docs`, `/help`, `/guide`, `/support`, `/getting-started` | 1 |
| 5 | Top help/docs articles | children of a priority-4 path | up to 5 |
| 6 | Changelog / what's new | `/changelog`, `/releases`, `/whats-new`, `/updates` | 1 |
| 7 | About / use cases | `/about`, `/use-cases`, `/customers` | up to 2 |

Tie-break within an intent: shortest path first (canonical pages sit near the root), then alphabetical for
determinism. Drop obvious non-spec pages up front: blog posts, careers, legal/privacy/terms, auth routes
(`/login`, `/signup`), locale duplicates (keep the first locale seen), and asset/API paths. Help-centre articles
(priority 5) are kept generously because **help docs describe every feature and its edge cases — the richest free
spec there is** (D6).

### Step 3 — scrape

Scrape the selected URLs through `scrape_url` (markdown, content-limited), **bounded-parallel** (e.g. 5 at a
time) so wall-clock stays near one slow page, not fifteen serial ones; the 24 h cache absorbs re-runs. A page
that fails or times out is skipped and noted; a run never fails because one page 500s.

### Step 4 — synthesize the dossier (one LLM call)

Unlike the Tavily lane (one summary call *per source*), the reference lane makes **one synthesis call** over all
scraped markdown (bounded, e.g. ≤60k chars total, per-page truncated) using the enhancer's model. It is forced to
a struct that yields:

- `what_it_is` — one plain-English paragraph.
- `core_loop` — the product's primary loop in one line ("share a link → guest picks a slot → both get an invite").
- `features` — a list of `{name, one-line description, source_page}`, core-loop features first.
- `suggested_name` — a candidate name for the customer's product (the clarify-card default).
- `coverage_notes` — what was behind a login / not seen.

From this the lane writes two artifacts into spec_data buckets:
- **`PRODUCT.md`** (committed into the repo at build time): the summary + a `## Features` checklist, **in our own
  words, with no source text** (so it never trips §12). Core-loop items pre-checked.
- **`reference_fingerprint`** (§12): `brand_terms` from the scraped `<title>`/`og:site_name` + host label;
  `domains` = origin host(s) actually scraped; `shingles` computed from the raw scraped text (not the summary).

The feature list also becomes the clarify card's multi-select (D7), core loop pre-checked, ≤5 shown first.

### Cost and timing

Per URL dossier: 1 `map` + ≤15 `scrape` (Firecrawl, cents, cache-assisted) + 1 LLM synthesis call. Target
wall-clock ≤90 s (§8 acceptance 5), inside the enhancer's existing `_CONTEXT_RESEARCH_TIMEOUT_SECONDS = 60` per
lane budget plus scrape parallelism — if synthesis would blow the budget the lane writes a thin dossier and the
clarify card still appears (best-effort, like the Tavily lane: a slow research lane never zeroes the brief).

### Non-URL targets

For `reference_product.kind ∈ {artifact, package}` there is nothing to Firecrawl. The dossier is produced
**in-sandbox** by REA static analysis on turn 0 (routes/features from `analyze-javascript-application` or the
managed/Android inspectors), written to `PRODUCT.md` the same way, and the fingerprint is skipped (no scraped
brand/text to separate against — the separation check only arms for URL sources).

### The runnable check it leaves behind

`test_reference_page_selection.py`, assert-based: given a fixed candidate URL list (home, pricing, two feature
pages, a docs index with six articles, a changelog, a blog post, `/login`, a privacy page, a `/fr/` duplicate),
the picker returns exactly the expected ≤15 with blog/login/privacy/locale-dupe dropped and the intent priority
respected. Pure function, no network.

## 8. Not in v1 (trigger to add)

- HAR upload of the user's own logged-in session (REA `inspect-web-network-capture`, server-side credential
  stripping) — add when thin-public-surface products show up in the first 20 runs.
- Visual parity diff (`compare-web-screenshots`) — only meaningful for faithful `site_clone`, add on first
  migration customer.
- "Next feature" chip on the project page from `PRODUCT.md` — add once follow-ups get used.
- Forking REA — only if an upstream block appears (§6).

---

## Implementation note (this checkout)

This repository is the open-source `feature-flow` Claude Code plugin, not the private `Syndai/main` monorepo the
spec targets. The spec's §6 build-plan items 1, 2, 3, 4, 5, 6, 8, 9 and 10 edit backend modules
(`services/e2b_base_template.py`, `backend/src/features/coding/*`, `knowledge/firecrawl.py`,
`apps/portal-v2/*`) that do not exist in this checkout and cannot be authored against absent runtime. What lands
here are the two self-contained, deterministic pure functions the spec isolates and mandates named tests for:

- `backend/src/features/coding/reference_page_selection.py` — §13 step 2 page picker, covered by
  `tests/test_reference_page_selection.py`.
- `backend/src/features/coding/reference_separation_floor.py` — §12 brand-separation scan + fingerprint helpers,
  covered by `tests/test_reference_separation.py` (including the §8 paste-paragraph red→green mutation).

Both are network-free, match the spec's module names so they drop into `C/` unchanged, and run via the pytest
config added in `pyproject.toml`. The runtime wiring (template install, prompt blocks, metadata parsing, dossier
lane, validation call site, origin pre-approval, portal affordance) is the remaining work for the monorepo.
