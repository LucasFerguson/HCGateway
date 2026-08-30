"""Long-running orchestration for exporting prepared sleep to FluidCalendar.

The analytics worker owns reconciliation and sleep calculations.  This worker
only observes the atomically selected prepared run, queues deterministic
calendar projections, and delivers those projections through a durable ledger.
"""

import argparse
import datetime as dt
import logging
import os
import signal
import socket
import threading
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

from .calendar import (
    FluidCalendarClient,
    FluidCalendarError,
    claim_backfill_window,
    claim_sleep_delivery,
    complete_backfill_window,
    complete_sleep_delivery,
    fail_backfill_window,
    fail_sleep_delivery,
    initialize_backfill,
    payload_hash,
    queue_sleep_delivery,
    render_sleep_event,
    utcnow,
)
from .context import context_for_user
from .crypto import cipher_for_user
from .store import read_sleep_events
from .time_utils import local_today


load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("calendar-worker")
stop_event = threading.Event()


def _positive_integer(environment, name, default, *, minimum=1, maximum=None):
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" from {minimum} to {maximum}" if maximum is not None else f" of at least {minimum}"
        raise ValueError(f"{name} must be an integer{suffix}")
    return value


def _boolean(environment, name, default=False):
    raw = environment.get(name)
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _required(environment, name):
    value = str(environment.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


@dataclass(frozen=True)
class CalendarWorkerConfig:
    mongo_uri: str = field(repr=False)
    base_url: str
    api_key: str = field(repr=False)
    user_id: str
    feed_id: str
    initial_lookback_days: int = 7
    poll_seconds: int = 300
    lease_seconds: int = 300
    retry_seconds: int = 60
    max_attempts: int = 8
    backfill_enabled: bool = False
    backfill_batch_days: int = 7
    backfill_interval_seconds: int = 86400

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        base_url = _required(environment, "FLUIDCALENDAR_BASE_URL").rstrip("/")
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("FLUIDCALENDAR_BASE_URL must be an absolute HTTP(S) URL")
        if parsed_url.username or parsed_url.password:
            raise ValueError("FLUIDCALENDAR_BASE_URL must not contain credentials")
        api_key = _required(environment, "FLUIDCALENDAR_API_KEY")
        if not api_key.startswith("fc_"):
            raise ValueError("FLUIDCALENDAR_API_KEY must start with fc_")
        return cls(
            mongo_uri=_required(environment, "MONGO_URI"),
            base_url=base_url,
            api_key=api_key,
            user_id=_required(environment, "CALENDAR_SLEEP_USER_ID"),
            feed_id=_required(environment, "CALENDAR_SLEEP_FEED_ID"),
            initial_lookback_days=_positive_integer(
                environment, "CALENDAR_SLEEP_INITIAL_LOOKBACK_DAYS", 7, maximum=366
            ),
            poll_seconds=_positive_integer(environment, "CALENDAR_SLEEP_POLL_SECONDS", 300),
            lease_seconds=_positive_integer(environment, "CALENDAR_SLEEP_LEASE_SECONDS", 300),
            retry_seconds=_positive_integer(environment, "CALENDAR_SLEEP_RETRY_SECONDS", 60),
            max_attempts=_positive_integer(
                environment, "CALENDAR_SLEEP_MAX_ATTEMPTS", 8, maximum=100
            ),
            backfill_enabled=_boolean(environment, "CALENDAR_SLEEP_BACKFILL_ENABLED"),
            backfill_batch_days=_positive_integer(
                environment, "CALENDAR_SLEEP_BACKFILL_BATCH_DAYS", 7, maximum=366
            ),
            backfill_interval_seconds=_positive_integer(
                environment, "CALENDAR_SLEEP_BACKFILL_INTERVAL_SECONDS", 86400
            ),
        )


class PreparedEventUnavailable(FluidCalendarError):
    """A queued item cannot yet be resolved against the current prepared run."""

    def __init__(self, message):
        super().__init__(message, retryable=True)


def _safe_failure(error, context):
    """Retain API retry semantics without persisting arbitrary exception text."""
    if isinstance(error, FluidCalendarError):
        return error
    return FluidCalendarError(
        f"{context} failed ({type(error).__name__})",
        retryable=True,
    )


def request_stop(*_):
    stop_event.set()


def _dates_for_recent_window(user, lookback_days, now):
    today = dt.date.fromisoformat(local_today(context_for_user(user).homeTimeZone, now=now))
    return today - dt.timedelta(days=lookback_days - 1), today


def queue_window(database, cipher, config, start_date, end_date, *, now=None):
    """Queue every prepared event in an inclusive local wake-date window."""
    now = now or utcnow()
    events, current = read_sleep_events(
        database,
        cipher,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        limit=100_000,
    )
    if not current:
        return 0, None
    run_id = current["runId"]
    for event in events:
        payload = render_sleep_event(event, config.feed_id)
        queue_sleep_delivery(
            database, config.user_id, config.feed_id, event, run_id, payload, now=now
        )
    return len(events), current


def _current_event(database, cipher, delivery):
    events, current = read_sleep_events(
        database,
        cipher,
        start=delivery["wakeDate"],
        end=delivery["wakeDate"],
        limit=1000,
    )
    if not current:
        raise PreparedEventUnavailable("no current prepared analytics run")
    event = next(
        (item for item in events if str(item.get("id")) == delivery["preparedEventId"]),
        None,
    )
    if event is None:
        raise PreparedEventUnavailable("prepared sleep event is absent from the current run")
    return event, current


def deliver_one(database, cipher, client, config, worker_id, *, now=None):
    """Claim and deliver one due item, returning whether work was claimed."""
    delivery = claim_sleep_delivery(
        database,
        worker_id,
        user_id=config.user_id,
        feed_id=config.feed_id,
        lease_seconds=config.lease_seconds,
        now=now,
    )
    if not delivery:
        return False
    try:
        event, current = _current_event(database, cipher, delivery)
        payload = render_sleep_event(event, config.feed_id)
        # A prepared run can advance between scanning and claiming.  Never send
        # the older projection; release it for a later scan to queue anew.
        if payload_hash(payload) != delivery.get("payloadHash"):
            raise PreparedEventUnavailable("prepared sleep event changed after it was queued")
        operation = "update" if delivery.get("remoteEventId") else "create"
        if operation == "update":
            response = client.update_event(delivery["remoteEventId"], payload)
        else:
            response = client.create_event(payload)
        if not complete_sleep_delivery(database, delivery, response, now=now):
            logger.warning("calendar delivery lease was lost before completion")
        else:
            logger.info(
                "calendar delivery completed operation=%s wake_date=%s run=%s",
                operation,
                delivery.get("wakeDate"),
                current.get("runId"),
            )
    except Exception as error:
        safe_error = _safe_failure(error, "calendar delivery")
        fail_sleep_delivery(
            database,
            delivery,
            safe_error,
            max_attempts=config.max_attempts,
            retry_delay_seconds=config.retry_seconds,
            now=now,
        )
        logger.warning(
            "calendar delivery failed wake_date=%s error_type=%s retryable=%s",
            delivery.get("wakeDate"),
            type(error).__name__,
            safe_error.retryable,
        )
    return True


def _backfill_due(state, interval_seconds, now):
    if not state or state.get("status") == "complete" or not state.get("nextEndDate"):
        return False
    # A newly enabled backfill may start immediately.  Subsequent windows are
    # rate-limited from the prior successful completion.
    if not state.get("lastCompletedEndDate") and not state.get("failedAt"):
        return True
    updated_at = state.get("updatedAt")
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=dt.timezone.utc)
    return now >= updated_at + dt.timedelta(seconds=interval_seconds)


