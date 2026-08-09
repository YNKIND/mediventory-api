from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_db, get_current_user, require_admin, get_clinic_id


router = APIRouter(prefix="/items", tags=["items"])


def resolve_qty(item: models.Item, payload: schemas.StockChange) -> tuple[Decimal, str]:
    if not payload.as_packs:
        return payload.change_qty, f"{payload.change_qty} {item.unit}"
    if not item.pack_unit or not item.pack_size or item.pack_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{item.name} has no pack size configured, enter the quantity in {item.unit} instead",
        )
    qty = payload.change_qty * item.pack_size
    return qty, f"{payload.change_qty} {item.pack_unit} ({item.pack_size} {item.unit} each)"


def get_clinic_item(db: Session, item_id: int, clinic_id: int) -> models.Item:
    """Fetch an item, but only if it belongs to this clinic. 404 otherwise."""
    item = db.query(models.Item).filter(
        models.Item.id == item_id,
        models.Item.clinic_id == clinic_id,
    ).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


def assert_name_free(db: Session, name: str, clinic_id: int, exclude_id: int | None = None):
    query = db.query(models.Item).filter(
        func.lower(models.Item.name) == name.strip().lower(),
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    )
    if exclude_id is not None:
        query = query.filter(models.Item.id != exclude_id)
    if query.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An active item named {name.strip()} already exists",
        )


