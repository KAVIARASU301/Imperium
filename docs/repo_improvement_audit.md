# Repository Improvement Audit (Imperium)

## Scope and approach

This audit reviewed the Python desktop trading terminal codebase for maintainability, reliability, security, and operational controls.

Quick checks performed:
- Python syntax check across repository (`python -m compileall -q .`).
- File-size hot-spot scan to identify refactor candidates.
- Pattern scan for broad exception handling and debug prints.

## Current strengths

- The project already includes risk-first controls in the main window flow (kill switch, drawdown guardrails, position limits).
- There is a dedicated observability module with incident response primitives and telemetry snapshots.
- Execution stack, trade ledger, and paper trading abstractions indicate good architectural direction.

## Priority improvement opportunities

### 1) Reduce single-file complexity in `core/main_window.py` (highest impact)

`core/main_window.py` is very large (~4.8k LOC) and acts as UI composition root, orchestration hub, and business-flow coordinator.

Why this matters:
- Large classes reduce testability and increase regression risk.
- Incident triage becomes slower because UI and business concerns are interleaved.

Recommended split plan:
- Extract `RiskController` (kill-switch, drawdown lock, exposure checks).
- Extract `DialogCoordinator` (open/close lifecycle for all dialogs).
- Extract `MarketDataOrchestrator` (tick fanout, throttling, subscription deltas).
- Keep `ImperiumMainWindow` as a composition shell.

### 2) Dependency hygiene and supply-chain hardening

`requirements.txt` includes likely-unnecessary or risky entries for a desktop client (`Flask`, `beautifulsoup4`, `constants`, `utils`, duplicate low-level `urllib3` pin with `requests`).

Why this matters:
- Larger dependency surface increases vulnerability and update overhead.
- Ambiguous packages like `utils` can cause import shadowing/namespace confusion.

Recommended actions:
- Classify dependencies into runtime/dev/optional extras.
- Remove ambiguous packages unless explicitly used.
- Add lockfile-based reproducibility (`pip-tools` or Poetry).
- Run SCA regularly (`pip-audit`/Dependabot).

### 3) Exception policy: replace catch-all blocks with typed handling

Repository-wide pattern scan shows many `except Exception` handlers and silent `pass` blocks.

Why this matters:
- Silent failure in trading workflows can hide execution/risk faults.
- Generic handlers can blur recoverable vs. fatal conditions.

Recommended actions:
- Introduce exception taxonomy (`UserRecoverableError`, `BrokerAPIError`, `StateCorruptionError`, etc.).
- Ban bare silent `pass` for execution and risk paths.
- Standardize log context keys (`order_id`, `symbol`, `mode`, `trace_id`).

### 4) Logging and persistence robustness

Main window currently configures a dedicated file handler directly.

Why this matters:
- Risk of duplicate handlers if imported/initialized repeatedly.
- No guaranteed rotation/retention policy for long-running sessions.

Recommended actions:
- Centralize logging bootstrap (single entrypoint).
- Use rotating handlers with size/time caps.
- Define retention and redaction policy (credentials/token patterns).

### 5) Documentation consistency and operational readiness

README still refers to "Options Badger" and `~/.options_badger/`, while runtime paths in code use `.imperium_desk` in multiple places.

Why this matters:
- Operator confusion during deployment/support.
- Higher onboarding/support burden.

Recommended actions:
- Align product naming and filesystem paths across README and code.
- Add a short architecture map: startup sequence, market data lifecycle, order lifecycle, risk gate order.
- Add runbook docs for "API outage", "stale feed", "order stuck", "forced unwind".

## What institutions typically implement in trading tools

The following controls are commonly present in institutional-grade stacks (scaled down as appropriate for your product):

### A) Governance and control plane
- Role-based permissions (separate trader, risk-admin, observer roles).
- Four-eyes approval for high-risk config changes.
- Immutable audit trail for all order and risk-control events.

### B) Pre-trade, in-trade, and post-trade risk
- Pre-trade limits: notional, quantity, instrument whitelist, time windows.
- Real-time limits: gross/net exposure, strategy-level drawdown, reject-rate tripwires.
- Post-trade surveillance: slippage drift, reject-rate anomaly, fill-quality trends.

### C) Operational resilience
- Circuit breakers across broker APIs and data feeds.
- Degraded mode behavior (read-only quote mode if execution is impaired).
- Heartbeats and watchdogs with deterministic fail-safe states.

### D) Observability and incident response
- Unified trace IDs from signal -> order -> fill -> P&L attribution.
- SLOs for latency, reject rate, stale tick windows.
- Automated incident playbooks plus human escalation hooks.

### E) SDLC and release safety
- CI gates: lint, type check, unit/integration smoke tests.
- Staged rollout/canary for automation changes.
- Signed release artifacts and dependency provenance checks.

### F) Data and security controls
- Secret storage isolation and rotation policy.
- Config tamper detection (hash/signature for critical risk config).
- Data retention policy for journals, telemetry, and PII-safe logs.

## Suggested 30/60/90 day roadmap

### 0-30 days (quick wins)
- Clean `requirements.txt` and split runtime/dev dependencies.
- Align README naming + storage path docs.
- Add `ruff` + `mypy` + `pip-audit` to CI (non-blocking initially).
- Replace top 20 high-risk `except Exception` blocks in execution/risk paths.

### 31-60 days
- Refactor `core/main_window.py` into focused controllers/services.
- Add unit tests for risk guardrails and order gating logic.
- Introduce log rotation and structured logging fields.

### 61-90 days
- Add role/permission model for automation/risk settings.
- Implement configuration change audit log + approval workflow.
- Define operational runbooks and incident escalation matrix.

## Concrete acceptance criteria for the next iteration

- Main window reduced by at least 35% LOC with no feature regression.
- Critical-path exception handlers are typed and observable.
- Dependency manifest reproducible and vulnerability-scanned in CI.
- README and runtime paths fully consistent.
- Risk and incident runbooks available in `docs/` and linked from README.
