"""Small fixture used for live review acceptance testing."""


def order_total(price_cents: int, quantity: int, discount_percent: int) -> int:
    if price_cents < 0 or quantity < 1 or not 0 <= discount_percent <= 100:
        raise ValueError("Invalid order")
    return round(price_cents * quantity * (100 - discount_percent) / 100)
