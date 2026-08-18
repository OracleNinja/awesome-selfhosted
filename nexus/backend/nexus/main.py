"""Process entry point.

Two ways in:

    python -m nexus.main          # reads configuration, starts uvicorn
    uvicorn nexus.main:app        # ASGI server of your choice

``app`` is built at import time for the second form. That means a configuration
error raises during import — which is exactly when you want it, because the
alternative is a server that binds a port and then fails every request.
"""

from __future__ import annotations

import sys

from nexus.app import create_app
from nexus.core.config import ConfigurationError, load_settings
from nexus.core.logging import configure_logging, get_logger


def _build() -> tuple:
    """Load configuration and build the app, or exit with an explanation.

    A traceback about pydantic internals does not help the person deploying
    this. A message naming the variable and how to fix it does.
    """
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        # `from None`: the operator needs the guidance above, not a
        # traceback through pydantic's validators.
        raise SystemExit(78) from None  # EX_CONFIG from sysexits.h

    configure_logging(settings)
    return settings, create_app(settings)


settings, app = _build()


def run() -> None:
    """Start uvicorn with the configured host and port."""
    import uvicorn

    logger = get_logger(__name__)
    logger.info(
        "serving",
        extra={"host": settings.host, "port": settings.port},
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        # Our logging is already configured; letting uvicorn install its own
        # handlers would produce every line twice, once unstructured.
        log_config=None,
        access_log=False,  # RequestContextMiddleware logs access, with ids
        # Give in-flight requests time to finish on SIGTERM instead of cutting
        # them off mid-transaction.
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    run()
