# core/utils/instrument_loader.py

"""Robust instrument loader for options trading with caching and retry logic.

Supports filtered index and stock options from NFO exchange.
"""

import logging
import time
import pickle
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from PySide6.QtCore import QThread, Signal
from kiteconnect import KiteConnect
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 4

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY"}
INDEX_EXPIRY_LIMIT = 4
STOCK_EXPIRY_LIMIT = 2

ALLOWED_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    # Top NFO stocks (filtered to keep load low)
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "SBIN",
    "BHARTIARTL",
    "LT",
    "ITC",
    "HINDUNILVR",
    "KOTAKBANK",
    "AXISBANK",
    "BAJFINANCE",
    "HCLTECH",
    "MARUTI",
    "ASIANPAINT",
    "SUNPHARMA",
    "TITAN",
    "ULTRACEMCO",
    "NTPC",
}

class InstrumentLoader(QThread):
    """Background thread for loading filtered NFO instruments with robust retry logic and caching"""

    instruments_loaded = Signal(dict)
    error_occurred = Signal(str)
    progress_update = Signal(str)
    loading_progress = Signal(int)

    def __init__(self, kite_client: KiteConnect, cache_dir: Optional[str] = None):
        super().__init__()
        self.kite = kite_client
        self.cache_dir = cache_dir or os.path.expanduser("~/.options_badger/cache")
        self.cache_file = os.path.join(self.cache_dir, "options_instruments_cache.pkl")
        self.cache_info_file = os.path.join(self.cache_dir, "options_cache_info.pkl")
        self._stop_requested = False

        # Create cache directory if it doesn't exist
        os.makedirs(self.cache_dir, exist_ok=True)

        # Configure requests session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=1,
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

    def stop(self) -> None:
        """Request the thread to stop"""
        self._stop_requested = True
        logger.info("Stop requested for InstrumentLoader")

    def is_cache_valid(self) -> bool:
        """Check if cached instruments are still valid (within 12 hours for options)"""
        try:
            if not os.path.exists(self.cache_file) or not os.path.exists(self.cache_info_file):
                return False

            with open(self.cache_info_file, 'rb') as f:
                cache_info: Dict[str, Any] = pickle.load(f)

            cache_time = cache_info.get('timestamp')
            if not cache_time:
                return False

            cache_version = cache_info.get('schema_version', 1)
            if cache_version != CACHE_SCHEMA_VERSION:
                logger.info(
                    f"Cache schema mismatch ({cache_version} != {CACHE_SCHEMA_VERSION}), refreshing"
                )
                return False

            # Check if cache is less than 12 hours old
            cache_age = datetime.now() - cache_time
            is_valid = cache_age < timedelta(hours=12)

            if is_valid:
                logger.info(f"Using cached instruments (age: {cache_age})")
            else:
                logger.info(f"Cache expired (age: {cache_age})")

            return is_valid

        except Exception as e:
            logger.error(f"Error checking cache validity: {e}")
            return False

    def load_cached_instruments(self) -> Optional[Dict[str, Any]]:
        """Load processed instruments from cache"""
        try:
            with open(self.cache_file, 'rb') as f:
                symbol_data: Dict[str, Any] = pickle.load(f)

            total_instruments = sum(len(data['instruments']) for data in symbol_data.values())
            logger.info(f"Loaded {len(symbol_data)} symbols with {total_instruments} instruments from cache")
            return symbol_data

        except Exception as e:
            logger.error(f"Error loading cached instruments: {e}")
            return None

    def save_instruments_to_cache(self, symbol_data: Dict[str, Any]) -> None:
        """Save processed instruments to cache with timestamp"""
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(symbol_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            total_instruments = sum(len(data['instruments']) for data in symbol_data.values())
            cache_info: Dict[str, Any] = {
                'timestamp': datetime.now(),
                'schema_version': CACHE_SCHEMA_VERSION,
                'symbols_count': len(symbol_data),
                'instruments_count': total_instruments
            }
            with open(self.cache_info_file, 'wb') as f:
                pickle.dump(cache_info, f, protocol=pickle.HIGHEST_PROTOCOL)

            logger.info(f"Cached {len(symbol_data)} symbols with {total_instruments} instruments")

        except Exception as e:
            logger.error(f"Error saving instruments to cache: {e}")

    def process_instruments(self, instruments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process raw exchange instruments into organized symbol data (filtered & fast)"""
        self.progress_update.emit("Processing instruments...")
        self.loading_progress.emit(70)

        symbol_data: Dict[str, Any] = {}
        total_instruments = len(instruments)
        processed_count = 0

        for inst in instruments:
            if self._stop_requested:
                raise Exception("Operation cancelled by user")

            symbol_name = inst.get("name")

            # 🔥 HARD FILTER: only required index symbols
            if symbol_name not in ALLOWED_SYMBOLS:
                continue

            # 🔥 Initialize symbol FIRST (critical fix)
            if symbol_name not in symbol_data:
                symbol_data[symbol_name] = {
                    "lot_size": inst["lot_size"],
                    "tick_size": inst["tick_size"],
                    "expiries": set(),
                    "strikes": set(),
                    "instruments": [],
                    "futures": [],
                    "exchange": inst["exchange"],
                    "instrument_token": None,  # index token if present
                }

            # 🔥 Capture index instrument token safely
            if inst.get("segment") == "INDICES":
                symbol_data[symbol_name]["instrument_token"] = inst["instrument_token"]
                continue

            inst_type = inst.get("instrument_type")

            # 🔥 Options
            if inst_type in ("CE", "PE"):
                symbol_data[symbol_name]["expiries"].add(inst["expiry"])
                symbol_data[symbol_name]["strikes"].add(inst["strike"])
                symbol_data[symbol_name]["instruments"].append(inst)

            # 🔥 Futures
            elif inst_type == "FUT":
                symbol_data[symbol_name]["futures"].append({
                    "instrument_token": inst["instrument_token"],
                    "expiry": inst["expiry"]
                })

            processed_count += 1

            # Progress update (cheap & smooth)
            if processed_count % 1000 == 0:
                progress = 70 + int((processed_count / total_instruments) * 20)
                self.loading_progress.emit(min(progress, 90))

        # 🔥 Finalize sets → sorted lists
        self.progress_update.emit("Finalizing instruments...")
        self.loading_progress.emit(90)

        pre_prune_count = sum(len(data["instruments"]) for data in symbol_data.values())

        for symbol, data in symbol_data.items():
            if self._stop_requested:
                raise Exception("Operation cancelled by user")

            sorted_expiries = sorted(data["expiries"])
            expiry_limit = INDEX_EXPIRY_LIMIT if symbol in INDEX_SYMBOLS else STOCK_EXPIRY_LIMIT
            filtered_expiries = sorted_expiries[:expiry_limit]
            allowed_expiries = set(filtered_expiries)

            data["expiries"] = filtered_expiries
            data["instruments"] = [
                inst for inst in data["instruments"] if inst.get("expiry") in allowed_expiries
            ]
            data["strikes"] = sorted({inst["strike"] for inst in data["instruments"]})
            data["futures"] = [
                fut for fut in sorted(data["futures"], key=lambda item: item["expiry"])
                if fut["expiry"] in allowed_expiries
            ]

        post_prune_count = sum(len(data["instruments"]) for data in symbol_data.values())
        logger.info(
            f"Processed {len(symbol_data)} symbols from {total_instruments} instruments (filtered); "
            f"options kept after expiry cap: {post_prune_count}/{pre_prune_count}"
        )

        return symbol_data

    def fetch_instruments_with_retry(self, exchange: str) -> List[Dict[str, Any]]:
        """Fetch instruments from specified exchange with robust retry logic"""
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            if self._stop_requested:
                logger.info(f"Stop requested, aborting {exchange} instrument fetch")
                raise Exception("Operation cancelled by user")

            try:
                progress_msg = f"Attempt {attempt + 1}/{max_retries}: Fetching {exchange} instruments..."
                self.progress_update.emit(progress_msg)
                logger.info(f"Attempt {attempt + 1}: Loading {exchange} instruments...")

                # Update progress based on exchange
                base_progress = 10 if exchange == "NFO" else 40
                self.loading_progress.emit(base_progress + (attempt * 5))

                # Set increasing timeout for each retry
                original_timeout = getattr(self.kite, 'timeout', 7)
                self.kite.timeout = min(45, original_timeout + (attempt * 8))

                # Fetch instruments
                instruments = self.kite.instruments(exchange)

                if not instruments:
                    raise Exception(f"No {exchange} instruments received from API")

                logger.info(f"Successfully fetched {len(instruments)} {exchange} instruments")
                return instruments

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Attempt {attempt + 1} failed for {exchange}: {error_msg}")

                if self._stop_requested:
                    raise e

                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + (attempt * 2)
                    delay = min(delay, 45)

                    logger.info(f"Retrying {exchange} fetch in {delay} seconds...")
                    self.progress_update.emit(f"Retry in {delay}s... ({error_msg})")

                    for i in range(int(delay)):
                        if self._stop_requested:
                            raise Exception("Operation cancelled by user")
                        time.sleep(1)
                else:
                    logger.error(f"All {exchange} fetch retries failed")
                    # Don't fail completely if one exchange fails
                    logger.warning(f"Continuing without {exchange} data")
                    return []

        return []

    def run(self) -> None:
        """Load filtered NFO instruments (fast & lightweight)."""
        try:
            self.loading_progress.emit(0)

            # 1️⃣ Use cache if valid
            if self.is_cache_valid():
                self.progress_update.emit("Loading cached instruments...")
                self.loading_progress.emit(60)

                cached_symbol_data = self.load_cached_instruments()
                if cached_symbol_data:
                    self.loading_progress.emit(100)
                    self.instruments_loaded.emit(cached_symbol_data)
                    return

            # 2️⃣ Fetch ONLY NFO
            self.progress_update.emit("Fetching NFO instruments...")
            self.loading_progress.emit(10)

            nfo_instruments = self.fetch_instruments_with_retry("NFO")
            if not nfo_instruments:
                raise Exception("Failed to load NFO instruments")

            self.loading_progress.emit(60)

            # 3️⃣ Process
            symbol_data = self.process_instruments(nfo_instruments)

            # 4️⃣ Cache & emit
            self.save_instruments_to_cache(symbol_data)
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

    def clear_cache(self) -> None:
        """Clear the instrument cache"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            if os.path.exists(self.cache_info_file):
                os.remove(self.cache_info_file)
            logger.info("Instrument cache cleared")
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")

    def get_cache_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the current cache"""
        try:
            if os.path.exists(self.cache_info_file):
                with open(self.cache_info_file, 'rb') as f:
                    cache_info: Dict[str, Any] = pickle.load(f)
                    return cache_info
        except Exception as e:
            logger.error(f"Error reading cache info: {e}")
        return None

    def force_refresh(self) -> None:
        """Force refresh by clearing cache and reloading"""
        self.clear_cache()
        if not self.isRunning():
            self.start()
