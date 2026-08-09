from datetime import datetime, timedelta, timezone
from decimal import Decimal
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, get_clinic_id


router = APIRouter(prefix="/analytics", tags=["analytics"])


def parse_range(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/trend", response_model=schemas.TrendResponse)
def usage_trend(
    days: int = Query(90, ge=7, le=365),
    scope: str = Query("clinic"),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    since = parse_range(days)
    clinic_ids = resolve_clinic_ids(db, current_user, scope, clinic_id)

    movements = db.query(models.StockMovement).filter(
        models.StockMovement.clinic_id.in_(clinic_ids),
        models.StockMovement.reason == "procedure",
        models.StockMovement.created_at >= since,
    ).all()

    item_ids = {mv.item_id for mv in movements}
    items = {}
    if item_ids:
        for it in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all():
            items[it.id] = it

    # Bucket by ISO week (year-week), summing units and cost.
    weekly = defaultdict(lambda: {"units": Decimal("0"), "cost": Decimal("0")})
    for mv in movements:
        used = -mv.change_qty
        if used <= 0:
            continue
        it = items.get(mv.item_id)
        if not it:
            continue
        cost = (used * it.unit_cost) if it.unit_cost is not None else Decimal("0")
        # Monday of that movement's week as the bucket label.
        d = mv.created_at
        monday = (d - timedelta(days=d.weekday())).date()
        key = monday.isoformat()
        weekly[key]["units"] += used
        weekly[key]["cost"] += cost

    points = [
        {"week": k, "units": v["units"], "cost": v["cost"].quantize(Decimal("0.01"))}
        for k, v in sorted(weekly.items())
    ]
    return {"days": days, "scope": scope, "points": points}


@router.get("/runout", response_model=list[schemas.RunoutRow])
def projected_runout(
    lookback_days: int = Query(30, ge=7, le=180),
    scope: str = Query("clinic"),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    since = parse_range(lookback_days)
    clinic_ids = resolve_clinic_ids(db, current_user, scope, clinic_id)

    # Sum recent consumption per item over the lookback window.
    movements = db.query(models.StockMovement).filter(
        models.StockMovement.clinic_id.in_(clinic_ids),
        models.StockMovement.reason == "procedure",
        models.StockMovement.created_at >= since,
    ).all()

    used_by_item = defaultdict(Decimal)
    for mv in movements:
        used = -mv.change_qty
        if used > 0:
            used_by_item[mv.item_id] += used

    # Only consider active items in these clinics with some recent usage.
    items = db.query(models.Item).filter(
        models.Item.clinic_id.in_(clinic_ids),
        models.Item.active == True,
    ).all()

    clinic_name_map = clinic_names(db, clinic_ids)
    multi = len(clinic_ids) > 1

    rows = []
    for it in items:
        used = used_by_item.get(it.id, Decimal("0"))
        if used <= 0:
            continue  # no recent usage, cannot project honestly
        per_day = used / Decimal(lookback_days)
        if per_day <= 0:
            continue
        days_left = float(Decimal(it.stock_qty) / per_day)
        rows.append({
            "item_id": it.id,
            "item_name": it.name,
            "clinic_name": clinic_name_map.get(it.clinic_id, "") if multi else "",
            "stock_qty": it.stock_qty,
            "unit": it.unit,
            "avg_daily_usage": per_day.quantize(Decimal("0.01")),
            "days_left": round(days_left, 1),
        })

    rows.sort(key=lambda r: r["days_left"])  # soonest first
    return rows

def resolve_clinic_ids(db: Session, current_user: models.User, scope: str, single_clinic_id: int) -> list[int]:
    """Which clinics to include.
    'all'  = every clinic the user is owner/admin of.
    a numeric string (e.g. '5') = that specific clinic, IF the user is owner/admin there.
    anything else (e.g. 'clinic') = just the currently selected clinic."""
    if scope == "all":
        memberships = db.query(models.ClinicMembership).filter(
            models.ClinicMembership.user_id == current_user.id,
            models.ClinicMembership.role.in_(["owner", "admin"]),
        ).all()
        ids = [m.clinic_id for m in memberships]
        return ids if ids else [single_clinic_id]

    # Specific clinic requested by id?
    if scope.isdigit():
        target = int(scope)
        membership = db.query(models.ClinicMembership).filter(
            models.ClinicMembership.user_id == current_user.id,
            models.ClinicMembership.clinic_id == target,
            models.ClinicMembership.role.in_(["owner", "admin"]),
        ).first()
        if membership:
            return [target]
        # Not a member (or not admin) of that clinic: fall back to current clinic.
        return [single_clinic_id]

    # Default: the currently selected clinic.
    return [single_clinic_id]