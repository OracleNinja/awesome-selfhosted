"""Shared test fixtures.

The suite splits in two:

* **Engine tests** need nothing but the Python dependencies and always run.
* **API tests** need a PostgreSQL database, because the schema uses JSONB and
  native enums.  Point ``TEST_DATABASE_URL`` at a throwaway database; without
  it those tests skip rather than fail, so ``pytest`` is still useful on a
  laptop with no database running.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("ALLOW_DEV_AUTH", "true")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault(
    "LOCAL_STORAGE_PATH", os.path.join(tempfile.gettempdir(), "genie-test-storage")
)
os.environ.setdefault("AI_PROVIDER", "none")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL


requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Set TEST_DATABASE_URL to a PostgreSQL database to run API tests.",
)


@pytest.fixture(scope="session")
def app_client():
    """FastAPI test client with a freshly migrated schema."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")

    from fastapi.testclient import TestClient

    from app.db.base import Base
    from app.db.session import engine
    from app.main import app

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with TestClient(app) as client:
        yield client

    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def sample_svg() -> str:
    """A logo-shaped test design: a filled block, a circle, a bar and a stroke."""
    return """<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm"
        viewBox="0 0 600 400">
      <rect x="40" y="40" width="300" height="180" fill="#1257A6"/>
      <circle cx="440" cy="140" r="90" fill="#C4161C"/>
      <path d="M60 300 L540 300 L540 340 L60 340 Z" fill="#0B0B0B"/>
      <path d="M100 250 Q300 200 500 250" fill="none" stroke="#F5C518" stroke-width="14"/>
    </svg>"""


@pytest.fixture(scope="session")
def sample_png() -> bytes:
    """A small flat-colour raster with a white background to trace."""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (600, 400), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([60, 60, 320, 240], fill=(18, 87, 166))
    draw.ellipse([360, 60, 540, 240], fill=(196, 22, 28))
    draw.rectangle([60, 290, 540, 340], fill=(11, 11, 11))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="session")
def business_plan(app_client):
    """Upgrade the test workspace so the production-module endpoints unlock."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import PlanTier, Subscription

    with SessionLocal() as db:
        subscription = db.scalar(select(Subscription))
        if subscription is not None:
            subscription.tier = PlanTier.business
            db.commit()
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The limiter is process-global; without a reset one test's requests
    would exhaust the budget for the next."""
    from app.core import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()
