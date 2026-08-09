from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.stock import apply_movement
from app.dependencies import get_db, get_current_user, get_clinic_id, require_admin
from app.levels import stock_level

from app.email import send_email, email_configured  # adjust to your email module's actual functions


router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


def get_clinic_po(db: Session, po_id: int, clinic_id: int) -> models.PurchaseOrder:
    po = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.id == po_id,
        models.PurchaseOrder.clinic_id == clinic_id,
    ).first()
    if not po:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase order not found")
    return po


def serialize_po(db: Session, po: models.PurchaseOrder) -> dict:
    lines = db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).all()

    line_out = []
    total_cost = Decimal("0")
    all_received = len(lines) > 0
    any_received = False
    for line in lines:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
        line_cost = (line.qty_ordered * line.unit_cost) if line.unit_cost is not None else None
        if line_cost is not None:
            total_cost += line_cost
        remaining = line.qty_ordered - line.qty_received
        if line.qty_received > 0:
            any_received = True
        if remaining > 0:
            all_received = False
        line_out.append({
            "id": line.id,
            "item_id": line.item_id,
            "item_name": item.name if item else "Unknown",
            "unit": item.unit if item else "unit",
            "qty_ordered": line.qty_ordered,
            "qty_received": line.qty_received,
            "qty_remaining": remaining,
            "unit_cost": line.unit_cost,
            "line_cost": line_cost,
        })

    # Derived receiving state for display.
    if po.status == "ordered" and any_received and not all_received:
        display_status = "partially received"
    else:
        display_status = po.status

    return {
        "id": po.id,
        "supplier_name": po.supplier_name,
        "status": po.status,
        "display_status": display_status,
        "note": po.note,
        "created_at": po.created_at,
        "ordered_at": po.ordered_at,
        "received_at": po.received_at,
        "estimated_total": total_cost.quantize(Decimal("0.01")),
        "lines": line_out,
    }


