"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    customers,
    dashboard,
    designs,
    files,
    invoices,
    machines,
    orders,
    pricing,
    products,
    studio,
    voice,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(designs.router)
api_router.include_router(studio.router)
api_router.include_router(machines.router)
api_router.include_router(customers.router)
api_router.include_router(orders.router)
api_router.include_router(products.router)
api_router.include_router(invoices.router)
api_router.include_router(pricing.router)
api_router.include_router(voice.router)
api_router.include_router(files.router)
