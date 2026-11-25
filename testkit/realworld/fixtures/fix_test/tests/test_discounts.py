from shopcalc.discounts import coupon_rate, volume_rate


def test_coupon_case_insensitive():
    assert coupon_rate("welcome10") == 0.10


def test_small_orders():
    assert volume_rate(9) == 0.0
    assert volume_rate(10) == 0.05
