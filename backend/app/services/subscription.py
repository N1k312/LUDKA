"""Подписка клуба — учёт оплат. Сама по себе игры не останавливает.

trial (14 дней) → active (оплаченный период) → past_due («не оплачено»).

Подписка продлевается автоматически: когда оплаченный срок прошёл, статус становится
past_due, но игры продолжают работать. Если клуб не платит, владелец платформы
сам отключает доступ в пульте (clubs.status = disabled) и может вернуть его.

Статус выводится из дат: «оплачено до» — это current_period_end, а если клуб
ещё ни разу не платил — trial_ends_at.
canceled — только старые отмены (команда cancel-subscription в консоли): играть нельзя,
пока владелец снова не продлит подписку.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)

TRIAL_PERIOD = timedelta(days=14)
DUE_SOON = timedelta(days=3)  # «скоро к оплате» в пульте
PLAYABLE = {SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}


MAX_AMOUNT = Decimal("9999999999.99")  # столько вмещает payments.amount NUMERIC(12, 2)


def parse_amount(raw: str) -> Decimal:
    """«20 000», «20000,50» → Decimal с копейками. ValueError с понятной причиной иначе.

    Отсекает nan, Infinity, слишком большие суммы и суммы, которые после округления
    до копеек становятся нулём (иначе их отвергла бы база).
    """
    try:
        amount = Decimal(raw.strip().replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError("Сумма — число, например 20000") from None
    if not amount.is_finite():
        raise ValueError("Сумма — обычное число, например 20000")
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля (минимум 0.01)")
    if amount > MAX_AMOUNT:
        raise ValueError("Сумма слишком большая (максимум 9 999 999 999.99)")
    return amount


def paid_until(sub: Subscription) -> datetime | None:
    return sub.current_period_end or sub.trial_ends_at


def compute_status(sub: Subscription, now: datetime) -> SubscriptionStatus:
    if sub.status == SubscriptionStatus.CANCELED:
        return SubscriptionStatus.CANCELED
    until = paid_until(sub)
    if until is not None and now < until:
        return SubscriptionStatus.ACTIVE if sub.current_period_end else SubscriptionStatus.TRIAL
    return SubscriptionStatus.PAST_DUE  # не оплачено — но игры не останавливаются сами


def unpaid_days(sub: Subscription, now: datetime) -> int:
    """Сколько полных дней прошло после «оплачено до» (0 — оплачено)."""
    until = paid_until(sub)
    return max(0, (now - until).days) if until and now >= until else 0


def allows_play(sub: Subscription | None, now: datetime) -> bool:
    return sub is not None and compute_status(sub, now) in PLAYABLE


def new_trial(club_id: int, now: datetime | None = None) -> Subscription:
    now = now or datetime.now(UTC)
    return Subscription(
        club_id=club_id, status=SubscriptionStatus.TRIAL, trial_ends_at=now + TRIAL_PERIOD
    )


async def refresh_statuses(db: AsyncSession, now: datetime | None = None) -> int:
    """Переводит статусы по датам (trial/active → past_due). Возвращает число изменений."""
    now = now or datetime.now(UTC)
    changed = 0
    for sub in await db.scalars(
        select(Subscription).where(Subscription.status != SubscriptionStatus.CANCELED)
    ):
        status = compute_status(sub, now)
        if status != sub.status:
            logger.info("Подписка клуба %s: %s → %s", sub.club_id, sub.status, status)
            sub.status = status
            changed += 1
    await db.flush()
    return changed


async def extend(
    db: AsyncSession,
    sub: Subscription,
    days: int,
    amount: Decimal | None = None,
    comment: str | None = None,
    now: datetime | None = None,
) -> Payment | None:
    """Продление после оплаты счётом или переводом.

    Новый период начинается с конца текущего, если он ещё не истёк (оплата заранее
    не теряет дни), иначе — с текущего момента.
    """
    now = now or datetime.now(UTC)
    until = paid_until(sub)
    start = until if until and until > now and sub.status != SubscriptionStatus.CANCELED else now
    sub.current_period_end = start + timedelta(days=days)
    sub.status = SubscriptionStatus.ACTIVE

    payment = None
    if amount is not None:
        payment = Payment(club_id=sub.club_id, amount=amount, paid_at=now, comment=comment)
        db.add(payment)
    await db.flush()
    return payment


async def cancel(db: AsyncSession, sub: Subscription) -> None:
    sub.status = SubscriptionStatus.CANCELED
    await db.flush()
