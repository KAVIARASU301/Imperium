from datetime import datetime, timedelta, time

TRADING_DAY_START = time(7, 30)  # 07:30 AM


def get_trading_day_str() -> str:
    """
    Returns trading day as YYYY-MM-DD string based on 07:30 AM cutoff.
    """
    now = datetime.now()

    trading_day = (
        now.date() - timedelta(days=1)
        if now.time() < TRADING_DAY_START
        else now.date()
    )

    return trading_day.isoformat()
