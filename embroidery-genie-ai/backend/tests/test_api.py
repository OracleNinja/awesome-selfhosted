"""API integration tests.

Runs the real application against a real PostgreSQL database: the full
upload -> analyze -> digitize -> export path, tenancy isolation, plan quotas
and the production management state machine.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from tests.conftest import requires_db

pytestmark = requires_db


# ------------------------------------------------------------------ system
def test_health(app_client):
    response = app_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_the_database(app_client):
    response = app_client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["database"] is True


# -------------------------------------------------------------------- auth
def test_me_provisions_a_workspace(app_client):
    response = app_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"]
    assert body["organization"]["slug"]
    assert body["role"] == "owner"
    assert body["subscription"]["tier"] == "free"
    assert body["subscription"]["designs_limit"] == 5


def test_plans_are_public(app_client):
    body = app_client.get("/api/v1/auth/plans").json()
    tiers = [plan["tier"] for plan in body["plans"]]
    assert tiers == ["free", "pro", "business"]


def test_profile_update(app_client):
    response = app_client.patch("/api/v1/auth/me", json={"full_name": "Sam Rivera"})
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Sam Rivera"


# ------------------------------------------------------------------ studio
def test_studio_reference_primes_the_ui(app_client):
    body = app_client.get("/api/v1/studio/reference").json()
    assert len(body["fabrics"]) >= 8
    assert any(f["extension"] == "dst" and f["available"] for f in body["formats"])
    assert body["machine_presets"]
    assert body["mockup_templates"]
    assert body["placements"]


def test_estimate_endpoint(app_client):
    body = app_client.get(
        "/api/v1/studio/estimate",
        params={"stitch_count": 12000, "color_count": 4, "speed_spm": 800,
                "fabric": "hat"},
    ).json()
    # 12k stitches on a cap head derated to 520 spm is roughly 23 min plus
    # colour-change overhead.
    assert 20 < body["minutes"] < 30
    assert body["effective_speed_spm"] == 520


# ----------------------------------------------------------------- designs
@pytest.fixture(scope="module")
def uploaded_design(app_client, sample_png):
    response = app_client.post(
        "/api/v1/designs/upload",
        files={"file": ("logo.png", sample_png, "image/png")},
        data={"name": "Test Logo", "analyze": "true", "use_ai": "false"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_runs_the_analyzer(uploaded_design):
    assert uploaded_design["name"] == "Test Logo"
    assert uploaded_design["compatibility_score"] is not None
    analysis = uploaded_design["analysis"]
    assert 0 <= analysis["compatibility_score"] <= 100
    assert analysis["verdict"]
    assert analysis["colors"]
    assert analysis["recommended_placements"]
    kinds = {f["kind"] for f in uploaded_design["files"]}
    assert {"original", "thumbnail"} <= kinds


def test_upload_rejects_unsupported_types(app_client):
    response = app_client.post(
        "/api/v1/designs/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"name": "Bad"},
    )
    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["detail"]


def test_vectorize(app_client, uploaded_design):
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/vectorize",
        json={"max_colors": 4, "target_width_mm": 80},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["layers"] >= 2
    assert body["palette"]


def test_digitize_then_export(app_client, uploaded_design):
    design_id = uploaded_design["id"]

    response = app_client.post(
        f"/api/v1/designs/{design_id}/digitize",
        json={"fabric": "cotton_shirt", "target_width_mm": 80},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stitch_count"] > 300
    assert body["color_count"] >= 2
    assert body["width_mm"] == pytest.approx(80, abs=3)
    assert body["estimated_minutes"] > 0
    assert body["colors"][0]["hex"].startswith("#")

    detail = app_client.get(f"/api/v1/designs/{design_id}").json()
    assert detail["status"] == "ready"
    assert detail["thread_chart"]

    stitches = app_client.get(f"/api/v1/designs/{design_id}/stitches").json()
    assert stitches["blocks"]
    assert stitches["blocks"][0]["coords"]

    export = app_client.post(
        f"/api/v1/designs/{design_id}/export", json={"formats": ["dst", "pes"]}
    )
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        names = set(archive.namelist())
    assert {"thread_chart.pdf", "production_notes.pdf", "preview.png"} <= names
    assert any(n.endswith(".dst") for n in names)
    assert any(n.endswith(".pes") for n in names)


def test_export_handles_non_ascii_design_names(app_client, uploaded_design):
    """Design names routinely contain em dashes and accents; HTTP headers are
    latin-1, so the filename has to be encoded per RFC 5987."""
    app_client.patch(
        f"/api/v1/designs/{uploaded_design['id']}",
        json={"name": "Northside Athletics — Café Crest"},
    )
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/export", json={"formats": ["dst"]}
    )
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    disposition.encode("latin-1")  # must not raise
    app_client.patch(
        f"/api/v1/designs/{uploaded_design['id']}", json={"name": "Test Logo"}
    )


def test_export_format_is_gated_by_plan(app_client, uploaded_design):
    """The free plan covers DST and PES only."""
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/export", json={"formats": ["vp3"]}
    )
    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_mockup_generation(app_client, uploaded_design):
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/mockup",
        json={"template": "tshirt_left_chest", "garment_color": "navy"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_mockup_rejects_unknown_template(app_client, uploaded_design):
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/mockup",
        json={"template": "spaceship", "garment_color": "navy"},
    )
    assert response.status_code == 422


def test_export_before_digitizing_is_refused(app_client):
    created = app_client.post("/api/v1/designs", json={"name": "Empty"}).json()
    response = app_client.post(
        f"/api/v1/designs/{created['id']}/export", json={"formats": ["dst"]}
    )
    assert response.status_code == 409
    assert "Digitize" in response.json()["detail"]
    app_client.delete(f"/api/v1/designs/{created['id']}")


def test_text_design(app_client):
    from app.embroidery.text import find_font

    try:
        find_font()
    except FileNotFoundError:
        pytest.skip("No TrueType font installed")

    response = app_client.post(
        "/api/v1/designs/text",
        json={"text": "GENIE", "font_key": "sans_bold", "height_mm": 25},
    )
    assert response.status_code == 201, response.text
    design = response.json()
    assert design["source"] == "text"
    assert any(f["kind"] == "vector" for f in design["files"])

    digitized = app_client.post(
        f"/api/v1/designs/{design['id']}/digitize", json={"fabric": "cotton_shirt"}
    )
    assert digitized.status_code == 200
    assert digitized.json()["stitch_count"] > 200
    app_client.delete(f"/api/v1/designs/{design['id']}")


def test_design_listing_and_search(app_client, uploaded_design):
    body = app_client.get("/api/v1/designs", params={"q": "Test"}).json()
    assert body["total"] >= 1
    assert any(item["id"] == uploaded_design["id"] for item in body["items"])


def test_unknown_design_is_404(app_client):
    response = app_client.get("/api/v1/designs/00000000-0000-4000-8000-000000000999")
    assert response.status_code == 404


# ---------------------------------------------------------------- machines
def test_machine_from_preset_and_crud(app_client):
    created = app_client.post("/api/v1/machines/from-preset/tajima_tmbp")
    assert created.status_code == 201, created.text
    machine = created.json()
    assert machine["brand"] == "Tajima"
    assert machine["needle_count"] == 15
    assert machine["is_default"] is True

    listed = app_client.get("/api/v1/machines").json()
    assert any(m["id"] == machine["id"] for m in listed)

    updated = app_client.put(
        f"/api/v1/machines/{machine['id']}",
        json={**{k: v for k, v in machine.items() if k != "id"}, "name": "Head 1"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Head 1"


def test_unknown_preset_is_404(app_client):
    assert app_client.post("/api/v1/machines/from-preset/nope").status_code == 404


def test_digitize_validates_against_the_hoop(app_client, uploaded_design):
    machine = app_client.post("/api/v1/machines/from-preset/brother_se700").json()
    response = app_client.post(
        f"/api/v1/designs/{uploaded_design['id']}/digitize",
        json={"fabric": "cotton_shirt", "target_width_mm": 200,
              "machine_id": machine["id"]},
    )
    assert response.status_code == 200
    codes = {issue["code"] for issue in response.json()["issues"]}
    assert "hoop_width" in codes


# --------------------------------------------------------------- customers
@pytest.fixture(scope="module")
def customer(app_client):
    response = app_client.post(
        "/api/v1/customers",
        json={"name": "Northside Athletics", "company": "Northside",
              "email": "buyer@northsideathletics.com"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_customer_detail_includes_history(app_client, customer):
    body = app_client.get(f"/api/v1/customers/{customer['id']}").json()
    assert body["name"] == "Northside Athletics"
    assert "orders" in body and "designs" in body


def test_customer_search(app_client, customer):
    body = app_client.get("/api/v1/customers", params={"q": "northside"}).json()
    assert body["total"] >= 1


# ----------------------------------------------------------- production gate
def test_production_module_is_business_only(app_client):
    """Orders, inventory and invoicing require the Business plan."""
    for path in ("/api/v1/orders", "/api/v1/products", "/api/v1/invoices"):
        response = app_client.get(path)
        assert response.status_code == 402, path
        assert "Business" in response.json()["detail"]


@pytest.fixture(scope="module")
def business_plan(app_client):
    """Upgrade the test workspace so the production endpoints unlock."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import PlanTier, Subscription

    with SessionLocal() as db:
        subscription = db.scalar(select(Subscription))
        subscription.tier = PlanTier.business
        db.commit()
    yield


