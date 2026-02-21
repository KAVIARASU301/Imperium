# auto_trader.py — Detailed Refactoring Plan

## Context
`auto_trader.py` (3756 lines) is `CVDSingleChartDialog` — a trading dialog
inside a larger PySide6 desktop app (`ScalperMainWindow`). It is launched from
`main_window.py` via `_on_strike_chart_requested()`, receives live CVD ticks
from `CVDEngine`, emits `automation_signal` + `automation_state_signal` back to
`main_window`, and uses `StrategySignalDetector` (strategy_signal_detector.py)
for signal computation.

The refactor is a **pure extraction** — zero logic changes, zero signal
interface changes, zero renames visible to `main_window.py`.

---

## Problems to Fix

### 1. `strategy_signal_detector.py` is misnamed / contains wrong things
The file currently holds:
- `DateNavigator` (a Qt widget — has nothing to do with strategy detection)
- `_DataFetchWorker` (background Kite API fetch worker — has nothing to do with strategies)
- `StrategySignalDetector` (the actual class that belongs here)

### 2. `auto_trader.py` is a God Object
`CVDSingleChartDialog` does 7 different jobs in one class:
- UI construction (top bar, EMA bar, setup dialog, both pyqtgraph plots)
- Visual settings management + persistence (JSON + QSettings, 30+ widget reads)
- Indicator math (_calculate_ema, _calculate_atr, _compute_adx, _is_chop_regime)
- ATR/confluence signal drawing and overlay rendering
- Live tick handling (deque, offset alignment, tick repaint timer)
- Trade simulation engine (_run_trade_simulation — ~300 line loop)
- Automation signal emission to main_window

### 3. Duplicate `DateNavigator` + `_DataFetchWorker`
Both exist verbatim in `auto_trader.py` AND `strategy_signal_detector.py`.
Only one copy should exist.

### 4. `strategy_signal_detector.py` imports Qt + pyqtgraph
It imports `QDialog`, `QWidget`, `pg`, `AxisItem`, `TextItem` — none of which
are needed by `StrategySignalDetector`. These are artifacts of the misplaced
classes.

---

## Target File Structure

```
core/
├── auto_trader/
│   ├── __init__.py                ← re-exports CVDSingleChartDialog so main_window import stays unchanged
│   ├── chart_dialog.py            ← CVDSingleChartDialog (slimmed, wires everything together)
│   ├── data_worker.py             ← _DataFetchWorker
│   ├── date_navigator.py          ← DateNavigator widget
│   ├── indicators.py              ← _calculate_ema, _calculate_atr, _compute_adx, _build_slope_direction_masks
│   ├── chop_filter.py             ← _is_chop_regime (uses indicators, has its own logic)
│   ├── setup_panel.py             ← _build_setup_dialog + all setup dialog helpers
│   ├── settings_manager.py        ← _load_persisted_setup_values, _persist_setup_values, JSON r/w
│   ├── simulator.py               ← _run_trade_simulation, _update_simulator_overlay
│   ├── signal_renderer.py         ← _draw_confluence_lines, _update_atr_reversal_markers, _emit_automation_market_state
│   └── constants.py               ← TRADING_START, TRADING_END, MINUTES_PER_SESSION (shared with strategy_signal_detector)
└── strategy_signal_detector.py    ← cleaned: only StrategySignalDetector class remains
```

---

## Step-by-Step Execution Plan

---

### STEP 0 — Create package skeleton

**Action:** Create `core/auto_trader/` directory and empty `__init__.py`.

```python
# core/auto_trader/__init__.py
from core.auto_trader.auto_trader_dialog import CVDSingleChartDialog

__all__ = ["CVDSingleChartDialog"]
```

**Why:** `main_window.py` line 55 does `from core.auto_trader import CVDSingleChartDialog`.
This import path works TODAY because `auto_trader.py` is a flat file. After refactoring,
the same import will resolve through the package `__init__.py` — **zero changes to main_window.py**.

