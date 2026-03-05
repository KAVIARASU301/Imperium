# core/market_data/instrument_loader.py

"""
Robust instrument loader for options trading with caching and retry logic.
Supports index and stock derivatives from NFO/BFO exchanges.
"""

import logging
import time
import pickle
import os
from datetime import datetime, timedelta
from dataclasses import dataclass
from hashlib import md5
from typing import Dict, List, Any, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from kiteconnect import KiteConnect
from PySide6.QtCore import QThread, Signal

from core.market_data.instrument_index import InstrumentIndex

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 6

INDEX_SYMBOLS: Set[str] = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
INDEX_EXPIRY_LIMIT = 4
STOCK_EXPIRY_LIMIT = 2


# ─────────────────────────────────────────────────────────────
# InstrumentConfig
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class InstrumentConfig:
    """
    Configuration describing which instrument universe to load.
    """

    exchange_mode: str = "NFO_ONLY"
    symbol_mode: str = "INDICES_ONLY"
    preferred_symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
    expiry_depth: int = 1

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "InstrumentConfig":

        preferred = settings.get(
            "inst_preferred_symbols",
            ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
        )

        if not isinstance(preferred, list):
            preferred = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

        return cls(
            exchange_mode=settings.get("inst_exchange_mode", "NFO_ONLY"),
            symbol_mode=settings.get("inst_symbol_mode", "INDICES_ONLY"),
            preferred_symbols=tuple(str(s).upper() for s in preferred),
            expiry_depth=int(settings.get("inst_expiry_depth", 1)),
        )

    def cache_key(self) -> str:
        payload = {
            "exchange_mode": self.exchange_mode,
            "symbol_mode": self.symbol_mode,
            "preferred_symbols": list(self.preferred_symbols),
            "expiry_depth": self.expiry_depth,
            "schema_version": CACHE_SCHEMA_VERSION,
        }
        return md5(repr(payload).encode("utf-8")).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────
