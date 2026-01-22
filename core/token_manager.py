import os
import json
import logging
from datetime import date
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TokenManager:
    """Manages secure storage of API credentials and access tokens."""

    def __init__(self):
        self.app_dir = Path.home() / ".options_badger"
        self.app_dir.mkdir(exist_ok=True)
        self.credentials_file = self.app_dir / "credentials.enc"
        self.token_file = self.app_dir / "token.enc"
        self.key_file = self.app_dir / ".key"
        self._cipher = self._get_or_create_cipher()

    def _get_or_create_cipher(self) -> Fernet:
        """Get an existing encryption key or create a new one."""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            os.chmod(self.key_file, 0o600)
        return Fernet(key)

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        """Saves encrypted API credentials."""
        try:
            data = json.dumps({"api_key": api_key, "api_secret": api_secret})
            encrypted = self._cipher.encrypt(data.encode('utf-8'))
            with open(self.credentials_file, 'wb') as f:
                f.write(encrypted)
            logger.info("API credentials saved securely.")
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")

    def load_credentials(self) -> Optional[Dict[str, str]]:
        """Loads and decrypts API credentials."""
        if not self.credentials_file.exists():
            return None
        try:
            with open(self.credentials_file, 'rb') as f:
                encrypted = f.read()
            decrypted = self._cipher.decrypt(encrypted)
            return json.loads(decrypted.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to load or decrypt credentials: {e}")
            return None

    def save_token_data(self, token_data: Dict[str, Any]) -> None:
        """Saves encrypted session token data, including date and mode."""
        try:
            token_data['date'] = str(date.today())
            data_to_save = json.dumps(token_data).encode('utf-8')
            encrypted_data = self._cipher.encrypt(data_to_save)
            with open(self.token_file, 'wb') as f:
                f.write(encrypted_data)
            logger.info("Session token data saved successfully.")
        except Exception as e:
            logger.error(f"Failed to save session token data: {e}")

    def load_token_data(self) -> Optional[Dict[str, Any]]:
        """Loads and decrypts token data if it's from today."""
        if not self.token_file.exists():
            return None
        try:
            with open(self.token_file, 'rb') as f:
                encrypted_data = f.read()
            decrypted_data = self._cipher.decrypt(encrypted_data)
            token_data = json.loads(decrypted_data.decode('utf-8'))
            if token_data.get("date") == str(date.today()):
                logger.info("Loaded valid session token for today.")
                return token_data
            else:
                logger.warning("Session token has expired.")
                self.clear_token_data()
                return None
        except Exception as e:
            logger.error(f"Failed to load token data: {e}")
            self.clear_token_data()
            return None

    def clear_token_data(self) -> None:
        """Clears the stored token file."""
        if self.token_file.exists():
            self.token_file.unlink()