from decimal import Decimal


def stock_level(qty, par) -> str | None:
    """Return 'critical', 'low', or None. Takes raw values so it can also
    be used to evaluate a hypothetical future quantity."""
    if par is None or par <= 0:
        return None
    if qty <= par * Decimal("0.25"):
        return "critical"
    if qty <= par:
        return "low"
    return None