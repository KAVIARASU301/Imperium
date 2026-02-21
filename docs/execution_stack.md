# Execution Stack (v1)

## Why this exists

Historically, order placement paths in the app were mostly **retail-style immediate orders**:

- direct broker order calls from UI handlers,
- limited routing behavior,
- minimal treatment of market microstructure,
- and no unified fill-quality telemetry for post-trade analysis.

That approach is simple, but in noisy or thin markets it can increase:

- slippage,
- adverse fills,
- inconsistent retry behavior,
- and operational coupling between UI timing and execution behavior.

The execution stack was introduced to make order handling more **systematic, policy-driven, and observable** without rewriting the entire app architecture.

---

## What was added

`core/execution_stack.py` introduces a modular pipeline:

1. **`ExecutionRequest`**
   - Canonical payload for execution intent.
   - Carries side, quantity, order type, book context (LTP/bid/ask), and policy hints (algo, urgency, slicing options).

2. **`SmartOrderRouter`**
   - Routing abstraction layer (currently single-broker / primary route).
   - Chooses queue behavior (`take`/`join`/`neutral`) and can adapt effective order type/limit price using spread context and urgency.

3. **`ExecutionAlgoPlanner`**
   - Converts parent quantity into child slices.
   - Supports:
     - `IMMEDIATE`
     - `IS` (implementation-shortfall style immediate handling in v1)
     - `TWAP`
     - `VWAP`
     - `POV`
   - Adds optional size randomization to reduce deterministic signaling.

4. **`SlippageModel`**
   - Estimates expected slippage and impact per child order before sending.
   - Uses spread + participation-derived impact proxy.

5. **`RetryPolicy`**
   - Classifies errors into buckets:
     - `transient` (network/timeouts)
     - `throttle` (rate limits)
     - `risk` (margin/RMS/insufficient funds)
     - `fatal` (everything else)
   - Applies bucket-specific retry limits and backoff timings.

6. **`FillQualityTracker`**
   - Writes execution telemetry to JSONL for each placed child order and retry/error event.
   - Output files:
     - `~/.imperium_desk/execution_quality_live.jsonl`
     - `~/.imperium_desk/execution_quality_paper.jsonl`

7. **`ExecutionStack.execute(...)`**
   - Orchestrates route selection → slicing → pre-trade estimate → place order → retry (if needed) → telemetry append.

---

## Where it is used in the app

The stack is initialized in `ImperiumMainWindow` and used in both major entry flows:

- **Single strike quick-order flow** (`_execute_single_strike_order`)
- **Buy/Exit panel multi-strike flow** (`_execute_orders`)

So both paths now share a unified execution policy instead of mixing direct placement in one flow and policy-driven placement in another.

---

## End-to-end flow (high-level)

For each parent order request:

1. Build `ExecutionRequest` with market context and execution policy.
2. Router derives queue posture and adjusted child order instructions.
3. Algo planner emits one or more child quantities.
4. For each child:
   - estimate slippage/impact,
   - submit to broker,
   - on error, retry using error-bucket policy,
   - append telemetry record.
5. Caller confirms resulting order IDs via existing app confirmation paths.

---

## Optional execution parameters

These can be attached to order payloads that feed the execution stack:

- `execution_algo`: `IMMEDIATE` (default), `IS`, `TWAP`, `VWAP`, `POV`
- `execution_urgency`: `normal` (default), `high`
- `max_child_orders`: max number of slices (default `1`)
- `participation_rate`: impact estimation hint (default `0.15`)
- `randomize_slices`: enable/disable slice randomization (default `True`)

When omitted, the system falls back to conservative defaults compatible with current behavior.

---

## Practical impact

### 1) Better execution consistency
A single policy layer now drives both single-strike and panel-order placement, reducing behavioral drift between UI paths.

### 2) Lower operational risk from ad-hoc retries
Retry handling is now explicit and error-aware, rather than implicit or scattered.

### 3) Improved observability
Per-child telemetry enables post-trade diagnostics such as:

- arrival-vs-order placement behavior,
- estimated impact regime by symbol/time,
- error-bucket distribution,
- retry frequency and latency profile.

### 4) Foundation for future enhancements
The abstractions are designed to be extensible for:

- richer implementation shortfall logic,
- venue/liquidity-aware routing,
- anti-signaling schedules and randomized timing,
- stronger TCA (arrival slippage, IS decomposition, adverse selection),
- tighter integration with risk controls and strategy-level execution templates.

---

## Scope and limitations (v1)

- Current router is a **single-broker abstraction** (not true multi-venue smart routing yet).
- Slippage/impact is a **proxy model**, useful for consistency and telemetry but not a full market impact engine.
- `VWAP`/`POV` in v1 are **slicing-policy approximations** (not full volume-curve calibrated execution).
- Final trade confirmation and position bookkeeping still leverage existing app flows.

This is intentional: v1 prioritizes a safe incremental migration from direct UI-triggered placements to a reusable execution layer.

---

## Telemetry record shape (conceptual)

Typical JSONL rows include fields like:

- `timestamp`
- `tradingsymbol`
- `execution_algo`
- `child_index`, `children`, `quantity`
- `arrival_price`, `limit_price`
- `expected_slippage`, `impact_estimate`
- `route`, `queue_priority`
- `latency_ms`
- `status` (`placed` / `error`)
- for errors: `error_bucket`, `error`, `attempt`

These records are append-only and intended for downstream analysis dashboards or offline TCA scripts.

---

## Summary

The execution stack was added to move order placement from a primarily UI-driven immediate model to a **structured, reusable execution subsystem** with:

- clearer intent (`ExecutionRequest`),
- policy-based routing/slicing,
- controlled retry behavior,
- and measurable execution outcomes via telemetry.

It improves consistency now and creates the technical base for deeper microstructure-aware execution in subsequent iterations.
