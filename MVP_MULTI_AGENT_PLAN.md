# MVP Multi-Agent Implementation Plan

This plan implements only the MVP specified in [`README.md`](README.md). The README is the product source of truth. If this plan and the README conflict, the README wins.

Selective reliability ideas from the local ArcFlash reference are planned in [`ARCFLASH_REFERENCE_ADOPTION_PLAN.md`](ARCFLASH_REFERENCE_ADOPTION_PLAN.md). That plan may harden this MVP but may not expand its product scope.

## Definition of done

The MVP is done only when one approved, existing ETAP demo project can be reset and run three consecutive times through this exact sequence:

```text
OPEN_PROJECT -> LOAD_FLOW -> COORDINATION -> ARC_FLASH -> REPORT
```

Each run must produce valid checkpoint records, three study screenshots, five checkpoint outcomes, and one external draft report. Failure must stop downstream execution, and retry must rerun only the failed checkpoint.

## Non-negotiable scope guardrail

Every agent brief and review must prohibit:

- drawing or PDF ingestion;
- ETAP model creation, editing, equipment extraction, or symbol placement;
- custom electrical calculations or interpretation;
- breaker recommendations or engineering approval;
- multiple projects or dynamic/additional studies;
- voice control;
- accounts, collaboration, a large dashboard, or unrelated platform work;
- schedules, webhooks, MCP, databases, telemetry, or plugin systems unless a later README change makes one essential.

ETAP performs all engineering calculations. H only operates the visible UI and captures evidence.

## Agent team

| Agent | Owns | Does not own |
| --- | --- | --- |
| Lead/orchestrator agent | Requirements traceability, phase gates, integration decisions, final acceptance | Electrical interpretation |
| Foundation/API agent | Phase 0 environment, H adapter contracts, schemas, test fakes, fixed study plan | ETAP workflow steps |
| Workflow agent | State machine, persistence, stop/retry behavior, five-outcome status view | H UI implementation |
| H operator agent | Local-desktop integration and visible ETAP checkpoint actions | Calculations or report conclusions |
| Reporting agent | External draft report from stored records and screenshots | Engineering approval |
| Verification agent | Contract, integration, failure-injection, and live acceptance tests | New product features |

Agents may work concurrently on schemas, fakes, report fixtures, and tests. Live H/ETAP work is sequential: the local H desktop supports one active desktop session per Python process/machine, and a second session can displace the first. The workflow must enforce a single-session lock.

No phase merges until its automated tests pass and its exit gate is met. Phases involving H or ETAP also require the stated manual acceptance test on the dedicated Windows machine.

## Requirements traceability

| ID | README requirement | Primary phase |
| --- | --- | --- |
| R1 | Use one approved, existing ETAP demo project without modifying its model | 0-1 |
| R2 | Use the exact fixed study plan and order | 0-4 |
| R3 | Run independent, restartable checkpoints | 1-5 |
| R4 | Stop on failure; retry only the failed checkpoint | 1-6 |
| R5 | Record project/study names, timestamp, status, screenshot, and error | 0-5 |
| R6 | Generate the required external draft report | 5 |
| R7 | Show only five checkpoint outcomes | 2, 5 |
| R8 | Reset and reliably rerun the full demo | 1, 6 |
| R9 | Keep secrets server-side, restrict access, and support manual cancellation | 0, 6 |

## Phase 0 - APIs, environment, and frozen contracts

### Goal

Prove that every external dependency required by the README is available before automating ETAP. Phase 0 creates no study workflow and performs no ETAP model action.

### Foundation/API agent deliverables

1. **Dedicated execution host**
   - Dedicated Windows machine or isolated VM with an interactive desktop.
   - Fixed display resolution, DPI scaling, ETAP version, and window layout documented.
   - ETAP demo installed and licensed sufficiently for Load Flow, Star coordination, and AC Arc Flash.
   - One approved project file, exact visible project name, Load Flow case, coordination view, and Arc Flash case recorded.

2. **H API and SDK access**
   - Verify the current live H documentation because the local snapshot describes a beta API.
   - Python runtime with a tested, pinned version of `hai-agents[desktop]`.
   - `HAI_API_KEY` supplied outside the repository and never written to plans, results, reports, or logs.
   - H region selected and fixed; verify API base, authentication, organization quota, and connectivity.
   - Local desktop environment contract: `kind: desktop`, `host: user_device`.
   - Prefer Python auto-connect. Do not introduce the two-terminal CLI bridge unless auto-connect cannot satisfy the fixed MVP.

