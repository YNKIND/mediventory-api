from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app import models


def recalc_item_stock_from_lots(db: Session, item: models.Item) -> None:
    """For a lot-tracked item, set stock_qty to the sum of active lots' remaining qty.
    Does not commit; caller commits."""
    total = db.query(models.ItemLot).filter(
        models.ItemLot.item_id == item.id,
        models.ItemLot.status == "active",
    ).all()
    item.stock_qty = sum((lot.qty_remaining for lot in total), Decimal("0"))


def mark_expired_lots(db: Session, clinic_id: int) -> int:
    """Flip any active lot past its expiry to 'expired'. Returns how many changed.
    Does not touch stock; expiry is informational until someone acts on it."""
    now = datetime.now(timezone.utc)
    lots = db.query(models.ItemLot).filter(
        models.ItemLot.clinic_id == clinic_id,
        models.ItemLot.status == "active",
        models.ItemLot.expiry_date.isnot(None),
        models.ItemLot.expiry_date < now,
    ).all()
    for lot in lots:
        lot.status = "expired"
    return len(lots)


def consume_from_lots(db: Session, item: models.Item, qty: Decimal) -> list[tuple[int, Decimal]]:
    """Draw `qty` from an item's active lots, earliest expiry first (FEFO).
    Returns list of (lot_id, qty_taken). Raises ValueError if not enough.
    Does not commit; caller commits."""
    remaining = qty
    taken = []
    lots = db.query(models.ItemLot).filter(
        models.ItemLot.item_id == item.id,
        models.ItemLot.status == "active",
        models.ItemLot.qty_remaining > 0,
    ).order_by(
        models.ItemLot.expiry_date.is_(None),  # dated lots first
        models.ItemLot.expiry_date.asc(),
        models.ItemLot.received_at.asc(),
    ).all()

    available = sum((lot.qty_remaining for lot in lots), Decimal("0"))
    if available < qty:
        raise ValueError(f"Only {available} available across lots, need {qty}")

    for lot in lots:
        if remaining <= 0:
            break
        draw = min(lot.qty_remaining, remaining)
        lot.qty_remaining -= draw
        if lot.qty_remaining <= 0:
            lot.status = "depleted"
        taken.append((lot.id, draw))
        remaining -= draw

    return taken