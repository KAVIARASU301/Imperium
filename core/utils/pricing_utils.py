def calculate_smart_limit_price(contract) -> float:
    """
    Calculate intelligent default limit price using LTP and bid-ask spread.
    """
    base_price = contract.ltp
    bid_price = contract.bid
    ask_price = contract.ask
    tick_size = 0.05

    if base_price <= 0:
        return ask_price if ask_price > 0 else 1.0

    spread_valid = 0 < bid_price < ask_price

    if spread_valid:
        spread = ask_price - bid_price
        mid_price = (bid_price + ask_price) / 2
        spread_pct = spread / mid_price * 100

        if spread_pct <= 0.5:
            target = bid_price + tick_size
        elif spread_pct <= 1.5:
            target = mid_price
        else:
            target = ask_price - tick_size

        return round(max(target, tick_size) / tick_size) * tick_size

    # Fallback: use buffered LTP pricing
    if base_price < 5:
        buffer = max(0.15, base_price * 0.05)
    elif base_price < 20:
        buffer = max(0.25, base_price * 0.03)
    elif base_price < 100:
        buffer = min(base_price * 0.02, 2.0)
    else:
        buffer = min(base_price * 0.015, 5.0)

    return round((base_price + buffer) / tick_size) * tick_size
