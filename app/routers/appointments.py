from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_db, get_current_user


router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", response_model=schemas.AppointmentOut)
def create_appointment(
    payload: schemas.AppointmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    procedure = db.query(models.Procedure).filter(models.Procedure.id == payload.procedure_id).first()
    if not procedure:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Procedure not found")
    if not procedure.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This procedure is retired")

    appt = models.Appointment(
        procedure_id=payload.procedure_id,
        patient_label=(payload.patient_label or "").strip() or None,
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


@router.patch("/{appointment_id}", response_model=schemas.AppointmentOut)
def update_appointment(
    appointment_id: int,
    payload: schemas.AppointmentUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Undo this appointment before editing it, otherwise its deducted supplies would no longer match",
        )

    if payload.procedure_id is not None:
        procedure = db.query(models.Procedure).filter(models.Procedure.id == payload.procedure_id).first()
        if not procedure:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Procedure not found")
        appt.procedure_id = payload.procedure_id

    if payload.patient_label is not None:
        appt.patient_label = payload.patient_label.strip() or None

    if payload.scheduled_at is not None:
        appt.scheduled_at = payload.scheduled_at

    db.commit()
    db.refresh(appt)
    return appt


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
    if appt.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This appointment was cancelled")

    supplies = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == appt.procedure_id
    ).all()

    shortages = []
    for line in supplies:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        if not item:
            continue
        if item.stock_qty < line.qty_per_procedure:
            shortages.append(f"{item.name} (need {line.qty_per_procedure} {item.unit}, have {item.stock_qty})")

    if shortages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient stock: " + "; ".join(shortages),
        )

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
                user_id=current_user.id,
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

    procedure_moves = db.query(models.StockMovement).filter(
        models.StockMovement.appointment_id == appt.id,
        models.StockMovement.reason == "procedure",
    ).all()

    reversal_moves = db.query(models.StockMovement).filter(
        models.StockMovement.appointment_id == appt.id,
        models.StockMovement.reason == "reversal",
    ).all()

    net_by_item = {}
    for mv in procedure_moves:
        net_by_item[mv.item_id] = net_by_item.get(mv.item_id, Decimal("0")) + mv.change_qty
    for mv in reversal_moves:
        net_by_item[mv.item_id] = net_by_item.get(mv.item_id, Decimal("0")) + mv.change_qty

    for item_id, net in net_by_item.items():
        if net == 0:
            continue
        item = db.query(models.Item).filter(models.Item.id == item_id).first()
        if item:
            apply_movement(
                db,
                item,
                -net,
                reason="reversal",
                note=f"Reversal of appointment #{appt.id}",
                appointment_id=appt.id,
                user_id=current_user.id,
            )

    appt.status = "scheduled"
    appt.completed_at = None
    db.commit()
    db.refresh(appt)
    return appt


@router.post("/{appointment_id}/cancel", response_model=schemas.AppointmentOut)
def cancel_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Undo this appointment first, then cancel it",
        )
    if appt.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already cancelled")

    appt.status = "cancelled"
    db.commit()
    db.refresh(appt)
    return appt


@router.post("/{appointment_id}/reopen", response_model=schemas.AppointmentOut)
def reopen_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only cancelled appointments can be reopened",
        )

    appt.status = "scheduled"
    db.commit()
    db.refresh(appt)
    return appt


@router.delete("/{appointment_id}")
def delete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Undo this appointment first, otherwise its supplies would stay deducted with nothing explaining why",
        )

    movements = db.query(models.StockMovement).filter(
        models.StockMovement.appointment_id == appt.id
    ).all()

    net_by_item = {}
    for mv in movements:
        net_by_item[mv.item_id] = net_by_item.get(mv.item_id, Decimal("0")) + mv.change_qty
    outstanding = {k: v for k, v in net_by_item.items() if v != 0}
    if outstanding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This appointment still has stock deducted against it. Undo it before deleting.",
        )

    for mv in movements:
        mv.appointment_id = None
        mv.note = f"{mv.note or ''} (from deleted appointment #{appt.id})".strip()

    db.delete(appt)
    db.commit()
    return {"deleted": True, "movements_detached": len(movements)}