---

### STEP 1 — Create `constants.py`

**File:** `core/auto_trader/constants.py`

**Extract from:** `auto_trader.py` lines 30–33, `strategy_signal_detector.py` lines 24–27
(identical content in both — this duplication gets deleted).

```python
from datetime import time

TRADING_START = time(9, 15)
TRADING_END   = time(15, 30)
MINUTES_PER_SESSION = 375  # 6h 15m
```

**After:** Both `chart_dialog.py` and `strategy_signal_detector.py` import from here.
Delete the 4 duplicate lines from `strategy_signal_detector.py`.

---

### STEP 2 — Create `date_navigator.py`

**File:** `core/auto_trader/date_navigator.py`

**Extract from:** `auto_trader.py` lines 39–124 (class `DateNavigator`).
Also delete the **identical copy** in `strategy_signal_detector.py` lines 33–118.

**Imports needed in new file:**
```python
from datetime import datetime, timedelta
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal
```

**No logic changes.** Same class, same signals, same methods.

**After:** `chart_dialog.py` imports `from core.auto_trader.date_navigator import DateNavigator`.
`strategy_signal_detector.py` deletes its copy entirely (it never used it anyway — it was dead code there).

---

### STEP 3 — Create `data_worker.py`

**File:** `core/auto_trader/data_worker.py`

**Extract from:** `auto_trader.py` lines 131–204 (class `_DataFetchWorker`).
Also delete the **identical copy** in `strategy_signal_detector.py` lines 125–198.

**Imports needed in new file:**
```python
import pandas as pd
from PySide6.QtCore import QObject, Signal
from core.cvd.cvd_historical import CVDHistoricalBuilder
from core.cvd.cvd_mode import CVDMode
```

**No logic changes.**

**After:** `chart_dialog.py` imports `from core.auto_trader.data_worker import _DataFetchWorker`.
`strategy_signal_detector.py` deletes its copy.

---

### STEP 4 — Create `indicators.py`

**File:** `core/auto_trader/indicators.py`

**Extract these 4 methods** from `CVDSingleChartDialog` in `auto_trader.py`:

| Method | Lines (approx) | Notes |
|--------|---------------|-------|
| `_calculate_ema` | 2069–2083 | `@staticmethod`, pure numpy |
| `_calculate_atr` | 2085–2105 | `@staticmethod`, pure numpy |
| `_compute_adx` | 3639–3674 | currently an instance method but uses ONLY `high/low/close` args |
| `_build_slope_direction_masks` | 3389–3415 | uses `self.timeframe_minutes` — add as parameter |

**Signature changes required:**

```python
# _build_slope_direction_masks: add timeframe_minutes as parameter
def build_slope_direction_masks(series: np.ndarray, timeframe_minutes: int) -> tuple[np.ndarray, np.ndarray]:
    ...
```
All other functions become module-level functions (not @staticmethod, just plain functions).

**Imports needed:**
```python
import numpy as np
import pandas as pd
```

**No Qt imports at all.** This file is 100% testable without the GUI.

**After:** `chart_dialog.py` calls `from core.auto_trader.indicators import calculate_ema, calculate_atr, compute_adx, build_slope_direction_masks`.
Remove all 4 method definitions from `CVDSingleChartDialog`.
Update call sites inside `chart_dialog.py` to use the module-level functions.

---

### STEP 5 — Create `chop_filter.py`

**File:** `core/auto_trader/chop_filter.py`

**Extract from:** `auto_trader.py` lines 3676–3713 (method `_is_chop_regime`).

**Why separate from `indicators.py`:** It uses `_calculate_ema`, `_calculate_atr`,
`_compute_adx` AND accesses strategy-level config flags. Keeping it separate keeps
`indicators.py` pure math.

