"""Standalone worker process.

    python -m nexus.worker
    python -m nexus.worker --queues maintenance,intel

Run this when the API is configured with ``NEXUS_RUN_WORKERS=false``, which is
the right split once background work is heavy enough to matter: a job that
saturates the CPU cannot then starve the event loop that serves the UI, and
workers can be scaled or restarted independently of the API.

Signals
-------
``SIGTERM`` (what an init system or ``docker stop`` sends) and ``SIGINT``
(Ctrl-C) both trigger a graceful stop: stop claiming, let in-flight jobs finish
within the grace period, release the pool, exit. Without that, every deploy
would abandon whatever was running and rely on lock expiry to redo it.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from nexus.core.config import ConfigurationError, load_settings
from nexus.core.context import AppContext
from nexus.core.logging import configure_logging, get_logger
from nexus.workers.worker import Worker

logger = get_logger(__name__)


async def _run(queues: list[str] | None) -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 78  # EX_CONFIG

    configure_logging(settings)

    # A worker process does not run sensors; those belong to whichever process
    # is configured to own them, and running two copies of a sensor would
    # double-ingest everything it sees.
    context = AppContext(settings.model_copy(update={"run_sensors": False, "run_workers": False}))
    await context.startup()

    if not context.database.available:
        logger.error("worker_start_aborted", extra={"reason": "database unavailable"})
        await context.shutdown()
        return 69  # EX_UNAVAILABLE

    # Importing the handlers package registers the job kinds. Without it the
    # worker would claim jobs and immediately mark them DEAD.
    import nexus.workers.handlers  # noqa: F401

    worker = Worker(context, queues=queues)

    loop = asyncio.get_running_loop()
    for signal_name in ("SIGTERM", "SIGINT"):
        with_signal = getattr(signal, signal_name, None)
        if with_signal is not None:
            # add_signal_handler rather than signal.signal: it schedules the
            # callback on the event loop instead of interrupting whatever
            # coroutine happens to be running, which would leave a session in
            # an undefined state.
            loop.add_signal_handler(with_signal, worker.stop)

    try:
        await worker.run()
    finally:
        await context.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-worker", description="NEXUS background worker.")
    parser.add_argument(
        "--queues",
        default=None,
        help="Comma-separated queues to serve. Default: all. Use this to "
        "dedicate a process to one lane so a backlog in another cannot delay it.",
    )
    args = parser.parse_args(argv)
    queues = [queue.strip() for queue in args.queues.split(",")] if args.queues else None
    return asyncio.run(_run(queues))


if __name__ == "__main__":
    raise SystemExit(main())
