# ArcFlash Reference Adoption Plan

This is a multi-agent hardening plan for selectively applying useful ideas from the local [`Reference/arcflash`](Reference/arcflash) study to the ETAP MVP.

It does not expand the product. [`README.md`](README.md) remains the product source of truth, and [`MVP_MULTI_AGENT_PLAN.md`](MVP_MULTI_AGENT_PLAN.md) remains the delivery plan. This document adds reliability and evidence-integrity requirements to those phases.

No ArcFlash source code will be copied. The reference repository has no visible license file, and it uses an older H SDK and a hosted-browser architecture. We will adopt concepts through original implementation against `hai-agents` 1.0.6 local desktop control.

## Objective

Harden this exact workflow:

```text
OPEN_PROJECT -> LOAD_FLOW -> COORDINATION -> ARC_FLASH -> REPORT
```

The hardening must ensure:

- a checkpoint is attached to the actual H session that executed it;
- success is backed by the expected visible observation and valid evidence;
- cancellation finishes before reset or retry starts;
- retries cannot consume stale results from an earlier attempt;
- screenshots cannot be substituted across sessions or written outside the evidence root;
- missing evidence is disclosed instead of hidden;
- every report page is visibly marked as a draft.

## Scope boundary

### Adopt

- actual session attachment truth;
- one-active-desktop-session enforcement;
- cancel-before-reset/retry ordering;
- bounded vendor and local execution;
- typed checkpoint commands and deterministic prompts;
- observation-backed completion;
- session-owned screenshot provenance;
- bounded and atomic evidence writes;
- optional per-session event cursors only if required by the H API path used;
- explicit missing-evidence report semantics;
- draft notice on every report page;
- failure-injection tests.

### Do not adopt

- React/FastAPI dashboard architecture;
- hosted-browser or public web-app control;
- deterministic user-facing replay;
- Electrisim;
- Gradium voice;
- NemoClaw deployment or cloud-host policy files during the local MVP;
- DigitalOcean deployment;
- open-source or custom electrical calculations;
- engineering interpretation, recommendations, or approval workflows;
- multiple users, hosts, sessions, projects, or dynamic studies;
- generalized event buses, queues, databases, telemetry, plugins, or attachment systems;
- hosted screenshot S3/SigV4 redirect logic unless the local H 1.0.6 resource API demonstrably uses that flow.

## Added requirement IDs

| ID | Requirement | Parent README requirement |
| --- | --- | --- |
| R10 | A run is attached only after H returns a nonempty session ID; results and resources must belong to that session. | R3, R5, R9 |
| R11 | `completed` requires the checkpoint-specific visible observation and a valid owned screenshot. | R3, R5 |
| R12 | Reset or retry starts only after cancellation reaches a terminal state or records a bounded cancellation failure. | R4, R8, R9 |
| R13 | Missing, unreadable, or rejected evidence is shown explicitly in the report. | R5, R6 |
| R14 | Every report page contains the exact notice `Draft - engineering review required`. | R6 |
| R15 | Vendor execution and evidence downloads are bounded, validated, and mapped to stable local errors. | R3, R5, R9 |

## Agent team and ownership

| Agent | Exclusive ownership | Required outputs |
| --- | --- | --- |
| Lead/integration agent | Requirement traceability, interface approval, merge gates, final scope audit | Decisions, integration review, phase acceptance |
| Foundation/contracts agent | `src/etap_automation/models.py`, `settings.py`, new path/persistence modules | Run layout, immutable attempt identity, atomic checkpoint persistence |
| H operator agent | `src/h_operator/` | Typed commands, prompt contracts, SDK 1.0.6 mapping, session/evidence ownership |
| Workflow agent | New `src/orchestrator/` | Fixed state machine, cancellation/retry sequencing, five-outcome status |
| Reporting agent | New `src/reporting/` | Evidence-driven draft report and validation |
| Verification agent | `tests/` and fixtures | Contract, failure-injection, integration, and acceptance tests |

