import sys
import logging

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from kiteconnect import KiteConnect
from core.login_manager import LoginManager
from core.main_window import ImperiumMainWindow
from core.token_manager import TokenManager
from core.paper_trading_manager import PaperTradingManager
from core.config import setup_logging
import locale

try:
    locale.setlocale(locale.LC_ALL, 'en_IN.UTF-8')
except locale.Error:
    locale.setlocale(locale.LC_ALL, '')

setup_logging()
logger = logging.getLogger(__name__)


def main():
    """Main function to run the application."""
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("assets/imperium_desk_icon.png"))

    temp_widget = QWidget()  # Temporary widget as QMessageBox parent
    temp_widget.hide()
    logger.info("Imperium Desk starting...")

    login_manager = LoginManager()

    if login_manager.exec() != QMessageBox.DialogCode.Accepted:
        logger.warning("Login process was not completed. Exiting.")
        return

    access_token = login_manager.get_access_token()
    trading_mode = login_manager.get_trading_mode()
    api_creds = login_manager.get_api_creds()

    if not all([access_token, trading_mode, api_creds]):
        QMessageBox.critical(temp_widget, "Login Failed", "Could not retrieve session details after login.")
        return

    # Always create a real Kite client instance for fetching data like instruments.
    real_kite_client = KiteConnect(api_key=api_creds['api_key'], access_token=access_token, timeout=30)

    # Determine which object to use for actual trading (live vs. paper)
    try:
        if trading_mode == 'live':
            logger.info("Starting in LIVE TRADING mode.")
            trader = real_kite_client
        else:
            logger.info("Starting in PAPER TRADING mode.")
            trader = PaperTradingManager()

        window = ImperiumMainWindow(
            trader=trader,
            real_kite_client=real_kite_client,
            api_key=api_creds['api_key'],
            access_token=access_token
        )
        window.show()
        sys.exit(app.exec())

    except Exception as e:
        logger.critical(f"Failed to initialize main window: {e}", exc_info=True)
        QMessageBox.critical(temp_widget, "Application Error", f"A critical error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()