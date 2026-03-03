from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "market_data" / "subscription_policy.py"
spec = spec_from_file_location("subscription_policy", MODULE_PATH)
subscription_policy = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(subscription_policy)
MarketSubscriptionPolicy = subscription_policy.MarketSubscriptionPolicy


class DummyMarketDataWorker:
    def __init__(self):
        self.calls = []

    def set_instruments(self, tokens):
        self.calls.append(set(tokens))


class DummyStrikeLadder:
    def __init__(self, visible_tokens, strike_tokens):
        self.visible_tokens = set(visible_tokens)
        self.strike_tokens = set(strike_tokens)

    def get_visible_contract_tokens(self):
        return set(self.visible_tokens)

    def get_contract_tokens_for_strikes(self, strikes):
        if strikes:
            return set(self.strike_tokens)
        return set()


class DummyBuyExitPanel:
    def __init__(self, strikes):
        self._strikes = set(strikes)

    def get_subscription_strikes(self):
        return set(self._strikes)


class DummyMainWindow:
    def __init__(self, mode, visible_tokens, strike_tokens, subscription_strikes, cvd_tokens):
        self.settings = {"layout_mode": mode}
        self.strike_ladder = DummyStrikeLadder(visible_tokens, strike_tokens)
        self.buy_exit_panel = DummyBuyExitPanel(subscription_strikes)
        self.active_cvd_tokens = set(cvd_tokens)
        self._last_subscription_set = set()
        self.market_data_worker = DummyMarketDataWorker()


def test_manual_mode_uses_visible_strikes_tokens_plus_cvd():
    window = DummyMainWindow(
        mode="manual",
        visible_tokens={1, 2, 3},
        strike_tokens={10, 11},
        subscription_strikes={12300.0},
        cvd_tokens={99},
    )
    policy = MarketSubscriptionPolicy(window)

    policy.update_market_subscriptions()

    assert window.market_data_worker.calls == [{1, 2, 3, 99}]


def test_auto_mode_uses_restricted_buy_exit_strike_tokens_plus_cvd():
    window = DummyMainWindow(
        mode="auto",
        visible_tokens={1, 2, 3},
        strike_tokens={10, 11},
        subscription_strikes={12300.0},
        cvd_tokens={99},
    )
    policy = MarketSubscriptionPolicy(window)

    policy.update_market_subscriptions()

    assert window.market_data_worker.calls == [{10, 11, 99}]
