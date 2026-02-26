import logging
import platform
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QSoundEffect

logger = logging.getLogger(__name__)


class SoundService(QObject):
    """Centralized service for all notification sound and system volume operations."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._active_sound_effects: list[QSoundEffect] = []

    def play_notification(
        self,
        success: bool = True,
        ensure_volume: bool = True,
        flow: str = "entry",
    ) -> None:
        """Play order notification sound and optionally normalize volume temporarily."""
        try:
            original_volume = self.get_system_volume() if ensure_volume else None
            if ensure_volume and original_volume is not None:
                self.set_system_volume(80)

            filename = self._resolve_notification_sound_filename(success=success, flow=flow)
            sound_path = self._resolve_sound_path(filename)
            if sound_path is None:
                logger.warning("Sound file not found for notification: %s", filename)
                return

            sound_effect = QSoundEffect(self)
            sound_effect.setSource(QUrl.fromLocalFile(str(sound_path)))
            sound_effect.setVolume(1.0)
            sound_effect.play()

            # Keep reference until playback ends to avoid GC stopping sound early.
            self._active_sound_effects.append(sound_effect)
            QTimer.singleShot(1500, lambda: self._cleanup_sound_effect(sound_effect))

            if ensure_volume and original_volume is not None:
                QTimer.singleShot(1200, lambda: self.set_system_volume(original_volume))

        except Exception as exc:
            logger.error("Error playing notification sound: %s", exc)

    @staticmethod
    def _resolve_notification_sound_filename(success: bool, flow: str) -> str:
        if not success:
            return "fail.wav"
        return "exit.wav" if flow == "exit" else "Pop.wav"

    def get_system_volume(self) -> Optional[int]:
        """Get system output volume as percentage (0-100)."""
        try:
            system = platform.system()

            if system == "Linux":
                volume = self._get_linux_volume()
                if volume is not None:
                    return volume
            elif system == "Windows":
                return self._get_windows_volume()
            elif system == "Darwin":
                return self._get_macos_volume()
            return None
        except Exception as exc:
            logger.debug("Could not get system volume: %s", exc)
            return None

    def set_system_volume(self, volume: int) -> None:
        """Set system output volume percentage (0-100)."""
        try:
            clamped_volume = max(0, min(100, volume))
            system = platform.system()

            if system == "Linux":
                self._set_linux_volume(clamped_volume)
            elif system == "Windows":
                self._set_windows_volume(clamped_volume)
            elif system == "Darwin":
                self._set_macos_volume(clamped_volume)
        except Exception as exc:
            logger.debug("Could not set system volume: %s", exc)

    def _resolve_sound_path(self, filename: str) -> Optional[Path]:
        root = Path(__file__).resolve().parents[2]
        sound_path = root / "assets" / filename
        return sound_path if sound_path.exists() else None

    def _cleanup_sound_effect(self, sound_effect: QSoundEffect) -> None:
        if sound_effect in self._active_sound_effects:
            self._active_sound_effects.remove(sound_effect)

    @staticmethod
    def _get_linux_volume() -> Optional[int]:
        try:
            result = subprocess.run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                for part in result.stdout.split():
                    if "%" in part:
                        return int(part.rstrip("%"))
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

        try:
            result = subprocess.run(
                ["amixer", "get", "Master"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                import re

                match = re.search(r"\[(\d+)%\]", result.stdout)
                if match:
                    return int(match.group(1))
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    @staticmethod
    def _set_linux_volume(volume: int) -> None:
        try:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"], timeout=1, check=False)
            logger.debug("Set system volume to %s%% (PulseAudio)", volume)
            return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            subprocess.run(["amixer", "set", "Master", f"{volume}%"], timeout=1, check=False)
            logger.debug("Set system volume to %s%% (ALSA)", volume)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _get_windows_volume() -> Optional[int]:
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            return int(volume.GetMasterVolumeLevelScalar() * 100)
        except ImportError:
            logger.debug("pycaw not installed - install with: pip install pycaw comtypes")
        except Exception:
            pass
        return None

    @staticmethod
    def _set_windows_volume(volume: int) -> None:
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume_interface = interface.QueryInterface(IAudioEndpointVolume)
            volume_interface.SetMasterVolumeLevelScalar(volume / 100.0, None)
            logger.debug("Set system volume to %s%% (Windows)", volume)
        except ImportError:
            logger.debug("pycaw not installed")
        except Exception:
            pass

    @staticmethod
    def _get_macos_volume() -> Optional[int]:
        try:
            result = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    @staticmethod
    def _set_macos_volume(volume: int) -> None:
        try:
            subprocess.run(["osascript", "-e", f"set volume output volume {volume}"], timeout=1, check=False)
            logger.debug("Set system volume to %s%% (macOS)", volume)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
