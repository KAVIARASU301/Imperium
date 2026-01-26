# sounds_robust.py - Robust Sound System with Multiple Backends
# ==============================================================

import os
import logging
import subprocess
import threading
from typing import Optional, Dict
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtCore import QUrl, QObject, QTimer
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class SoundManager(QObject):
    """
    Robust sound manager with multiple audio backends
    Falls back to system commands if Qt audio fails
    """

    _instance: Optional['SoundManager'] = None
    _initialized: bool = False

    def __new__(cls) -> 'SoundManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        super().__init__()
        self.sounds: Dict[str, Optional[QSoundEffect]] = {}
        self.sound_files_paths: Dict[str, str] = {}
        self.default_volume = 0.9
        self.enabled = True
        self.use_qt_audio = True
        self.use_system_fallback = True
        self.qt_sounds_initialized = False  # NEW: Track Qt initialization

        # Sound file mappings - only required sounds
        self.sound_files = {
            'alert': 'alert.wav',
            'success': 'success.wav',
            'error': 'error.wav',
            'order_placed': 'placed.wav'
        }

        self._detect_audio_system()
        self._load_sound_file_paths()  # CHANGED: Only load file paths, not Qt sounds
        self._initialized = True
        logger.info("SoundManager initialized successfully")

    def _detect_audio_system(self):
        """Detect available audio systems"""
        # Check for common Linux audio players
        self.audio_players = []

        players_to_check = [
            ('paplay', ['paplay']),  # PulseAudio
            ('aplay', ['aplay']),  # ALSA
            ('ffplay', ['ffplay', '-nodisp', '-autoexit']),  # FFmpeg
            ('cvlc', ['cvlc', '--play-and-exit', '--intf', 'dummy']),  # VLC
            ('mpg123', ['mpg123', '-q']),  # mpg123
        ]

        for name, cmd in players_to_check:
            try:
                result = subprocess.run(['which', name], capture_output=True, timeout=2)
                if result.returncode == 0:
                    self.audio_players.append((name, cmd))
                    logger.info(f"Found audio player: {name}")
            except Exception:
                continue

        if self.audio_players:
            logger.info(f"Available audio players: {[p[0] for p in self.audio_players]}")
        else:
            logger.warning("No system audio players found")

    def find_project_root(self):
        """Find the project root directory"""
        # Start from the current file location
        current_file = os.path.abspath(__file__)
        current_dir = os.path.dirname(current_file)

        # Look for indicators of project root
        indicators = ['main.py', 'sounds.py', 'assets', 'core']

        # Check current directory and parent directories
        check_dir = current_dir
        for _ in range(5):  # Check up to 5 levels up
            if any(os.path.exists(os.path.join(check_dir, indicator)) for indicator in indicators):
                return check_dir
            parent = os.path.dirname(check_dir)
            if parent == check_dir:  # Reached root
                break
            check_dir = parent

        # Fallback to current working directory
        return os.getcwd()

    def find_assets_directory(self):
        """Find the assets directory with sound files"""
        project_root = self.find_project_root()

        # Try different possible locations
        possible_assets_dirs = [
            os.path.join(project_root, "assets"),
            os.path.join(project_root, "sounds"),
            os.path.join(project_root, "audio"),
            os.path.join(os.getcwd(), "assets"),
            os.path.join(os.getcwd(), "sounds"),
            "assets",
            "sounds"
        ]

        for assets_dir in possible_assets_dirs:
            if os.path.exists(assets_dir) and os.path.isdir(assets_dir):
                # Check if it actually contains sound files
                sound_files = ['alert.wav', 'success.wav', 'error.wav', 'placed.wav']
                if any(os.path.exists(os.path.join(assets_dir, sf)) for sf in sound_files):
                    return assets_dir

        return None

    def _load_sound_file_paths(self):
        """Load sound file paths (but don't create Qt sounds yet)"""
        # Find assets directory dynamically
        assets_dir = self.find_assets_directory()

        if assets_dir is None:
            logger.error("❌ Could not find assets directory with sound files!")
            logger.info("Expected sound files: alert.wav, success.wav, error.wav, placed.wav")
            logger.info(f"Current working directory: {os.getcwd()}")
            logger.info(f"Script location: {os.path.dirname(os.path.abspath(__file__))}")
            return

        logger.info(f"✅ Using assets directory: {assets_dir}")

        for sound_name, filename in self.sound_files.items():
            sound_path = self._find_sound_file(filename, assets_dir)
            if sound_path:
                self.sound_files_paths[sound_name] = sound_path
                # Initialize sounds dict but don't create Qt sounds yet
                self.sounds[sound_name] = None
            else:
                self.sounds[sound_name] = None
                self.sound_files_paths[sound_name] = ""

        # Log sound loading status
        loaded_count = sum(1 for path in self.sound_files_paths.values() if path)
        total_count = len(self.sound_files)
        logger.info(f"Sound files found: {loaded_count}/{total_count}")

        # Debug info
        for name, path in self.sound_files_paths.items():
            if path:
                logger.info(f"  ✅ {name}: {path}")
            else:
                logger.warning(f"  ❌ {name}: NOT FOUND")

    def _initialize_qt_sounds(self):
        """Initialize Qt sounds only when QApplication is available"""
        if self.qt_sounds_initialized:
            return

        app = QApplication.instance()
        if app is None:
            logger.debug("QApplication not available, skipping Qt sound initialization")
            return

        logger.info("Initializing Qt sounds...")
        for sound_name, sound_path in self.sound_files_paths.items():
            if sound_path and self.use_qt_audio:
                self.sounds[sound_name] = self._create_qt_sound_effect(sound_path)

        self.qt_sounds_initialized = True
        logger.info("Qt sounds initialized successfully")

    def _find_sound_file(self, filename: str, assets_dir: str) -> str:
        """Find sound file with fallback extensions"""
        base_name = filename.replace('.wav', '').replace('.mp3', '')
        possible_files = [f"{base_name}.wav", f"{base_name}.mp3"]

        for file_to_try in possible_files:
            sound_file_path = os.path.join(assets_dir, file_to_try)
            if os.path.exists(sound_file_path):
                logger.info(f"✓ Found sound file: {sound_file_path}")
                return os.path.abspath(sound_file_path)

        logger.warning(f"✗ Sound file not found: {filename}")
        return ""

    def _create_qt_sound_effect(self, sound_path: str) -> Optional[QSoundEffect]:
        """Create a QSoundEffect for the given file"""
        try:
            app = QApplication.instance()
            if app is None:
                # Don't log warning here - this is normal during initialization
                return None

            sound_effect = QSoundEffect(app)
            file_url = QUrl.fromLocalFile(sound_path)
            sound_effect.setSource(file_url)
            sound_effect.setVolume(self.default_volume)
            sound_effect.setLoopCount(1)

            # Wait a moment for loading
            import time
            time.sleep(0.1)

            if sound_effect.source().isEmpty():
                logger.warning(f"Qt sound source is empty for: {sound_path}")
                return None

            logger.info(f"✓ Qt sound loaded: {sound_path}")
            return sound_effect

        except Exception as e:
            logger.warning(f"Failed to create Qt sound effect for {sound_path}: {e}")
            return None

    def _play_with_qt(self, sound_name: str) -> bool:
        """Play sound using Qt QSoundEffect"""
        if not self.use_qt_audio:
            return False

        # Initialize Qt sounds if not done yet
        if not self.qt_sounds_initialized:
            self._initialize_qt_sounds()

        sound_effect = self.sounds.get(sound_name)
        if sound_effect is None:
            return False

        try:
            if hasattr(sound_effect, 'isPlaying') and sound_effect.isPlaying():
                sound_effect.stop()

            sound_effect.play()
            logger.debug(f"🔊 Qt playing sound: {sound_name}")
            return True

        except Exception as e:
            logger.warning(f"Qt sound playback failed for {sound_name}: {e}")
            return False

    def _play_with_system(self, sound_name: str) -> bool:
        """Play sound using system audio player"""
        if not self.use_system_fallback:
            return False

        sound_path = self.sound_files_paths.get(sound_name, "")
        if not sound_path or not os.path.exists(sound_path):
            return False

        for player_name, player_cmd in self.audio_players:
            try:
                cmd = player_cmd + [sound_path]
                # Run in background thread to avoid blocking
                threading.Thread(
                    target=self._run_system_player,
                    args=(cmd, player_name, sound_name),
                    daemon=True
                ).start()
                logger.debug(f"🔊 System playing sound: {sound_name} with {player_name}")
                return True

            except Exception as e:
                logger.debug(f"Failed to play with {player_name}: {e}")
                continue

        return False

    def _run_system_player(self, cmd, player_name, sound_name):
        """Run system audio player in background"""
        try:
            subprocess.run(cmd, timeout=5, capture_output=True)
        except Exception as e:
            logger.debug(f"System player {player_name} failed for {sound_name}: {e}")

    def _play_sound_safe(self, sound_name: str) -> bool:
        """Safely play a sound effect with fallback"""
        if not self.enabled:
            return False

        if sound_name not in self.sounds:
            logger.warning(f"Unknown sound: {sound_name}")
            return False

        # Try Qt audio first
        if self._play_with_qt(sound_name):
            return True

        # Fallback to system audio
        if self._play_with_system(sound_name):
            return True

        logger.warning(f"All audio methods failed for: {sound_name}")
        return False

    # ==========================================================================
    # PUBLIC SOUND METHODS
    # ==========================================================================

    def play_alert(self) -> bool:
        """Play alert sound (for price alerts, notifications)"""
        return self._play_sound_safe('alert')

    def play_success(self) -> bool:
        """Play success sound (for completed orders, successful operations)"""
        return self._play_sound_safe('success')

    def play_error(self) -> bool:
        """Play error sound (for failed orders, errors, rejections)"""
        return self._play_sound_safe('error')

    def play_order_placed(self) -> bool:
        """Play order placed sound (when order is submitted)"""
        return self._play_sound_safe('order_placed')

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def set_volume(self, volume: float):
        """Set volume for Qt sounds (0.0 to 1.0)"""
        self.default_volume = max(0.0, min(1.0, volume))

        # Update existing Qt sounds
        for sound_effect in self.sounds.values():
            if sound_effect:
                sound_effect.setVolume(self.default_volume)

        logger.info(f"Qt sound volume set to: {self.default_volume}")

    def enable_sounds(self, enabled: bool = True):
        """Enable or disable all sounds"""
        self.enabled = enabled
        status = "enabled" if enabled else "disabled"
        logger.info(f"Sounds {status}")

    def disable_sounds(self):
        """Disable all sounds"""
        self.enable_sounds(False)

    def test_all_sounds(self):
        """Test all available sounds with delays"""
        app = QApplication.instance()
        if app is None:
            logger.warning("Cannot test sounds: No QApplication instance")
            return False

        logger.info("🔊 Testing all sounds...")

        self.play_alert()
        if app:
            QTimer.singleShot(1000, self.play_order_placed)
            QTimer.singleShot(2000, self.play_success)
            QTimer.singleShot(3000, self.play_error)

        logger.info("Sound test sequence started")
        return True

    def test_sound_immediate(self, sound_name: str) -> bool:
        """Test a specific sound immediately"""
        logger.info(f"Testing {sound_name} sound immediately...")

        # Try both methods
        qt_result = self._play_with_qt(sound_name)
        if not qt_result:
            system_result = self._play_with_system(sound_name)
            logger.info(f"{sound_name}: Qt={'✅' if qt_result else '❌'}, System={'✅' if system_result else '❌'}")
            return system_result

        logger.info(f"{sound_name}: Qt=✅")
        return True

    def get_sound_status(self) -> Dict[str, bool]:
        """Get status of all sounds"""
        return {
            name: bool(self.sound_files_paths.get(name))
            for name in self.sound_files.keys()
        }

    def get_audio_info(self) -> Dict:
        """Get detailed audio system information"""
        return {
            'qt_audio_available': QApplication.instance() is not None,
            'sound_files_found': {name: bool(path) for name, path in self.sound_files_paths.items()},
            'system_players': [p[0] for p in self.audio_players],
            'qt_sounds_loaded': {name: (sound is not None) for name, sound in self.sounds.items()},
            'enabled': self.enabled
        }


