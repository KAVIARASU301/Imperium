import os
import pickle
from typing import Any, Dict


class InstrumentIndex:
    """
    Ultra-fast lookup structure for options/futures token lookups.
    """

    def __init__(self, cache_dir: str):
        self.cache_file = os.path.join(cache_dir, "instrument_index.pkl")
        self.data: Dict[str, Any] = {}

    def load(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False

        with open(self.cache_file, "rb") as f:
            self.data = pickle.load(f)

        return True

    def save(self):
        with open(self.cache_file, "wb") as f:
            pickle.dump(self.data, f, protocol=pickle.HIGHEST_PROTOCOL)

    def build_from_symbol_data(self, symbol_data):
        index = {}

        for symbol, data in symbol_data.items():
            symbol_index = {
                "expiries": data.get("expiries", []),
                "strikes": data.get("strikes", []),
                "options": {},
                "futures": {},
            }

            for inst in data.get("instruments", []):
                key = (
                    inst.get("expiry"),
                    inst.get("strike"),
                    inst.get("instrument_type"),
                )
                symbol_index["options"][key] = inst.get("instrument_token")

            for fut in data.get("futures", []):
                symbol_index["futures"][fut.get("expiry")] = fut.get("instrument_token")

            index[symbol] = symbol_index

        self.data = index
        self.save()

    def get_option_token(self, symbol, expiry, strike, option_type):
        return self.data[symbol]["options"].get((expiry, strike, option_type))

    def get_future_token(self, symbol, expiry):
        return self.data[symbol]["futures"].get(expiry)

    def to_symbol_data_stub(self) -> Dict[str, Dict[str, Any]]:
        """Convert index payload to lightweight symbol data for fast startup."""
        symbol_data: Dict[str, Dict[str, Any]] = {}

        for symbol, data in self.data.items():
            symbol_data[symbol] = {
                "lot_size": 1,
                "tick_size": 0.05,
                "exchange": "NFO",
                "instrument_token": None,
                "expiries": data.get("expiries", []),
                "strikes": data.get("strikes", []),
                "instruments": [],
                "futures": [
                    {"expiry": expiry, "instrument_token": token}
                    for expiry, token in data.get("futures", {}).items()
                ],
            }

        return symbol_data
