"""Модели базы данных (MVP).

Все таблицы клуба содержат club_id. Связи между таблицами клуба сделаны
составными внешними ключами (club_id, id), поэтому база сама не даст
связать, например, игру гостя клуба А с призом клуба Б.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Enum хранится строкой с CHECK-ограничением (без типа ENUM в PostgreSQL)."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def club_fk() -> ForeignKey:
    return ForeignKey("clubs.id", ondelete="CASCADE")


# --- Перечисления ---------------------------------------------------------


class ClubStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class BotStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class StaffRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"


class GameType(StrEnum):
    WHEEL = "wheel"


class RedemptionAction(StrEnum):
    CHECK = "check"
    REDEEM = "redeem"


class RedemptionResult(StrEnum):
    VALID = "valid"
    REDEEMED = "redeemed"
    ALREADY_REDEEMED = "already_redeemed"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"


class SubscriptionStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


# --- Клубы и боты ---------------------------------------------------------


class Club(Base):
    __tablename__ = "clubs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Almaty")
    status: Mapped[ClubStatus] = mapped_column(
        str_enum(ClubStatus, "club_status"), default=ClubStatus.ACTIVE
    )
    # Правила акции, которые гость видит в Mini App.
    rules_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class ClubBot(Base):
    __tablename__ = "club_bots"

    # Telegram id бота: по нему вебхук /tg/{bot_id} определяет клуб.
    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk(), unique=True)
    username: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[str] = mapped_column(Text)
    webhook_secret: Mapped[str] = mapped_column(String(128))
    status: Mapped[BotStatus] = mapped_column(
        str_enum(BotStatus, "bot_status"), default=BotStatus.ACTIVE
    )
    created_at: Mapped[datetime] = created_at_column()


class ClubStaff(Base):
    __tablename__ = "club_staff"
    __table_args__ = (UniqueConstraint("club_id", "telegram_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[StaffRole] = mapped_column(str_enum(StaffRole, "staff_role"))
    created_at: Mapped[datetime] = created_at_column()


# --- Гости и игры ---------------------------------------------------------


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (
        UniqueConstraint("club_id", "telegram_id"),
        # Один номер телефона — один гость в клубе (NULL до того, как гость поделился номером).
        UniqueConstraint("club_id", "phone"),
        # Цель для составных внешних ключей из plays и prize_codes.
        UniqueConstraint("club_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class GameConfig(Base):
    __tablename__ = "games_config"
    __table_args__ = (CheckConstraint("cooldown_seconds > 0", name="cooldown_positive"),)

    club_id: Mapped[int] = mapped_column(BigInteger, club_fk(), primary_key=True)
    game_type: Mapped[GameType] = mapped_column(str_enum(GameType, "game_type"), primary_key=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=7 * 24 * 3600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PrizeOption(Base):
    __tablename__ = "prize_options"
    __table_args__ = (
        UniqueConstraint("club_id", "id"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        CheckConstraint("ttl_days > 0", name="ttl_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    game_type: Mapped[GameType] = mapped_column(str_enum(GameType, "game_type"))
    title: Mapped[str] = mapped_column(String(128))
    emoji: Mapped[str | None] = mapped_column(String(16))
    weight: Mapped[int] = mapped_column(Integer)
    is_empty: Mapped[bool] = mapped_column(Boolean, default=False)
    ttl_days: Mapped[int] = mapped_column(Integer, default=14)
    # Порядок сектора на колесе.
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Выключенный приз не выпадает, но старые игры и промокоды на него ссылаются.
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Play(Base):
    __tablename__ = "plays"
    __table_args__ = (
        UniqueConstraint("club_id", "id"),
        ForeignKeyConstraint(
            ["club_id", "guest_id"], ["guests.club_id", "guests.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["club_id", "prize_option_id"], ["prize_options.club_id", "prize_options.id"]
        ),
        # Проверка лимита: последняя игра гостя в этой игре.
        Index("ix_plays_guest_game_time", "club_id", "guest_id", "game_type", "played_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    guest_id: Mapped[int] = mapped_column(BigInteger)
    game_type: Mapped[GameType] = mapped_column(str_enum(GameType, "game_type"))
    prize_option_id: Mapped[int] = mapped_column(BigInteger)
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Промокоды ------------------------------------------------------------


class PrizeCode(Base):
    __tablename__ = "prize_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["club_id", "guest_id"], ["guests.club_id", "guests.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["club_id", "prize_option_id"], ["prize_options.club_id", "prize_options.id"]
        ),
        ForeignKeyConstraint(["club_id", "play_id"], ["plays.club_id", "plays.id"]),
        Index("ix_prize_codes_club_guest", "club_id", "guest_id"),
    )

    # Уникален на всей платформе.
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    guest_id: Mapped[int] = mapped_column(BigInteger)
    prize_option_id: Mapped[int] = mapped_column(BigInteger)
    play_id: Mapped[int | None] = mapped_column(BigInteger)
    # Название приза на момент выдачи: не меняется, если клуб переименует приз.
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Telegram id админа, погасившего промокод.
    redeemed_by: Mapped[int | None] = mapped_column(BigInteger)


class RedemptionLog(Base):
    __tablename__ = "redemption_log"
    __table_args__ = (
        # Подсчёт неудачных проверок админа (защита от перебора).
        Index("ix_redemption_log_admin_time", "club_id", "admin_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    # Без внешнего ключа: в журнал пишутся и несуществующие коды.
    code: Mapped[str] = mapped_column(String(32))
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[RedemptionAction] = mapped_column(
        str_enum(RedemptionAction, "redemption_action")
    )
    result: Mapped[RedemptionResult] = mapped_column(
        str_enum(RedemptionResult, "redemption_result")
    )
    created_at: Mapped[datetime] = created_at_column()


# --- Подписка и оплаты ----------------------------------------------------


class Subscription(Base):
    __tablename__ = "subscriptions"

    club_id: Mapped[int] = mapped_column(BigInteger, club_fk(), primary_key=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        str_enum(SubscriptionStatus, "subscription_status"), default=SubscriptionStatus.TRIAL
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[int] = mapped_column(BigInteger, club_fk())
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
