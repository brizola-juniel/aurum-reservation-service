"""add case-insensitive location and room uniqueness

Revision ID: 202605120002
Revises: 202605120001
Create Date: 2026-05-12 10:30:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202605120002"
down_revision: str | Sequence[str] | None = "202605120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("uq_locations_name_ci", "locations", [sa.text("lower(name)")], unique=True)
    op.create_index(
        "uq_rooms_location_name_ci",
        "rooms",
        ["location_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_rooms_location_name_ci", table_name="rooms")
    op.drop_index("uq_locations_name_ci", table_name="locations")
