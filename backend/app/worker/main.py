"""CLI entrypoint for running the CodeSentinel background worker daemon.

Usage:
    python -m app.worker.main [--worker-id ID] [--poll-interval SECONDS]
"""

import argparse
import logging
import signal
import sys

from app.core.config import settings
from app.core.lifecycle import initialize_application
from app.worker.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeSentinel Distributed Background Worker")
    parser.add_argument("--worker-id", type=str, default=None, help="Unique worker identifier")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.WORKER_POLL_INTERVAL_SECONDS,
        help="Polling delay in seconds when queue is empty",
    )
    args = parser.parse_args()

    # Configure structured logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )
    logger = logging.getLogger("codesentinel.worker")
    logger.info("Initializing CodeSentinel Worker Daemon...")

    # Coordinate secret resolution, production validation, and database initialization
    initialize_application()

    worker = Worker(
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
    )

    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s signal, initiating graceful shutdown...", sig_name)
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        worker.run()
    except Exception as exc:
        logger.exception("Worker terminated due to unhandled exception: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