3. **Minimal H adapter contract**
   - Start one bounded local-desktop session.
   - Observe session lifecycle and terminal outcomes.
   - Obtain schema-validated structured output.
   - Retrieve and persist a screenshot/resource immediately.
   - Cancel the active session manually or programmatically.
   - Apply per-checkpoint time/step bounds.
   - Reject a second local session while one is active.

4. **Frozen local contracts**
   - `config/study_plan.json` containing exactly the README project and three studies in order.
   - Checkpoint schema with: `step`, `project`, `study`, `timestamp`, `status`, `screenshot`, and `error`.
   - Status vocabulary frozen to `pending`, `running`, `completed`, `failed`, and `cancelled` for local workflow state.
   - UTC ISO-8601 timestamps.
   - Completed study checkpoints require a nonempty screenshot; failed/cancelled checkpoints require an error.
   - Stable, timestamped evidence paths under `evidence/`; paths outside approved project/evidence/report roots are rejected.
   - Fake H client and fake ETAP responses for offline tests.

5. **Safety and reset checklist**
   - Allow only the ETAP executable, approved project, evidence directory, and report output.
   - Document manual cancel and known-baseline reset procedures.
   - Confirm the agent is not given unrelated desktop tasks or general filesystem access.

### Phase 0 tests

Automated:

- exact canonical study plan passes validation;
- wrong project, reordered/missing/duplicate/extra study, wrong case/view, and unknown fields fail before session creation;
- completed and failed checkpoint fixtures validate; malformed results and wrong steps fail;
- completed study result without a readable screenshot fails;
- evidence paths outside the allowlist fail;
- secrets do not appear in checked-in configuration, result fixtures, or captured logs;
- fake H contract covers start, lifecycle, structured result, screenshot retrieval, error, timeout, and cancel;
- lifecycle mapping covers queued/pending/running/completed and failed/timed-out/interrupted vendor states;
- a second concurrent desktop session is rejected locally;
- dependency import/version smoke test passes.

Opt-in live smoke tests:

- harmless authenticated H API read succeeds; invalid authentication fails without exposing the key;
- a bounded local-desktop session completes and returns valid structured output;
- one test screenshot is downloaded, nonempty, readable, and stored under `evidence/`;
- manual cancellation stops a bounded session;
- a deliberately tiny time/step limit is recorded as failure and does not advance;
- the smoke test resets and runs twice with distinct timestamped evidence.

Manual ETAP readiness checks:

- approved project opens and its visible identity matches;
- `Base Case` Load Flow can run in the demo;
- existing `Main Bus - Feeder 1` coordination view can open;
- `Normal Operation` AC Arc Flash can run;
- no required action edits the model.

### Phase 0 exit gate

Credentials, region, quota, desktop connection, screenshot retrieval, cancellation, session lock, schemas, paths, and test fakes are proven. The approved ETAP project and all named cases/views are manually confirmed. All offline tests and opt-in smoke tests pass. No workflow automation has been added.

## Phase 1 - OPEN_PROJECT

### Deliverables

- Independently runnable `OPEN_PROJECT` checkpoint.
- Start one H local-desktop session, open the approved file, and verify the exact visible project identity.
- Save a timestamped screenshot and atomically write the checkpoint result.
- Stop on missing file, identity mismatch, H error, timeout, screenshot failure, or cancellation.
- Reset instructions return ETAP to a known baseline.

### Tests

- success interaction sequence with fake H;
- missing project and wrong visible identity;
- H error/timeout and cancellation;
- screenshot retrieval/write failure;
- schema-valid result and atomic persistence;
- rerun produces new evidence and cannot reuse stale success;
- no later checkpoint starts on failure.

Live acceptance: reset and run `OPEN_PROJECT` successfully three consecutive times.

### Exit gate

Exact identity evidence and valid JSON are produced on all three runs, and failures remain isolated to `OPEN_PROJECT`.

## Phase 2 - LOAD_FLOW and orchestration core

### Deliverables

- Strict state machine for `OPEN_PROJECT -> LOAD_FLOW`.
- Select only `Base Case`, run ETAP Load Flow through visible H actions, open the result view, and capture evidence.
- Persist complete metadata.
- Retry only `LOAD_FLOW`; never rerun `OPEN_PROJECT` automatically.
- Read-only status output containing only the five checkpoint names and their outcomes.

### Tests

