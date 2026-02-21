# Execution Stack (v1)

This release introduces a modular execution pipeline in `core/execution_stack.py`:

- **Smart order router abstraction** (`SmartOrderRouter`) for queue-priority decisions.
- **Execution algorithms** (`ExecutionAlgoPlanner`) with support for `IMMEDIATE`, `IS`, `TWAP`, `VWAP`, and `POV` slicing patterns.
- **Pre-trade slippage and impact estimation** (`SlippageModel`).
- **Error-aware retry policy** (`RetryPolicy`) tuned by error class (transient, throttle, risk, fatal).
- **Fill-quality telemetry** (`FillQualityTracker`) persisted as JSONL at:
  - `~/.imperium_desk/execution_quality_live.jsonl`
  - `~/.imperium_desk/execution_quality_paper.jsonl`

## How main window uses it

`ImperiumMainWindow._execute_single_strike_order()` now routes orders through `ExecutionStack.execute(...)` before final confirmation. Child-order confirmations are scheduled asynchronously to avoid blocking the Qt event loop.

## Order params (optional)

The following keys can be provided to single-order execution calls:

- `execution_algo`: `IMMEDIATE` (default), `IS`, `TWAP`, `VWAP`, `POV`
- `execution_urgency`: `normal` (default) or `high`
- `max_child_orders`: max slices (default `1`)
- `participation_rate`: participation hint used in impact estimate (default `0.15`)
- `randomize_slices`: `True` by default