**Signature change:**
```python
def is_chop_regime(
    idx: int,
    price: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    timeframe_minutes: int,
    strategy_type: str | None = None,
    chop_filter_atr_reversal: bool = True,
    chop_filter_ema_cross: bool = True,
    chop_filter_atr_divergence: bool = True,
) -> bool:
```

All `self.*` references become parameters. This decouples it from the dialog.

**After:** `chart_dialog.py` calls it by passing in the relevant arrays and config values.

---

### STEP 6 — Create `settings_manager.py`

**File:** `core/auto_trader/settings_manager.py`

**Extract from:** `auto_trader.py`:
- `_settings_key_prefix()` lines 1499–1500
- `_global_settings_key_prefix()` lines 1502–1504
- `_setup_json_file_path()` lines 1506–1510
- `_read_setup_json()` lines 1512–1522
- `_write_setup_json()` lines 1524–1530

**Pattern:** Make a `SetupSettingsManager` class (or just module-level functions):
```python
class SetupSettingsManager:
    def __init__(self, instrument_token: int):
        self._token = instrument_token
        self._settings = QSettings("OptionsBadger", "AutoTrader")

    def read(self) -> dict: ...
    def write(self, values: dict): ...
    def key_prefix(self) -> str: ...
    def global_key_prefix(self) -> str: ...
```

**`_load_persisted_setup_values` and `_persist_setup_values`** stay in `chart_dialog.py`
because they touch 30+ widget references. They just delegate the I/O to `SetupSettingsManager`.

---

### STEP 7 — Create `setup_panel.py`

**File:** `core/auto_trader/setup_panel.py`

**Extract from:** `auto_trader.py`:
- `_build_setup_dialog()` lines 976–1372 (~400 lines)
- All `_compact_form` and `_set_input_w`, `_set_combo_w` local helpers
- `_open_setup_dialog()` lines 1373–1376
- `_set_color_button()` lines 1378–1391
- `_pick_color()` lines 1393–1402
- `_on_pick_background_image()` lines 1404–1416
- `_on_clear_background_image()` lines 1418–1422
- `_update_bg_image_label()` lines 1424–1428
- `_apply_background_image()` lines 1430–1453
- `_on_setup_visual_settings_changed()` lines 1455–1457
- `_apply_visual_settings()` lines 1459–1488
- `_recolor_existing_confluence_lines()` lines 1490–1497

**Design:** Create a `SetupPanel` class that takes the parent dialog as a reference.
It will own all setup-dialog widgets as `self.xxx` attributes, which `CVDSingleChartDialog`
accesses via `self.setup_panel.xxx` (or via direct attribute promotion in `__init__`).

**Key boundary rule:** `SetupPanel` creates the widgets, `CVDSingleChartDialog` wires the 
value-change signals back to its own methods (`_on_automation_settings_changed`, etc.)
in `_connect_signals()`.

---

### STEP 8 — Create `simulator.py`

**File:** `core/auto_trader/simulator.py`

**Extract from:** `auto_trader.py`:
- `_run_trade_simulation()` lines 3097–3387 (~290 lines) — the main simulation loop
- `_update_simulator_overlay()` lines 3062–3095
- `_clear_simulation_markers()` lines 1854–1862
- `_set_simulator_summary_text()` lines 1864–1868
- `_on_simulator_run_clicked()` lines 1839–1852
- `_resolve_side_strategy_from_masks()` lines 3024–3034
- `_resolve_signal_side_and_strategy()` lines 3036–3060
- `_strategy_priority()` lines 3015–3022

**Design:** Make `TradeSimulator` a standalone class, NOT a QObject.
```python
class TradeSimulator:
    def run(self, x_arr, short_mask, long_mask, price_data, ..., stop_points) -> dict:
        ...
```
`CVDSingleChartDialog` holds a `self.simulator = TradeSimulator()` instance.
The `_update_simulator_overlay` wrapper stays in `chart_dialog.py` since it touches
plot marker objects (`sim_taken_long_markers`, etc).