@router.post("", response_model=schemas.ItemOut)
def create_item(
    payload: schemas.ItemCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item name is required")
    assert_name_free(db, payload.name, clinic_id)

    if payload.unit_cost is not None and payload.unit_cost < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit cost cannot be negative")
    if payload.par_level is not None and payload.par_level < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Minimum stock cannot be negative")
    if payload.reorder_qty is not None and payload.reorder_qty < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reorder quantity cannot be negative")

    pack_size = payload.pack_size if payload.pack_size and payload.pack_size > 0 else Decimal("1")
    item = models.Item(
        clinic_id=clinic_id,
        name=payload.name.strip(),
        category=(payload.category or "").strip() or None,
        unit=(payload.unit or "unit").strip() or "unit",
        pack_unit=(payload.pack_unit or "").strip() or None,
        pack_size=pack_size,
        par_level=payload.par_level or Decimal("0"),
        reorder_qty=payload.reorder_qty or Decimal("0"),
        supplier_name=(payload.supplier_name or "").strip() or None,
        supplier_sku=(payload.supplier_sku or "").strip() or None,
        unit_cost=payload.unit_cost,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[schemas.ItemOut])
def list_items(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Item).filter(models.Item.clinic_id == clinic_id)
    if not include_inactive:
        query = query.filter(models.Item.active == True)
    return query.order_by(models.Item.name).all()


@router.patch("/{item_id}", response_model=schemas.ItemOut)
def update_item(
    item_id: int,
    payload: schemas.ItemUpdate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    item = get_clinic_item(db, item_id, clinic_id)

    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item name cannot be empty")
        assert_name_free(db, payload.name, clinic_id, exclude_id=item.id)
        item.name = payload.name.strip()

    if payload.category is not None:
        item.category = payload.category.strip() or None
    if payload.unit is not None:
        item.unit = payload.unit.strip() or "unit"
    if payload.pack_unit is not None:
        item.pack_unit = payload.pack_unit.strip() or None
    if payload.pack_size is not None:
        if payload.pack_size <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pack size must be greater than zero")
        item.pack_size = payload.pack_size
    if payload.par_level is not None:
        if payload.par_level < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Minimum stock cannot be negative")
        item.par_level = payload.par_level
    if payload.reorder_qty is not None:
        if payload.reorder_qty < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reorder quantity cannot be negative")
        item.reorder_qty = payload.reorder_qty
    if payload.supplier_name is not None:
        item.supplier_name = payload.supplier_name.strip() or None
    if payload.supplier_sku is not None:
        item.supplier_sku = payload.supplier_sku.strip() or None
    if payload.unit_cost is not None:
        if payload.unit_cost < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unit cost cannot be negative")
        item.unit_cost = payload.unit_cost

    if item.pack_unit and (not item.pack_size or item.pack_size <= 0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An item with a pack unit needs a pack size greater than zero",
        )

    db.commit()
    db.refresh(item)
    return item


@router.post("/{item_id}/receive", response_model=schemas.ItemOut)
def receive_stock(
    item_id: int,
    payload: schemas.StockChange,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    item = get_clinic_item(db, item_id, clinic_id)
    if not item.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This item is retired. Restore it first.")
    if payload.change_qty <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Receive quantity must be positive")

    qty, description = resolve_qty(item, payload)
    note = f"Received {description}"
    if payload.note:
        note = f"{note}. {payload.note}"
    apply_movement(db, item, qty, reason="received", note=note, user_id=current_user.id)
    return item


@router.post("/{item_id}/adjust", response_model=schemas.ItemOut)
def adjust_stock(
    item_id: int,
    payload: schemas.StockChange,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    item = get_clinic_item(db, item_id, clinic_id)
    if not item.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This item is retired. Restore it first.")
    if payload.change_qty == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Adjustment cannot be zero")

    qty, description = resolve_qty(item, payload)
    new_qty = item.stock_qty + qty
    if new_qty < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adjustment would put stock below zero (current: {item.stock_qty} {item.unit}, change: {qty})",
        )
    note = f"Adjusted {description}"
    if payload.note:
        note = f"{note}. {payload.note}"
    apply_movement(db, item, qty, reason="correction", note=note, user_id=current_user.id)
    return item


@router.get("/{item_id}/movements", response_model=list[schemas.MovementOut])
def item_movements(
    item_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    item = get_clinic_item(db, item_id, clinic_id)

    movements = db.query(models.StockMovement).filter(
        models.StockMovement.item_id == item.id,
        models.StockMovement.clinic_id == clinic_id,
    ).order_by(models.StockMovement.created_at.desc()).all()

    names = {u.id: u.full_name for u in db.query(models.User).filter(models.User.clinic_id == clinic_id).all()}

    return [{
        "id": mv.id,
        "change_qty": mv.change_qty,
        "expected_qty": mv.expected_qty,
        "reason": mv.reason,
        "note": mv.note,
        "appointment_id": mv.appointment_id,
        "user_name": names.get(mv.user_id),
        "created_at": mv.created_at,
    } for mv in movements]


@router.delete("/{item_id}", response_model=schemas.ItemOut)
def deactivate_item(
    item_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    item = get_clinic_item(db, item_id, clinic_id)
    if not item.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Item is already retired")
    item.active = False
    db.commit()
    db.refresh(item)
    return item


@router.post("/{item_id}/restore", response_model=schemas.ItemOut)
def restore_item(
    item_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    item = get_clinic_item(db, item_id, clinic_id)
    assert_name_free(db, item.name, clinic_id, exclude_id=item.id)
    item.active = True
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[schemas.ItemOut])
def list_items(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Item).filter(models.Item.clinic_id == clinic_id)
    if not include_inactive:
        query = query.filter(models.Item.active == True)
    items = query.order_by(models.Item.name).all()

    # Most recent "received" movement per item, for the "recently received" sort.
    last_received = {}
    rows = db.query(
        models.StockMovement.item_id,
        sa_func.max(models.StockMovement.created_at)
    ).filter(
        models.StockMovement.clinic_id == clinic_id,
        models.StockMovement.reason == "received",
    ).group_by(models.StockMovement.item_id).all()
    for item_id, ts in rows:
        last_received[item_id] = ts

    result = []
    for item in items:
        result.append({
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "unit": item.unit,
            "pack_unit": item.pack_unit,
            "pack_size": item.pack_size,
            "stock_qty": item.stock_qty,
            "par_level": item.par_level,
            "reorder_qty": item.reorder_qty,
            "supplier_name": item.supplier_name,
            "supplier_sku": item.supplier_sku,
            "unit_cost": item.unit_cost,
            "active": item.active,
            "last_received_at": last_received.get(item.id),
        })
    return result