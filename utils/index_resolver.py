from typing import Optional, Tuple

# Canonical index definitions
INDEX_METADATA = {
    "NIFTY": {
        "exchange": "NSE",
        "ltp_symbol": "NIFTY 50",
        "search_name": "NIFTY 50"
    },
    "BANKNIFTY": {
        "exchange": "NSE",
        "ltp_symbol": "NIFTY BANK",
        "search_name": "NIFTY BANK"
    },
    "FINNIFTY": {
        "exchange": "NSE",
        "ltp_symbol": "NIFTY FIN SERVICE",
        "search_name": "NIFTY FIN SERVICE"
    },
    "MIDCPNIFTY": {
        "exchange": "NSE",
        "ltp_symbol": "NIFTY MID SELECT",
        "search_name": "NIFTY MID SELECT"
    },
    "SENSEX": {
        "exchange": "BSE",          # 🔥 CRITICAL
        "ltp_symbol": "SENSEX",
        "search_name": "SENSEX"
    }
}


def resolve_index(symbol: str) -> Optional[dict]:
    return INDEX_METADATA.get(symbol.upper())