---

### STEP 9 — Create `signal_renderer.py`

**File:** `core/auto_trader/signal_renderer.py`

**Extract from:** `auto_trader.py`:
- `_draw_confluence_lines()` lines 2728–3007 (the big signal drawing method ~280 lines)
- `_clear_confluence_lines()` lines 2708–2726
- `_update_atr_reversal_markers()` lines 2112–2244
- `_emit_automation_market_state()` lines 2263–2303
- `_latest_closed_bar_index()` lines 2305–2326
- `_on_atr_settings_changed()` lines 2107–2110
- `_on_atr_marker_filter_changed()` lines 1806–1813
- `_on_setup_atr_marker_filter_changed()` lines 1815–1820

**Design:** `SignalRenderer` takes references to the plot objects and data arrays.
It does NOT own widgets — it receives them in `__init__` or on each call.

```python
class SignalRenderer:
    def __init__(self, price_plot, cvd_plot, strategy_detector):
        ...

    def update_atr_markers(self, price_data, cvd_data, x_arr, settings): ...
    def draw_confluence_lines(self, masks, x_arr): ...
```

**The `_emit_automation_market_state` stays in `chart_dialog.py`** because it emits
`self.automation_state_signal` which is a `Signal` defined on the dialog class itself.
`signal_renderer.py` can compute the market state values and return them as a dict;
`chart_dialog.py` emits.

---

### STEP 10 — Slim down `chart_dialog.py`

After all extractions, `CVDSingleChartDialog` retains:
- Class-level constants (SIGNAL_FILTER_*, ATR_MARKER_*, ROUTE_*, BG_TARGET_* etc.)
- Signal declarations (`automation_signal`, `automation_state_signal`, `_cvd_tick_received`)
- `__init__` — instantiates all sub-components, calls `_setup_ui`, `_connect_signals`
- `_setup_ui` — builds the two pyqtgraph plot widgets, top bar, EMA bar, crosshairs, timers
- `_connect_signals` — wires navigator, cvd_engine, internal signals
- `_load_and_plot` — creates QThread + _DataFetchWorker, starts fetch
- `_on_fetch_result`, `_on_fetch_error`, `_on_fetch_done`
- `_plot_data` — main plot rendering method (uses indicators, renderer)
- `_plot_live_ticks_only` — tick overlay rendering
- `_apply_cvd_tick`, `_on_cvd_tick_update` — live tick ingestion
- `_cleanup_overlapping_ticks` — deque maintenance
- `_on_date_changed`, `_on_focus_mode_changed`, `_on_timeframe_changed` — UI event handlers
- `_on_mouse_moved` — crosshair handling
- `_load_persisted_setup_values`, `_persist_setup_values` — settings load/save (delegates to SettingsManager for I/O)
- `_on_automation_settings_changed` — emits `automation_state_signal`
- `_start_refresh_timer`, `_refresh_if_live` — live refresh logic
- `_blink_dot`, `_fix_axis_after_show`, `resizeEvent`, `showEvent`, `closeEvent`, `changeEvent`
- `_time_to_session_index` — session index helper
- `_display_symbol_for_title` — static symbol formatter
- `_export_chart_image` — PNG export
- `_enabled_ema_periods` — EMA checkbox reader

**Estimated final size: ~900–1100 lines** (down from 3756).

---

### STEP 11 — Clean up `strategy_signal_detector.py`

**Remove:**
- Lines 1–19: All imports that were only there for `DateNavigator` and `_DataFetchWorker`
- Lines 24–27: `TRADING_START`, `TRADING_END`, `MINUTES_PER_SESSION` (now imported from `constants.py`)
- Lines 33–118: `DateNavigator` class
- Lines 125–198: `_DataFetchWorker` class

**Add:**
```python
from core.auto_trader.constants import TRADING_START, TRADING_END, MINUTES_PER_SESSION
```

