# core/utils/instrument_loader.py

"""Robust instrument loader for options trading with caching and retry logic"""

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


class InstrumentLoader(QThread):
    """Background thread for loading NFO instruments with robust retry logic and caching"""

    instruments_loaded = Signal(dict)
    error_occurred = Signal(str)
    progress_update = Signal(str)  # For status updates
    loading_progress = Signal(int)  # Progress percentage (0-100)

    def __init__(self, kite_client: KiteConnect, cache_dir: Optional[str] = None):
        super().__init__()
        self.kite = kite_client
        self.cache_dir = cache_dir or os.path.expanduser("~/.options_badger/cache")
        self.cache_file = os.path.join(self.cache_dir, "nfo_instruments_cache.pkl")
        self.cache_info_file = os.path.join(self.cache_dir, "nfo_cache_info.pkl")
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
        self.session.mount("https://", adapter)  # Only HTTPS for security

    def stop(self) -> None:
        """Request the thread to stop"""
        self._stop_requested = True
        logger.info("Stop requested for InstrumentLoader")

    def is_cache_valid(self) -> bool:
        """Check if cached NFO instruments are still valid (within 12 hours for options)"""
        try:
            if not os.path.exists(self.cache_file) or not os.path.exists(self.cache_info_file):
                return False

            with open(self.cache_info_file, 'rb') as f:
                cache_info: Dict[str, Any] = pickle.load(f)

            cache_time = cache_info.get('timestamp')
            if not cache_time:
                return False

            # Check if cache is less than 12 hours old (options data changes more frequently)
            cache_age = datetime.now() - cache_time
            is_valid = cache_age < timedelta(hours=12)

            if is_valid:
                logger.info(f"Using cached NFO instruments (age: {cache_age})")
            else:
                logger.info(f"NFO cache expired (age: {cache_age})")

            return is_valid

        except Exception as e:
            logger.error(f"Error checking NFO cache validity: {e}")
            return False

    def load_cached_instruments(self) -> Optional[Dict[str, Any]]:
        """Load processed NFO instruments from cache"""
        try:
            with open(self.cache_file, 'rb') as f:
                symbol_data: Dict[str, Any] = pickle.load(f)

            total_instruments = sum(len(data['instruments']) for data in symbol_data.values())
            logger.info(f"Loaded {len(symbol_data)} symbols with {total_instruments} instruments from cache")
            return symbol_data

        except Exception as e:
            logger.error(f"Error loading cached NFO instruments: {e}")
            return None

    def save_instruments_to_cache(self, symbol_data: Dict[str, Any]) -> None:
        """Save processed instruments to cache with timestamp"""
        try:
            # Save processed symbol data
            with open(self.cache_file, 'wb') as f:
                pickle.dump(symbol_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            # Save cache info
            total_instruments = sum(len(data['instruments']) for data in symbol_data.values())
            cache_info: Dict[str, Any] = {
                'timestamp': datetime.now(),
                'symbols_count': len(symbol_data),
                'instruments_count': total_instruments
            }
            with open(self.cache_info_file, 'wb') as f:
                pickle.dump(cache_info, f, protocol=pickle.HIGHEST_PROTOCOL)

            logger.info(f"Cached {len(symbol_data)} symbols with {total_instruments} instruments")

        except Exception as e:
            logger.error(f"Error saving NFO instruments to cache: {e}")

    def process_instruments(self, instruments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process raw NFO instruments into organized symbol data"""
        self.progress_update.emit("Processing instruments data...")
        self.loading_progress.emit(70)

        symbol_data: Dict[str, Any] = {}
        processed_count = 0
        total_instruments = len(instruments)

        for inst in instruments:
            if self._stop_requested:
                raise Exception("Operation cancelled by user")

            symbol_name = inst['name']

            # Ensure the symbol is initialized in symbol_data
            if symbol_name not in symbol_data:
                symbol_data[symbol_name] = {
                    'lot_size': inst['lot_size'],
                    'tick_size': inst['tick_size'],
                    'expiries': set(),
                    'strikes': set(),
                    'instruments': [],
                    'futures': []
                }

            # Process CE and PE options
            if inst['instrument_type'] in ['CE', 'PE']:
                # Update lot size if it's different (though unlikely for same symbol)
                symbol_data[symbol_name]['lot_size'] = inst['lot_size']
                symbol_data[symbol_name]['tick_size'] = inst['tick_size']

                # Add expiry and strike data
                symbol_data[symbol_name]['expiries'].add(inst['expiry'])
                symbol_data[symbol_name]['strikes'].add(inst['strike'])
                symbol_data[symbol_name]['instruments'].append(inst)

            # Process FUT (Futures) contracts
            elif inst['instrument_type'] == 'FUT':
                symbol_data[symbol_name]['futures'].append(inst)

            processed_count += 1

            # Update progress every 1000 instruments
            if processed_count % 1000 == 0:
                progress = 70 + int((processed_count / total_instruments) * 20)
                self.loading_progress.emit(min(progress, 90))

        # Convert sets to sorted lists for better usability
        self.progress_update.emit("Finalizing data structure...")
        self.loading_progress.emit(90)

        for symbol in symbol_data:
            if self._stop_requested:
                raise Exception("Operation cancelled by user")

            symbol_data[symbol]['expiries'] = sorted(list(symbol_data[symbol]['expiries']))
            symbol_data[symbol]['strikes'] = sorted(list(symbol_data[symbol]['strikes']))

        logger.info(
            f"Processed {len(symbol_data)} option and futures symbols from {total_instruments} instruments")
        return symbol_data

    def fetch_nfo_instruments_with_retry(self) -> List[Dict[str, Any]]:
        """Fetch NFO instruments with robust retry logic"""
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            if self._stop_requested:
                logger.info("Stop requested, aborting NFO instrument fetch")
                raise Exception("Operation cancelled by user")

            try:
                progress_msg = f"Attempt {attempt + 1}/{max_retries}: Fetching NFO instruments..."
                self.progress_update.emit(progress_msg)
                logger.info(f"Attempt {attempt + 1}: Loading NFO instruments...")

                # Update progress
                self.loading_progress.emit(10 + (attempt * 10))

                # Set increasing timeout for each retry
                original_timeout = getattr(self.kite, 'timeout', 7)
                self.kite.timeout = min(45, original_timeout + (attempt * 8))  # Longer timeouts for NFO

                # Fetch NFO instruments
                instruments = self.kite.instruments("NFO")

                if not instruments:
                    raise Exception("No NFO instruments received from API")

                logger.info(f"Successfully fetched {len(instruments)} NFO instruments")
                self.loading_progress.emit(60)
                return instruments

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Attempt {attempt + 1} failed: {error_msg}")

                if self._stop_requested:
                    raise e

                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + (attempt * 2)
                    delay = min(delay, 45)  # Cap at 45 seconds for NFO

                    logger.info(f"Retrying NFO fetch in {delay} seconds...")
                    self.progress_update.emit(f"Retry in {delay}s... ({error_msg})")

                    for i in range(int(delay)):
                        if self._stop_requested:
                            raise Exception("Operation cancelled by user")
                        time.sleep(1)
                else:
                    logger.error("All NFO fetch retries failed")
                    raise Exception(
                        f"Failed to load NFO instruments after {max_retries} attempts. Last error: {error_msg}")

        # This should never be reached due to the raise in the else block above,
        # but added for type checker satisfaction
        raise Exception("Unexpected exit from retry loop")

    def run(self) -> None:
        """Load NFO instruments with caching and robust error handling"""
        try:
            self.loading_progress.emit(0)

            # Check if we have valid cached instruments first
            if self.is_cache_valid():
                self.progress_update.emit("Loading cached NFO instruments...")
                self.loading_progress.emit(50)

                cached_symbol_data = self.load_cached_instruments()
                if cached_symbol_data:
                    self.progress_update.emit("Using cached NFO instruments")
                    self.loading_progress.emit(100)
                    self.instruments_loaded.emit(cached_symbol_data)
                    return

            # If no valid cache, fetch from API
            self.progress_update.emit("Fetching fresh NFO instruments from API...")
            self.loading_progress.emit(5)

            raw_instruments = self.fetch_nfo_instruments_with_retry()

            if self._stop_requested:
                return

            # Process the raw instruments
            symbol_data = self.process_instruments(raw_instruments)

            if not self._stop_requested:
                # Save to cache
                self.save_instruments_to_cache(symbol_data)

                self.progress_update.emit(f"Loaded {len(symbol_data)} option symbols successfully")
                self.loading_progress.emit(100)
                self.instruments_loaded.emit(symbol_data)

        except Exception as e:
            if not self._stop_requested:
                error_msg = str(e)
                logger.error(f"NFO InstrumentLoader failed: {error_msg}")

                # Try to fall back to cached instruments even if expired
                if "cancelled" not in error_msg.lower():
                    logger.info("Attempting to use expired NFO cache as fallback...")
                    self.progress_update.emit("Trying expired cache as fallback...")

                    cached_symbol_data = self.load_cached_instruments()
                    if cached_symbol_data:
                        logger.warning("Using expired cached NFO instruments as fallback")
                        self.progress_update.emit("Using cached NFO instruments (fallback)")
                        self.loading_progress.emit(100)
                        self.instruments_loaded.emit(cached_symbol_data)
                        return

                self.loading_progress.emit(0)
                self.error_occurred.emit(error_msg)

    def clear_cache(self) -> None:
        """Clear the NFO instrument cache"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            if os.path.exists(self.cache_info_file):
                os.remove(self.cache_info_file)
            logger.info("NFO instrument cache cleared")
        except Exception as e:
            logger.error(f"Error clearing NFO cache: {e}")

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