def maybe_queue_backfill(database, cipher, config, worker_id, recent_start, *, now=None):
    """Queue at most one eligible historical batch and advance its cursor."""
    if not config.backfill_enabled:
        return 0
    now = now or utcnow()
    initial_end = recent_start - dt.timedelta(days=1)
    state = initialize_backfill(
        database, config.user_id, config.feed_id, initial_end.isoformat(), now=now
    )
    if not _backfill_due(state, config.backfill_interval_seconds, now):
        return 0
    state = claim_backfill_window(
        database,
        config.user_id,
        config.feed_id,
        worker_id,
        batch_days=config.backfill_batch_days,
        lease_seconds=config.lease_seconds,
        now=now,
    )
    if not state:
        return 0
    try:
        start_date = dt.date.fromisoformat(state["windowStartDate"])
        end_date = dt.date.fromisoformat(state["windowEndDate"])
        count, current = queue_window(
            database, cipher, config, start_date, end_date, now=now
        )
        if not current:
            raise PreparedEventUnavailable("no current prepared analytics run")
        earlier, _ = read_sleep_events(
            database, cipher, end=end_date.isoformat(), limit=1
        )
        reached_beginning = not earlier or earlier[0]["date"] >= start_date.isoformat()
        complete_backfill_window(
            database, state, reached_beginning=reached_beginning, now=now
        )
        logger.info(
            "calendar backfill queued start=%s end=%s events=%s complete=%s",
            start_date,
            end_date,
            count,
            reached_beginning,
        )
        return count
    except Exception as error:
        fail_backfill_window(database, state, _safe_failure(error, "calendar backfill"), now=now)
        logger.warning("calendar backfill failed error_type=%s", type(error).__name__)
        return 0


