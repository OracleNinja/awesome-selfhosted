"""Shared response models.

These exist for two reasons beyond runtime validation:

1. **The OpenAPI document is generated from them.** A schema declared here
   appears in ``/api/docs`` with field names, types, and descriptions, so the
   API documentation cannot drift from the code — it *is* the code.
2. **They constrain what leaves the process.** A response model acts as an
   allow-list: a field not declared here is dropped, even if the ORM object
   carries it. That is how ``password_hash`` cannot accidentally be serialised
   into a user listing.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable machine-readable error code. Branch on this, not on the message.",
        examples=["VALIDATION_FAILED"],
    )
    message: str = Field(description="Human-readable explanation. Wording may change.")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Structured, client-safe context — e.g. which fields failed validation.",
    )
    request_id: str | None = Field(
        default=None,
        description="Correlation id, also returned in the X-Request-ID header.",
    )


class ErrorResponse(BaseModel):
    """The single error shape returned by every endpoint."""

    error: ErrorDetail


class PageMeta(BaseModel):
    """Pagination metadata.

    Cursor-based rather than offset-based for event feeds: ``OFFSET 100000``
    makes PostgreSQL walk and discard 100,000 rows, and on a table receiving
    inserts the page boundaries shift under the reader, so rows are seen twice
    or missed. A cursor encodes "after this key", which is a single index seek
    and is stable while new rows arrive.
    """

    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. Null when this is the last page.",
    )
    has_more: bool = Field(description="Whether more rows exist after this page.")
    count: int = Field(description="Number of items in this page.")


class Page(BaseModel, Generic[T]):
    """A page of results."""

    items: list[T]
    page: PageMeta


class OkResponse(BaseModel):
    """Acknowledgement for actions with no meaningful body."""

    ok: bool = True
    message: str | None = None


# Reusable OpenAPI response declarations. Attaching these to routes documents
# the failure modes of each endpoint, which is half of what makes an API
# usable by someone who did not write it.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Malformed request."},
    401: {"model": ErrorResponse, "description": "Authentication required or session expired."},
    403: {"model": ErrorResponse, "description": "Authenticated but not permitted."},
    404: {"model": ErrorResponse, "description": "No such resource."},
    409: {"model": ErrorResponse, "description": "Conflicts with current state."},
    422: {"model": ErrorResponse, "description": "Request failed schema validation."},
    429: {"model": ErrorResponse, "description": "Rate limited; see Retry-After."},
    500: {"model": ErrorResponse, "description": "Internal error; quote the request id."},
    503: {"model": ErrorResponse, "description": "A required dependency is unavailable."},
}


def responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Select the documented error responses relevant to one endpoint."""
    return {code: ERROR_RESPONSES[code] for code in codes if code in ERROR_RESPONSES}
