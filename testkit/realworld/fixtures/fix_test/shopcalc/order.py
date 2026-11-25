from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    qty: int
    unit_price: float


@dataclass
class Order:
    lines: list = field(default_factory=list)
    coupon: str | None = None
    country: str = "DE"

    def subtotal(self) -> float:
        return sum(l.qty * l.unit_price for l in self.lines)

    def item_count(self) -> int:
        return sum(l.qty for l in self.lines)
