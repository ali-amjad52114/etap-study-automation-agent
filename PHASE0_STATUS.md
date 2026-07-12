# Phase 0 Status

Phase 0 establishes the contracts and dependencies required by the MVP. It does not automate an ETAP study.

## Completed

- Approved project frozen to `C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI` (`EXAMPLE`).
- Fixed study order and selectors validated:
  - Load Flow: `Base Case`
  - Coordination: `Main Bus - Feeder 1`
  - Arc Flash: `Normal Operation`
- H region configured as US.
- H API authentication and quota verified against the US service.
- Python 3.12 virtual environment created.
- `hai-agents` 1.0.6 and `hai-drivers` 0.1.1 installed.
- Secret-safe settings loader implemented.
- Checkpoint and study-plan contracts implemented.
- H adapter and deterministic offline fake implemented.
- Vendor lifecycle mapping, timeout, cancellation, and one-session lock implemented.
- Screenshot persistence restricted to the evidence root and structurally valid PNG data.
- Publishable files scanned for H key patterns.
- Offline automated test suite implemented.
- ArcFlash-reference hardening implemented in code-only mode: exact SDK boundary, typed prompts, session/evidence ownership, bounded PNG validation, immutable attempts, atomic persistence, observation-backed completion, fixed orchestration, failure injection, and per-page-marked draft PDF reporting.
- Full offline suite: 208 tests passing as of July 12, 2026.

## Live acceptance progress

- H US local-desktop sessions successfully opened and visibly verified project `EXAMPLE`.
- Load Flow `Base Case` completed with persisted session-owned screenshot evidence.
- ETAP's exact coordination target was discovered and frozen as `Main Bus - Feeder 1`.
- Live integration fixes were validated for H observation-event screenshot discovery, production screenshot bucket handling, SDK event sort arguments, and iterator-based resource downloads.
- The strongest preserved run is `20260712T182915389728Z`: OPEN_PROJECT and LOAD_FLOW completed with screenshots; COORDINATION failed closed before the exact view-name correction, and its corrected checkpoint-only retry was blocked before ETAP control by exhausted H bridge quota; ARC_FLASH and REPORT did not run.
- Further live runs are temporarily blocked by H session quota (`limit=10`, `active=10`, `available=0`) even though the session list reports no active sessions.

## Deferred by request

The following live desktop gates remain incomplete:

- H-controlled ETAP desktop session;
- visible ETAP project-name verification;
- visible confirmation of the three named cases/views;
- live screenshot capture through H;
- live desktop reset/rerun check.

These items must pass before Phase 0 can be marked fully complete or Phase 1 `OPEN_PROJECT` can be accepted. They are deferred, not waived.

## Run the offline gates

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

`.env` must contain a valid server-side `HAI_API_KEY`; it is excluded from version control. Use `.env.example` as the non-secret template.
