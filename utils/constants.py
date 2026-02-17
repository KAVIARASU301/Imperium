"""Constants used throughout the application"""


# Application info
APP_NAME = "Imperium Desk"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Trading Tools Inc."

# Trading constants
DEFAULT_LOT_SIZES = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50
}

# Strike step rules based on NSE standards
STRIKE_STEP_RULES = {
    'NIFTY': {
        (0, 10000): 50,
        (10000, 20000): 50,
        (20000, float('inf')): 100
    },
    'BANKNIFTY': {
        (0, 20000): 100,
        (20000, float('inf')): 100
    },
    'FINNIFTY': {
        (0, float('inf')): 50
    },
    'MIDCPNIFTY': {
        (0, float('inf')): 25
    }
}

# UI constants
REFRESH_INTERVAL_MS = 2000  # 2 seconds
MAX_STRIKE_RANGE = 10
DEFAULT_STRIKE_RANGE = 5
MIN_OI_THRESHOLD = 100000

# Color scheme
COLORS = {
    "profit": "#4CAF50",
    "loss": "#F44336",
    "neutral": "#6c757d",
    "buy": "#007bff",
    "sell": "#ffc107",
    "background": "#0f0f0f",
    "foreground": "#1a1a1a",
    "text": "#ffffff",
    "text_muted": "#888888",
    "border": "#333333",
    "hover": "#2a2a2a",
    "selected": "#3a3a3a"
}

# Market timings (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Order limits
MAX_ORDER_SIZE = 1800  # Maximum quantity per order as per exchange
MAX_ORDERS_PER_SECOND = 10
ORDER_RATE_LIMIT_WINDOW = 1  # seconds

# Risk management defaults
DEFAULT_MAX_LOSS = 20000
DEFAULT_MAX_POSITIONS = 20
DEFAULT_PROFIT_TARGET = 20.0  # percentage
DEFAULT_STOP_LOSS = 10.0  # percentage

# Index mappings for price lookup
INDEX_SYMBOL_MAP = {
    'NIFTY': 'NIFTY 50',
    'BANKNIFTY': 'NIFTY BANK',
    'FINNIFTY': 'NIFTY FIN SERVICE',
    'MIDCPNIFTY': 'NIFTY MID SELECT'
}

# Exchange codes
EXCHANGE_NFO = "NFO"
EXCHANGE_NSE = "NSE"

# Product types
PRODUCT_MIS = "MIS"
PRODUCT_NRML = "NRML"

# Order types
ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"

# Transaction types
TRANSACTION_TYPE_BUY = "BUY"
TRANSACTION_TYPE_SELL = "SELL"

# Validity types
VALIDITY_DAY = "DAY"
VALIDITY_IOC = "IOC"

# Error messages
ERROR_MESSAGES = {
    "NO_INSTRUMENTS": "Instruments not loaded. Please wait...",
    "NO_CONNECTION": "No connection to Kite API",
    "INVALID_TOKEN": "Invalid access token. Please login again.",
    "MARKET_CLOSED": "Market is closed",
    "MAX_LOSS_REACHED": "Maximum daily loss reached",
    "INSUFFICIENT_MARGIN": "Insufficient margin for this order",
    "ORDER_FAILED": "Order placement failed",
    "POSITION_EXIT_FAILED": "Failed to exit position"
}

# Success messages
SUCCESS_MESSAGES = {
    "ORDER_PLACED": "Order placed successfully",
    "POSITION_EXITED": "Position exited successfully",
    "ALL_POSITIONS_EXITED": "All positions exited successfully",
    "SETTINGS_SAVED": "Settings saved successfully",
    "DATA_REFRESHED": "Data refreshed successfully"
}