Rules:

- Only the foundation agent changes persisted schemas.
- Only the H operator agent changes H prompts or SDK mapping.
- Only the workflow agent decides order, stop, retry, and resume behavior.
- Reporting never calls H or ETAP.
- The orchestrator never imports `hai_agents`; it consumes a checkpoint-runner protocol.
- Live H/ETAP work is sequential. Offline agents may work in parallel against frozen protocols and fakes.
- No agent may add a feature from the “Do not adopt” list.

## Target dependency flow

```text
StudyPlan + RunLayout
        |
        v
CheckpointCommand -> HCheckpointRunner -> OperatorOutcome
        |                                  |
        +------------ Orchestrator <-------+
                           |
                           v
                atomic CheckpointResult
                           |
                           v
                    ReportGenerator
```

Data flows in one direction. Reporting cannot start H, and the H adapter cannot advance workflow state.

## Wave A - Lock the H 1.0.6 boundary

Maps to original Phase 0.

### H operator agent tasks

1. Pin `hai-agents[desktop]==1.0.6` instead of a floating `1.0.x` range.
2. Add an SDK-surface contract test for the exact synchronous methods used:
   - `create_session`;
   - `get_session_status`;
   - `get_session`;
   - `get_session_resource`;
   - `cancel_session`.
3. Construct the H client using project `Settings.hai_region` and the protected secret value.
4. Forward fixed `max_steps` and `max_time_s` to H session creation.
5. Retain the local timeout and cancellation because H bounds request an answer rather than guaranteeing a hard kill.
6. Reject nonpositive or out-of-policy bounds.
7. Normalize malformed SDK response objects into stable `HOperatorError` subclasses without leaking raw secrets or vendor exceptions.

### Verification agent tests

- installed SDK version is exactly 1.0.6;
- required SDK methods and argument names exist;
- US and EU settings select the correct H environment;
- the API key never appears in `repr`, logs, exceptions, results, or fixtures;
- exact `max_steps` and `max_time_s` are forwarded;
- invalid limits fail before session creation;
- missing status, unknown status, missing answer, and malformed answer map to stable failures;
- create failure, empty session ID, polling failure, and cancellation failure always release ownership.

### Gate A

All existing Phase 0 tests remain green. The SDK surface is pinned and verified without starting a live desktop session.

## Wave B - Typed checkpoint and prompt contracts

Maps to original Phase 0 contracts and Phases 1-4 implementation.

### H operator agent files

- `src/h_operator/contracts.py`
- `src/h_operator/prompts.py`
- `src/h_operator/checkpoints.py`

### Required interfaces

```text
CheckpointCommand
  step
  project
  project_file
  study_case | view
  max_steps
  max_time_seconds

OperatorOutcome
  step
  session_id
  vendor_state
  observed_identity
  screenshot_key
  error_code
  error
```

`CheckpointCommand` must reject irrelevant selectors and additional fields. For example, `COORDINATION` accepts only `view=Main Bus - Feeder 1`; it cannot accept a study case or calculation action.

### Prompt contract

Create one deterministic prompt template for each UI checkpoint:

- `OPEN_PROJECT`: open the approved ANSI `.OTI`, visibly verify `EXAMPLE`, take evidence, and stop on mismatch.
- `LOAD_FLOW`: select only `Base Case`, run ETAP Load Flow, show the result view, take evidence, and stop.
- `COORDINATION`: open only the existing `Main Bus - Feeder 1` Star coordination view, take evidence, and stop; do not invent a run action.
- `ARC_FLASH`: select only `Normal Operation`, run ETAP AC Arc Flash, show the result view, take evidence, and stop.

Every prompt must state:

- visible UI actions only;
- no model, equipment, setting, or library edits;
- no electrical calculation or interpretation outside ETAP;
- no additional studies;
- exact expected visible identity;
- screenshot and structured answer required;
- claim success only when the final observation confirms the expected state;
- stop and return a failed result on mismatch.

