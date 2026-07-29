from pydantic import BaseModel, EmailStr
from decimal import Decimal
from typing import Optional


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