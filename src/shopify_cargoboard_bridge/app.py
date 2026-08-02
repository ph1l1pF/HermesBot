
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .clients import CargoboardClient, ShopifyClient
from .config import Settings
from .mappers import order_to_draft
from .models import Address, PackageInput
from .store import DraftStore


settings = Settings()
store = DraftStore(settings.state_path)
app = FastAPI(title=settings.app_title)
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
static_dir = Path(__file__).with_name("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def shopify_client() -> ShopifyClient:
    return ShopifyClient(settings)


def cargoboard_client() -> CargoboardClient:
    return CargoboardClient(settings)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "shopify_configured": settings.has_shopify,
        "cargoboard_configured": settings.has_cargoboard,
        "billing_configured": settings.has_billing_address,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    drafts = store.all()
    orders = []
    error = None
    if settings.has_shopify:
        try:
            orders = shopify_client().list_orders(limit=20)
        except Exception as exc:
            error = f"Shopify konnte nicht geladen werden: {exc}"
    return templates.TemplateResponse("index.html", {
        "request": request,
        "settings": settings,
        "drafts": drafts,
        "orders": orders,
        "error": error,
    })


@app.post("/sync")
def sync_orders():
    if not settings.has_shopify:
        raise HTTPException(status_code=400, detail="Shopify credentials missing")
    client = shopify_client()
    orders = client.list_orders(limit=20)
    existing_ids = {draft.shopify_order_id for draft in store.all()}
    created = 0
    for order in orders:
        if order.id in existing_ids:
            continue
        store.upsert(order_to_draft(order, settings, store.new_id()))
        created += 1
    return RedirectResponse(url=f"/?synced={created}", status_code=303)


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_detail(request: Request, draft_id: str):
    draft = store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return templates.TemplateResponse("draft.html", {
        "request": request,
        "settings": settings,
        "draft": draft,
    })


@app.post("/drafts/{draft_id}")
def update_draft(
    draft_id: str,
    delivery_name: str = Form(""),
    delivery_street: str = Form(""),
    delivery_city: str = Form(""),
    delivery_post_code: str = Form(""),
    delivery_country_code: str = Form("DE"),
    package_length_cm: int = Form(0),
    package_width_cm: int = Form(0),
    package_height_cm: int = Form(0),
    package_weight_kg: float = Form(0.0),
    package_type: str = Form("PA"),
    package_content: str = Form(""),
    notes: str = Form(""),
):
    draft = store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.delivery_address = Address(
        name=delivery_name,
        street=delivery_street,
        city=delivery_city,
        post_code=delivery_post_code,
        country_code=delivery_country_code,
    )
    draft.package = PackageInput(
        quantity=1,
        package_type=package_type,
        length_cm=package_length_cm,
        width_cm=package_width_cm,
        height_cm=package_height_cm,
        weight_kg=package_weight_kg,
        content=package_content,
        stackable=draft.package.stackable,
        pallet_exchange=draft.package.pallet_exchange,
    )
    draft.notes = notes
    store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/quote")
def create_quote(draft_id: str):
    if not settings.has_cargoboard:
        raise HTTPException(status_code=400, detail="Cargoboard API key missing")
    draft = store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.quotation = cargoboard_client().place_quotation(draft)
    draft.status = "quoted"
    store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/confirm")
def confirm_order(draft_id: str):
    if not settings.has_cargoboard:
        raise HTTPException(status_code=400, detail="Cargoboard API key missing")
    draft = store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.order = cargoboard_client().place_order(draft, draft.quotation)
    draft.status = "ordered"
    store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/delete")
def delete_draft(draft_id: str):
    store.delete(draft_id)
    return RedirectResponse(url="/", status_code=303)
