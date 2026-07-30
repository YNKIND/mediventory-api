from pydantic import BaseModel, EmailStr
from decimal import Decimal
from typing import Optional
from datetime import datetime

class ItemCreate(BaseModel):
    name: str
    category: Optional[str] = None
    unit: str = "unit"
    par_level: Decimal = Decimal("0")
    reorder_qty: Decimal = Decimal("0")


class ItemOut(BaseModel):
    id: int
    name: str
    category: Optional[str]
    unit: str
    stock_qty: Decimal
    par_level: Decimal
    reorder_qty: Decimal
    active: bool

    class Config:
        from_attributes = True


class StockChange(BaseModel):
    change_qty: Decimal
    note: Optional[str] = None

class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class ProcedureCreate(BaseModel):
    name: str
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
    qty_per_procedure: Decimal


class AppointmentCreate(BaseModel):
    procedure_id: int
    patient_label: Optional[str] = None
    scheduled_at: datetime


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
    stock_qty: Decimal
    par_level: Decimal
    suggested_qty: Decimal


class DashboardSummary(BaseModel):
    total_items: int
    low_count: int
    critical_count: int
    appointments_today: int
    completed_today: int


class MovementOut(BaseModel):
    id: int
    change_qty: Decimal
    reason: str
    note: Optional[str]
    appointment_id: Optional[int]
    created_at: datetime

class Config:
    from_attributes = True

class NewItemInline(BaseModel):
    name: str
    category: Optional[str] = None
    unit: str = "unit"
    par_level: Decimal = Decimal("0")
    reorder_qty: Decimal = Decimal("0")


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