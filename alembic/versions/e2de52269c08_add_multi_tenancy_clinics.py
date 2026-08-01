"""add multi-tenancy clinics

Revision ID: e2de52269c08
Revises: 6b7d92980c9b
Create Date: 2026-08-01 00:13:42.147027

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2de52269c08'
down_revision: Union[str, Sequence[str], None] = '6b7d92980c9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the clinics table first.
    op.create_table(
        'clinics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('timezone', sa.String(), server_default='America/Toronto', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_clinics_id'), 'clinics', ['id'], unique=False)

    # 2. Insert one clinic for all existing data.
    op.execute("INSERT INTO clinics (name, timezone, active) VALUES ('My Clinic', 'America/Toronto', true)")

    # 3. Add clinic_id columns as NULLABLE first, so existing rows are allowed.
    op.add_column('users', sa.Column('clinic_id', sa.Integer(), nullable=True))
    op.add_column('items', sa.Column('clinic_id', sa.Integer(), nullable=True))
    op.add_column('procedures', sa.Column('clinic_id', sa.Integer(), nullable=True))
    op.add_column('appointments', sa.Column('clinic_id', sa.Integer(), nullable=True))
    op.add_column('stock_movements', sa.Column('clinic_id', sa.Integer(), nullable=True))

    # 4. Backfill every existing row with the clinic we just created.
    op.execute("UPDATE users SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1)")
    op.execute("UPDATE items SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1)")
    op.execute("UPDATE procedures SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1)")
    op.execute("UPDATE appointments SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1)")
    op.execute("UPDATE stock_movements SET clinic_id = (SELECT id FROM clinics ORDER BY id LIMIT 1)")

    # 5. Now that no NULLs remain, enforce NOT NULL and add foreign keys.
    op.alter_column('users', 'clinic_id', nullable=False)
    op.alter_column('items', 'clinic_id', nullable=False)
    op.alter_column('procedures', 'clinic_id', nullable=False)
    op.alter_column('appointments', 'clinic_id', nullable=False)
    op.alter_column('stock_movements', 'clinic_id', nullable=False)

    op.create_foreign_key('fk_users_clinic', 'users', 'clinics', ['clinic_id'], ['id'])
    op.create_foreign_key('fk_items_clinic', 'items', 'clinics', ['clinic_id'], ['id'])
    op.create_foreign_key('fk_procedures_clinic', 'procedures', 'clinics', ['clinic_id'], ['id'])
    op.create_foreign_key('fk_appointments_clinic', 'appointments', 'clinics', ['clinic_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_clinic', 'stock_movements', 'clinics', ['clinic_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_stock_movements_clinic', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_appointments_clinic', 'appointments', type_='foreignkey')
    op.drop_constraint('fk_procedures_clinic', 'procedures', type_='foreignkey')
    op.drop_constraint('fk_items_clinic', 'items', type_='foreignkey')
    op.drop_constraint('fk_users_clinic', 'users', type_='foreignkey')
    op.drop_column('stock_movements', 'clinic_id')
    op.drop_column('appointments', 'clinic_id')
    op.drop_column('procedures', 'clinic_id')
    op.drop_column('items', 'clinic_id')
    op.drop_column('users', 'clinic_id')
    op.drop_index(op.f('ix_clinics_id'), table_name='clinics')
    op.drop_table('clinics')
