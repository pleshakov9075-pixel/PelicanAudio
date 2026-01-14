"""add welcome bonus and task fields

Revision ID: 0004_add_welcome_bonus_and_task_fields
Revises: 0003_tasks_table
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_add_welcome_bonus_and_task_fields"
down_revision = "0003_tasks_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("welcome_bonus_given", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("tasks", sa.Column("user_lyrics_raw", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("suggested_title", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "suggested_title")
    op.drop_column("tasks", "user_lyrics_raw")
    op.drop_column("users", "welcome_bonus_given")
