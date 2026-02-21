# Auto-Trade Gap Analysis vs Institutional Prop Bots

## Scope reviewed
- `core/auto_trader/*` for signal generation, simulation, and setup controls.
- `core/main_window.py` for live automation order routing and state handling.
- `core/trade_ledger.py` and `utils/paper_rms.py` for persistence and risk controls.

## What the current auto-trader already does well
1. **Signal framework with multiple strategies**
   - Supports ATR reversal, ATR divergence, EMA cross, and range breakout strategy paths with filtering and prioritization.
2. **Basic live automation flow exists end-to-end**
   - Receives automation signals, checks market state, builds order payloads, and executes through direct route or buy/exit panel route.
3. **Stateful trade management**
   - Tracks active auto-trades by instrument token, including side, strategy type, stop-loss points, and trailing stop behavior.
4. **Cutoff safety**
   - Explicit 3:00 PM auto-cutoff handling to stop/exit automation.
5. **Built-in simulator**
   - Uses same family of signal masks and stop logic to simulate trades and report wins/losses/points.
6. **Persisted trade ledger**
   - Records finalized trades to SQLite with strategy tags and day summaries.

## Core gaps vs institutional/prop-grade bots

### 1) Portfolio-level risk engine is too shallow
Current:
- Position-level stop logic exists per active trade.
- Paper RMS uses a very simple `price * qty * safety_factor` margin model.

Missing institutional features:
- Real broker/SPAN-like margin engine and stress margin model.
- Portfolio VAR/CVAR and scenario-based risk (gap down/up, vol shock, correlation spike).
- Exposure caps by symbol/sector/strategy/expiry/greeks bucket.
- Intraday drawdown guardrails (hard stop, soft throttle, lockout windows).
- Real-time concentration risk and kill-switch policies tied to P&L and volatility regimes.

### 2) Execution stack is retail-style, not microstructure-aware
Current:
- Predominantly market-style immediate execution paths.
- Limited execution tactics in automation routing.

Missing institutional features:
- Smart order router abstraction (venue/liquidity selection, queue-position logic).
- Execution algos (TWAP/VWAP/POV/IS), slicing, randomization, anti-signaling behavior.
- Dynamic slippage models and pre-trade impact estimation.
- Fill-quality analytics (arrival slippage, implementation shortfall, adverse selection).
- Retry policies tuned by error type/latency budget and market microstructure.

### 3) Signal governance and model risk controls are lightweight
Current:
- Strategy filters and priorities, some chop/consolidation controls, and simulator overlay.

Implemented baseline (lightweight):
- Added a live signal governance layer with confidence scoring and strategy-fusion weighting.
- Added regime-aware strategy enable/disable matrix (`trend`, `chop`, `high_vol`).
- Added rolling walk-forward-style stability scoring from realized per-strategy edge snapshots.
- Added feature drift score and strategy health score gates that can hold live execution.
- Added deployment guardrails with `shadow` and `canary` modes before `live` enablement.

Missing institutional features:
- Robust out-of-sample walk-forward pipeline and parameter stability checks.
- Regime classifier with automated strategy enable/disable matrix.
- Ensemble weighting and confidence-scored signal fusion.
- Feature drift detection and strategy health degradation alerts.
- Shadow-mode deployment and canary rollout before full capital enablement.

### 4) Observability and incident response are not production-grade
Current:
- Logging and local trade ledger; no clear event-sourced execution journal.

Implemented baseline (lightweight):
- Added an append-only execution journal (`execution_journal_<mode>.jsonl`) and trace context IDs that flow through signal -> order placement/error events.
- Added a rolling telemetry dashboard snapshot (`telemetry_dashboard_<mode>.json`) with latency, reject-rate, slippage, hit-ratio, and risk-utilization metrics.
- Added anomaly detection hooks for stuck orders, stale ticks, duplicate signals, and runaway loops.
- Added incident playbooks with optional auto-remediation hooks (pause strategy, unwind risk, reroute).
- Added periodic post-trade TCA summary generation (`tca_report_<mode>.json`) from the execution journal.

Missing institutional features:
- Structured telemetry (trace IDs across signal -> order -> fill -> exit).
- Real-time dashboards for latency, reject rates, slippage, hit ratios, and risk utilization.
- Automated anomaly detection (stuck orders, stale ticks, duplicate signals, runaway loops).
- Incident playbooks with auto-remediation actions (pause strategy, unwind risk, reroute).
- Post-trade TCA and periodic model/execution attribution reports.

### 5) Reliability/continuity architecture is single-node minded
Current:
- Desktop app workflow with local state and UI-coupled automation.

Missing institutional features:
- Service separation (signal engine, execution engine, risk engine, state store).
- High-availability failover with hot/warm redundancy.
- Durable message bus and idempotent command handling.
- Replayable event log for deterministic recovery and audit.
- Clock synchronization and deterministic sequencing controls.

### 6) Compliance, controls, and audit trail are minimal
Current:
- Trade records captured, but mostly business-level fields.

Missing institutional features:
- Immutable audit trail for decision provenance (input features, signal confidence, risk checks).
- Role-based permissions + maker/checker controls for config changes.
- Signed config snapshots per deployment session.
- Policy engine for allowed products/times/limits by account and strategy.
- Surveillance hooks for abnormal behavior and abuse prevention.

### 7) Research-to-production workflow is underdeveloped
Current:
- Simulator in UI, but no explicit research MLOps pipeline.

Missing institutional features:
- Versioned datasets, feature store, and experiment registry.
- CI validation gates for strategy updates (performance + risk + robustness thresholds).
- Parameter governance with approval workflow and rollback tags.
- Parallel paper/live shadow reconciliation with statistical drift reports.

## Highest-impact roadmap (practical order)
1. **Risk-first hardening**
   - Build portfolio risk limits + intraday drawdown locks + global kill switch.
2. **Execution quality layer**
   - Add slippage-aware limit/market logic, slicing, and fill-quality metrics.
3. **State & reliability decoupling**
   - Move automation from UI-coupled flow to a background service with durable state.
4. **Observability + alerts**
   - Introduce structured events, dashboards, and anomaly alerts.
5. **Governed strategy lifecycle**
   - Add walk-forward validation, canary mode, and config approvals.

## Brutally honest verdict
This auto-trader is a **strong advanced-retail / semi-systematic desk tool**, not yet an institutional prop bot. The biggest gaps are not “more indicators”; they are **risk architecture, execution microstructure intelligence, resilience, and governance**. Closing those will matter more than adding another entry signal.
