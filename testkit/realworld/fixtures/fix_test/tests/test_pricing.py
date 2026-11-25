from shopcalc import Line, Order, order_total


def test_plain_order():
    o = Order([Line("A", 2, 10.0)])
    assert order_total(o) == 23.8


def test_coupon_beats_volume():
    o = Order([Line("A", 12, 10.0)], coupon="VIP20")
    assert order_total(o) == round(120 * 0.8 * 1.19, 2)


def test_volume_discount_at_fifty_items():
    o = Order([Line("A", 50, 2.0)], country="FR")
    assert order_total(o) == round(100 * 0.90 * 1.20, 2)


def test_unknown_country_has_no_vat():
    o = Order([Line("A", 1, 9.99)], country="US")
    assert order_total(o) == 9.99
