"""Подписка: статусы по датам, продление, отмена; без подписки колесо стоит, а погашение работает."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Payment, Subscription, SubscriptionStatus as S
from app.services import subscription
from app.services.subscription import GRACE_PERIOD, allows_play, compute_status

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def sub(status=S.TRIAL, trial_ends_at=None, current_period_end=None) -> Subscription:
    return Subscription(club_id=1, status=status, trial_ends_at=trial_ends_at,
                        current_period_end=current_period_end)


@pytest.mark.parametrize(
    "s,expected",
    [
        (sub(trial_ends_at=NOW + DAY), S.TRIAL),
        (sub(trial_ends_at=NOW - DAY), S.PAST_DUE),              # отсрочка после пробного периода
        (sub(trial_ends_at=NOW - GRACE_PERIOD - DAY), S.CANCELED),
        (sub(S.ACTIVE, NOW - 30 * DAY, NOW + DAY), S.ACTIVE),
        (sub(S.ACTIVE, NOW - 30 * DAY, NOW - DAY), S.PAST_DUE),
        (sub(S.PAST_DUE, NOW - 30 * DAY, NOW - 4 * DAY), S.CANCELED),
        (sub(S.CANCELED, NOW + DAY), S.CANCELED),                # отмена вручную не снимается датами
        (sub(trial_ends_at=None), S.CANCELED),
    ],
)
def test_compute_status(s, expected):
    assert compute_status(s, NOW) == expected


@pytest.mark.parametrize("status,playable", [(S.TRIAL, True), (S.ACTIVE, True), (S.PAST_DUE, True), (S.CANCELED, False)])
def test_allows_play_by_status(status, playable):
    ends = {
        S.TRIAL: sub(trial_ends_at=NOW + DAY),
        S.ACTIVE: sub(S.ACTIVE, None, NOW + DAY),
        S.PAST_DUE: sub(S.ACTIVE, None, NOW - DAY),
        S.CANCELED: sub(S.CANCELED, NOW + DAY),
    }[status]
    assert allows_play(ends, NOW) is playable
    assert allows_play(None, NOW) is False


async def test_extend_before_expiry_keeps_remaining_days(session: AsyncSession):
    club = Club(name="А")
    session.add(club)
    await session.flush()
    s = sub(trial_ends_at=NOW + 5 * DAY)
    s.club_id = club.id
    session.add(s)

    payment = await subscription.extend(session, s, 30, Decimal("20000"), "счёт №1", now=NOW)

    assert s.status == S.ACTIVE
    assert s.current_period_end == NOW + 35 * DAY  # 5 оставшихся дней пробного + 30 оплаченных
    assert payment.amount == Decimal("20000") and payment.comment == "счёт №1"
    assert (await session.scalars(select(Payment).where(Payment.club_id == club.id))).all() == [payment]


async def test_extend_after_cancel_starts_now(session: AsyncSession):
    club = Club(name="А")
    session.add(club)
    await session.flush()
    s = sub(S.CANCELED, NOW + 5 * DAY)
    s.club_id = club.id
    session.add(s)

    await subscription.extend(session, s, 30, now=NOW)

    assert s.status == S.ACTIVE and s.current_period_end == NOW + 30 * DAY


async def test_refresh_statuses_moves_by_dates(session: AsyncSession):
    clubs = [Club(name=n) for n in ("trial", "expired", "canceled")]
    session.add_all(clubs)
    await session.flush()
    trial = sub(trial_ends_at=NOW + DAY)
    expired = sub(S.ACTIVE, None, NOW - 10 * DAY)
    canceled = sub(S.CANCELED, NOW + DAY)
    for club, s in zip(clubs, (trial, expired, canceled)):
        s.club_id = club.id
    session.add_all([trial, expired, canceled])
    await session.flush()

    changed = await subscription.refresh_statuses(session, now=NOW)

    assert changed == 1
    assert (trial.status, expired.status, canceled.status) == (S.TRIAL, S.CANCELED, S.CANCELED)
