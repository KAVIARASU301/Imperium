# utils/fii_dii_store.py
import json
from pathlib import Path
from datetime import date

DATA_DIR = Path.home() / ".imperium_desk"
DATA_DIR.mkdir(exist_ok=True)

DATA_FILE = DATA_DIR / "fii_dii_data.json"


class FIIDIIStore:
    def __init__(self):
        self._data = {}
        self.load()

    def load(self):
        if DATA_FILE.exists():
            with open(DATA_FILE, "r") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self._data, f, indent=2)

    def set_day_data(self, day: date, fii: dict, dii: dict):
        key = day.isoformat()
        self._data[key] = {
            "fii": fii,
            "dii": dii
        }
        self.save()

    def get_day_data(self, day: date):
        return self._data.get(day.isoformat())

    def get_all(self):
        return self._data
