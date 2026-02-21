import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class SettingsManagerMixin:
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



    def _read_setup_json(self) -> dict:
        json_path = self._setup_json_file_path()
        if not json_path.exists():
            return {}
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed reading Auto Trader setup JSON: %s", exc)
            return {}



    def _write_setup_json(self, values: dict):
        json_path = self._setup_json_file_path()
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(values, f, indent=2)
        except Exception as exc:
            logger.warning("Failed writing Auto Trader setup JSON: %s", exc)


