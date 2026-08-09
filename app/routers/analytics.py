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


def clinic_names(db: Session, clinic_ids: list[int]) -> dict:
    rows = db.query(models.Clinic).filter(models.Clinic.id.in_(clinic_ids)).all()
    return {c.id: c.name for c in rows}


@router.get("/summary", response_model=schemas.AnalyticsSummary)
def analytics_summary(
    days: int = Query(30, ge=1, le=365),
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
        for item in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all():
            items[item.id] = item

    users = {u.id: u.full_name for u in db.query(models.User).all()}
    cnames = clinic_names(db, clinic_ids)

    total_units = Decimal("0")
    total_cost = Decimal("0")
    by_category = {}
    by_provider = {}
    by_clinic = {}

    for mv in movements:
        used = -mv.change_qty
        if used <= 0:
            continue
        item = items.get(mv.item_id)
        if not item:
            continue

        cost = (used * item.unit_cost) if item.unit_cost is not None else Decimal("0")
        total_units += used
        total_cost += cost

        cat = item.category or "Uncategorized"
        e = by_category.setdefault(cat, {"category": cat, "units": Decimal("0"), "cost": Decimal("0")})
        e["units"] += used
        e["cost"] += cost

        provider = users.get(mv.user_id, "Unknown")
        p = by_provider.setdefault(provider, {"provider": provider, "units": Decimal("0"), "cost": Decimal("0")})
        p["units"] += used
        p["cost"] += cost

        cname = cnames.get(mv.clinic_id, f"Clinic {mv.clinic_id}")
        c = by_clinic.setdefault(mv.clinic_id, {"clinic": cname, "units": Decimal("0"), "cost": Decimal("0")})
        c["units"] += used
        c["cost"] += cost

    category_rows = sorted(by_category.values(), key=lambda r: r["cost"], reverse=True)
    provider_rows = sorted(by_provider.values(), key=lambda r: r["cost"], reverse=True)
    clinic_rows = sorted(by_clinic.values(), key=lambda r: r["cost"], reverse=True)

    return {
        "days": days,
        "scope": scope,
        "clinic_count": len(clinic_ids),
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
        "by_clinic": [
            {"clinic": r["clinic"], "units": r["units"], "cost": r["cost"].quantize(Decimal("0.01"))}
            for r in clinic_rows
        ],
    }


@router.get("/cost-per-procedure", response_model=list[schemas.ProcedureCostRow])
def cost_per_procedure(
    days: int = Query(30, ge=1, le=365),
    scope: str = Query("clinic"),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    since = parse_range(days)
    clinic_ids = resolve_clinic_ids(db, current_user, scope, clinic_id)

    appts = db.query(models.Appointment).filter(
        models.Appointment.clinic_id.in_(clinic_ids),
        models.Appointment.status == "completed",
        models.Appointment.completed_at >= since,
    ).all()

    if not appts:
        return []

    appt_ids = [a.id for a in appts]
    procedures = {p.id: p for p in db.query(models.Procedure).filter(models.Procedure.clinic_id.in_(clinic_ids)).all()}

    movements = db.query(models.StockMovement).filter(
        models.StockMovement.clinic_id.in_(clinic_ids),
        models.StockMovement.reason == "procedure",
        models.StockMovement.appointment_id.in_(appt_ids),
    ).all()

    item_ids = {mv.item_id for mv in movements}
    items = {i.id: i for i in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all()} if item_ids else {}

    appt_to_proc = {a.id: a.procedure_id for a in appts}

    # Group by procedure NAME across clinics, since different clinics have distinct
    # procedure rows for what is really the same procedure.
    proc_cost = {}
    proc_count = {}
    proc_name_by_key = {}

    for a in appts:
        proc = procedures.get(a.procedure_id)
        key = proc.name.lower() if proc else f"proc-{a.procedure_id}"
        proc_name_by_key[key] = proc.name if proc else f"Procedure #{a.procedure_id}"
        proc_count[key] = proc_count.get(key, 0) + 1

    proc_key_by_id = {}
    for pid, proc in procedures.items():
        proc_key_by_id[pid] = proc.name.lower()

    for mv in movements:
        proc_id = appt_to_proc.get(mv.appointment_id)
        if proc_id is None:
            continue
        key = proc_key_by_id.get(proc_id)
        if key is None:
            continue
        item = items.get(mv.item_id)
        if not item or item.unit_cost is None:
            continue
        used = -mv.change_qty
        if used <= 0:
            continue
        proc_cost[key] = proc_cost.get(key, Decimal("0")) + used * item.unit_cost

    rows = []
    for key, count in proc_count.items():
        total = proc_cost.get(key, Decimal("0"))
        avg = (total / count).quantize(Decimal("0.01")) if count else Decimal("0")
        rows.append({
            "procedure_id": 0,
            "procedure_name": proc_name_by_key[key],
            "times_completed": count,
            "total_cost": total.quantize(Decimal("0.01")),
            "avg_cost": avg,
        })

    rows.sort(key=lambda r: r["total_cost"], reverse=True)
    return rows


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

    # Bucket by week (Monday), summing units and cost.
    weekly = defaultdict(lambda: {"units": Decimal("0"), "cost": Decimal("0")})
    for mv in movements:
        used = -mv.change_qty
        if used <= 0:
            continue
        it = items.get(mv.item_id)
        if not it:
            continue
        cost = (used * it.unit_cost) if it.unit_cost is not None else Decimal("0")
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