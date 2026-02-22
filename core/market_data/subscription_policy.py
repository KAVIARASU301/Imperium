import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.main_window import ImperiumMainWindow

logger = logging.getLogger(__name__)


class MarketSubscriptionPolicy:
    """Single source of truth for market-data token reconciliation and CVD chart retargeting."""

    def __init__(self, main_window: "ImperiumMainWindow"):
        self.main_window = main_window

    def update_market_subscriptions(self):
        w = self.main_window
        required_tokens = set()

        if hasattr(w.strike_ladder, "get_visible_contract_tokens"):
            required_tokens.update(w.strike_ladder.get_visible_contract_tokens())
        required_tokens.update(w.active_cvd_tokens)

        if required_tokens == w._last_subscription_set:
            logger.debug("Subscription set unchanged. Skipping update.")
            return

        logger.info(
            "Subscription diff detected | Old: %s | New: %s",
            len(w._last_subscription_set),
            len(required_tokens),
        )
        w._last_subscription_set = required_tokens.copy()
        w.market_data_worker.set_instruments(required_tokens)

    def update_cvd_chart_symbol(self, symbol: str, cvd_token: int, suffix: str = ""):
        """Update menu-opened (header-linked) CVD single chart dialog with new symbol."""
        w = self.main_window
        if w.header_linked_cvd_token is None:
            return

        dialog = w.cvd_single_chart_dialogs.get(w.header_linked_cvd_token)
        if not dialog or dialog.isHidden():
            w.header_linked_cvd_token = None
            return

        w._retarget_cvd_dialog(
            dialog=dialog,
            old_token=w.header_linked_cvd_token,
            new_token=cvd_token,
            symbol=symbol,
            suffix=suffix,
        )
        w.header_linked_cvd_token = cvd_token

    def log_active_subscriptions(self):
        """Diagnostic method to verify CVD tokens are subscribed."""
        w = self.main_window
        if not hasattr(w, "market_data_worker"):
            return

        active_tokens = w.market_data_worker.subscribed_tokens
        cvd_tokens = w.active_cvd_tokens

        logger.info("[CVD] Active CVD tokens: %s", cvd_tokens)
        logger.info("[CVD] Subscribed tokens: %s", len(active_tokens))

        missing = cvd_tokens - active_tokens
        if missing:
            logger.warning("[CVD] Tokens NOT subscribed: %s", missing)
        else:
            logger.info("[CVD] All CVD tokens properly subscribed ✓")
