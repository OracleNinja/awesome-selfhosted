"""Cross-cutting API behaviour: error envelopes, limits, and documentation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


class TestErrorEnvelope:
    async def test_unknown_route_uses_the_standard_shape(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert isinstance(body["error"]["message"], str)
        # FastAPI's default {"detail": ...} must never reach a client.
        assert "detail" not in body

    async def test_wrong_method_uses_the_standard_shape(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/health/live")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"

    async def test_errors_carry_a_correlation_id(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/nope")
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


class TestBodyLimits:
    async def test_oversized_body_is_rejected(self, client: AsyncClient) -> None:
        """One client must not be able to exhaust process memory with one
        request. The check happens before the body is buffered."""
        payload = "x" * (2 * 1024 * 1024)
        response = await client.post(
            "/api/v1/health", content=payload, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


class TestOpenApi:
    async def test_schema_is_served(self, client: AsyncClient) -> None:
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "NEXUS"
        assert "/api/v1/health" in schema["paths"]

    async def test_every_endpoint_documents_a_summary(self, client: AsyncClient) -> None:
        """An endpoint without a summary is an endpoint nobody can use without
        reading the source."""
        schema = (await client.get("/api/openapi.json")).json()
        undocumented = [
            f"{method.upper()} {path}"
            for path, operations in schema["paths"].items()
            for method, operation in operations.items()
            if method in {"get", "post", "patch", "put", "delete"} and not operation.get("summary")
        ]
        assert undocumented == []

    async def test_docs_page_renders(self, client: AsyncClient) -> None:
        response = await client.get("/api/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
