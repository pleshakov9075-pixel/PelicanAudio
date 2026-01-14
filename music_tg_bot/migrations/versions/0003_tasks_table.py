"""Add tasks table.

Revision ID: 0003_tasks_table
Revises: 0002_users_tg_id_bigint
Create Date: 2025-02-14 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_tasks_table"
down_revision = "0002_users_tg_id_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("edit_request", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("progress_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("progress_message_id", sa.BigInteger(), nullable=True),
        sa.Column("genapi_request_id", sa.Integer(), nullable=True),
        sa.Column("suno_request_id", sa.Integer(), nullable=True),
        sa.Column("mp3_url_1", sa.Text(), nullable=True),
        sa.Column("mp3_url_2", sa.Text(), nullable=True),
        sa.Column("title_text", sa.String(length=128), nullable=True),
        sa.Column("lyrics_current", sa.Text(), nullable=True),
        sa.Column("tags_current", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
