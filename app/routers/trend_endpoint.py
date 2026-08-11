# ---- REPLACE your existing usage_trend function in app/routers/analytics.py with this ----

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

    cnames = clinic_names(db, clinic_ids)
    multi = len(clinic_ids) > 1

    def week_key(d):
        monday = (d - timedelta(days=d.weekday())).date()
        return monday.isoformat()

    # Combined weekly totals (used for single-clinic "points" and the "All clinics" line).
    total_weekly = defaultdict(lambda: {"units": Decimal("0"), "cost": Decimal("0")})
    # Per-clinic weekly (only needed when multi).
    per_clinic_weekly = defaultdict(lambda: defaultdict(lambda: {"units": Decimal("0"), "cost": Decimal("0")}))

    for mv in movements:
        used = -mv.change_qty
        if used <= 0:
            continue
        it = items.get(mv.item_id)
        if not it:
            continue
        cost = (used * it.unit_cost) if it.unit_cost is not None else Decimal("0")
        wk = week_key(mv.created_at)
        total_weekly[wk]["units"] += used
        total_weekly[wk]["cost"] += cost
        if multi:
            per_clinic_weekly[mv.clinic_id][wk]["units"] += used
            per_clinic_weekly[mv.clinic_id][wk]["cost"] += cost

    points = [
        {"week": k, "units": v["units"], "cost": v["cost"].quantize(Decimal("0.01"))}
        for k, v in sorted(total_weekly.items())
    ]

    series = None
    if multi:
        series = []
        # "All clinics" total line first.
        series.append({
            "clinic": "All clinics",
            "points": points,
        })
        # One line per clinic.
        for cid in clinic_ids:
            weekly = per_clinic_weekly.get(cid, {})
            cpoints = [
                {"week": k, "units": v["units"], "cost": v["cost"].quantize(Decimal("0.01"))}
                for k, v in sorted(weekly.items())
            ]
            series.append({
                "clinic": cnames.get(cid, f"Clinic {cid}"),
                "points": cpoints,
            })

    return {"days": days, "scope": scope, "points": points, "series": series}