@router.post("", response_model=schemas.PurchaseOrderOut)
def create_po(
    payload: schemas.PurchaseOrderCreate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.lines:
        raise HTTPException(status_code=400, detail="A purchase order needs at least one line")

    po = models.PurchaseOrder(
        clinic_id=clinic_id,
        supplier_name=(payload.supplier_name or "").strip() or None,
        note=(payload.note or "").strip() or None,
        status="draft",
        created_by=current_user.id,
    )
    db.add(po)
    db.flush()

    seen = set()
    for line in payload.lines:
        if line.item_id in seen:
            raise HTTPException(status_code=400, detail="The same item appears twice in the order")
        seen.add(line.item_id)
        if line.qty_ordered <= 0:
            raise HTTPException(status_code=400, detail="Order quantity must be greater than zero")

        item = db.query(models.Item).filter(
            models.Item.id == line.item_id,
            models.Item.clinic_id == clinic_id,
        ).first()
        if not item:
            raise HTTPException(status_code=400, detail="Item not found in this clinic")

        unit_cost = line.unit_cost if line.unit_cost is not None else item.unit_cost
        db.add(models.PurchaseOrderLine(
            purchase_order_id=po.id,
            item_id=item.id,
            qty_ordered=line.qty_ordered,
            qty_received=Decimal("0"),
            unit_cost=unit_cost,
        ))

    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/from-reorder", response_model=list[schemas.PurchaseOrderOut])
def create_from_reorder(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    """Generate draft POs from everything at or below minimum, one PO per supplier."""
    items = db.query(models.Item).filter(
        models.Item.clinic_id == clinic_id,
        models.Item.active == True,
    ).all()

    # Group low items by supplier.
    by_supplier = {}
    for item in items:
        if stock_level(item.stock_qty, item.par_level) is None:
            continue
        suggested = item.reorder_qty if item.reorder_qty and item.reorder_qty > 0 else (item.par_level - item.stock_qty)
        if suggested <= 0:
            continue
        key = item.supplier_name or "No supplier"
        by_supplier.setdefault(key, []).append((item, suggested))

    if not by_supplier:
        raise HTTPException(status_code=400, detail="Nothing is below minimum right now, so there is nothing to order")

    created = []
    for supplier, entries in by_supplier.items():
        po = models.PurchaseOrder(
            clinic_id=clinic_id,
            supplier_name=None if supplier == "No supplier" else supplier,
            status="draft",
            created_by=current_user.id,
        )
        db.add(po)
        db.flush()
        for item, qty in entries:
            db.add(models.PurchaseOrderLine(
                purchase_order_id=po.id,
                item_id=item.id,
                qty_ordered=qty,
                qty_received=Decimal("0"),
                unit_cost=item.unit_cost,
            ))
        db.flush()
        created.append(po)

    db.commit()
    return [serialize_po(db, po) for po in created]


@router.get("", response_model=list[schemas.PurchaseOrderSummary])
def list_pos(
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    pos = db.query(models.PurchaseOrder).filter(
        models.PurchaseOrder.clinic_id == clinic_id
    ).order_by(models.PurchaseOrder.created_at.desc()).all()

    result = []
    for po in pos:
        full = serialize_po(db, po)
        result.append({
            "id": po.id,
            "supplier_name": po.supplier_name,
            "status": po.status,
            "display_status": full["display_status"],
            "created_at": po.created_at,
            "line_count": len(full["lines"]),
            "estimated_total": full["estimated_total"],
        })
    return result


@router.get("/{po_id}", response_model=schemas.PurchaseOrderOut)
def get_po(
    po_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    po = get_clinic_po(db, po_id, clinic_id)
    return serialize_po(db, po)


@router.post("/{po_id}/mark-ordered", response_model=schemas.PurchaseOrderOut)
def mark_ordered(
    po_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    po = get_clinic_po(db, po_id, clinic_id)
    if po.status != "draft":
        raise HTTPException(status_code=400, detail="Only a draft order can be marked as ordered")
    po.status = "ordered"
    po.ordered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/{po_id}/receive", response_model=schemas.PurchaseOrderOut)
def receive_po(
    po_id: int,
    payload: schemas.PurchaseOrderReceive,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    po = get_clinic_po(db, po_id, clinic_id)
    if po.status == "cancelled":
        raise HTTPException(status_code=400, detail="This order was cancelled")
    if po.status == "received":
        raise HTTPException(status_code=400, detail="This order is already fully received")

    lines = {l.id: l for l in db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).all()}

    # Validate all first.
    to_apply = []
    for entry in payload.lines:
        line = lines.get(entry.line_id)
        if not line:
            raise HTTPException(status_code=400, detail="A line in this receipt does not belong to this order")
        if entry.qty_received < 0:
            raise HTTPException(status_code=400, detail="Received quantity cannot be negative")
        if entry.qty_received == 0:
            continue
        remaining = line.qty_ordered - line.qty_received
        if entry.qty_received > remaining:
            item = db.query(models.Item).filter(models.Item.id == line.item_id).first()
            raise HTTPException(
                status_code=400,
                detail=f"Receiving more than ordered for {item.name if item else 'an item'} ({entry.qty_received} > {remaining} remaining)",
            )
        to_apply.append((line, entry.qty_received))

    if not to_apply:
        raise HTTPException(status_code=400, detail="Enter a received quantity on at least one line")

    # Apply: each received line goes through the ledger, then updates the PO line.
    for line, qty in to_apply:
        item = db.query(models.Item).filter(
            models.Item.id == line.item_id,
            models.Item.clinic_id == clinic_id,
        ).first()
        if not item:
            raise HTTPException(status_code=400, detail="Item not found in this clinic")
        apply_movement(
            db, item, qty, reason="received",
            note=f"Received against PO #{po.id}",
            user_id=current_user.id,
        )
        line.qty_received = line.qty_received + qty

    # If the PO was still a draft (received without marking ordered), move it forward.
    if po.status == "draft":
        po.status = "ordered"
        po.ordered_at = datetime.now(timezone.utc)

    # Fully received?
    all_lines = db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).all()
    if all((l.qty_received >= l.qty_ordered) for l in all_lines):
        po.status = "received"
        po.received_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/{po_id}/cancel", response_model=schemas.PurchaseOrderOut)
def cancel_po(
    po_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    po = get_clinic_po(db, po_id, clinic_id)
    if po.status == "received":
        raise HTTPException(status_code=400, detail="A fully received order cannot be cancelled")
    po.status = "cancelled"
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.delete("/{po_id}")
def delete_po(
    po_id: int,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(require_admin),
):
    po = get_clinic_po(db, po_id, clinic_id)
    # Only allow deleting a draft with nothing received, to protect the ledger.
    received_any = db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id,
        models.PurchaseOrderLine.qty_received > 0,
    ).first()
    if received_any:
        raise HTTPException(status_code=400, detail="This order has received stock recorded against it. Cancel it instead of deleting.")
    db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).delete()
    db.delete(po)
    db.commit()
    return {"deleted": True}

@router.patch("/{po_id}/lines", response_model=schemas.PurchaseOrderOut)
def update_po_lines(
    po_id: int,
    payload: schemas.PurchaseOrderLinesUpdate,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    po = get_clinic_po(db, po_id, clinic_id)
    if po.status != "draft":
        raise HTTPException(status_code=400, detail="Only a draft order can be edited. This order has already been placed.")

    existing = {l.id: l for l in db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).all()}

    # Validate first.
    seen_items = set()
    for line in payload.lines:
        if line.qty_ordered <= 0:
            raise HTTPException(status_code=400, detail="Order quantity must be greater than zero. Remove the line instead to drop it.")

    keep_ids = set()
    for line in payload.lines:
        if line.id is not None:
            # Update an existing line's quantity.
            target = existing.get(line.id)
            if not target:
                raise HTTPException(status_code=400, detail="A line does not belong to this order")
            target.qty_ordered = line.qty_ordered
            keep_ids.add(line.id)
        else:
            # Add a new line for an item in this clinic.
            item = db.query(models.Item).filter(
                models.Item.id == line.item_id,
                models.Item.clinic_id == clinic_id,
            ).first()
            if not item:
                raise HTTPException(status_code=400, detail="Item not found in this clinic")
            if item.id in seen_items:
                raise HTTPException(status_code=400, detail="The same item appears twice")
            new_line = models.PurchaseOrderLine(
                purchase_order_id=po.id,
                item_id=item.id,
                qty_ordered=line.qty_ordered,
                qty_received=0,
                unit_cost=item.unit_cost,
            )
            db.add(new_line)
        if line.item_id:
            seen_items.add(line.item_id)

    # Delete any existing line the client dropped (not in keep_ids and had an id).
    for lid, line in existing.items():
        if lid not in keep_ids:
            db.delete(line)

    # A PO must have at least one line.
    remaining = len(payload.lines)
    if remaining == 0:
        raise HTTPException(status_code=400, detail="An order needs at least one line. Cancel the order instead to remove it.")

    db.commit()
    db.refresh(po)
    return serialize_po(db, po)



@router.post("/{po_id}/send", response_model=schemas.PurchaseOrderOut)
def send_po_to_supplier(
    po_id: int,
    payload: schemas.SendPORequest,
    db: Session = Depends(get_db),
    clinic_id: int = Depends(get_clinic_id),
    current_user: models.User = Depends(get_current_user),
):
    po = get_clinic_po(db, po_id, clinic_id)
    if po.status not in ("draft", "ordered"):
        raise HTTPException(status_code=400, detail="Only a draft or ordered order can be emailed to a supplier.")

    to_email = (payload.to_email or "").strip()
    if not to_email or "@" not in to_email:
        raise HTTPException(status_code=400, detail="A valid supplier email is required.")

    lines = db.query(models.PurchaseOrderLine).filter(
        models.PurchaseOrderLine.purchase_order_id == po.id
    ).all()
    if not lines:
        raise HTTPException(status_code=400, detail="This order has no lines to send.")

    clinic = db.query(models.Clinic).filter(models.Clinic.id == clinic_id).first()
    clinic_label = clinic.name if clinic else "Our clinic"

    row_html = []
    total = Decimal("0")
    for ln in lines:
        item = db.query(models.Item).filter(models.Item.id == ln.item_id).first()
        name = item.name if item else f"Item {ln.item_id}"
        unit = item.unit if item else "unit"
        sku = item.supplier_sku if item and item.supplier_sku else ""
        cost_cell = ""
        if ln.unit_cost is not None:
            line_total = ln.qty_ordered * ln.unit_cost
            total += line_total
            cost_cell = f"${ln.unit_cost:.2f}"
        row_html.append(
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #E4EAF0;'>{name}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #E4EAF0;'>{sku}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #E4EAF0;'>{Decimal(ln.qty_ordered)} {unit}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #E4EAF0;'>{cost_cell}</td>"
            f"</tr>"
        )

    note_html = ""
    if payload.note and payload.note.strip():
        note_html = f"<p style='margin-top:16px;'>{payload.note.strip()}</p>"

    total_html = ""
    if total > 0:
        total_html = f"<p style='margin-top:16px;font-weight:600;'>Estimated total: ${total:.2f}</p>"

    supplier_html = f"<p><strong>Supplier:</strong> {po.supplier_name}</p>" if po.supplier_name else ""

    html = f"""
    <div style="font-family: Arial, sans-serif; color: #0B1524; line-height: 1.6;">
      <h2 style="color: #1B69E8;">Purchase order #{po.id}</h2>
      <p>From <strong>{clinic_label}</strong></p>
      {supplier_html}
      <table style="border-collapse: collapse; width: 100%; margin-top: 16px;">
        <thead>
          <tr>
            <th style="text-align:left;padding:8px;border-bottom:2px solid #0B1524;">Item</th>
            <th style="text-align:left;padding:8px;border-bottom:2px solid #0B1524;">SKU</th>
            <th style="text-align:left;padding:8px;border-bottom:2px solid #0B1524;">Quantity</th>
            <th style="text-align:left;padding:8px;border-bottom:2px solid #0B1524;">Unit cost</th>
          </tr>
        </thead>
        <tbody>
          {''.join(row_html)}
        </tbody>
      </table>
      {total_html}
      {note_html}
      <p style="margin-top:24px;font-size:13px;color:#566172;">
        Please confirm receipt and expected delivery. Reply to this order or contact us at {current_user.email}.
      </p>
    </div>
    """

    subject = f"Purchase order #{po.id} from {clinic_label}"
    sent = send_email(to_email, subject, html)
    if not sent:
        if not email_configured():
            raise HTTPException(status_code=400, detail="Email is not configured on the server, so the order could not be sent.")
        raise HTTPException(status_code=502, detail="The email could not be sent. Please try again.")

    if po.status == "draft":
        po.status = "ordered"
        po.ordered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)