from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ItemCreate(BaseModel):
    name: str
    category: Optional[str] = None
    unit: Optional[str] = "unit"
    pack_unit: Optional[str] = None
    pack_size: Optional[Decimal] = Decimal("1")
    par_level: Optional[Decimal] = Decimal("0")
    reorder_qty: Optional[Decimal] = Decimal("0")
    supplier_name: Optional[str] = None
    supplier_sku: Optional[str] = None
    unit_cost: Optional[Decimal] = None


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    pack_unit: Optional[str] = None
    pack_size: Optional[Decimal] = None
    par_level: Optional[Decimal] = None
    reorder_qty: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    supplier_sku: Optional[str] = None
    unit_cost: Optional[Decimal] = None


class ItemOut(BaseModel):
    id: int
    name: str
    category: Optional[str]
    unit: str
    pack_unit: Optional[str]
    pack_size: Decimal
    stock_qty: Decimal
    par_level: Decimal
    reorder_qty: Decimal
    supplier_name: Optional[str]
    supplier_sku: Optional[str]
    unit_cost: Optional[Decimal]
    active: bool

    class Config:
        from_attributes = True


class NewItemInline(BaseModel):
    name: str
    category: Optional[str] = None
    unit: Optional[str] = "unit"
    pack_unit: Optional[str] = None
    pack_size: Optional[Decimal] = Decimal("1")
    par_level: Optional[Decimal] = Decimal("0")
    reorder_qty: Optional[Decimal] = Decimal("0")
    supplier_name: Optional[str] = None
    supplier_sku: Optional[str] = None
    unit_cost: Optional[Decimal] = None



class StockChange(BaseModel):
    change_qty: Decimal
    note: Optional[str] = None
    as_packs: bool = False




class ProcedureCreate(BaseModel):
    name: str
    code: Optional[str] = None


class ProcedureUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None


class ProcedureOut(BaseModel):
    id: int
    name: str
    code: Optional[str]
    active: bool

    class Config:
        from_attributes = True


class SupplyLine(BaseModel):
    item_id: int
    qty_per_procedure: Decimal


class SupplyLineOut(BaseModel):
    item_id: int
    item_name: str
    unit: str
    qty_per_procedure: Decimal


class NewItemInline(BaseModel):
    name: str
    category: Optional[str] = None
    unit: Optional[str] = "unit"
    pack_unit: Optional[str] = None
    pack_size: Optional[Decimal] = Decimal("1")
    par_level: Optional[Decimal] = Decimal("0")
    reorder_qty: Optional[Decimal] = Decimal("0")


class SupplyInput(BaseModel):
    item_id: Optional[int] = None
    new_item: Optional[NewItemInline] = None
    qty_per_procedure: Decimal


class ProcedureWithSuppliesCreate(BaseModel):
    name: str
    code: Optional[str] = None
    supplies: list[SupplyInput]


class StockCheckLine(BaseModel):
    item_id: int
    item_name: str
    unit: str
    required: Decimal
    on_hand: Decimal
    after: Decimal
    par_level: Decimal
    sufficient: bool
    level: Optional[str]


class StockCheck(BaseModel):
    procedure_id: int
    procedure_name: str
    has_supplies: bool
    ready: bool
    lines: list[StockCheckLine]


class AppointmentCreate(BaseModel):
    procedure_id: int
    patient_label: Optional[str] = None
    scheduled_at: datetime


class AppointmentUpdate(BaseModel):
    procedure_id: Optional[int] = None
    patient_label: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class AppointmentOut(BaseModel):
    id: int
    procedure_id: int
    patient_label: Optional[str]
    scheduled_at: datetime
    status: str
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    item_id: int
    item_name: str
    unit: str
    stock_qty: Decimal
    par_level: Decimal
    level: str


class ReorderLineOut(BaseModel):
    item_id: int
    item_name: str
    unit: str
    pack_unit: Optional[str]
    pack_size: Decimal
    stock_qty: Decimal
    par_level: Decimal
    suggested_qty: Decimal
    suggested_packs: Optional[Decimal]
    supplier_name: Optional[str]
    supplier_sku: Optional[str]
    unit_cost: Optional[Decimal]
    estimated_cost: Optional[Decimal]


class MovementOut(BaseModel):
    id: int
    change_qty: Decimal
    expected_qty: Optional[Decimal] = None
    reason: str
    note: Optional[str]
    appointment_id: Optional[int]
    user_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class DashboardSummary(BaseModel):
    total_items: int
    low_count: int
    critical_count: int
    appointments_today: int
    completed_today: int

class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: str
    active: bool

    class Config:
        from_attributes = True


class UserAdminCreate(BaseModel):
    email: EmailStr
    full_name: str
    role: str = "staff"
    password: Optional[str] = None
    send_invite: bool = True


class UserAdminUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class SimpleMessage(BaseModel):
    message: str

class CompletionDraftLine(BaseModel):
    item_id: int
    item_name: str
    unit: str
    expected_qty: Decimal
    on_hand: Decimal
    in_bom: bool


class CompletionDraft(BaseModel):
    appointment_id: int
    procedure_name: str
    patient_label: Optional[str]
    lines: list[CompletionDraftLine]


class ConfirmedLine(BaseModel):
    item_id: int
    actual_qty: Decimal
    expected_qty: Optional[Decimal] = None


class CompleteRequest(BaseModel):
    lines: list[ConfirmedLine]

class RegisterRequest(BaseModel):
    clinic_name: str
    email: EmailStr
    full_name: str
    password: str


class MeOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: str
    active: bool
    clinic_id: int
    clinic_name: str

class ImportProblem(BaseModel):
    row: int
    issue: str


class ImportPreviewItem(BaseModel):
    name: str
    category: Optional[str]
    unit: str
    pack_unit: Optional[str]
    pack_size: Decimal
    par_level: Decimal
    reorder_qty: Decimal
    supplier_name: Optional[str]
    supplier_sku: Optional[str]
    unit_cost: Optional[Decimal]


class ImportPreview(BaseModel):
    total_rows: int
    valid_count: int
    problem_count: int
    valid: list[ImportPreviewItem]
    problems: list[ImportProblem]
    detected_columns: list[str]


class ImportResult(BaseModel):
    created: int
    skipped: int
    problems: list[ImportProblem]

class CategoryConsumption(BaseModel):
    category: str
    units: Decimal
    cost: Decimal


class ProviderConsumption(BaseModel):
    provider: str
    units: Decimal
    cost: Decimal


class AnalyticsSummary(BaseModel):
    days: int
    total_consumption_units: Decimal
    total_consumption_cost: Decimal
    by_category: list[CategoryConsumption]
    by_provider: list[ProviderConsumption]


class ProcedureCostRow(BaseModel):
    procedure_id: int
    procedure_name: str
    times_completed: int
    total_cost: Decimal
    avg_cost: Decimal

class ClinicMembershipOut(BaseModel):
    clinic_id: int
    clinic_name: str
    role: str

class AddToClinicRequest(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: str = "staff"