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
    def __init__(self, visible_tokens):
        self.visible_tokens = set(visible_tokens)

    def get_visible_contract_tokens(self):
        return set(self.visible_tokens)


class DummyMainWindow:
    def __init__(self, visible_tokens, cvd_tokens):
        self.strike_ladder = DummyStrikeLadder(visible_tokens)
        self.active_cvd_tokens = set(cvd_tokens)
        self._last_subscription_set = set()
        self.market_data_worker = DummyMarketDataWorker()


def test_subscriptions_use_visible_strike_tokens_plus_cvd():
    window = DummyMainWindow(
        visible_tokens={1, 2, 3},
        cvd_tokens={99},
    )
    policy = MarketSubscriptionPolicy(window)

    policy.update_market_subscriptions()

    assert window.market_data_worker.calls == [{1, 2, 3, 99}]
