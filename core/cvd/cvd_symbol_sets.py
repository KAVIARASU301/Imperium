# core/cvd/cvd_symbol_sets.py

import json
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


class CVDSymbolSetManager:
    """
    Persistent storage for CVD-specific symbol sets.

    IMPORTANT:
    - Stores ONLY symbol names (e.g. HDFCBANK, SBIN)
    - No instrument tokens
    - No Kite dependency
    - No UI logic

    This keeps CVD sets fully independent from Market Monitor sets.
    """

    FILE_NAME = "cvd_symbol_sets.json"

    def __init__(self, base_dir: Path):
        """
        Parameters
        ----------
        base_dir : Path
            Base config directory (usually ~/.options_scalper or similar)
        """
        self.base_dir = base_dir
        self.file_path = self.base_dir / self.FILE_NAME
        self._ensure_file_exists()

    # ------------------------------------------------------------------

    def _ensure_file_exists(self):
        """Create empty file if missing."""
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            if not self.file_path.exists():
                self._write([])
        except Exception as e:
            logger.error("Failed to initialize CVD symbol set storage", exc_info=True)

    # ------------------------------------------------------------------

    def load_sets(self) -> List[Dict]:
        """Load all saved CVD symbol sets."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            logger.warning("Failed to load CVD symbol sets, returning empty list")
        return []

    # ------------------------------------------------------------------

    def save_sets(self, sets: List[Dict]):
        """Overwrite all CVD symbol sets."""
        self._write(sets)

    # ------------------------------------------------------------------

    def add_set(self, name: str, symbols: List[str]):
        """Add a new symbol set."""
        sets = self.load_sets()

        # Normalize
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not name or not symbols:
            return

        sets.append({
            "name": name.strip(),
            "symbols": symbols
        })

        self.save_sets(sets)

    # ------------------------------------------------------------------

    def delete_set(self, index: int):
        """Delete a symbol set by index."""
        sets = self.load_sets()
        if 0 <= index < len(sets):
            sets.pop(index)
            self.save_sets(sets)

    # ------------------------------------------------------------------

    def update_set_symbols(self, index: int, symbols: List[str]):
        """Update symbols of an existing set."""
        sets = self.load_sets()
        if not (0 <= index < len(sets)):
            return

        sets[index]["symbols"] = [s.strip().upper() for s in symbols if s.strip()]
        self.save_sets(sets)

    # ------------------------------------------------------------------

    def _write(self, data: List[Dict]):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logger.error("Failed to save CVD symbol sets", exc_info=True)