# ------------------------------------------------------------------- orders
def test_order_lifecycle(app_client, business_plan, customer, uploaded_design):
    created = app_client.post(
        "/api/v1/orders",
        json={
            "customer_id": customer["id"],
            "placement": "Left chest",
            "items": [
                {
                    "design_id": uploaded_design["id"],
                    "description": "Navy tee, left chest",
                    "quantity": 24,
                    "unit_price": 18.50,
                    "unit_cost": 9.20,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["number"].startswith("EG-")
    assert order["status"] == "quote"
    assert order["quantity"] == 24
    assert order["total"] == pytest.approx(444.0)
    assert order["profit"] == pytest.approx(444.0 - 220.8)
    assert order["events"][0]["to_status"] == "quote"

    # Legal transition.
    approved = app_client.post(
        f"/api/v1/orders/{order['id']}/status",
        json={"status": "approved", "note": "Customer signed off"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert len(approved.json()["events"]) == 2

    # Illegal transition is refused with an explanation.
    bad = app_client.post(
        f"/api/v1/orders/{order['id']}/status", json={"status": "delivered"}
    )
    assert bad.status_code == 409
    assert "can only move to" in bad.json()["detail"]

    for target in ("digitizing", "sewing", "completed", "delivered"):
        response = app_client.post(
            f"/api/v1/orders/{order['id']}/status", json={"status": target}
        )
        assert response.status_code == 200, f"{target}: {response.text}"

    board = app_client.get("/api/v1/orders/board").json()
    assert "delivered" in board["columns"]


# ---------------------------------------------------------------- inventory
def test_product_and_stock(app_client, business_plan):
    created = app_client.post(
        "/api/v1/products",
        json={"sku": "GIL-5000-NVY-L", "name": "Gildan 5000 Navy L",
              "blank_cost": 4.25, "stock_quantity": 10, "reorder_level": 12},
    )
    assert created.status_code == 201, created.text
    product = created.json()

    duplicate = app_client.post(
        "/api/v1/products",
        json={"sku": "GIL-5000-NVY-L", "name": "Duplicate", "blank_cost": 1},
    )
    assert duplicate.status_code == 409

    listed = app_client.get("/api/v1/products", params={"low_stock": True}).json()
    assert any(p["id"] == product["id"] and p["needs_reorder"] for p in listed["items"])
    assert listed["inventory_value"] == pytest.approx(42.5)

    received = app_client.post(
        f"/api/v1/products/{product['id']}/stock", json={"delta": 50}
    )
    assert received.json()["stock_quantity"] == 60

    oversell = app_client.post(
        f"/api/v1/products/{product['id']}/stock", json={"delta": -500}
    )
    assert oversell.status_code == 409


def test_stock_is_consumed_when_an_order_starts_sewing(app_client, business_plan, customer):
    product = app_client.post(
        "/api/v1/products",
        json={"sku": "STOCK-TEST", "name": "Stock Test", "blank_cost": 3.0,
              "stock_quantity": 5},
    ).json()

    order = app_client.post(
        "/api/v1/orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": product["id"], "description": "Blank",
                       "quantity": 10, "unit_price": 10.0, "unit_cost": 3.0}],
        },
    ).json()

    app_client.post(f"/api/v1/orders/{order['id']}/status", json={"status": "approved"})
    short = app_client.post(
        f"/api/v1/orders/{order['id']}/status", json={"status": "sewing"}
    )
    assert short.status_code == 409
    assert "Not enough stock" in short.json()["detail"]

    app_client.post(f"/api/v1/products/{product['id']}/stock", json={"delta": 20})
    ok = app_client.post(
        f"/api/v1/orders/{order['id']}/status", json={"status": "sewing"}
    )
    assert ok.status_code == 200
    assert app_client.get(f"/api/v1/products/{product['id']}").json()["stock_quantity"] == 15


# ----------------------------------------------------------------- invoices
def test_invoice_from_order_and_payment(app_client, business_plan, customer):
    order = app_client.post(
        "/api/v1/orders",
        json={
            "customer_id": customer["id"],
            "items": [{"description": "Caps", "quantity": 12, "unit_price": 22.0,
                       "unit_cost": 11.0}],
        },
    ).json()

    invoice = app_client.post("/api/v1/invoices", json={"order_id": order["id"]}).json()
    assert invoice["number"].startswith("INV-")
    assert invoice["subtotal"] == pytest.approx(264.0)
    assert invoice["line_items"]

    assert app_client.post(f"/api/v1/invoices/{invoice['id']}/send").json()["status"] == "sent"

    partial = app_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments", json={"amount": 100.0}
    ).json()
    assert partial["balance_due"] == pytest.approx(164.0)
    assert partial["status"] == "sent"

    over = app_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments", json={"amount": 1000.0}
    )
    assert over.status_code == 409

    final = app_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments", json={"amount": 164.0}
    ).json()
    assert final["status"] == "paid"
    assert final["balance_due"] == pytest.approx(0.0)

    assert app_client.post(f"/api/v1/invoices/{invoice['id']}/void").status_code == 409


