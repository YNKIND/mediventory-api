from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, get_clinic_id


router = APIRouter(prefix="/analytics", tags=["analytics"])


def parse_range(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/summary", response_model=schemas.AnalyticsSummary)
def analytics_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    since = parse_range(days)

    # All consumption movements in the window for this clinic.
    # "procedure" = used in an appointment. Negative change_qty = stock leaving.
    movements = db.query(models.StockMovement).filter(
        models.StockMovement.clinic_id == clinic_id,
        models.StockMovement.reason == "procedure",
        models.StockMovement.created_at >= since,
    ).all()

    # Build item lookup once.
    item_ids = {mv.item_id for mv in movements}
    items = {}
    if item_ids:
        for item in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all():
            items[item.id] = item

    # Users for provider breakdown.
    users = {u.id: u.full_name for u in db.query(models.User).filter(models.User.clinic_id == clinic_id).all()}

    total_units = Decimal("0")
    total_cost = Decimal("0")
    by_category = {}
    by_provider = {}

    for mv in movements:
        used = -mv.change_qty  # movements are negative; consumption is positive
        if used <= 0:
            continue
        item = items.get(mv.item_id)
        if not item:
            continue

        cost = (used * item.unit_cost) if item.unit_cost is not None else Decimal("0")
        total_units += used
        total_cost += cost

        cat = item.category or "Uncategorized"
        entry = by_category.setdefault(cat, {"category": cat, "units": Decimal("0"), "cost": Decimal("0")})
        entry["units"] += used
        entry["cost"] += cost

        provider = users.get(mv.user_id, "Unknown")
        pentry = by_provider.setdefault(provider, {"provider": provider, "units": Decimal("0"), "cost": Decimal("0")})
        pentry["units"] += used
        pentry["cost"] += cost

    category_rows = sorted(by_category.values(), key=lambda r: r["cost"], reverse=True)
    provider_rows = sorted(by_provider.values(), key=lambda r: r["cost"], reverse=True)

    return {
        "days": days,
        "total_consumption_units": total_units,
        "total_consumption_cost": total_cost.quantize(Decimal("0.01")),
        "by_category": [
            {"category": r["category"], "units": r["units"], "cost": r["cost"].quantize(Decimal("0.01"))}
            for r in category_rows
        ],
        "by_provider": [
            {"provider": r["provider"], "units": r["units"], "cost": r["cost"].quantize(Decimal("0.01"))}
            for r in provider_rows
        ],
    }


@router.get("/cost-per-procedure", response_model=list[schemas.ProcedureCostRow])
def cost_per_procedure(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    since = parse_range(days)

    # Completed appointments in the window, grouped by procedure.
    appts = db.query(models.Appointment).filter(
        models.Appointment.clinic_id == clinic_id,
        models.Appointment.status == "completed",
        models.Appointment.completed_at >= since,
    ).all()

    if not appts:
        return []

    appt_ids = [a.id for a in appts]
    procedures = {p.id: p for p in db.query(models.Procedure).filter(models.Procedure.clinic_id == clinic_id).all()}

    # All procedure movements tied to those appointments.
    movements = db.query(models.StockMovement).filter(
        models.StockMovement.clinic_id == clinic_id,
        models.StockMovement.reason == "procedure",
        models.StockMovement.appointment_id.in_(appt_ids),
    ).all()

    item_ids = {mv.item_id for mv in movements}
    items = {i.id: i for i in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all()} if item_ids else {}

    # Map appointment -> procedure.
    appt_to_proc = {a.id: a.procedure_id for a in appts}

    # Accumulate cost per procedure, and count appointments per procedure.
    proc_cost = {}
    proc_count = {}
    for a in appts:
        proc_count[a.procedure_id] = proc_count.get(a.procedure_id, 0) + 1

    for mv in movements:
        proc_id = appt_to_proc.get(mv.appointment_id)
        if proc_id is None:
            continue
        item = items.get(mv.item_id)
        if not item or item.unit_cost is None:
            continue
        used = -mv.change_qty
        if used <= 0:
            continue
        proc_cost[proc_id] = proc_cost.get(proc_id, Decimal("0")) + used * item.unit_cost

    rows = []
    for proc_id, count in proc_count.items():
        proc = procedures.get(proc_id)
        if not proc:
            continue
        total = proc_cost.get(proc_id, Decimal("0"))
        avg = (total / count).quantize(Decimal("0.01")) if count else Decimal("0")
        rows.append({
            "procedure_id": proc_id,
            "procedure_name": proc.name,
            "times_completed": count,
            "total_cost": total.quantize(Decimal("0.01")),
            "avg_cost": avg,
        })

    rows.sort(key=lambda r: r["total_cost"], reverse=True)
    return rows