import csv
import io
import re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.dependencies import get_db, get_current_user, get_clinic_id
from app.stock import apply_movement


router = APIRouter(prefix="/imports", tags=["imports"])


# The columns we accept. Keys are canonical; values are accepted header variants (lowercased).
COLUMN_ALIASES = {
    "name": ["name", "item", "item name", "product", "description"],
    "category": ["category", "cat", "type"],
    "unit": ["unit", "base unit", "uom"],
    "pack_unit": ["pack unit", "pack", "order unit"],
    "pack_size": ["pack size", "units per pack", "per pack", "qty per pack"],
    "par_level": ["minimum", "minimum stock", "par", "par level", "min"],
    "reorder_qty": ["reorder qty", "reorder", "order qty", "reorder quantity"],
    "supplier_name": ["supplier", "supplier name", "vendor"],
    "supplier_sku": ["supplier sku", "sku", "catalog", "catalog number", "item number"],
    "unit_cost": ["unit cost", "cost", "price", "unit price"],
    "supplier_email": ["supplier email", "email", "order email", "supplier e-mail"],
    "stock_qty": ["stock", "current stock", "opening stock", "on hand", "in stock",
                  "quantity", "qty", "count"],
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def map_headers(headers: list[str]) -> dict:
    """Map the file's headers to our canonical field names."""
    mapping = {}
    for i, raw in enumerate(headers):
        key = (raw or "").strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                mapping[canonical] = i
                break
    return mapping


def unmapped_headers(headers: list[str], mapping: dict) -> list[str]:
    """Headers present in the file that we did not recognise, so we can say so."""
    used = set(mapping.values())
    out = []
    for i, raw in enumerate(headers):
        label = (raw or "").strip()
        if label and i not in used:
            out.append(label)
    return out


def parse_decimal(value: str):
    value = (value or "").strip().replace(",", "").replace("$", "")
    if value == "":
        return None
    try:
        d = Decimal(value)
        return d
    except (InvalidOperation, ValueError):
        return "INVALID"


def parse_rows(content: str):
    """Parse CSV text into (headers, header_mapping, list_of_raw_rows)."""
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty.")
    headers = rows[0]
    mapping = map_headers(headers)
    if "name" not in mapping:
        raise HTTPException(
            status_code=400,
            detail="Could not find a 'Name' column. The file needs at least a column for the item name.",
        )
    return headers, mapping, rows[1:]


def build_preview(db: Session, clinic_id: int, content: str) -> dict:
    headers, mapping, data_rows = parse_rows(content)

    # Existing active names in this clinic, to catch duplicates against the DB.
    existing_names = {
        n[0].lower()
        for n in db.query(models.Item.name).filter(
            models.Item.clinic_id == clinic_id,
            models.Item.active == True,
        ).all()
    }

    valid = []
    problems = []
    seen_in_file = set()

    def cell(row, key):
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    for n, row in enumerate(data_rows, start=2):  # row 1 is headers
        if not any((c or "").strip() for c in row):
            continue  # skip blank lines

        name = cell(row, "name")
        if not name:
            problems.append({"row": n, "issue": "Missing item name"})
            continue

        key = name.lower()
        if key in seen_in_file:
            problems.append({"row": n, "issue": f"'{name}' appears more than once in the file"})
            continue
        if key in existing_names:
            problems.append({"row": n, "issue": f"'{name}' already exists in inventory"})
            continue
        seen_in_file.add(key)

        # Numeric fields
        numeric = {}
        row_ok = True
        for field in ["pack_size", "par_level", "reorder_qty", "unit_cost", "stock_qty"]:
            parsed = parse_decimal(cell(row, field))
            if parsed == "INVALID":
                problems.append({"row": n, "issue": f"'{cell(row, field)}' is not a valid number for {field.replace('_', ' ')}"})
                row_ok = False
                break
            if parsed is not None and parsed < 0:
                problems.append({"row": n, "issue": f"{field.replace('_', ' ')} cannot be negative"})
                row_ok = False
                break
            numeric[field] = parsed
        if not row_ok:
            continue

        pack_unit = cell(row, "pack_unit") or None
        pack_size = numeric["pack_size"] if numeric["pack_size"] and numeric["pack_size"] > 0 else Decimal("1")
        if pack_unit and pack_size <= 0:
            problems.append({"row": n, "issue": f"'{name}' has a pack unit but no valid pack size"})
            continue

        # A malformed supplier email is rejected here rather than failing at send time,
        # weeks later, when someone is trying to place a real order.
        supplier_email = cell(row, "supplier_email") or None
        if supplier_email and not EMAIL_RE.match(supplier_email):
            problems.append({"row": n, "issue": f"'{supplier_email}' is not a valid email address"})
            continue

        valid.append({
            "name": name,
            "category": cell(row, "category") or None,
            "unit": cell(row, "unit") or "unit",
            "pack_unit": pack_unit,
            "pack_size": pack_size,
            "par_level": numeric["par_level"] or Decimal("0"),
            "reorder_qty": numeric["reorder_qty"] or Decimal("0"),
            "supplier_name": cell(row, "supplier_name") or None,
            "supplier_sku": cell(row, "supplier_sku") or None,
            "unit_cost": numeric["unit_cost"],
            "supplier_email": supplier_email,
            "stock_qty": numeric["stock_qty"] or Decimal("0"),
        })

    ignored = unmapped_headers(headers, mapping)

    return {
        "total_rows": len([r for r in data_rows if any((c or "").strip() for c in r)]),
        "valid_count": len(valid),
        "problem_count": len(problems),
        "valid": valid,
        "problems": problems,
        "detected_columns": sorted(mapping.keys()),
        "ignored_columns": ignored,
    }


@router.post("/items/preview", response_model=schemas.ImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")  # handles Excel's byte-order mark
    except UnicodeDecodeError:
        try:
            content = raw.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read the file. Save it as UTF-8 CSV and try again.")
    return build_preview(db, clinic_id, content)


@router.post("/items/commit", response_model=schemas.ImportResult)
async def commit_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")

    preview = build_preview(db, clinic_id, content)

    created = 0
    received = 0
    for spec in preview["valid"]:
        item = models.Item(
            clinic_id=clinic_id,
            name=spec["name"],
            category=spec["category"],
            unit=spec["unit"],
            pack_unit=spec["pack_unit"],
            pack_size=spec["pack_size"],
            par_level=spec["par_level"],
            reorder_qty=spec["reorder_qty"],
            supplier_name=spec["supplier_name"],
            supplier_sku=spec["supplier_sku"],
            supplier_email=spec["supplier_email"],
            unit_cost=spec["unit_cost"],
        )
        db.add(item)
        created += 1

        opening = spec["stock_qty"]
        if opening and opening > 0:
            # Every item is created at zero and its opening count is recorded as a real
            # received movement. Writing stock_qty directly would leave stock with no
            # entry in the ledger explaining where it came from, which is the one
            # invariant the analytics, variance and runout math all depend on.
            db.flush()  # the movement needs item.id
            apply_movement(
                db,
                item,
                change_qty=opening,
                reason="received",
                note="Opening count from CSV import",
                user_id=current_user.id,
            )
            received += 1

    db.commit()
    return {
        "created": created,
        "skipped": preview["problem_count"],
        "problems": preview["problems"],
    }


# ---------------------------------------------------------------------------
# Procedure import
#
# One CSV row is one supply line. Each line becomes its own ProcedureSupply row,
# never merged with another. Rows are grouped into procedures by procedure name.
# ---------------------------------------------------------------------------

PROC_COLUMN_ALIASES = {
    "procedure_name": ["procedure", "procedure name", "procedure_name", "name", "treatment"],
    "procedure_code": ["code", "procedure code", "procedure_code", "cdt", "cdt code"],
    "item_name": ["item", "item name", "item_name", "supply", "supply item", "product", "material"],
    "qty": ["qty", "quantity", "qty per procedure", "qty_per_procedure", "quantity per procedure",
            "qty per case", "amount", "used"],
}


def map_proc_headers(headers: list[str]) -> dict:
    mapping = {}
    for i, raw in enumerate(headers):
        key = (raw or "").strip().lower()
        for canonical, aliases in PROC_COLUMN_ALIASES.items():
            if key in aliases:
                mapping[canonical] = i
                break
    return mapping


def parse_proc_rows(content: str):
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty.")
    headers = rows[0]
    mapping = map_proc_headers(headers)
    missing = [c for c in ("procedure_name", "item_name", "qty") if c not in mapping]
    if missing:
        pretty = {"procedure_name": "Procedure", "item_name": "Item", "qty": "Qty"}
        raise HTTPException(
            status_code=400,
            detail="Could not find a column for: "
                   + ", ".join(pretty[m] for m in missing)
                   + ". The file needs one row per supply line, with a procedure name, an item name and a quantity.",
        )
    return headers, mapping, rows[1:]


def build_procedure_preview(db: Session, clinic_id: int, content: str) -> dict:
    headers, mapping, data_rows = parse_proc_rows(content)

    # Active items in this clinic, matched case insensitively by name.
    items = db.query(models.Item).filter(
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    ).all()
    items_by_name = {i.name.strip().lower(): i for i in items}

    existing_procs = {
        p[0].strip().lower()
        for p in db.query(models.Procedure.name).filter(
            models.Procedure.clinic_id == clinic_id,
            models.Procedure.active == True,
        ).all()
    }

    def cell(row, key):
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    # Group rows into procedures, keeping the order they first appear in the file.
    grouped = {}   # lower name -> dict
    problems = []
    missing_items = set()
    total_lines = 0

    for n, row in enumerate(data_rows, start=2):
        if not any((c or "").strip() for c in row):
            continue

        proc_name = cell(row, "procedure_name")
        if not proc_name:
            problems.append({"row": n, "issue": "Missing procedure name"})
            continue

        key = proc_name.lower()
        if key not in grouped:
            grouped[key] = {
                "name": proc_name,
                "code": cell(row, "procedure_code") or None,
                "lines": [],
                "blocked": None,
                "seen_items": set(),
            }
            if key in existing_procs:
                grouped[key]["blocked"] = f"'{proc_name}' already exists in this clinic"
        group = grouped[key]
        if group["code"] is None and cell(row, "procedure_code"):
            group["code"] = cell(row, "procedure_code")

        total_lines += 1

        item_name = cell(row, "item_name")
        if not item_name:
            problems.append({"row": n, "issue": f"'{proc_name}': missing item name"})
            group["blocked"] = group["blocked"] or "One or more supply lines are invalid"
            continue

        qty = parse_decimal(cell(row, "qty"))
        if qty == "INVALID":
            problems.append({"row": n, "issue": f"'{item_name}': '{cell(row, 'qty')}' is not a valid quantity"})
            group["blocked"] = group["blocked"] or "One or more supply lines are invalid"
            continue
        if qty is None or qty <= 0:
            problems.append({"row": n, "issue": f"'{item_name}': quantity must be greater than zero"})
            group["blocked"] = group["blocked"] or "One or more supply lines are invalid"
            continue

        item = items_by_name.get(item_name.lower())
        if item is None:
            problems.append({"row": n, "issue": f"'{item_name}' is not in this clinic's inventory"})
            missing_items.add(item_name)
            group["blocked"] = group["blocked"] or "One or more supply items are not in inventory"
            continue

        if item.id in group["seen_items"]:
            problems.append({"row": n, "issue": f"'{proc_name}': '{item_name}' appears more than once"})
            group["blocked"] = group["blocked"] or "One or more supply lines are duplicated"
            continue
        group["seen_items"].add(item.id)

        # One CSV row, one supply line, one ProcedureSupply row on commit.
        group["lines"].append({
            "item_id": item.id,
            "item_name": item.name,
            "unit": item.unit,
            "qty_per_procedure": qty,
        })

    valid = []
    skipped = []
    for group in grouped.values():
        if group["blocked"]:
            skipped.append({"name": group["name"], "reason": group["blocked"]})
            continue
        if not group["lines"]:
            skipped.append({"name": group["name"], "reason": "No usable supply lines"})
            continue
        valid.append({
            "name": group["name"],
            "code": group["code"],
            "line_count": len(group["lines"]),
            "lines": group["lines"],
        })

    return {
        "total_rows": total_lines,
        "procedure_count": len(valid),
        "line_count": sum(p["line_count"] for p in valid),
        "valid": valid,
        "skipped_procedures": skipped,
        "problem_count": len(problems),
        "problems": problems,
        "missing_items": sorted(missing_items),
        "detected_columns": sorted(mapping.keys()),
        "ignored_columns": unmapped_headers(headers, mapping),
    }


async def _read_upload(file: UploadFile) -> str:
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")
    raw = await file.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read the file. Save it as UTF-8 CSV and try again.")


@router.post("/procedures/preview", response_model=schemas.ProcedureImportPreview)
async def preview_procedure_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    content = await _read_upload(file)
    return build_procedure_preview(db, clinic_id, content)


@router.post("/procedures/commit", response_model=schemas.ProcedureImportResult)
async def commit_procedure_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    content = await _read_upload(file)
    preview = build_procedure_preview(db, clinic_id, content)

    created_procedures = 0
    created_lines = 0
    for spec in preview["valid"]:
        procedure = models.Procedure(
            clinic_id=clinic_id,
            name=spec["name"],
            code=spec["code"],
            active=True,
        )
        db.add(procedure)
        db.flush()  # the supply rows need procedure.id

        # Each consumed item is added as its own ProcedureSupply row.
        for line in spec["lines"]:
            db.add(models.ProcedureSupply(
                procedure_id=procedure.id,
                item_id=line["item_id"],
                qty_per_procedure=line["qty_per_procedure"],
            ))
            created_lines += 1

        created_procedures += 1

    db.commit()
    return {
        "created_procedures": created_procedures,
        "created_lines": created_lines,
        "skipped_procedures": len(preview["skipped_procedures"]),
        "problems": preview["problems"],
        "missing_items": preview["missing_items"],
    }