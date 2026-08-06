"""The proactive worker — what makes this system autonomous rather than something a
person runs by hand (Phase 6).

    python -m app.tools.worker                 # run on a loop, honouring the env config
    python -m app.tools.worker --once          # a single scan, then exit (cron-friendly)
    python -m app.tools.worker --once --force  # ignore the enabled flag, for a smoke test

**Why a separate process, not a FastAPI startup task.** Phase 4 established that the
traffic sources run as their own processes and Postgres is the only shared state, and
that holds here for concrete reasons: `uvicorn --reload` runs two processes and
`--workers N` runs N, so an in-process scheduler would fire the scan two-or-N times over.
Coupling "the scans happen" to "the API is up" is also just wrong — they fail
independently.

**Why a plain loop, not APScheduler.** One job, one fixed interval, one consumer. A
scheduler library would buy cron expressions, job stores and misfire policies that
nothing here needs, in exchange for a dependency that has to be installed on demo
morning. Sleeping *after* the work also means the interval is the gap between runs, so a
slow scan can never stack up behind itself — which is the failure an in-process timer
would have had.

**Why a queue was rejected.** Upstash Redis is already here, but its REST API has no
blocking pop, so a queue would be polled anyway — a broker's worth of moving parts for a
job that runs every few minutes with a single consumer. Postgres holds the retry state
instead, which has the side benefit that a dead-lettered notification is *already*
readable by the dashboard rather than needing a bridge.

**Safety.** Disabled by default (`PROACTIVE_SCAN_ENABLED=false`), because pulling this
branch should never start something that sends on a timer. And `PROACTIVE_MAX_SENDS_PER_RUN`
caps a run, because in Phase 7 every send is real Meta quota — the provider is logged at
startup for exactly that reason.
"""
import argparse
import logging
import signal
import sys
import time

from app.core import config
from app.services.proactive_notifier import scan_and_notify

logger = logging.getLogger("zentric.worker")

_shutdown = False


def _request_shutdown(signum, _frame):
    """Finish the scan in flight, then stop. Killing a worker mid-scan is safe — every
    step is separately deduplicated — but a clean stop keeps the logs readable."""
    global _shutdown
    _shutdown = True
    logger.info("Signal %s received — stopping after the current scan.", signum)


def run_once(max_sends: int | None) -> dict:
    result = scan_and_notify(max_sends=max_sends)
    level = logging.WARNING if (result["failed"] or result["dead_lettered"]) else logging.INFO
    logger.log(
        level,
        "Scan complete: %s sent, %s failed, %s dead-lettered, %s skipped (already dead)%s",
        result["sent"], result["failed"], result["dead_lettered"], result["skipped_dead"],
        " [send cap reached]" if result["capped"] else "",
    )
    return result


def run_forever(interval_seconds: int, max_sends: int | None) -> None:
    logger.info("Proactive worker started: every %ss, max %s sends/run.",
                interval_seconds, max_sends if max_sends is not None else "unlimited")
    while not _shutdown:
        try:
            run_once(max_sends)
        except Exception:
            # The worker outliving a bad scan is the entire point of it being a loop.
            # Per-parcel failures are already handled inside scan_and_notify; this is
            # the belt-and-braces case — the DB being unreachable, say, which on this
            # machine is a DNS wobble that resolves itself.
            logger.exception("Scan raised — continuing; next run in %ss.", interval_seconds)

        # Sleep in short slices so a Ctrl-C doesn't wait out the whole interval.
        slept = 0
        while slept < interval_seconds and not _shutdown:
            time.sleep(min(1, interval_seconds - slept))
            slept += 1
    logger.info("Proactive worker stopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true",
                        help="Run a single scan and exit (for cron, or a smoke test)")
    parser.add_argument("--force", action="store_true",
                        help="Run even when PROACTIVE_SCAN_ENABLED is false")
    parser.add_argument("--interval", type=int, default=None,
                        help=f"Seconds between scans (default {config.PROACTIVE_SCAN_INTERVAL_SECONDS})")
    parser.add_argument("--max-sends", type=int, default=None,
                        help="Cap sends per run (default from PROACTIVE_MAX_SENDS_PER_RUN)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )

    # Which channel this worker will send through, stated before it sends anything. On
    # `cloud` every message is real quota against the number reserved for the defense.
    logger.info("WhatsApp provider: %s", config.WHATSAPP_PROVIDER)
    if config.WHATSAPP_PROVIDER != "mock":
        logger.warning("Provider is not 'mock' — this worker will spend real WhatsApp quota.")

    if not config.PROACTIVE_SCAN_ENABLED and not args.force:
        logger.info("PROACTIVE_SCAN_ENABLED is false — nothing to do. "
                    "Set it to true, or pass --force for a one-off run.")
        return 0

    max_sends = args.max_sends if args.max_sends is not None else config.PROACTIVE_MAX_SENDS_PER_RUN
    if max_sends is not None and max_sends <= 0:
        max_sends = None  # 0 / negative reads as "no cap", matching the env default

    if args.once:
        run_once(max_sends)
        return 0

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _request_shutdown)

    interval = args.interval or config.PROACTIVE_SCAN_INTERVAL_SECONDS
    run_forever(interval, max_sends)
    return 0


if __name__ == "__main__":
    sys.exit(main())
