# core/utils/config_manager.py
"""Configuration management for the application"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class ConfigManager:
    """Handles configuration file operations"""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            self.config_dir = Path.home() / ".imperium_desk"
        else:
            self.config_dir = Path(config_dir)

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.window_state_file = self.config_dir / "window_state.json"
        self.table_states_file = self.config_dir / "table_states.json"
        # --- ADD THIS LINE for the new state file ---
        self.dialog_states_file = self.config_dir / "dialog_states.json"
        self.journal_file = self.config_dir / "journal_entries.json"

        self.default_settings = {
            'trading_mode': 'live',
            'default_symbol': 'NIFTY',
            'default_product': 'MIS',
            'default_lots': 1,
            'auto_adjust_ladder': True,
            'auto_refresh': True,
            'refresh_interval': 2,
            'timeout': 7,
        }

    # ... (load_settings, save_settings, and other methods remain the same) ...
    def load_settings(self) -> Dict[str, Any]:
        """Load settings from a config file"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    settings = json.load(f)
                merged_settings = self.default_settings.copy()
                merged_settings.update(settings)
                return merged_settings
            else:
                return self.default_settings.copy()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading config: {e}")
            return self.default_settings.copy()

    def save_settings(self, settings: Dict[str, Any]) -> bool:
        """Save settings to a config file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(settings, f, indent=4)
            logger.info("Settings saved successfully")
            return True
        except IOError as e:
            logger.error(f"Error saving config: {e}")
            return False

    def reset_to_defaults(self) -> bool:
        """Reset config to default values"""
        return self.save_settings(self.default_settings)

    def save_window_state(self, state: Dict[str, Any]) -> bool:
        """Save window geometry and state"""
        try:
            with open(self.window_state_file, 'w') as f:
                json.dump(state, f, indent=4)
            return True
        except IOError as e:
            logger.error(f"Error saving window state: {e}")
            return False

    def load_window_state(self) -> Optional[Dict[str, Any]]:
        """Load window geometry and state"""
        try:
            if self.window_state_file.exists():
                with open(self.window_state_file, 'r') as f:
                    return json.load(f)
            return None
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading window state: {e}")
            return None

    def get_log_dir(self) -> Path:
        """Get the log directory path"""
        log_dir = self.config_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        return log_dir

    def get_cache_dir(self) -> Path:
        """Get the cache directory path"""
        cache_dir = self.config_dir / "cache"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir

    def clear_cache(self) -> bool:
        """Clear all cached data"""
        try:
            cache_dir = self.get_cache_dir()
            for file in cache_dir.iterdir():
                if file.is_file():
                    file.unlink()
            logger.info("Cache cleared successfully")
            return True
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return False

    def save_table_column_states(self, table_name: str, state: Dict[str, Any]) -> bool:
        """Saves the column state (e.g., widths) for a specific table."""
        all_states = {}
        try:
            if self.table_states_file.exists():
                with open(self.table_states_file, 'r') as f:
                    all_states = json.load(f)
        except (IOError, json.JSONDecodeError):
            all_states = {}

        all_states[table_name] = state
        try:
            with open(self.table_states_file, 'w') as f:
                json.dump(all_states, f, indent=4)
            return True
        except IOError as e:
            logger.error(f"Error saving table states: {e}")
            return False

    def load_table_column_states(self, table_name: str) -> Optional[Dict[str, Any]]:
        """Loads the column state for a specific table."""
        if not self.table_states_file.exists():
            return None
        try:
            with open(self.table_states_file, 'r') as f:
                all_states = json.load(f)
                return all_states.get(table_name)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error loading table states: {e}")
            return None

    # --- ADD THESE TWO NEW METHODS ---
    def save_dialog_state(self, dialog_name: str, state_data: str) -> bool:
        """Saves the state (e.g., geometry as a string) for a specific dialog."""
        all_states = {}
        try:
            if self.dialog_states_file.exists():
                with open(self.dialog_states_file, 'r') as f:
                    all_states = json.load(f)
        except (IOError, json.JSONDecodeError):
            all_states = {}

        all_states[dialog_name] = state_data
        try:
            with open(self.dialog_states_file, 'w') as f:
                json.dump(all_states, f, indent=4)
            return True
        except IOError as e:
            logger.error(f"Error saving dialog state for {dialog_name}: {e}")
            return False

    def load_dialog_state(self, dialog_name: str) -> Optional[str]:
        """Loads the state (e.g., geometry as a string) for a specific dialog."""
        if not self.dialog_states_file.exists():
            return None
        try:
            with open(self.dialog_states_file, 'r') as f:
                all_states = json.load(f)
                return all_states.get(dialog_name)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error loading dialog state for {dialog_name}: {e}")
            return None

    def load_journal_entries(self) -> List[Dict[str, Any]]:
        """Load journal entries from disk."""
        if not self.journal_file.exists():
            return []
        try:
            with open(self.journal_file, "r") as f:
                entries = json.load(f)
            if isinstance(entries, list):
                return entries
            return []
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error loading journal entries: {e}")
            return []

    def save_journal_entries(self, entries: List[Dict[str, Any]]) -> bool:
        """Save journal entries to disk."""
        try:
            with open(self.journal_file, "w") as f:
                json.dump(entries, f, indent=4)
            return True
        except IOError as e:
            logger.error(f"Error saving journal entries: {e}")
            return False


    def load_market_monitor_sets(self) -> List[Dict[str, Any]]:
        """
        Loads the saved symbol sets for the Market Monitor.
        Returns a default list if none are found.
        """
        # --- This get_setting method is part of your original file ---
        all_settings = self.load_settings()

        default_sets = [
            {"name": "Major Indices", "symbols": "NIFTY,BANKNIFTY,FINNIFTY,SENSEX"},
            {"name": "Set 2", "symbols": ""},
            {"name": "Set 3", "symbols": ""},
            {"name": "Set 4", "symbols": ""},
            {"name": "Set 5", "symbols": ""},
        ]
        return all_settings.get('market_monitor_sets', default_sets)

    def save_market_monitor_sets(self, sets: List[Dict[str, Any]]):
        """Saves the symbol sets for the Market Monitor."""
        # --- This save_setting method is part of your original file ---
        all_settings = self.load_settings()
        all_settings['market_monitor_sets'] = sets
        self.save_settings(all_settings)