### Verification agent tests

- golden prompt for each checkpoint;
- exact project, case, and view values present;
- prohibited actions and extra study names absent;
- structured answer schema is attached;
- expected step is stored with the session;
- a schema-valid answer for a different step is rejected;
- `completed` requires nonempty screenshot key and no error;
- `failed` requires a nonempty error;
- unknown fields/statuses are rejected.

### Gate B

Four prompt contracts and typed commands pass offline tests. No workflow sequencing or live UI implementation is added by this wave.

## Wave C - Session attachment and cancellation ownership

Maps to original Phases 0-2 and is reused by Phases 3-6.

### H operator agent tasks

- Treat a run as attached only after receiving a nonempty session ID.
- Persist the actual H session ID in `OperatorOutcome`.
- Reject result or resource session IDs that differ from the active/owning session.
- Make cancellation idempotent.
- Preserve the primary timeout error if vendor cancellation also fails.
- Add an instance lifecycle lock and cancellation event so `cancel()` cannot release the global desktop lease while `wait()` is still using it.
- Do not allow session B until waiter/session A has exited.

### Workflow agent tasks

- Implement `cancel -> terminal acknowledgement -> reset -> retry` ordering.
- If cancellation does not reach terminal state inside the bound, record failure and do not reset, retry, or advance.
- Retry receives a new session and attempt ID.
- Prior evidence remains immutable.

### Verification agent tests

- empty/missing session ID rejects attachment;
- answer and resource session IDs must equal the owner session;
- repeated cancel is deterministic;
- timeout plus cancel failure preserves timeout as primary;
- concurrent wait/cancel never releases the desktop lease early;
- a second run is blocked until the first waiter exits;
- retry call order is exactly cancel, terminal confirmation, reset, new session;
- cancellation pending or failed blocks retry and all downstream checkpoints;
- late completion from the cancelled session cannot complete the retry.

### Gate C

Threaded and deterministic fake tests prove no overlapping desktop ownership and correct cancellation/retry ordering.

## Wave D - Run layout and atomic persistence

Maps to original Phases 0-2.

### Foundation agent files

- `src/etap_automation/paths.py`
- `src/etap_automation/persistence.py`

### Interfaces

```text
RunLayout.create(evidence_root, report_root, now)
RunLayout.checkpoint_json(step, attempt)
RunLayout.screenshot_png(step, attempt)
RunLayout.report_path()

write_checkpoint_atomic(result, path)
read_checkpoint(path) -> CheckpointResult
```

Requirements:

- timestamped run ID and attempt ID;
- paths only under configured evidence/report roots;
- retries never overwrite earlier evidence;
- write sibling temporary file, flush, fsync, and replace atomically;
- reject collision unless a newly allocated attempt path is used;
- re-read and validate final JSON before exposing completion;
- no database.

### Verification agent tests

- traversal, absolute escape, and symlink escape rejected;
- run/attempt IDs cannot collide under deterministic clock fixtures;
- partial JSON is never accepted;
- simulated write/replace failure cleans temporary files;
- retry creates a new attempt path;
- earlier evidence bytes and hashes remain unchanged;
- restart from stored records yields the same current state.

### Gate D

Every persisted result and screenshot has an immutable, allowlisted attempt path, and interrupted writes cannot appear completed.

## Wave E - Screenshot provenance and integrity

Maps to original Phase 0 and UI checkpoint Phases 1-4.

### H operator agent tasks

