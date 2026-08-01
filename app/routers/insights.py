from decimal import Decimal, ROUND_CEILING

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models, schemas
from app.dependencies import get_db, get_current_user, get_clinic_id
from app.levels import stock_level


router = APIRouter(tags=["insights"])


@router.get("/alerts", response_model=list[schemas.AlertOut])
def get_alerts(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    items = db.query(models.Item).filter(
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    ).all()
    alerts = []
    for item in items:
        level = stock_level(item.stock_qty, item.par_level)
        if level is None:
            continue
        alerts.append({
            "item_id": item.id,
            "item_name": item.name,
            "unit": item.unit,
            "stock_qty": item.stock_qty,
            "par_level": item.par_level,
            "level": level,
        })
    alerts.sort(key=lambda a: (0 if a["level"] == "critical" else 1, a["item_name"]))
    return alerts


@router.get("/reorder-list", response_model=list[schemas.ReorderLineOut])
def get_reorder_list(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    items = db.query(models.Item).filter(
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    ).all()
    lines = []
    for item in items:
        if stock_level(item.stock_qty, item.par_level) is None:
            continue
        if item.reorder_qty and item.reorder_qty > 0:
            suggested = item.reorder_qty
        else:
            suggested = item.par_level - item.stock_qty

        suggested_packs = None
        if item.pack_unit and item.pack_size and item.pack_size > 0:
            suggested_packs = (suggested / item.pack_size).to_integral_value(rounding=ROUND_CEILING)

        estimated_cost = None
        if item.unit_cost is not None:
            estimated_cost = (suggested * item.unit_cost).quantize(Decimal("0.01"))

        lines.append({
            "item_id": item.id,
            "item_name": item.name,
            "unit": item.unit,
            "pack_unit": item.pack_unit,
            "pack_size": item.pack_size,
            "stock_qty": item.stock_qty,
            "par_level": item.par_level,
            "suggested_qty": suggested,
            "suggested_packs": suggested_packs,
            "supplier_name": item.supplier_name,
            "supplier_sku": item.supplier_sku,
            "unit_cost": item.unit_cost,
            "estimated_cost": estimated_cost,
        })

    lines.sort(key=lambda line: ((line["supplier_name"] or "zzz").lower(), line["item_name"].lower()))
    return lines


@router.get("/dashboard/summary", response_model=schemas.DashboardSummary)
def dashboard_summary(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    from datetime import datetime, timedelta, timezone

    items = db.query(models.Item).filter(
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    ).all()

    low_count = 0
    critical_count = 0
    for item in items:
        level = stock_level(item.stock_qty, item.par_level)
        if level == "critical":
            critical_count += 1
        elif level == "low":
            low_count += 1

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    todays = db.query(models.Appointment).filter(
        models.Appointment.clinic_id == clinic_id,
        models.Appointment.scheduled_at >= day_start,
        models.Appointment.scheduled_at < day_end,
    ).all()

    return {
        "total_items": len(items),
        "low_count": low_count,
        "critical_count": critical_count,
        "appointments_today": len(todays),
        "completed_today": len([a for a in todays if a.status == "completed"]),
    }