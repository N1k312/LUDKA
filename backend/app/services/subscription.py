"""Доступность акции по подписке клуба.

trial (до trial_ends_at) → active (до current_period_end) → past_due (отсрочка 3 дня) → canceled.
Автоматические переходы статусов по датам — шаг 8; здесь решение принимается по датам,
поэтому оно верно даже если статус ещё не обновлён.
"""

from datetime import datetime, timedelta

from app.models import Subscription, SubscriptionStatus

GRACE_PERIOD = timedelta(days=3)


def allows_play(sub: Subscription | None, now: datetime) -> bool:
    if sub is None:
        return False
    if sub.status == SubscriptionStatus.TRIAL:
        return sub.trial_ends_at is not None and now < sub.trial_ends_at
    if sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        return sub.current_period_end is not None and now < sub.current_period_end + GRACE_PERIOD
    return False
