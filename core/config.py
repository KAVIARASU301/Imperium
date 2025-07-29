# core/config.py
"""
Application configuration and utilities
Purpose: Central configuration management and logging setup
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

# Application constants
APP_NAME = "Options Scalper"
APP_VERSION = "1.0.0"

# Trading constants
DEFAULT_LOT_SIZES = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50
}

# UI constants
REFRESH_INTERVAL_MS = 1000  # 2 seconds
MAX_STRIKE_RANGE = 10
DEFAULT_STRIKE_RANGE = 3

# Color scheme
COLORS = {
    "profit": "#28a745",
    "loss": "#dc3545",
    "neutral": "#6c757d",
    "buy": "#007bff",
    "sell": "#ffc107",
    "background": "#f8f9fa",
    "text": "#212529"
}


def setup_logging():
    """Configure application logging"""
    log_dir = Path.home() / ".options_scalper" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create timestamp for a log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"scalper_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Reduce noise from libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('kiteconnect').setLevel(logging.WARNING)

    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(f"Options Scalper {APP_VERSION} starting...")
    logger.info(f"Log file: {log_file}")