from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_current_user


router = APIRouter(prefix="/items", tags=["items"])


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
        par_level=payload.par_level,
        reorder_qty=payload.reorder_qty,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[schemas.ItemOut])
def list_items(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return db.query(models.Item).filter(models.Item.active == True).all()


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
    apply_movement(db, item, payload.change_qty, reason="received", note=payload.note)
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
    apply_movement(db, item, payload.change_qty, reason="correction", note=payload.note)
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