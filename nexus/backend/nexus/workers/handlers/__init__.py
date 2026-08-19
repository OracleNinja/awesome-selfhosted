"""Job handlers.

Importing this package registers every handler. A handler module that is not
imported here is a job kind that cannot run — the worker will mark such a job
DEAD with "no handler registered" rather than guessing, which is the correct
outcome after a rollback to a build that predates the kind.
"""

from nexus.workers.handlers import maintenance

__all__ = ["maintenance"]
