from decimal import Decimal

from sqlalchemy.orm import Session

from app import models


def apply_movement(
    db: Session,
    item: models.Item,
    change_qty: Decimal,
    reason: str,
    note: str | None = None,
    appointment_id: int | None = None,
    user_id: int | None = None,
) -> models.StockMovement:
    movement = models.StockMovement(
        item_id=item.id,
        change_qty=change_qty,
        reason=reason,
        note=note,
        appointment_id=appointment_id,
        user_id=user_id,
    )
    db.add(movement)
    item.stock_qty = item.stock_qty + change_qty
    db.commit()
    db.refresh(item)
    db.refresh(movement)
    return movement