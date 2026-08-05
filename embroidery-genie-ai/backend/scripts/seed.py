"""Seed a workspace with realistic demo data.

    python scripts/seed.py

Creates a user, workspace, machines, customers, blanks and one fully
digitized design so a fresh install has something to look at.  Safe to re-run:
it looks for the demo workspace first.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
    Design,
    Machine,
    Membership,
    OrgRole,
    Organization,
    PlanTier,
    Product,
    Subscription,
    User,
)
from app.services import designs as pipeline  # noqa: E402
from app.services.machines import get_preset  # noqa: E402

DEMO_EMAIL = os.environ.get("SEED_EMAIL", "demo@embroiderygenie.ai")
DEMO_SLUG = "demo-workshop"

DEMO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="70mm" height="45mm"
    viewBox="0 0 700 450">
  <rect x="40" y="40" width="620" height="240" rx="24" fill="#12305C"/>
  <circle cx="180" cy="160" r="80" fill="#F5C518"/>
  <path d="M300 100 L620 100 L620 150 L300 150 Z" fill="#FFFFFF"/>
  <path d="M300 190 L540 190 L540 230 L300 230 Z" fill="#FFFFFF"/>
  <path d="M40 330 L660 330 L660 380 L40 380 Z" fill="#C4161C"/>
</svg>"""


def main() -> None:
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == DEMO_SLUG))
        if org:
            print(f"Demo workspace '{DEMO_SLUG}' already exists (id={org.id}).")
            return

        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=DEMO_EMAIL,
                full_name="Demo Operator",
                company="Northside Embroidery",
                onboarded=True,
            )
            db.add(user)
            db.flush()

        org = Organization(name="Demo Workshop", slug=DEMO_SLUG, currency="USD")
        db.add(org)
        db.flush()

        db.add(Membership(user_id=user.id, organization_id=org.id,
                          role=OrgRole.owner, is_default=True))
        # Business tier so the production module is visible in the demo.
        db.add(Subscription(organization_id=org.id, tier=PlanTier.business, seats=5))
        db.flush()

        # ------------------------------------------------------------ machines
        for index, key in enumerate(("tajima_tmbp", "ricoma_mt1502", "generic_cap")):
            preset = get_preset(key)
            db.add(Machine(
                organization_id=org.id,
                name=f"{preset.brand} {preset.model}",
                brand=preset.brand,
                model=preset.model,
                heads=preset.heads,
                needle_count=preset.needle_count,
                hoop_width_mm=preset.hoop_width_mm,
                hoop_height_mm=preset.hoop_height_mm,
                max_stitch_count=preset.max_stitch_count,
                max_speed_spm=preset.max_speed_spm,
                supported_formats=list(preset.formats),
                hourly_rate=18.0,
                is_default=index == 0,
                notes=preset.notes,
            ))

        # ----------------------------------------------------------- customers
        customers = [
            Customer(organization_id=org.id, name="Northside Athletics",
                     company="Northside Athletics Club",
                     email="orders@northsideathletics.com", phone="+1 555 0142",
                     tags=["sports", "repeat"]),
            Customer(organization_id=org.id, name="Harbour Coffee",
                     company="Harbour Coffee Roasters",
                     email="hello@harbourcoffee.example.com", tags=["hospitality"]),
            Customer(organization_id=org.id, name="Mercer Construction",
                     company="Mercer Construction LLC",
                     email="admin@mercerbuild.com", tags=["workwear", "bulk"]),
        ]
        db.add_all(customers)

        # ------------------------------------------------------------- blanks
        blanks = [
            ("GIL-5000-NVY-L", "Gildan 5000 Heavy Cotton — Navy L", "cotton_shirt", 4.25, 120, 24),
            ("GIL-5000-BLK-XL", "Gildan 5000 Heavy Cotton — Black XL", "cotton_shirt", 4.25, 86, 24),
            ("GIL-18500-CHR-L", "Gildan 18500 Hoodie — Charcoal L", "hoodie", 14.80, 40, 12),
            ("RICH-112-BLK", "Richardson 112 Trucker — Black", "hat", 7.90, 200, 48),
            ("BEAN-KNIT-NVY", "Cuffed Knit Beanie — Navy", "beanie", 5.40, 8, 24),
            ("TOTE-CAN-NAT", "Canvas Tote — Natural", "canvas", 6.20, 60, 12),
        ]
        for sku, name, fabric, cost, stock, reorder in blanks:
            db.add(Product(
                organization_id=org.id, sku=sku, name=name, fabric_profile=fabric,
                blank_cost=cost, stock_quantity=stock, reorder_level=reorder,
                category="blank", supplier="SanMar",
            ))

        db.flush()

        # ------------------------------------------------------------- design
        design = Design(
            organization_id=org.id,
            owner_id=user.id,
            customer_id=customers[0].id,
            name="Northside Athletics — Left Chest",
            description="Primary club mark, left chest placement.",
            tags=["logo", "left-chest"],
        )
        db.add(design)
        db.flush()

        pipeline.store_file(
            db, design, pipeline.FileKind.original,
            "northside.svg", DEMO_SVG.encode(), "image/svg+xml",
        )
        pipeline.store_file(
            db, design, pipeline.FileKind.vector,
            "vector.svg", DEMO_SVG.encode(), "image/svg+xml",
        )

        machine = db.scalar(
            select(Machine).where(Machine.organization_id == org.id,
                                  Machine.is_default.is_(True))
        )
        result = pipeline.digitize_design(
            db, design,
            fabric="cotton_shirt",
            machine_id=machine.id if machine else None,
            target_width_mm=89.0,
        )
        db.commit()

        print(f"Seeded workspace '{org.name}' ({org.slug})")
        print(f"  user            : {user.email}")
        print(f"  machines        : 3")
        print(f"  customers       : {len(customers)}")
        print(f"  blanks          : {len(blanks)}")
        print(f"  demo design     : {design.name}")
        print(f"    stitches      : {result['stitch_count']:,}")
        print(f"    colours       : {result['color_count']}")
        print(f"    size          : {result['width_mm']} x {result['height_mm']} mm")
        print(f"    est. run time : {result['estimated_minutes']} min")
        print()
        print("Sign in with Supabase using this email, or run with ALLOW_DEV_AUTH=true.")
        print(f"Due date example: {(date.today() + timedelta(days=10)).isoformat()}")


if __name__ == "__main__":
    main()