# =============================================================================
# GLOBAL SOUND MANAGER INSTANCE
# =============================================================================

_sound_manager = SoundManager()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def play_alert() -> bool:
    return _sound_manager.play_alert()


def play_success() -> bool:
    return _sound_manager.play_success()


def play_error() -> bool:
    return _sound_manager.play_error()


def play_order_placed() -> bool:
    return _sound_manager.play_order_placed()


def test_all_sounds():
    return _sound_manager.test_all_sounds()


def test_sound_immediate(sound_name: str) -> bool:
    return _sound_manager.test_sound_immediate(sound_name)


def get_sound_status() -> Dict[str, bool]:
    return _sound_manager.get_sound_status()


def get_audio_info() -> Dict:
    return _sound_manager.get_audio_info()


def set_sound_volume(volume: float):
    _sound_manager.set_volume(volume)


def enable_sounds(enabled: bool = True):
    _sound_manager.enable_sounds(enabled)


def disable_sounds():
    _sound_manager.disable_sounds()


# =============================================================================
# DIAGNOSTIC FUNCTION
# =============================================================================

def diagnose_audio():
    """Comprehensive audio system diagnosis"""
    print("🔍 AUDIO SYSTEM DIAGNOSIS")
    print("=" * 50)

    info = get_audio_info()

    print(f"Qt Application Available: {'✅' if info['qt_audio_available'] else '❌'}")
    print(f"Sound System Enabled: {'✅' if info['enabled'] else '❌'}")

    print("\n📁 Sound Files:")
    for name, found in info['sound_files_found'].items():
        print(f"  {name}: {'✅ Found' if found else '❌ Missing'}")

    print("\n🔊 Qt Sound Loading:")
    for name, loaded in info['qt_sounds_loaded'].items():
        print(f"  {name}: {'✅ Loaded' if loaded else '❌ Failed'}")

    print(f"\n🖥️ System Audio Players: {info['system_players']}")

    print("\n🧪 Testing sounds...")
    for sound_name in ['alert', 'success', 'error', 'order_placed']:
        result = test_sound_immediate(sound_name)
        print(f"  {sound_name}: {'✅ Played' if result else '❌ Failed'}")

    print("=" * 50)


if __name__ == "__main__":
    # Quick test when run directly
    from PySide6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    diagnose_audio()

    input("\nPress Enter to exit...")