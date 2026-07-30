from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_current_user


router = APIRouter(prefix="/items", tags=["items"])
def resolve_qty(item: models.Item, payload: schemas.StockChange) -> tuple[Decimal, str]:
    """Convert a requested change into base units. Returns (qty, description)."""
    if not payload.as_packs:
        return payload.change_qty, f"{payload.change_qty} {item.unit}"
    if not item.pack_unit or not item.pack_size or item.pack_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{item.name} has no pack size configured, enter the quantity in {item.unit} instead",
        )
    qty = payload.change_qty * item.pack_size
    return qty, f"{payload.change_qty} {item.pack_unit} ({item.pack_size} {item.unit} each)"

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=schemas.ItemOut)
def create_item(
    payload: schemas.ItemCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = models.Item(
        name=payload.name,
        category=payload.category,
        unit=payload.unit,
        pack_unit=payload.pack_unit,
        pack_size=payload.pack_size,
        par_level=payload.par_level,
        reorder_qty=payload.reorder_qty,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[schemas.ItemOut])
def list_items(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Item)
    if not include_inactive:
        query = query.filter(models.Item.active == True)
    return query.order_by(models.Item.name).all()
@router.post("/{item_id}/receive", response_model=schemas.ItemOut)
def receive_stock(
    item_id: int,
    payload: schemas.StockChange,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    if payload.change_qty <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Receive quantity must be positive")
    qty, description = resolve_qty(item, payload)
    note = f"Received {description}"
    if payload.note:
        note = f"{note}. {payload.note}"
    apply_movement(db, item, qty, reason="received", note=note)
    return item


@router.post("/{item_id}/adjust", response_model=schemas.ItemOut)
def adjust_stock(
    item_id: int,
    payload: schemas.StockChange,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
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
    apply_movement(db, item, qty, reason="correction", note=note)
    return item

@router.get("/{item_id}/movements", response_model=list[schemas.MovementOut])
def item_movements(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return db.query(models.StockMovement).filter(
        models.StockMovement.item_id == item_id
    ).order_by(models.StockMovement.created_at.desc()).all()

@router.delete("/{item_id}", response_model=schemas.ItemOut)
def deactivate_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
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
    current_user: models.User = Depends(get_current_user),
):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    item.active = True
    db.commit()
    db.refresh(item)
    return item