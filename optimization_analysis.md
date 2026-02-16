# Options Badger Optimization Analysis

Date: 2026-02-16  
Scope: repository-wide static review focused on runtime performance, API load, UI responsiveness, and avoidable I/O.

## Executive summary

This codebase is feature-rich and generally structured well for a desktop trading client, but it has several recurring bottlenecks:

1. **Repeated full-table / full-dataset rebuilds** on periodic timers.
2. **High-frequency quote polling patterns** that can be reduced or batched.
3. **Pandas/DataFrame usage in tick paths** where append + sort patterns create avoidable overhead.
4. **Synchronous sleeps and blocking behavior** still present in order/confirmation paths.
5. **Redundant JSON read-modify-write loops** on settings/state persistence.

If addressed in order of impact, the project can reduce UI jank during volatile markets, reduce Kite API pressure, and improve latency consistency.

---

## Method used

- Static code inspection of core, dialogs, widgets, and utils modules.
- Pattern scan for repeated loops, timers, quote polling, JSON I/O, and blocking calls.
- Sanity check via bytecode compilation.

---

## High-impact optimization opportunities (prioritized)

## P0 — Avoid rebuilding option-chain contracts every refresh cycle

### Evidence
- `OptionChainDialog._fetch_market_data()` clears and reconstructs `contracts_data` every timer run, then fetches quotes for all symbols. This is executed every 2 seconds while active.

### Why this is expensive
- Instrument-to-strike mapping is mostly static for a selected `(symbol, expiry)`.
- Rebuilding nested dictionaries each refresh adds CPU churn and object allocations.

### Recommendation
- Cache contracts per `(symbol, expiry)` once, refresh only when symbol/expiry changes.
- Keep a `cached_symbols` list for quote payloads and reuse it.
- Incremental update UI rows from quote deltas instead of full chain redraw when possible.

### Expected impact
- Lower CPU and GC pressure in option-chain window.
- Improved frame stability and reduced refresh jitter.

---

## P0 — Reduce O(n^2/n^3) lookup patterns in strike ladder build/update

### Evidence
- Strike ladder build path loops strikes × option-types × entire instruments list while constructing contracts.
- Quote application loops over all contract containers and then over all contracts for symbol matching.
- Regular table-wide updates iterate every row and refresh OI widgets on each update cycle.

### Why this is expensive
- Repeated nested scanning scales poorly with larger instrument universes or wider ladders.
- UI updates for unchanged cells trigger unnecessary repaints.

### Recommendation
- Pre-index instruments by `(symbol, expiry, strike, option_type)` once.
- Maintain `token -> contract` map for direct tick application.
- Track dirty rows/cells and update only changed table items.

### Expected impact
- Significant reduction in CPU during high tick throughput.
- Better responsiveness while scrolling/interaction in ladder view.

---

## P1 — Replace DataFrame concat/sort on each tick in market monitor

### Evidence
- `MarketChartWidget.add_tick()` appends bars using `pd.concat` and then `sort_index` on each new bucket.
- Candlestick plotting path rebuilds list data using per-row `iloc` extraction when rendering.

### Why this is expensive
- `concat + sort` in streaming paths is one of the more expensive DataFrame append patterns.
- Per-tick list reconstruction and repeated row indexing multiply cost over time.

### Recommendation
- Maintain a preallocated ring buffer (NumPy arrays/deques) for live bars.
- Convert to DataFrame only for snapshot/export, not for every tick mutation.
- For rendering, vectorize extraction (`[['open','high','low','close']].to_numpy()`) instead of repeated `iloc`.

### Expected impact
- Smoother real-time charting and lower memory churn.

---

## P1 — Remove remaining blocking sleeps from order confirmation flow

### Evidence
- Order/exit paths still contain `time.sleep(...)` in confirmation/retry logic.

### Why this is expensive
- Any sleep on the GUI thread causes perceived freezes and input lag.
- Retry loops doing synchronous waits increase latency variability.

### Recommendation
- Use async Qt scheduling (`QTimer.singleShot`) for all retry/confirm loops.
- Centralize confirmation state machine with timeout and callback updates.

### Expected impact
- Better UI responsiveness under order bursts and network slowness.

---

## P1 — Cut repeated network probes for reconnect path

### Evidence
- Market data worker performs DNS + HTTP checks in start/reconnect flows, including repeated retries.

### Why this can hurt
- Extra HTTP checks add latency before reconnect and introduce additional external dependency points.

### Recommendation
- Use a cheaper reachability strategy (e.g., DNS-only fast check + socket connect timeout).
- Add cooldown/debounce around full HTTP probe.

### Expected impact
- Faster recovery from transient disconnections.

---

## P2 — Consolidate JSON persistence and debounce writes

### Evidence
- Config/state helpers perform repeated read-modify-write cycles for settings/table/dialog/journal files.
- Similar patterns exist in table/dialog classes persisting widths/state frequently.

### Why this matters
- Frequent small synchronous writes can cause stutter on slower disks and create file contention.

### Recommendation
- Keep in-memory caches and batch writes via debounce timer (e.g., 500–1500ms).
- Use atomic writes (`tmp` + rename) for reliability.

### Expected impact
- Less I/O jitter and improved reliability for app state files.

---

## P2 — API call budget and batching strategy

### Evidence
- Multiple widgets/dialogs independently poll quotes/LTP on their own timers.
- Some views fetch full quote payloads where LTP-only might suffice.

### Recommendation
- Introduce central quote scheduler with subscriber model.
- Coalesce symbol sets across open views into one batched request per interval.
- Choose `ltp()` vs `quote()` based on minimum data needed.

### Expected impact
- Lower API rate pressure and fewer dropped/failed calls.

---

## P3 — Rendering/memory improvements for long sessions

### Opportunities
- Ensure old chart points are pruned consistently across all chart views.
- Avoid recreating heavyweight Qt objects (pens/brushes/items) in hot loops where reuse is possible.
- Consider viewport virtualization for very large tables.

---

## Suggested implementation roadmap

### Phase 1 (quick wins, low risk)
1. Cache option-chain contracts by `(symbol, expiry)`.
2. Add `token -> contract` lookup map in strike ladder.
3. Replace blocking sleeps in confirmation with timer-driven retries.

### Phase 2 (medium complexity)
1. Move market monitor live path from DataFrame append/sort to ring buffer model.
2. Introduce dirty-row update strategy for strike ladder table.
3. Debounce and batch config/state file writes.

### Phase 3 (architecture-level)
1. Build centralized market data/quote polling broker shared across views.
2. Add observability counters (API calls/sec, UI update duration, tick backlog).
3. Tune timers and backpressure using measured metrics.

---

## Validation plan after optimizations

Track before/after:
- UI frame/update latency during market open.
- CPU (%) with ladder + option chain + monitor open.
- API call count/min and error/reject rates.
- Reconnect median time and 95th percentile.
- File write frequency for state persistence.

Recommended tools:
- `cProfile` + `snakeviz` for CPU hotspots.
- `py-spy` for low-overhead live profiling.
- Lightweight in-app telemetry counters for timers and network calls.

---

## Key files reviewed

- `dialogs/option_chain_dialog.py`
- `widgets/strike_ladder.py`
- `widgets/market_monitor_widget.py`
- `core/main_window.py`
- `core/market_data_worker.py`
- `utils/config_manager.py`

