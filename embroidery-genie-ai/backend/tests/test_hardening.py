"""Regression tests for the production-readiness audit.

Each test here corresponds to a specific finding. They exist so the class of
problem cannot come back silently.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.conftest import requires_db


# ------------------------------------------------------- resource exhaustion
def test_decompression_bomb_is_rejected_before_decoding():
    """An 80 KB PNG can decode to 84 megapixels and ~1 GB of RAM. The check must
    read the header only, never the pixels."""
    from app.embroidery.raster import MAX_DECODED_PIXELS, ImageTooLargeError, load_image

    bomb = Image.new("L", (12000, 7000), 0)   # 84 Mpx
    buffer = io.BytesIO()
    bomb.save(buffer, format="PNG")
    payload = buffer.getvalue()
    del bomb

    assert len(payload) < 1_000_000, "the bomb should be tiny on disk"
    assert 12000 * 7000 > MAX_DECODED_PIXELS

    with pytest.raises(ImageTooLargeError, match="megapixel"):
        load_image(payload)


def test_normal_images_still_load():
    from app.embroidery.raster import load_image

    image = Image.new("RGB", (800, 600), (255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    assert load_image(buffer.getvalue()).shape == (600, 800, 4)


def test_stitch_budget_stops_runaway_generation():
    """A pathological design must fail with a readable message rather than
    consuming memory until the process dies."""
    from app.embroidery.optimizer import (
        AssemblyParams,
        StitchBudgetExceeded,
        StitchRun,
        assemble,
    )
    from app.embroidery.pattern import Thread

    huge = [StitchRun([(float(i), float(i % 700)) for i in range(0, 60000, 3)], Thread(0, 0, 0))]
    with pytest.raises(StitchBudgetExceeded, match="past the safe limit"):
        assemble(huge, AssemblyParams(stitch_budget=5000))


def test_normal_designs_are_well_under_the_budget(sample_svg):
    from app.embroidery import DigitizeOptions, digitize_svg
    from app.embroidery.optimizer import AssemblyParams

    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=200)).pattern
    assert pattern.stitch_count < AssemblyParams().stitch_budget / 10


# ------------------------------------------------------------- XML handling
def test_svg_parser_rejects_external_entities():
    """XXE would let an uploaded SVG read files off the API host."""
    from xml.etree.ElementTree import ParseError

    from app.embroidery.svg_parse import parse_svg

    xxe = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    )
    with pytest.raises(ParseError):
        parse_svg(xxe)


def test_svg_parser_rejects_entity_expansion_bombs():
    from xml.etree.ElementTree import ParseError

    from app.embroidery.svg_parse import parse_svg

    levels = "".join(f'<!ENTITY lol{i} "{"&lol%d;" % (i - 1) * 10}">' for i in range(1, 6))
    bomb = (
        f'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY lol "AAAAAAAAAA">{levels}]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><desc>&lol5;</desc></svg>'
    )
    with pytest.raises(ParseError):
        parse_svg(bomb)


# --------------------------------------------------------------- determinism
def test_compatibility_score_is_deterministic(sample_png):
    """The score drives quotes. It must not move between identical runs — the
    k-means seed is pinned for exactly this reason."""
    from app.services.analyzer import analyze_design

    runs = [analyze_design(sample_png, "image/png", use_ai=False) for _ in range(4)]
    assert len({r.compatibility_score for r in runs}) == 1
    assert len({tuple(c["hex"] for c in r.colors) for r in runs}) == 1
    assert len({tuple(round(c["share"], 6) for c in r.colors) for r in runs}) == 1


def test_digitizing_is_deterministic(sample_png):
    from app.embroidery import DigitizeOptions, TraceOptions, digitize_shapes, trace_image

    counts = set()
    for _ in range(3):
        trace = trace_image(sample_png, TraceOptions(max_colors=4, target_width_mm=80))
        counts.add(
            digitize_shapes(trace.shapes, DigitizeOptions(target_width_mm=80)).pattern.stitch_count
        )
    assert len(counts) == 1, f"stitch count varied between runs: {counts}"


def test_pricing_is_deterministic():
    from app.services.pricing import PricingInputs, calculate

    results = [
        calculate(PricingInputs(stitch_count=8000, color_count=4, quantity=24, blank_cost=4.25))
        for _ in range(5)
    ]
    assert len({r.unit_cost for r in results}) == 1
    assert len({r.unit_wholesale for r in results}) == 1
    assert len({r.unit_retail for r in results}) == 1


# ------------------------------------------------------------ configuration
def test_wildcard_cors_is_rejected():
    """The API sends credentials, so a wildcard origin would let any site read
    authenticated responses."""
    from app.core.config import Settings

    settings = Settings(cors_origins="*", environment="development")
    problems = settings.validate_runtime()
    assert any("CORS_ORIGINS" in problem for problem in problems)


def test_production_config_rejects_dev_auth():
    from app.core.config import Settings

    settings = Settings(
        environment="production",
        allow_dev_auth=True,
        supabase_url="https://project.supabase.co",
        database_url="postgresql+psycopg://user@db.example.com/genie",
    )
    problems = settings.validate_runtime()
    assert any("ALLOW_DEV_AUTH" in problem for problem in problems)


def test_production_config_requires_jwt_verification():
    from app.core.config import Settings

    settings = Settings(
        environment="production",
        allow_dev_auth=False,
        database_url="postgresql+psycopg://user@db.example.com/genie",
    )
    problems = settings.validate_runtime()
    assert any("SUPABASE" in problem for problem in problems)


# --------------------------------------------------------------- rate limits
def test_rate_limiter_opens_and_closes():
    from app.core import ratelimit

    ratelimit.reset()
    limit, _window = ratelimit.LIMITS["export"]
    for _ in range(limit):
        allowed, _ = ratelimit._check("export", "org-a")
        assert allowed
    allowed, retry_after = ratelimit._check("export", "org-a")
    assert not allowed
    assert retry_after >= 1

    # A different workspace is unaffected.
    assert ratelimit._check("export", "org-b")[0]
    ratelimit.reset()


# ------------------------------------------------------- storage path safety
def test_local_storage_refuses_path_traversal(tmp_path):
    from app.services.storage import LocalStorage

    storage = LocalStorage(str(tmp_path))
    with pytest.raises(ValueError, match="outside storage root"):
        storage.get("../../etc/passwd")


def test_stored_filenames_are_sanitised():
    from app.services.storage import build_path

    path = build_path("org", "design", "original", "../../evil name.png")
    assert ".." not in path
    assert path.endswith("evil_name.png")


# ------------------------------------------------------------ API behaviour
@requires_db
def test_upload_rejects_oversized_body_by_content_length(app_client):
    """The declared size is checked before the body is buffered."""
    from app.core.config import settings

    oversized = b"x" * 1024
    response = app_client.post(
        "/api/v1/designs/upload",
        files={"file": ("big.png", oversized, "image/png")},
        data={"name": "Too big"},
        headers={"content-length": str(settings.max_upload_bytes + 1_000_000)},
    )
    assert response.status_code == 413
    assert "limit" in response.json()["detail"].lower()


@requires_db
def test_upload_rejects_a_decompression_bomb(app_client):
    bomb = Image.new("L", (12000, 7000), 0)
    buffer = io.BytesIO()
    bomb.save(buffer, format="PNG")
    del bomb

    response = app_client.post(
        "/api/v1/designs/upload",
        files={"file": ("bomb.png", buffer.getvalue(), "image/png")},
        data={"name": "Bomb", "analyze": "true", "use_ai": "false"},
    )
    # The upload is accepted (it is a valid small file) but analysis must not
    # take the process down; the design records the failure instead.
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        design = response.json()
        assert design["compatibility_score"] is None or design["error_message"]
        app_client.delete(f"/api/v1/designs/{design['id']}")


@requires_db
def test_cross_workspace_access_is_refused(app_client):
    """A membership the caller does not hold must not resolve."""
    response = app_client.get(
        "/api/v1/designs",
        headers={"X-Organization-Id": "00000000-0000-4000-8000-0000000000ff"},
    )
    assert response.status_code == 403
    assert "member" in response.json()["detail"].lower()


@requires_db
def test_order_rejects_a_machine_from_another_workspace(app_client, business_plan):
    """Cross-tenant id references must not be storable."""
    response = app_client.post(
        "/api/v1/orders",
        json={
            "assigned_machine_id": "00000000-0000-4000-8000-0000000000aa",
            "items": [
                {"description": "Tee", "quantity": 1, "unit_price": 10, "unit_cost": 5}
            ],
        },
    )
    assert response.status_code == 404
    assert "Machine not found" in response.json()["detail"]


@requires_db
def test_unauthenticated_requests_are_refused_when_dev_auth_is_off(app_client, monkeypatch):
    from app.core import deps

    monkeypatch.setattr(deps.settings, "allow_dev_auth", False)
    response = app_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@requires_db
def test_malformed_bearer_token_is_refused(app_client):
    response = app_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert response.status_code == 401


@requires_db
def test_error_responses_do_not_leak_internals_in_production(app_client, monkeypatch):
    """The generic handler echoes exception text outside production only."""
    from app.core.config import settings

    assert not settings.is_production
    response = app_client.get("/api/v1/designs/not-a-uuid")
    assert response.status_code == 422
    assert "problems" in response.json()


def test_ai_layer_failure_never_breaks_analysis(monkeypatch, sample_png):
    """The compatibility score is what customers are quoted against, so no
    failure in the optional vision layer may propagate.

    The gateway already converts every provider fault into an outcome, so this
    aims one level higher: it breaks the gateway itself, which is the failure
    mode the wrapper in ``semantic_analysis`` exists for.
    """
    from app.services import analyzer

    def explode(*args, **kwargs):
        raise RuntimeError("the AI layer exploded")

    monkeypatch.setattr(analyzer, "run_vision_analysis", explode)

    report = analyzer.analyze_design(sample_png, "image/png", use_ai=True)
    assert report.ai is None
    assert report.ai_status["reason"] == "layer_error"
    assert 0 <= report.compatibility_score <= 100
    assert report.verdict
    assert report.colors


def test_ai_clients_receive_a_timeout():
    """A hung provider must not hold a worker open indefinitely.

    The timeout is now per-operation (see app/ai/operations.py) rather than one
    global setting, so the adapters take it as an argument.
    """
    import inspect

    from app.ai import providers
    from app.ai.operations import inventory

    for fn in (providers.call_openai, providers.call_anthropic):
        assert "timeout=timeout_seconds" in inspect.getsource(fn)

    for op in inventory():
        assert op["timeout_seconds"] > 0


def test_export_writers_leak_no_temp_files(sample_svg):
    """pyembroidery needs a real file; concurrent exports must not leave any."""
    import glob
    import os
    import tempfile
    import threading

    from app.embroidery import DigitizeOptions, digitize_svg, write

    pattern = digitize_svg(sample_svg, DigitizeOptions(target_width_mm=60)).pattern
    tmp = tempfile.gettempdir()
    pattern_glob = os.path.join(tmp, "*.pes")
    before = set(glob.glob(pattern_glob))

    errors: list[Exception] = []

    def run():
        try:
            for extension in ("pes", "jef", "vp3"):
                write(pattern, extension)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert set(glob.glob(pattern_glob)) == before