- legal and illegal state transitions;
- `LOAD_FLOW` cannot start before successful `OPEN_PROJECT`;
- exact study case selection;
- success, ETAP failure, H timeout/error, cancellation, and screenshot failure;
- failure stops downstream execution;
- retry invokes only `LOAD_FLOW` and preserves prior evidence;
- status output exposes exactly five outcomes and no feature controls.

Live acceptance: one successful run plus one induced Load Flow failure followed by checkpoint-only retry.

### Exit gate

Load Flow is repeatable, ordered, evidenced, and independently retryable.

## Phase 3 - COORDINATION

### Deliverables

- Add `COORDINATION` only after successful `LOAD_FLOW`.
- Open the existing approved `Main Bus - Feeder 1` Star/protection coordination view and capture it.
- Do not invent a calculation run, modify settings, or alter the model; the README requires opening and capturing this view.

### Tests

- predecessor gating and exact view selection;
- missing/wrong view, H error/timeout, cancellation, and screenshot failure;
- current-checkpoint-only retry;
- preservation of OPEN_PROJECT and LOAD_FLOW results;
- no ARC_FLASH start after failure.

Live acceptance: successful capture plus an induced wrong/missing-view stop.

### Exit gate

The approved view is repeatably captured without model changes, and failure isolation is proven.

## Phase 4 - ARC_FLASH

### Deliverables

- Add `ARC_FLASH` only after successful `COORDINATION`.
- Select only `Normal Operation`, run AC Arc Flash in ETAP, open the result view, and capture it.
- Preserve all prior evidence; provide no recommendation or independent calculation.

### Tests

- predecessor gating and exact case selection;
- ETAP calculation failure, wrong case, H error/timeout, cancellation, and screenshot failure;
- current-checkpoint-only retry and prior-result preservation;
- full evidence metadata validation.

Live acceptance: successful run plus an induced failure followed by ARC_FLASH-only retry.

### Exit gate

All four ETAP checkpoints execute in fixed order, capture evidence, and stop correctly.

## Phase 5 - REPORT

### Deliverables

- Generate an external draft report only from the approved plan, checkpoint JSON, and available screenshots.
- Include project name, fixed planned sequence, all available study screenshots, all five statuses, and explicit missing/failed steps and errors.
- Include the exact notice: **Draft - engineering review required**.
- Write a REPORT checkpoint result without changing earlier evidence.
- Finalize the read-only five-outcome status view.

### Tests

- deterministic all-success golden report;
- one fixture for each missing/failed checkpoint;
- missing or unreadable screenshot path;
- exact sequence and notice text;
- no engineering approval, recommendation, or invented result language;
- REPORT failure leaves earlier evidence unchanged;
- status view renders exactly OPEN_PROJECT, LOAD_FLOW, COORDINATION, ARC_FLASH, and REPORT.

Live acceptance: an engineer confirms that a success report and partial-failure report contain every required field. The engineer reviews output; the automation does not approve it.

### Exit gate

Success and partial-failure reports are deterministic, complete, clearly marked as drafts, and backed only by stored evidence.

## Phase 6 - End-to-end MVP hardening

### Deliverables

- One-command execution of the approved plan.
- Dedicated-machine setup, launch, reset, cancel, failure, and retry instructions.
- Pinned dependencies and stable evidence/report locations.
- No new product capability.

### Tests

- full mocked end-to-end success and failure-injection suite;
- fresh-machine setup smoke test;
- failure injected at every checkpoint stops downstream work;
- retry at every checkpoint runs only that checkpoint;
- cancellation during each UI checkpoint stops control and records cancellation;
- full validation of JSON records, screenshots, report, and five-outcome status;
- reset and complete the live workflow three consecutive times.

### Final MVP gate

The same approved ETAP demo project completes three consecutive reset/reruns with all required evidence and one draft report per run. Cancellation works. Failure and checkpoint-only retry work at every stage. An engineer can review the result. Nothing in the scope-exclusion list has been built.

## Phase review checklist

The verification agent answers all of these before approving any phase:

1. Does the change map to a requirement ID in this plan and the README?
2. Are automated tests present for success, failure, cancellation, ordering, and evidence where applicable?
3. Did all tests pass?
4. Was the required live/manual acceptance completed?
5. Does failure stop downstream work?
6. Does retry target only the failed checkpoint?
7. Are secrets absent and paths restricted?
8. Did the change add any excluded feature?

Any “yes” to question 8 blocks the phase.