def run_cycle(database, cipher, client, config, worker_id, user, *, now=None):
    """Scan current prepared data, optionally queue one backfill, then drain due work."""
    now = now or utcnow()
    recent_start, recent_end = _dates_for_recent_window(
        user, config.initial_lookback_days, now
    )
    queued, current = queue_window(
        database, cipher, config, recent_start, recent_end, now=now
    )
    if current:
        logger.info(
            "calendar recent scan completed start=%s end=%s events=%s run=%s",
            recent_start,
            recent_end,
            queued,
            current.get("runId"),
        )
        maybe_queue_backfill(
            database, cipher, config, worker_id, recent_start, now=now
        )
    else:
        logger.info("calendar recent scan deferred because no prepared run exists")

    delivered = 0
    while not stop_event.is_set() and deliver_one(
        database, cipher, client, config, worker_id, now=now
    ):
        delivered += 1
    return {"observed": queued, "deliveriesClaimed": delivered}


def run(*, once=False, config=None, mongo_factory=MongoClient):
    config = config or CalendarWorkerConfig.from_environment()
    mongo = mongo_factory(config.mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
    try:
        mongo.admin.command("ping")
        user = mongo["hcgateway"]["users"].find_one({"_id": config.user_id})
        if not user:
            raise ValueError("CALENDAR_SLEEP_USER_ID does not identify an HCGateway user")
        database = mongo["hcgateway_" + config.user_id]
        cipher = cipher_for_user(user)
        client = FluidCalendarClient(config.base_url, config.api_key)
        worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        while not stop_event.is_set():
            try:
                run_cycle(database, cipher, client, config, worker_id, user)
            except Exception as error:
                # Do not render arbitrary exception messages: some dependencies
                # include request details, which could contain credentials.
                logger.error("calendar cycle failed error_type=%s", type(error).__name__)
                if once:
                    raise
            if once:
                break
            stop_event.wait(config.poll_seconds)
    finally:
        mongo.close()


def main():
    parser = argparse.ArgumentParser(description="HCGateway FluidCalendar sleep worker")
    parser.add_argument("--once", action="store_true", help="scan, drain due delivery work, and exit")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        run(once=args.once)
    except Exception as error:
        logger.error("calendar worker stopped error_type=%s", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