**Keep:**
- Lines 205 onwards: `StrategySignalDetector` class — untouched

**Clean imports** — only keep what `StrategySignalDetector` actually uses:
```python
import numpy as np
import pandas as pd
import logging
from collections import deque
```
Remove: `pyqtgraph`, `PySide6`, `KiteConnect`, `QObject`, `QThread`, `AxisItem`, `TextItem`.

---

## Call Site Impact in `main_window.py`

| Location | Current Code | Change Needed |
|----------|-------------|---------------|
| Line 55 | `from core.auto_trader import CVDSingleChartDialog` | **None** — `__init__.py` re-exports it |
| Lines 4221–4234 | `CVDSingleChartDialog(kite=..., ...)` constructor call | **None** — same constructor signature |
| `automation_signal` handler | `dialog.automation_signal.connect(...)` | **None** — signal lives on the dialog |
| `automation_state_signal` handler | `dialog.automation_state_signal.connect(...)` | **None** — signal lives on the dialog |
| `_on_cvd_single_chart_closed` | `del self.cvd_single_chart_dialogs[cvd_token]` | **None** |

**Zero changes to `main_window.py`.**

---

## Execution Order (safest sequence)

```
1. constants.py           — no dependencies on any other new file
2. date_navigator.py      — depends on constants.py
3. data_worker.py         — depends on existing CVDHistoricalBuilder, CVDMode
4. indicators.py          — no Qt deps
5. chop_filter.py         — depends on indicators.py
6. settings_manager.py    — depends on QSettings only
7. simulator.py           — depends on indicators.py
8. signal_renderer.py     — depends on indicators.py, chop_filter.py
9. setup_panel.py         — depends on settings_manager.py (for defaults)
10. chart_dialog.py        — imports from all above
11. __init__.py            — re-exports CVDSingleChartDialog
12. Clean strategy_signal_detector.py
```

---

## Key Decisions & Rules

### What MUST stay on `CVDSingleChartDialog`
- All PySide6 `Signal` declarations (signals must be on the class with the Qt metaclass)
- `QThread` / `QTimer` creation (must be owned by a QObject)
- `_setup_ui` (builds the actual plot widgets — pyqtgraph objects attached to `self`)
- `_connect_signals` (all signal wiring goes here)

### What MUST be passed in, not pulled from `self`
- Indicator functions in `indicators.py` get arrays as arguments, not `self.all_price_data`
- `chop_filter.is_chop_regime` gets config booleans as arguments, not `self._chop_filter_atr_reversal`
- `simulator.TradeSimulator.run()` gets data arrays + config as arguments

### What stays duplicated intentionally
- Nothing — all duplication is removed

### Backward compatibility
- `from core.auto_trader import CVDSingleChartDialog` works identically
- `CVDSingleChartDialog.__init__` signature: `(kite, instrument_token, symbol, cvd_engine, parent)` — unchanged
- `automation_signal` and `automation_state_signal` — unchanged
- All attributes that `main_window.py` accesses on the dialog — unchanged

---

## Risk Areas

| Risk | Mitigation |
|------|-----------|
| `_load_persisted_setup_values` touches 30+ widget refs in sequence | Keep it in `chart_dialog.py`; only delegate JSON file I/O to `settings_manager.py` |
| `_draw_confluence_lines` calls `self.strategy_detector.*` methods | Pass `strategy_detector` as argument to `SignalRenderer` |
| `_is_chop_regime` accesses `self.all_price_data` etc | All arrays become parameters |
| `_run_trade_simulation` has nested `_close_trade` closure using `nonlocal` | Closure pattern stays inside the extracted function — no change needed |
| QThread owned by dialog — must not move to sub-module | Thread creation stays in `_load_and_plot` in `chart_dialog.py` |
| Setup dialog widget refs used in `_load_persisted_setup_values` | Widget objects stay on dialog; only I/O layer extracted |
```