from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import SessionLocal
from app import models, schemas
from app.dependencies import get_current_user
from decimal import Decimal
from app.levels import stock_level

router = APIRouter(prefix="/procedures", tags=["procedures"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=schemas.ProcedureOut)
def create_procedure(
    payload: schemas.ProcedureCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = models.Procedure(name=payload.name, code=payload.code)
    db.add(procedure)
    db.commit()
    db.refresh(procedure)
    return procedure


@router.get("", response_model=list[schemas.ProcedureOut])
def list_procedures(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return db.query(models.Procedure).filter(models.Procedure.active == True).all()


@router.get("/{procedure_id}/supplies", response_model=list[schemas.SupplyLineOut])
def get_supplies(
    procedure_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = db.query(models.Procedure).filter(models.Procedure.id == procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procedure not found")
    lines = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure_id
    ).all()
    result = []
    for line in lines:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        result.append({
            "item_id": line.item_id,
            "item_name": item.name if item else "Unknown",
            "qty_per_procedure": line.qty_per_procedure,
        })
    return result


@router.put("/{procedure_id}/supplies", response_model=list[schemas.SupplyLineOut])
def set_supplies(
    procedure_id: int,
    lines: list[schemas.SupplyLine],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = db.query(models.Procedure).filter(models.Procedure.id == procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procedure not found")

    # validate every item exists before changing anything
    for line in lines:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        if not item:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Item {line.item_id} not found",
            )

    # full replace: delete the old BOM, then insert the new one
    db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure_id
    ).delete()
    for line in lines:
        db.add(models.ProcedureSupply(
            procedure_id=procedure_id,
            item_id=line.item_id,
            qty_per_procedure=line.qty_per_procedure,
        ))
    db.commit()

    return get_supplies(procedure_id, db, current_user)

@router.post("/with-supplies", response_model=schemas.ProcedureOut)
def create_procedure_with_supplies(
    payload: schemas.ProcedureWithSuppliesCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Procedure name is required")
    if not payload.supplies:
        raise HTTPException(
            status_code=400,
            detail="A procedure needs at least one supply, otherwise completing it would deduct nothing",
        )

    # validate every line before touching the database
    seen_item_ids = set()
    seen_new_names = set()
    for position, line in enumerate(payload.supplies, start=1):
        if line.item_id is None and line.new_item is None:
            raise HTTPException(status_code=400, detail=f"Supply {position}: pick an existing item or define a new one")
        if line.item_id is not None and line.new_item is not None:
            raise HTTPException(status_code=400, detail=f"Supply {position}: pick either an existing item or a new one, not both")
        if line.qty_per_procedure <= 0:
            raise HTTPException(status_code=400, detail=f"Supply {position}: quantity must be greater than zero")

        if line.item_id is not None:
            item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
            if not item:
                raise HTTPException(status_code=400, detail=f"Supply {position}: item {line.item_id} not found")
            if line.item_id in seen_item_ids:
                raise HTTPException(status_code=400, detail=f"Supply {position}: {item.name} is listed twice")
            seen_item_ids.add(line.item_id)
        else:
            new_name = line.new_item.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail=f"Supply {position}: new item needs a name")
            key = new_name.lower()
            if key in seen_new_names:
                raise HTTPException(status_code=400, detail=f"Supply {position}: {new_name} is listed twice")
            seen_new_names.add(key)
            clash = db.query(models.Item).filter(
                func.lower(models.Item.name) == key,
                models.Item.active == True,
            ).first()
            if clash:
                raise HTTPException(
                    status_code=400,
                    detail=f"Supply {position}: {new_name} already exists in inventory, select it from the list instead",
                )

    procedure = models.Procedure(name=payload.name.strip(), code=payload.code)
    db.add(procedure)
    db.flush()

    for line in payload.supplies:
        if line.item_id is not None:
            item_id = line.item_id
        else:
            new_item = models.Item(
                name=line.new_item.name.strip(),
                category=(line.new_item.category or "").strip() or None,
                unit=(line.new_item.unit or "unit").strip() or "unit",
                par_level=line.new_item.par_level,
                reorder_qty=line.new_item.reorder_qty,
            )
            db.add(new_item)
            db.flush()
            item_id = new_item.id

        db.add(models.ProcedureSupply(
            procedure_id=procedure.id,
            item_id=item_id,
            qty_per_procedure=line.qty_per_procedure,
        ))

    db.commit()
    db.refresh(procedure)
    return procedure


@router.get("/{procedure_id}/stock-check", response_model=schemas.StockCheck)
def procedure_stock_check(
    procedure_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = db.query(models.Procedure).filter(models.Procedure.id == procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=404, detail="Procedure not found")

    supplies = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure_id
    ).all()

    lines = []
    ready = True
    for supply in supplies:
        item = db.query(models.Item).filter(models.Item.id == supply.item_id).first()
        if not item:
            continue
        on_hand = item.stock_qty
        required = supply.qty_per_procedure
        after = on_hand - required
        sufficient = on_hand >= required
        if not sufficient:
            ready = False
        lines.append({
            "item_id": item.id,
            "item_name": item.name,
            "unit": item.unit,
            "required": required,
            "on_hand": on_hand,
            "after": after,
            "par_level": item.par_level,
            "sufficient": sufficient,
            "level": stock_level(after, item.par_level),
        })

    lines.sort(key=lambda line: (line["sufficient"], line["item_name"]))

    return {
        "procedure_id": procedure.id,
        "procedure_name": procedure.name,
        "has_supplies": len(lines) > 0,
        "ready": ready,
        "lines": lines,
    }