# InstrumentLoader
# ─────────────────────────────────────────────────────────────
class InstrumentLoader(QThread):

    instruments_loaded = Signal(dict)
    error_occurred = Signal(str)
    progress_update = Signal(str)
    loading_progress = Signal(int)

    def __init__(
        self,
        kite_client: KiteConnect,
        config: Optional[InstrumentConfig] = None,
        cache_dir: Optional[str] = None,
    ):
        super().__init__()

        self.kite = kite_client
        self.config = config or InstrumentConfig()

        self.cache_dir = cache_dir or os.path.expanduser("~/.imperium_desk/cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        cache_key = self.config.cache_key()

        self.cache_file = os.path.join(self.cache_dir, f"instruments_{cache_key}.pkl")
        self.cache_info_file = os.path.join(
            self.cache_dir, f"instruments_{cache_key}_info.pkl"
        )

        self._stop_requested = False

        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=1,
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ─────────────────────────────────────────────────────────

    def stop(self):
        self._stop_requested = True

    # ─────────────────────────────────────────────────────────
    # Cache
    # ─────────────────────────────────────────────────────────

    def is_cache_valid(self) -> bool:
        try:

            if not os.path.exists(self.cache_file):
                return False

            if not os.path.exists(self.cache_info_file):
                return False

            with open(self.cache_info_file, "rb") as f:
                info = pickle.load(f)

            if info.get("schema_version") != CACHE_SCHEMA_VERSION:
                return False

            age = datetime.now() - info["timestamp"]
            return age < timedelta(hours=12)

        except Exception as e:
            logger.error(f"Cache validation failed: {e}")
            return False

    def load_cached_instruments(self):

        try:
            with open(self.cache_file, "rb") as f:
                return pickle.load(f)

        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            return None

    def save_instruments_to_cache(self, symbol_data):

        try:

            with open(self.cache_file, "wb") as f:
                pickle.dump(symbol_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            info = {
                "timestamp": datetime.now(),
                "schema_version": CACHE_SCHEMA_VERSION,
                "symbols_count": len(symbol_data),
            }

            with open(self.cache_info_file, "wb") as f:
                pickle.dump(info, f, protocol=pickle.HIGHEST_PROTOCOL)

            logger.info("Instrument cache saved")

        except Exception as e:
            logger.error(f"Cache save failed: {e}")

    # ─────────────────────────────────────────────────────────
    # Fetch
    # ─────────────────────────────────────────────────────────

    def fetch_instruments_with_retry(self, exchange: str):

        max_retries = 5
        base_delay = 2

        for attempt in range(1, max_retries + 1):

            try:
                return self.kite.instruments(exchange)

            except Exception as e:

                if attempt < max_retries:
                    delay = base_delay * attempt
                    logger.warning(
                        f"{exchange} fetch failed (attempt {attempt}/{max_retries}): {e}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"All retries failed for {exchange}")
                    return []

        return []

    # ─────────────────────────────────────────────────────────
    # Processing
    # ─────────────────────────────────────────────────────────

    def process_instruments(self, instruments):

        symbol_data = {}
        total_instruments = len(instruments)
        processed_count = 0

        for inst in instruments:

            if self._stop_requested:
                raise Exception("Cancelled")

            symbol_name = inst.get("name")

            if not symbol_name:
                continue

            if symbol_name not in symbol_data:
                symbol_data[symbol_name] = {
                    "lot_size": inst["lot_size"],
                    "tick_size": inst["tick_size"],
                    "exchange": inst["exchange"],
                    "instrument_token": None,
                    "expiries": set(),
                    "strikes": set(),
                    "instruments": [],
                    "futures": [],
                }

            inst_type = inst.get("instrument_type")

            if inst_type in ("CE", "PE"):

                symbol_data[symbol_name]["expiries"].add(inst["expiry"])
                symbol_data[symbol_name]["strikes"].add(inst["strike"])
                symbol_data[symbol_name]["instruments"].append(inst)

            elif inst_type == "FUT":

                symbol_data[symbol_name]["futures"].append(
                    {
                        "instrument_token": inst["instrument_token"],
                        "expiry": inst["expiry"],
                    }
                )

            processed_count += 1

            if processed_count % 1000 == 0:

                progress = 70 + int((processed_count / total_instruments) * 20)
                self.loading_progress.emit(min(progress, 90))

        # ── expiry pruning ──

        for symbol, data in symbol_data.items():

            sorted_expiries = sorted(data["expiries"])

            if self.config.expiry_depth < 0:

                expiry_limit = (
                    INDEX_EXPIRY_LIMIT
                    if symbol in INDEX_SYMBOLS
                    else STOCK_EXPIRY_LIMIT
                )

            else:
                expiry_limit = self.config.expiry_depth + 1

            filtered_expiries = sorted_expiries[:expiry_limit]

            allowed = set(filtered_expiries)

            data["expiries"] = filtered_expiries

            data["instruments"] = [
                inst for inst in data["instruments"] if inst.get("expiry") in allowed
            ]

            data["strikes"] = sorted({inst["strike"] for inst in data["instruments"]})

            data["futures"] = sorted(
                data["futures"], key=lambda item: item["expiry"]
            )

        return symbol_data

    # ─────────────────────────────────────────────────────────
    # Thread
    # ─────────────────────────────────────────────────────────

    def run(self):

        try:

            self.loading_progress.emit(0)

            if self.is_cache_valid():

                self.progress_update.emit("Loading cached instruments")

                cached = self.load_cached_instruments()

                if cached:
                    self.loading_progress.emit(100)
                    self.instruments_loaded.emit(cached)
                    return

            # fetch exchanges

            exchanges = ["NFO"] if self.config.exchange_mode == "NFO_ONLY" else ["NFO", "BFO"]

            all_instruments = []

            for ex in exchanges:
                data = self.fetch_instruments_with_retry(ex)
                all_instruments.extend(data)

            if not all_instruments:
                raise Exception("Failed to load instruments")

            self.loading_progress.emit(60)

            symbol_data = self.process_instruments(all_instruments)

            # symbol filtering

            if self.config.symbol_mode == "INDICES_ONLY":

                symbol_data = {
                    k: v for k, v in symbol_data.items() if k in INDEX_SYMBOLS
                }

            elif self.config.symbol_mode == "CUSTOM":

                preferred = set(self.config.preferred_symbols)

                symbol_data = {
                    k: v for k, v in symbol_data.items() if k in preferred
                }

            self.save_instruments_to_cache(symbol_data)

            index = InstrumentIndex(self.cache_dir)
            index.build_from_symbol_data(symbol_data)

            self.loading_progress.emit(100)

            self.instruments_loaded.emit(symbol_data)

        except Exception as e:

            if not self._stop_requested:

                logger.error(f"InstrumentLoader failed: {e}")

                cached = self.load_cached_instruments()

                if cached:
                    logger.warning("Using expired cache fallback")
                    self.instruments_loaded.emit(cached)
                else:
                    self.error_occurred.emit(str(e))
