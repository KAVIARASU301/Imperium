import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SetupSettingsMigrationMixin:
    """One-time migration helper from legacy JSON setup storage to QSettings."""

    def _settings_key_prefix(self) -> str:
        return f"chart_setup/{self.instrument_token}"

    @staticmethod
    def _global_settings_key_prefix() -> str:
        return "chart_setup/global"

    @staticmethod
    def _setup_json_file_path() -> Path:
        config_dir = Path.home() / ".imperium_desk"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "auto_trader_setup.json"

    def _read_setup_json_for_migration(self) -> dict:
        if getattr(self, "_settings", None) is None:
            return {}

        migration_flag = "chart_setup/json_migrated"
        if self._settings.value(migration_flag, False, type=bool):
            return {}

        json_path = self._setup_json_file_path()
        if not json_path.exists():
            return {}

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return {}
            return payload
        except Exception as exc:
            logger.warning("Failed reading Auto Trader setup JSON for migration: %s", exc)
            return {}

    def _mark_setup_json_migrated(self):
        if getattr(self, "_settings", None) is None:
            return
        self._settings.setValue("chart_setup/json_migrated", True)
        self._settings.sync()