- Accept only the `screenshots` resource bucket.
- Require the resource session ID to equal the completed owner session.
- Require the resource key to equal the key returned by the validated structured answer.
- Validate key with a conservative basename-only PNG pattern; reject slashes, backslashes, `..`, controls, and absolute paths.
- Enforce a maximum payload size of 5 MiB before writing.
- Use bounded streaming if H 1.0.6 returns a stream; otherwise reject oversized materialized bytes.
- Validate a real PNG: signature, IHDR, sane nonzero dimensions, and terminal IEND.
- Write atomically to the `RunLayout` path and never overwrite.
- Return evidence metadata: session ID, key, byte size, timestamp, and SHA-256 integrity digest.
- Never store the API key, authorization header, or presigned source URL.

### Verification agent tests

- correct owned screenshot succeeds;
- foreign session, arbitrary key, wrong bucket, wrong extension, traversal, and control characters fail;
- zero bytes, truncated PNG, absurd dimensions, missing IEND, and mislabeled content fail;
- exactly 5 MiB is accepted if otherwise valid; 5 MiB plus one byte fails;
- validation or fetch failure leaves no output or temporary file;
- collision fails rather than overwriting;
- evidence metadata matches stored bytes;
- no credential or source URL appears in JSON.

### Hosted-browser exclusion

Do not implement ArcFlash's H screenshot S3 host, SigV4 redirect, or URL proxy logic unless the selected local desktop H 1.0.6 resource method actually returns that shape. Local desktop evidence should use the documented session resource API by bucket and key.

### Gate E

Only a valid, owned, bounded screenshot can support checkpoint completion.

## Wave F - Observation-backed checkpoint completion

Maps sequentially to original Phases 1-4.

### H operator agent task

Return an exact visible identity assertion for the checkpoint. Do not perform OCR-based engineering interpretation.

### Workflow agent completion rule

A vendor `completed` state is necessary but insufficient. A checkpoint becomes locally `completed` only after all are true:

1. actual session ID attached;
2. structured answer matches expected step;
3. expected visible project/case/view identity confirmed;
4. owned screenshot retrieved and validated;
5. checkpoint JSON atomically persisted.

Otherwise it becomes `failed`, records the reason, and blocks downstream execution.

### Sequential enablement

1. `OPEN_PROJECT`: exact visible project identity.
2. `LOAD_FLOW`: exact `Base Case` and visible result state.
3. `COORDINATION`: exact existing `Main Bus - Feeder 1` view.
4. `ARC_FLASH`: exact `Normal Operation` and visible result state.

Each checkpoint keeps the original live acceptance gate from `MVP_MULTI_AGENT_PLAN.md`. No later checkpoint is enabled before its predecessor passes.

### Verification agent tests

- completed vendor answer without observation fails;
- wrong project/case/view observation fails;
- stale or foreign screenshot fails;
- matching observation and evidence completes;
- failure blocks every downstream runner call;
- no observation content is treated as an electrical conclusion.

### Gate F

Each checkpoint is proven independently with fakes, then through its original dedicated-machine acceptance gate.

## Wave G - Optional event cursor

Implement only if the actual selected H integration consumes `get_session_changes` or paginated events. Status polling remains sufficient otherwise.

If required:

- cursor is opaque and scoped to one session;
- initialize at session creation;
- advance monotonically;
- deduplicate events;
- never reuse across sessions or retry attempts;
- ignore late events from cancelled/foreign sessions;
- observe terminal event once.

Tests must cover duplicate pages, stale/foreign cursors, late prior-attempt events, and fresh cursor allocation on retry.

Do not build a generalized event bus, database, or replay service.

## Wave H - Fixed orchestrator and five-outcome status

Maps to original Phase 2 and is reused through Phase 6.

### Workflow agent files

- `src/orchestrator/state.py`
- `src/orchestrator/engine.py`
- `src/orchestrator/status.py`
- optional `src/orchestrator/cli.py`

### Requirements

- exact five-step transition table;
- predecessor completion required;
- failure/cancellation stops downstream work;
- retry only the failed/cancelled checkpoint after Gate C cancellation ordering;
- attempt history immutable;
- resume from disk selects the same next legal step;
- status exposes exactly five checkpoint names and outcomes;
- CLI, if added, permits only the approved plan run, cancel, and checkpoint retry.

