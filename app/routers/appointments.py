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


@router.get("/{appointment_id}/completion-draft", response_model=schemas.CompletionDraft)
def completion_draft(
    appointment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

    procedure = db.query(models.Procedure).filter(models.Procedure.id == appt.procedure_id).first()
    supplies = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == appt.procedure_id
    ).all()

    lines = []
    for supply in supplies:
        item = db.query(models.Item).filter(models.Item.id == supply.item_id).first()
        if not item:
            continue
        lines.append({
            "item_id": item.id,
            "item_name": item.name,
            "unit": item.unit,
            "expected_qty": supply.qty_per_procedure,
            "on_hand": item.stock_qty,
            "in_bom": True,
        })

    lines.sort(key=lambda line: line["item_name"].lower())

    return {
        "appointment_id": appt.id,
        "procedure_name": procedure.name if procedure else "Unknown",
        "patient_label": appt.patient_label,
        "lines": lines,
    }


@router.post("/{appointment_id}/complete", response_model=schemas.AppointmentOut)
def complete_appointment(
    appointment_id: int,
    payload: schemas.CompleteRequest | None = None,
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

    # If no confirmed lines were sent, fall back to the BOM as-is.
    if payload is None or not payload.lines:
        supplies = db.query(models.ProcedureSupply).filter(
            models.ProcedureSupply.procedure_id == appt.procedure_id
        ).all()
        confirmed = [
            schemas.ConfirmedLine(
                item_id=s.item_id,
                actual_qty=s.qty_per_procedure,
                expected_qty=s.qty_per_procedure,
            )
            for s in supplies
        ]
    else:
        confirmed = payload.lines

    # Build the expected map from the BOM so we can record variance even if the
    # frontend didn't send expected values.
    bom = db.query(models.ProcedureSupply).filter(
        models.ProcedureSupply.procedure_id == appt.procedure_id
    ).all()
    expected_map = {s.item_id: s.qty_per_procedure for s in bom}

    # Validate every line before touching stock.
    seen = set()
    resolved = []
    for line in confirmed:
        if line.item_id in seen:
            raise HTTPException(status_code=400, detail="The same item appears twice in the completion")
        seen.add(line.item_id)

        if line.actual_qty < 0:
            raise HTTPException(status_code=400, detail="Quantities cannot be negative")

        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        if not item:
            raise HTTPException(status_code=400, detail=f"Item {line.item_id} not found")
        if not item.active:
            raise HTTPException(status_code=400, detail=f"{item.name} is retired and cannot be used")

        if line.actual_qty > 0 and item.stock_qty < line.actual_qty:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock: {item.name} (need {line.actual_qty} {item.unit}, have {item.stock_qty})",
            )

        expected = line.expected_qty if line.expected_qty is not None else expected_map.get(item.id)
        resolved.append((item, line.actual_qty, expected))

    # Deduct. Skip zero-quantity lines (user removed them from the draft).
    for item, actual_qty, expected in resolved:
        if actual_qty == 0:
            continue
        apply_movement(
            db,
            item,
            -actual_qty,
            reason="procedure",
            note=f"Appointment #{appt.id}",
            appointment_id=appt.id,
            user_id=current_user.id,
            expected_qty=expected,
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