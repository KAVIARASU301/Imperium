# Main Window Complexity Reduction — Additional Extraction Opportunities

## Why this analysis
`core/main_window.py` is still the highest-complexity module in the project (4,575 LOC with 158 methods), even after introducing `RiskController`, `DialogCoordinator`, and `MarketDataOrchestrator`. This document lists **other areas** that can be moved out to reduce coupling and improve testability.

## High-value candidates to move out next

### 1) CVD automation lifecycle manager
**Current signals of complexity in `ImperiumMainWindow`:**
- State persistence/recovery: `_persist_cvd_automation_state`, `_load_cvd_automation_state`, `_reconcile_failed_auto_entry`, `_reconcile_cvd_automation_positions`.
- Automation routing: `_on_cvd_automation_signal`, `_on_cvd_automation_market_state`.
- Rule enforcement: `_is_cvd_auto_cutoff_reached`, `_enforce_cvd_auto_cutoff_exit`, `_exit_position_automated`.
- Instrument mapping: `_get_atm_contract_for_signal`.

**Extraction target:** `core/cvd/cvd_automation_coordinator.py`

**Benefits:**
- Consolidates automation state machine and retries into one service.
- Makes CVD automation testable without a full Qt window.
- Reduces risk of UI-side regressions when changing automation rules.

---

### 2) Dialog presentation services (feature-specific)
`DialogCoordinator` exists, but many dialog methods are still inside `main_window.py` and include both lifecycle and business refresh logic.

**Methods to extract by domain:**
- **Order/history stack:** `_show_order_history_dialog`, `_refresh_order_history_from_ledger`, `_show_pending_orders_dialog`, `_update_pending_order_widgets`, `_cancel_order_by_id`.
- **Analytics stack:** `_show_pnl_history_dialog`, `_show_performance_dialog`.
- **Watch/monitor stack:** `_show_market_monitor_dialog`, `_show_watchlist_dialog`, `_show_cvd_market_monitor_dialog`, `_show_cvd_chart_dialog`, `_open_cvd_chart_after_subscription`, `_retarget_cvd_dialog`.

**Extraction targets:**
- `core/presentation/order_dialog_service.py`
- `core/presentation/analytics_dialog_service.py`
- `core/presentation/monitor_dialog_service.py`

**Benefits:**
- Keeps main window as composition root only.
- Enables per-feature maintenance without touching unrelated dialogs.

---

### 3) Account/health polling service
Polling and resilience logic is mixed into the UI class.

**Methods to extract:**
- `_periodic_api_health_check`, `_fetch_profile_safe`, `_fetch_margins_safe`, `_get_account_balance_safe`, `_update_account_info`.

**Extraction target:** `core/account/account_health_service.py`

**Benefits:**
- Encapsulates circuit-breaker and fallback behavior.
- Allows deterministic unit tests for API degradation scenarios.
- Prevents timer + network concerns from cluttering UI code.

---

### 4) Market subscription policy engine
Subscription decisioning is currently distributed between market callbacks and chart/dialog operations.

**Methods to extract:**
- `_update_market_subscriptions`, `_update_cvd_chart_symbol`, `_log_active_subscriptions`.

**Extraction target:** `core/market_data/subscription_policy.py`

**Benefits:**
- One source of truth for token set reconciliation.
- Easier to validate no-subscription-leak and re-subscribe-on-restore behavior.

---

### 5) Order execution flow facade (UI-independent)
Order placement, confirmation, and post-order behavior are still driven directly by `ImperiumMainWindow` methods.

**Methods to extract:**
- `_place_order`, `_on_paper_trade_update`, `_on_paper_order_rejected`.

**Extraction target:** `core/execution/execution_facade.py`

**Benefits:**
- Separates execution concerns from widget orchestration.
- Makes paper/live parity checks easier.
- Supports future CLI or headless automation reuse.

---

### 6) Position synchronization adapter
Position events and dialog sync methods still live in `main_window.py`.

**Methods to extract:**
- `_on_positions_updated`, `_on_position_added`, `_on_position_removed`, `_sync_positions_to_dialog`, `_on_refresh_completed`.

**Extraction target:** `core/positions/position_sync_adapter.py`

**Benefits:**
- Isolates push/pull update policy and throttling decisions.
- Enables targeted tests for stale/duplicate position events.

---

### 7) UI shell utilities bundle
A lot of shell-level UI concerns can move to a dedicated helper without touching business logic.

**Methods to extract:**
- Theme/layout helpers: `_apply_dark_theme`, `_setup_ui`, `_setup_status_footer`, `_create_main_widgets`, `_create_left_column`, `_create_center_column`, `_create_fourth_column`, `_setup_menu_bar`.
- Window UX helpers: resize grips, fade effects, status publication helpers.

**Extraction target:** `widgets/main_window_shell.py` (or `core/ui/main_window_shell.py`)

**Benefits:**
- Cleaner `ImperiumMainWindow.__init__`.
- Smaller diff surface for UI-only changes.

## Suggested extraction order (lowest risk first)
1. **Account/health polling service**
2. **Position synchronization adapter**
3. **Dialog presentation services**
4. **Market subscription policy engine**
5. **Execution flow facade**
6. **CVD automation lifecycle manager** (largest but highest payoff)

## Practical stop condition per extraction
For each extraction, stop only when all are true:
- `ImperiumMainWindow` method count decreases meaningfully.
- Moved service can be unit tested without creating full `QMainWindow`.
- No signal/slot behavior regressions in smoke checks.
- No direct widget references from business services (except explicit adapter layer).

## Quick metric target for next iteration
- Reduce `core/main_window.py` from ~4.6k LOC to **<3.2k LOC**.
- Reduce `ImperiumMainWindow` methods from 158 to **<110**.
- Cap any new service file at **<600 LOC** to avoid creating a new monolith.