### Verification agent tests

- exhaustive legal/illegal transitions;
- injected failure at each step leaves every downstream step pending;
- retry call history contains only the failed step;
- predecessors and prior evidence remain unchanged;
- fresh process resume is deterministic;
- status contains exactly OPEN_PROJECT, LOAD_FLOW, COORDINATION, ARC_FLASH, REPORT.

### Gate H

Full offline orchestration passes without importing or contacting H.

## Wave I - Missing-evidence report semantics

Maps to original Phase 5.

### Reporting agent files

- `src/reporting/model.py`
- `src/reporting/generator.py`
- `src/reporting/validation.py`

### Requirements

- read only the approved plan, persisted checkpoint results, and stored screenshots;
- include project, fixed sequence, all five statuses, available screenshots, failed/cancelled steps, errors, and missing/unreadable evidence;
- never silently omit missing evidence or imply success;
- never invent ETAP results or engineering conclusions;
- place exact `Draft - engineering review required` notice on every page;
- REPORT failure must not mutate earlier records or evidence.

### Verification agent tests

- deterministic all-success golden report;
- fixture for each failed/cancelled checkpoint;
- fixture for each missing/unreadable screenshot;
- all available screenshots retained in partial reports;
- exact sequence and status disclosure;
- every rendered/extracted page contains the exact notice;
- no final, approved, recommended, compliant, or invented-result language;
- REPORT failure preserves earlier file hashes.

### Gate I

Both success and partial-failure reports are deterministic, evidence-backed, and marked as drafts on every page.

## Wave J - End-to-end failure injection and acceptance

Maps to original Phase 6.

### Verification/integration agent tests

- complete offline success;
- failure and cancellation at every checkpoint;
- cancellation timeout;
- foreign session/resource;
- stale retry result;
- screenshot size/type/integrity failure;
- observation mismatch;
- persistence interruption;
- process restart/resume;
- missing-evidence report;
- three consecutive live reset/reruns on the dedicated Windows ETAP machine.

### Lead scope audit

Confirm no implementation added:

- electrical calculations or interpretation;
- dynamic studies or multiple projects;
- model edits;
- voice, dashboard, accounts, or approval;
- hosted browser, public service, deployment stack, or generalized infrastructure.

### Final gate

All original MVP gates pass, plus R10-R15. The same approved ANSI demo runs three consecutive times with owned evidence, correct cancel/retry behavior, five outcomes, and one per-page-marked draft report per run.

## Parallel execution schedule

| Wave | Parallel work | Must remain sequential |
| --- | --- | --- |
| A | H SDK contract + verification tests | Interface approval |
| B | Prompt templates + golden tests | Final prompt freeze |
| C | Adapter ownership + orchestrator cancellation tests | Live desktop cancellation |
| D | Paths/persistence + tests | Schema merge |
| E | Screenshot validation + fixtures/tests | Live resource proof |
| F | Fake checkpoint runners + workflow tests | OPEN_PROJECT, LOAD_FLOW, COORDINATION, ARC_FLASH live enablement |
| G | Cursor code/tests only if required | Decision to enable cursor work |
| H | State engine + status tests | Merge with checkpoint runners |
| I | Report generator + report tests | Final report acceptance |
| J | Offline failure matrix | Live three-run acceptance |

## Immediate next assignment

Start only Waves A, B, and D in code-only mode:

1. Foundation agent: `RunLayout` and atomic persistence contracts.
2. H operator agent: exact SDK pin/surface test, typed commands, and prompt templates.
3. Verification agent: contract, prompt, path, and persistence tests.
4. Lead: approve interfaces and confirm all existing 71 tests remain green.

Do not implement a live `OPEN_PROJECT` runner until these gates pass. Do not begin `LOAD_FLOW` until `OPEN_PROJECT` passes its original three-reset/rerun acceptance gate.
