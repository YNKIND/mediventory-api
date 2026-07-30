from sqlalchemy import Column, Integer, String, Numeric, Boolean, ForeignKey, DateTime, func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    unit = Column(String, nullable=False, default="unit")
    stock_qty = Column(Numeric(12, 2), nullable=False, default=0)
    par_level = Column(Numeric(12, 2), nullable=False, default=0)
    reorder_qty = Column(Numeric(12, 2), nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    change_qty = Column(Numeric(12, 2), nullable=False)
    reason = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
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