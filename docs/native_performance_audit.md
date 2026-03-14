# Native Performance Audit (Rust/C++ Opportunities)

## Scope and goal
This audit reviews the current Python/PySide6 codebase and identifies where **native code (Rust or C++)** will produce the biggest performance and responsiveness gains with the lowest integration risk.

The app is primarily an event-driven desktop trading terminal. Most user-visible lag typically comes from:
- high-frequency tick processing,
- repeated per-frame/per-tick indicator math,
- large-table UI updates,
- repeated DataFrame transforms for historical/monitor views.

## Executive summary
Use native code in a phased way:

1. **Rust first** for compute-heavy, testable kernels (indicators, tick-to-bars/CVD transforms, risk math).
2. **Keep Qt UI in Python**; optimize data feeding to the UI rather than rewriting widgets in C++.
3. **Optional C++/Qt plugin** only for the most expensive render paths if Python-side throttling is still insufficient.

## High-impact candidates

### 1) Indicator math engine (`core/cvd/indicators.py`) — **Best first Rust target**
Why this is hot:
- Multiple indicator functions use explicit Python loops over arrays (`calculate_ema`, `calculate_atr`, `compute_adx`, `is_chop_regime`, `calculate_cvd_zscore`).
- These are called on rolling market data and scale linearly with bar count.

Native strategy:
- Build a Rust crate with `pyo3`/`maturin` exposing vectorized kernels for EMA/ATR/ADX/Z-score/chop mask.
- Keep Python API signatures identical (`np.ndarray -> np.ndarray`) for drop-in replacement.

Expected gains:
- Usually **3x–20x** vs pure Python loops (depends on array lengths/frequency).
- Lower GIL pressure if kernels release GIL for long loops.

---

### 2) Tick→CVD/OHLC data transforms (`core/cvd/data_worker.py`, `core/cvd/cvd_historical.py`) — **High value Rust target**
Why this is hot:
- Heavy Pandas processing pipeline: parsing/coercion, sorting, diff/cumsum, resampling, and repeated DataFrame copies.
- Includes session-aware transforms and post-resample open/high/low corrections.

Native strategy:
- Move core transforms to Rust (Arrow/Polars or custom structs):
  - signed volume derivation,
  - session cumulative CVD,
  - timeframe aggregation,
  - gapless-open enforcement.
- Return compact arrays/DataFrames back to Python only at boundaries.

Expected gains:
- Faster historical loading and lower memory churn.
- More deterministic latency during chart refresh and backfills.

---

### 3) Real-time tick processing core (`core/market_data/market_data_worker.py`, `core/cvd/cvd_engine.py`) — **Medium-high target**
Why this is hot:
- High-frequency bursts are queued and drained; each tick still incurs Python dict/object overhead.
- Per-tick CVD updates run in Python for every subscribed token.

Native strategy:
- Rust module to process raw tick batches into compact structs and incremental CVD updates.
- Emit only aggregated deltas/events back to Python UI thread.

Expected gains:
- Better throughput during bursty market periods.
- Reduced jitter in UI responsiveness.

---

### 4) Position/order simulation scans (`core/execution/paper_trading_manager.py`) — **Good Rust target**
Why this is hot:
- Periodic scan of pending orders and position updates in Python.
- Complexity grows with active order count and symbol universe.

Native strategy:
- Move matching/trigger evaluation loop (LIMIT/SL/SLM conditions) to Rust.
- Keep persistence/signals in Python; native layer returns state transitions.

Expected gains:
- Stable simulation latency under larger paper books.

---

### 5) Large-table rendering/update pressure (`core/widgets/positions_table.py`) — **Optimize Python first; C++ only if needed**
Why this is hot:
- Frequent in-place row/cell updates and group PnL recomputation across many symbols.
- Most cost is usually UI model/view churn, not raw arithmetic.

Native strategy:
- First optimize Python-side update batching/throttling/coalescing.
- If still constrained, move to a C++ `QAbstractTableModel` plugin and bind to PySide.

Expected gains:
- Smoother UI under heavy tick rates, especially with many open rows.

## Lower-priority / not worth native rewrite now
- Login/session/token/account flows (`core/account/*`): mostly network-bound.
- Generic config/logging/util modules: low CPU impact.
- One-off dialogs without heavy real-time updates.

## Rust vs C++ decision guidance

### Prefer Rust when
- You want safe, fast numeric/data kernels.
- You need easier CI packaging for Python wheels (`maturin`).
- You want to avoid memory/threading pitfalls.

### Prefer C++ when
- You are implementing Qt-native models/delegates/painting paths.
- You need deepest integration with Qt event/model-view internals.

## Recommended roadmap
1. **Phase 1 (2–4 weeks):** Rust indicator engine + parity tests against current Python outputs.
2. **Phase 2:** Rust tick/CVD aggregation pipeline for historical + live batch transforms.
3. **Phase 3:** Rust paper execution loop + risk primitives.
4. **Phase 4 (optional):** C++ Qt model for positions table only if UI profiling still shows bottlenecks.

## Guardrails for migration
- Keep Python fallback path for each native module behind a feature flag.
- Add golden-data tests to guarantee byte/float parity for indicators and CVD bars.
- Benchmark with representative market sessions (open, lunchtime, closing volatility).
- Ensure native modules fail gracefully and do not block order placement path.

## Evidence pointers used for this audit
- Indicator loops and rolling computations: `core/cvd/indicators.py`.
- Tick historical transforms/resampling: `core/cvd/data_worker.py`, `core/cvd/cvd_historical.py`.
- Live tick queue/drain and callback path: `core/market_data/market_data_worker.py`.
- Per-tick CVD state transitions: `core/cvd/cvd_engine.py`.
- Periodic paper-order scanning and execution checks: `core/execution/paper_trading_manager.py`.
- Frequent table update logic for positions/groups: `core/widgets/positions_table.py`.
