from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models, schemas
from app.dependencies import get_current_user


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