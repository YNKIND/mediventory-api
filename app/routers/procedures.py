from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, get_clinic_id
from app.levels import stock_level


router = APIRouter(prefix="/procedures", tags=["procedures"])


def get_clinic_procedure(db: Session, procedure_id: int, clinic_id: int) -> models.Procedure:
    proc = db.query(models.Procedure).filter(
        models.Procedure.id == procedure_id,
        models.Procedure.clinic_id == clinic_id,
    ).first()
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return proc


def resolve_supply_lines(db: Session, supplies: list[schemas.SupplyInput], clinic_id: int) -> list[tuple[int, Decimal]]:
    if not supplies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A procedure needs at least one supply, otherwise completing it would deduct nothing",
        )

    seen_item_ids = set()
    seen_new_names = set()

    for position, line in enumerate(supplies, start=1):
        if line.item_id is None and line.new_item is None:
            raise HTTPException(status_code=400, detail=f"Supply {position}: pick an existing item or define a new one")
        if line.item_id is not None and line.new_item is not None:
            raise HTTPException(status_code=400, detail=f"Supply {position}: pick either an existing item or a new one, not both")
        if line.qty_per_procedure is None or line.qty_per_procedure <= 0:
            raise HTTPException(status_code=400, detail=f"Supply {position}: quantity must be greater than zero")

        if line.item_id is not None:
            item = db.query(models.Item).filter(
                models.Item.id == line.item_id,
                models.Item.clinic_id == clinic_id,
            ).first()
            if not item:
                raise HTTPException(status_code=400, detail=f"Supply {position}: item not found")
            if line.item_id in seen_item_ids:
                raise HTTPException(status_code=400, detail=f"Supply {position}: {item.name} is listed twice")
            seen_item_ids.add(line.item_id)
        else:
            new_name = (line.new_item.name or "").strip()
            if not new_name:
                raise HTTPException(status_code=400, detail=f"Supply {position}: new item needs a name")
            key = new_name.lower()
            if key in seen_new_names:
                raise HTTPException(status_code=400, detail=f"Supply {position}: {new_name} is listed twice")
            seen_new_names.add(key)
            clash = db.query(models.Item).filter(
                func.lower(models.Item.name) == key,
                models.Item.clinic_id == clinic_id,
                models.Item.active == True,
            ).first()
            if clash:
                raise HTTPException(
                    status_code=400,
                    detail=f"Supply {position}: {new_name} already exists in inventory, select it from the list instead",
                )

    resolved = []
    for line in supplies:
        if line.item_id is not None:
            resolved.append((line.item_id, line.qty_per_procedure))
        else:
            spec = line.new_item
            pack_size = spec.pack_size if spec.pack_size and spec.pack_size > 0 else Decimal("1")
            new_item = models.Item(
                clinic_id=clinic_id,
                name=spec.name.strip(),
                category=(spec.category or "").strip() or None,
                unit=(spec.unit or "unit").strip() or "unit",
                pack_unit=(spec.pack_unit or "").strip() or None,
                pack_size=pack_size,
                par_level=spec.par_level or Decimal("0"),
                reorder_qty=spec.reorder_qty or Decimal("0"),
            )
            db.add(new_item)
            db.flush()
            resolved.append((new_item.id, line.qty_per_procedure))

    return resolved


@router.post("", response_model=schemas.ProcedureOut)
def create_procedure(
    payload: schemas.ProcedureCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Procedure name is required")
    procedure = models.Procedure(clinic_id=clinic_id, name=payload.name.strip(), code=(payload.code or "").strip() or None)
    db.add(procedure)
    db.commit()
    db.refresh(procedure)
    return procedure


@router.post("/with-supplies", response_model=schemas.ProcedureOut)
def create_procedure_with_supplies(
    payload: schemas.ProcedureWithSuppliesCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Procedure name is required")

    resolved = resolve_supply_lines(db, payload.supplies, clinic_id)

    procedure = models.Procedure(clinic_id=clinic_id, name=payload.name.strip(), code=(payload.code or "").strip() or None)
    db.add(procedure)
    db.flush()

    for item_id, qty in resolved:
        db.add(models.ProcedureSupply(procedure_id=procedure.id, item_id=item_id, qty_per_procedure=qty))

    db.commit()
    db.refresh(procedure)
    return procedure


@router.get("", response_model=list[schemas.ProcedureOut])
def list_procedures(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Procedure).filter(models.Procedure.clinic_id == clinic_id)
    if not include_inactive:
        query = query.filter(models.Procedure.active == True)
    return query.order_by(models.Procedure.name).all()


@router.patch("/{procedure_id}", response_model=schemas.ProcedureOut)
def update_procedure(
    procedure_id: int,
    payload: schemas.ProcedureUpdate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)

    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Procedure name cannot be empty")
        procedure.name = payload.name.strip()
    if payload.code is not None:
        procedure.code = payload.code.strip() or None

    db.commit()
    db.refresh(procedure)
    return procedure


@router.get("/{procedure_id}/supplies", response_model=list[schemas.SupplyLineOut])
def get_supplies(
    procedure_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)
    lines = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure.id
    ).all()
    result = []
    for line in lines:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        result.append({
            "item_id": line.item_id,
            "item_name": item.name if item else "Unknown",
            "unit": item.unit if item else "unit",
            "qty_per_procedure": line.qty_per_procedure,
        })
    result.sort(key=lambda line: line["item_name"].lower())
    return result


@router.put("/{procedure_id}/supplies", response_model=list[schemas.SupplyLineOut])
def set_supplies(
    procedure_id: int,
    supplies: list[schemas.SupplyInput],
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)

    resolved = resolve_supply_lines(db, supplies, clinic_id)

    db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure.id
    ).delete()

    for item_id, qty in resolved:
        db.add(models.ProcedureSupply(procedure_id=procedure.id, item_id=item_id, qty_per_procedure=qty))

    db.commit()
    return get_supplies(procedure_id, db, clinic_id, current_user)


@router.get("/{procedure_id}/stock-check", response_model=schemas.StockCheck)
def procedure_stock_check(
    procedure_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)

    supplies = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == procedure.id
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


@router.delete("/{procedure_id}", response_model=schemas.ProcedureOut)
def deactivate_procedure(
    procedure_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)
    if not procedure.active:
        raise HTTPException(status_code=400, detail="Procedure is already retired")

    upcoming = db.query(models.Appointment).filter(
        models.Appointment.procedure_id == procedure.id,
        models.Appointment.clinic_id == clinic_id,
        models.Appointment.status == "scheduled",
    ).count()
    if upcoming > 0:
        raise HTTPException(
            status_code=400,
            detail=f"{upcoming} scheduled appointment{'s' if upcoming != 1 else ''} still use this procedure. Complete or cancel them first.",
        )

    procedure.active = False
    db.commit()
    db.refresh(procedure)
    return procedure


@router.post("/{procedure_id}/restore", response_model=schemas.ProcedureOut)
def restore_procedure(
    procedure_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    procedure = get_clinic_procedure(db, procedure_id, clinic_id)
    procedure.active = True
    db.commit()
    db.refresh(procedure)
    return procedure