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
    expected_qty: Decimal | None = None,
    occurred_at=None,
) -> models.StockMovement:
    """Record a stock movement and update the item's cached quantity.

    occurred_at dates the movement to when the stock actually moved, rather than
    when the record was made. Procedure deductions pass the appointment's own
    time, so a Friday appointment completed on Monday is still counted on Friday
    by the analytics. Every other caller leaves it None and gets the column
    default, which is correct for receiving, adjustments and corrections, since
    those genuinely happen at the moment they are entered.
    """
    movement = models.StockMovement(
        clinic_id=item.clinic_id,
        item_id=item.id,
        change_qty=change_qty,
        expected_qty=expected_qty,
        reason=reason,
        note=note,
        appointment_id=appointment_id,
        user_id=user_id,
    )
    if occurred_at is not None:
        movement.created_at = occurred_at
    db.add(movement)
    item.stock_qty = item.stock_qty + change_qty
    db.commit()
    db.refresh(item)
    db.refresh(movement)
    return movement