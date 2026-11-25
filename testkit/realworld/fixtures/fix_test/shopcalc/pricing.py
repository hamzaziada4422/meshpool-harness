from .discounts import coupon_rate, volume_rate
from .tax import vat_rate


def order_total(order) -> float:
    """Subtotal, minus the larger of the volume and coupon discounts (they do not stack), plus VAT; rounded
    to cents."""
    sub = order.subtotal()
    rate = max(volume_rate(order.item_count()), coupon_rate(order.coupon))
    net = sub * (1 - rate)
    return round(net * (1 + vat_rate(order.country)), 2)
