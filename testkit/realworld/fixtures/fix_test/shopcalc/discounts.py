COUPONS = {"WELCOME10": 0.10, "VIP20": 0.20}


def volume_rate(item_count: int) -> float:
    """Volume discount: 5% from 10 items, 10% from 50 items (inclusive thresholds)."""
    if item_count > 50:
        return 0.10
    if item_count >= 10:
        return 0.05
    return 0.0


def coupon_rate(code):
    return COUPONS.get((code or "").upper(), 0.0)
