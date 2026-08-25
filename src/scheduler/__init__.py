from __future__ import annotations

from src.config import settings

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:  # pragma: no cover
    BackgroundScheduler = None
    IntervalTrigger = None


scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone) if BackgroundScheduler else None


def init_scheduler() -> None:
    if scheduler is None or scheduler.running:
        return
    from src.scheduler.jobs import reminder_sender_job

    scheduler.add_job(
        reminder_sender_job,
        trigger=IntervalTrigger(minutes=1, timezone=settings.scheduler_timezone),
        id="reminder_sender",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
