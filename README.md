# ETAP Study Automation Agent

Automate a fixed sequence of engineering studies in an existing ETAP project, capture evidence after each study, and generate a draft report for engineering review.

> **Project status:** code-only MVP core implemented. Contracts, fixed prompts, H adapter boundaries, checkpoint orchestration, evidence integrity, retry/resume logic, and draft PDF reporting are covered by offline tests. Live H-controlled ETAP checkpoints and the dedicated-machine acceptance runs remain deferred.

Implementation is governed by the test-gated [`MVP_MULTI_AGENT_PLAN.md`](MVP_MULTI_AGENT_PLAN.md).

Phase 0 implementation progress and deferred live-desktop gates are tracked in [`PHASE0_STATUS.md`](PHASE0_STATUS.md).

The scoped multi-agent plan for useful ArcFlash-reference reliability ideas is [`ARCFLASH_REFERENCE_ADOPTION_PLAN.md`](ARCFLASH_REFERENCE_ADOPTION_PLAN.md).

## MVP goal

The MVP operates one approved, already-built ETAP demo project. It does not create or modify the electrical model.

```text
Existing ETAP project
  -> H controls the ETAP desktop application
  -> ETAP runs three predefined studies
  -> Screenshots and step results are saved
  -> An external draft report is generated
```

The MVP is complete when the same demo can be reset and reliably rerun from start to finish.

## Fixed study sequence

1. Open the approved ETAP project and confirm its identity.
2. Run load flow and capture a screenshot.
3. Open the protection coordination view and capture a screenshot.
4. Run arc flash and capture a screenshot.
5. Generate a draft report.

Study selection is deliberately fixed for the MVP. The agent must not choose additional studies dynamically.

## Checkpoints

Each stage is an independent, restartable checkpoint:

```text
OPEN_PROJECT -> LOAD_FLOW -> COORDINATION -> ARC_FLASH -> REPORT
```

A checkpoint should return a machine-readable result such as:

```json
{
  "step": "LOAD_FLOW",
  "status": "completed",
  "screenshot": "evidence/load-flow.png",
  "error": null
}
```

If a checkpoint fails, execution should stop or retry only that checkpoint. It should not automatically restart the entire workflow.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| ETAP | Owns the electrical model, calculations, study cases, and engineering result views. |
| H computer-use agent | Visibly controls the Windows desktop: opens ETAP, changes modes, runs studies, opens results, and captures evidence. |
| Workflow orchestrator | Enforces checkpoint order, starts H tasks, records results, restricts access, and stops on failure. |
| Report generator | Produces an external draft report from checkpoint results and screenshots. |
| Engineer | Reviews and approves the output. Automation never provides engineering approval. |

H performs UI actions; it does not calculate electrical results. ETAP remains the analysis engine.

## Study plan

Execution should be driven by a manually approved, structured plan so that future drawing ingestion can produce the same input without being coupled to the runner:

```json
{
  "project": "DemoProject",
  "project_file": "DemoProject.oti",
  "studies": [
    {
      "type": "load_flow",
      "study_case": "Base Case"
    },
    {
      "type": "coordination",
      "view": "Main Bus - Feeder 1"
    },
    {
      "type": "arc_flash",
      "study_case": "Normal Operation"
    }
  ]
}
```

## Evidence and report requirements

Every study record must include:

- study and project names;
- timestamp;
- completion status;
- screenshot path;
- error message when applicable.

The generated report must include the project name, planned sequence, all available study screenshots, the status of every checkpoint, missing or failed steps, and this notice:

> **Draft - engineering review required**

The status UI should remain small and show only the five checkpoint outcomes.

## Safety

H local desktop control can see the screen and operate the real mouse, keyboard, shell, and files. Run it on a dedicated Windows machine or isolated VM with only the approved ETAP project and required credentials available. Keep API keys server-side, restrict allowed files and services, and retain a manual stop/cancel mechanism.

The H Python SDK is currently the documented option for local desktop control. The local reference recommends installing the desktop extra with `pip install "hai-agents[desktop]"`. This is reference information, not a working setup command for this repository yet.

## Repository contents

```text
.
|-- README.md                         # This project overview
|-- Reference/
|   |-- README.md                     # Reference-document index
|   |-- ETAP/
|   |   |-- etap_gettingstarted_demo.pdf
|   |   `-- free_tool.pdf
|   `-- h-computer-use-agents/        # Local H documentation snapshot
|-- .rocketride/                      # Pipeline-builder catalogs, schemas, and docs
`-- .claude/                          # Local development guidance
```

`Reference/`, `.rocketride/`, and `.claude/` are local-only development material and are excluded from the public repository. The vendor reference documents are not redistributed.

The ETAP demo references confirm that Load Flow, protective device coordination (Star), and AC Arc Flash are available demo modules, although demo restrictions apply. The H reference documents local Windows desktop control, session lifecycle management, screenshots/resources, cancellation, structured output, and error handling.

## Not in scope for the MVP

- drawing or PDF ingestion;
- automatic ETAP model creation;
- equipment extraction or symbol placement;
- custom electrical calculations;
- multiple ETAP projects or dynamic study selection;
- breaker recommendations or engineering approval;
- user accounts, collaboration features, or a large dashboard;
- voice control before the desktop workflow is reliable.

Optional voice commands and drawing-to-model automation are future phases. They must plug into the structured study-plan boundary rather than replace the runner.

## Implementation layout

```text
config/study_plan.json     # Approved fixed study plan
src/h_operator/            # H session and local-desktop integration
src/orchestrator/          # Fixed state machine, persistence, and retry policy
src/reporting/             # Evidence-driven draft PDF generation
evidence/                  # Timestamped screenshots and step results
reports/                   # Generated draft reports
tests/                     # Contract, failure-injection, workflow, and report tests
```

Install and run the offline suite:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Run one bounded live pass (requires an unlocked dedicated Windows desktop and a valid `.env`):

```powershell
.\.venv\Scripts\python.exe -u scripts\run_live_mvp.py
```

The live runner stops at the first failed checkpoint and never retries or resets automatically.

## Suggested next milestone

Implement `OPEN_PROJECT` as an independently runnable checkpoint. It should start an H local-desktop session, open the approved ETAP demo project, verify the visible project name, save one screenshot, and write the checkpoint JSON result. Once that step is reliable and resettable, add the remaining studies one at a time.

## Local reference material

- [`Reference/README.md`](Reference/README.md) - local reference index.
- [`Reference/h-computer-use-agents/README.md`](Reference/h-computer-use-agents/README.md) - H concepts, API/SDK summary, and documentation map.
- [`Reference/h-computer-use-agents/desktop/local-control.md`](Reference/h-computer-use-agents/desktop/local-control.md) - local Windows desktop control.
- [`Reference/ETAP/etap_gettingstarted_demo.pdf`](Reference/ETAP/etap_gettingstarted_demo.pdf) - ETAP demo getting-started guide.
- [`Reference/ETAP/free_tool.pdf`](Reference/ETAP/free_tool.pdf) - ETAP demo features and restrictions.

These links work in the local development workspace only. The H documentation is a local snapshot dated July 12, 2026. Check the live vendor documentation before implementation because the API is described as beta.
