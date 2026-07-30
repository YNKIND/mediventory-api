from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_current_user


router = APIRouter(prefix="/appointments", tags=["appointments"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=schemas.AppointmentOut)
def create_appointment(
    payload: schemas.AppointmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = db.query(models.Procedure).filter(models.Procedure.id == payload.procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Procedure not found")
    appt = models.Appointment(
        procedure_id=payload.procedure_id,
        patient_label=payload.patient_label,
        scheduled_at=payload.scheduled_at,
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


@router.get("", response_model=list[schemas.AppointmentOut])
def list_appointments(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return db.query(models.Appointment).order_by(models.Appointment.scheduled_at).all()


@router.post("/{appointment_id}/complete", response_model=schemas.AppointmentOut)
def complete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status == "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Appointment already completed")

    supplies = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == appt.procedure_id
    ).all()

    for line in supplies:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        if item:
            apply_movement(
                db,
                item,
                -line.qty_per_procedure,
                reason="procedure",
                note=f"Appointment #{appt.id}",
                appointment_id=appt.id,
            )

    appt.status = "completed"
    appt.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(appt)
    return appt


@router.post("/{appointment_id}/uncomplete", response_model=schemas.AppointmentOut)
def uncomplete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status != "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Appointment is not completed")

    movements = db.query(models.StockMovement).filter(
        models.StockMovement.appointment_id == appt.id,
        models.StockMovement.reason == "procedure",
    ).all()

    for mv in movements:
        item = db.query(models.Item).filter(models.Item.id == mv.item_id).first()
        if item:
            apply_movement(
                db,
                item,
                -mv.change_qty,
                reason="reversal",
                note=f"Reversal of appointment #{appt.id}",
                appointment_id=appt.id,
            )

    appt.status = "scheduled"
    appt.completed_at = None
    db.commit()
    db.refresh(appt)
    return appt