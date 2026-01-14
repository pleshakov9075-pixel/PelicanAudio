"""add task_id to transactions

Revision ID: 0005_add_task_id_to_transactions
Revises: 0004_add_welcome_bonus_and_task_fields
Create Date: 2025-02-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_add_task_id_to_transactions"
down_revision = "0004_add_welcome_bonus_and_task_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_transactions_task_id",
        "transactions",
        "tasks",
        ["task_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_transactions_task_id", "transactions", type_="foreignkey")
    op.drop_column("transactions", "task_id")
