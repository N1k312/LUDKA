"""cups and case games

Игры «Стаканчики» и «Кейс»: новые значения game_type и настройки для уже созданных
клубов (выключены, лимит 7 дней — клуб включает сам).

Revision ID: b7c1d2e3f405
Revises: 741d324ef66b
Create Date: 2026-11-25 15:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b7c1d2e3f405'
down_revision: Union[str, Sequence[str], None] = '741d324ef66b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("games_config", "prize_options", "plays")


def _check(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{v}'" for v in values)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_game_type")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_game_type "
                   f"CHECK (game_type IN ({allowed}))")


def upgrade() -> None:
    _check(("wheel", "cups", "case"))
    op.execute(
        "INSERT INTO games_config (club_id, game_type, cooldown_seconds, enabled) "
        "SELECT c.id, g.game_type, 604800, false FROM clubs c "
        "CROSS JOIN (VALUES ('cups'), ('case')) AS g(game_type) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DELETE FROM {table} WHERE game_type IN ('cups', 'case')")
    _check(("wheel",))