# ------------------------------------------------------------------ pricing
def test_pricing_from_a_design(app_client, uploaded_design):
    response = app_client.post(
        "/api/v1/pricing/quote",
        json={"design_id": uploaded_design["id"], "quantity": 24, "blank_cost": 4.25},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unit_cost"] > 4.25            # blank plus machine, labour, thread
    assert body["unit_wholesale"] > body["unit_cost"]
    assert body["unit_retail"] >= body["unit_wholesale"]
    assert body["total_wholesale"] == pytest.approx(body["unit_wholesale"] * 24, rel=0.01)
    assert body["margins"]["wholesale_profit_per_unit"] > 0
    assert body["breakdown"]["thread"]["meters"] > 0
    assert body["quantity_breaks"]
    assert body["assumptions"]


def test_pricing_quantity_breaks_reduce_unit_cost(app_client, uploaded_design):
    one = app_client.post(
        "/api/v1/pricing/quote",
        json={"design_id": uploaded_design["id"], "quantity": 1, "blank_cost": 4.25},
    ).json()
    many = app_client.post(
        "/api/v1/pricing/quote",
        json={"design_id": uploaded_design["id"], "quantity": 144, "blank_cost": 4.25},
    ).json()
    # The digitizing fee amortises across the run.
    assert many["unit_cost"] < one["unit_cost"]


def test_pricing_requires_a_digitized_design(app_client):
    created = app_client.post("/api/v1/designs", json={"name": "Not digitized"}).json()
    response = app_client.post(
        "/api/v1/pricing/quote", json={"design_id": created["id"], "quantity": 10}
    )
    assert response.status_code == 409
    app_client.delete(f"/api/v1/designs/{created['id']}")


# -------------------------------------------------------------------- voice
@pytest.mark.parametrize(
    "transcript,intent",
    [
        ("Create a hat logo.", "create_design"),
        ("Convert this image for embroidery.", "digitize"),
        ("Prepare a 50 shirt order.", "create_order"),
        ("Export DST and PES.", "export"),
        ("How much would 24 hoodies cost?", "show_price"),
        ("Show me a mockup on a black cap.", "show_mockup"),
    ],
)
def test_voice_intents(app_client, transcript, intent):
    body = app_client.post("/api/v1/voice/command", json={"transcript": transcript}).json()
    assert body["intent"] == intent, body
    assert body["confidence"] > 0.5


def test_voice_extracts_entities(app_client):
    body = app_client.post(
        "/api/v1/voice/command", json={"transcript": "Prepare a 50 shirt order."}
    ).json()
    assert body["entities"]["quantity"] == 50
    assert body["entities"]["fabric"] == "cotton_shirt"
    assert body["action"]["route"] == "/orders/new"


def test_voice_admits_when_it_does_not_understand(app_client):
    body = app_client.post(
        "/api/v1/voice/command", json={"transcript": "banana telephone"}
    ).json()
    assert body["intent"] == "unknown"
    assert body["confidence"] == 0.0
    assert body["suggestions"]


# ---------------------------------------------------------------- dashboard
def test_dashboard_aggregates(app_client, business_plan):
    body = app_client.get("/api/v1/dashboard").json()
    assert body["designs"]["total"] >= 1
    assert body["orders"]["by_status"]
    assert "revenue_month" in body["finance"]
    assert body["subscription"]["tier"] == "business"


# ------------------------------------------------------------------ tenancy
def test_files_from_another_workspace_are_refused(app_client):
    response = app_client.get(
        "/api/v1/files/raw/00000000-0000-4000-8000-000000000042/x/original/a.png"
    )
    assert response.status_code == 403
