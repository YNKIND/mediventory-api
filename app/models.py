from sqlalchemy import Column, Integer, String, Numeric, Boolean, ForeignKey, DateTime, func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, server_default="staff", default="staff")
    active = Column(Boolean, nullable=False, server_default="true", default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    unit = Column(String, nullable=False, default="unit")
    pack_unit = Column(String, nullable=True)
    pack_size = Column(Numeric(12, 2), nullable=False, server_default="1", default=1)
    stock_qty = Column(Numeric(12, 2), nullable=False, default=0)
    par_level = Column(Numeric(12, 2), nullable=False, default=0)
    reorder_qty = Column(Numeric(12, 2), nullable=False, default=0)
    supplier_name = Column(String, nullable=True)
    supplier_sku = Column(String, nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    active = Column(Boolean, nullable=False, default=True)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    change_qty = Column(Numeric(12, 2), nullable=False)
    expected_qty = Column(Numeric(12, 2), nullable=True)
    reason = Column(String, nullable=False)
    note = Column(String, nullable=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Procedure(Base):
    __tablename__ = "procedures"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)


class ProcedureSupply(Base):
    __tablename__ = "procedure_supplies"

    id = Column(Integer, primary_key=True, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty_per_procedure = Column(Numeric(12, 2), nullable=False)


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    patient_label = Column(String, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="scheduled")
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())