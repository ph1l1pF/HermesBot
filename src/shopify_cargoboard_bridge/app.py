
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import ShopifyAuthStore
from .clients import CargoboardClient, ShopifyClient
from .config import Settings, normalize_shop_domain
from .mappers import order_to_draft
from .models import Address, PackageInput
from .store import DraftStore


settings = Settings()
draft_store = DraftStore(settings.state_path)
auth_store = ShopifyAuthStore(settings.shopify_auth_path)
app = FastAPI(title=settings.app_title)
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
static_dir = Path(__file__).with_name("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def current_shopify_token() -> str:
    return settings.shopify_token or auth_store.access_token()


def shopify_client() -> ShopifyClient:
    token = current_shopify_token()
    if not settings.shopify_shop:
        raise HTTPException(status_code=400, detail="Shop domain missing")
    if not token:
        raise HTTPException(status_code=400, detail="Shopify access token missing")
    return ShopifyClient(settings, access_token=token)


def cargoboard_client() -> CargoboardClient:
    return CargoboardClient(settings)


def shopify_callback_url(request: Request) -> str:
    return str(request.url_for("shopify_callback"))


def build_shopify_auth_url(request: Request) -> str:
    if not settings.has_shopify_credentials:
        raise HTTPException(status_code=400, detail="Shopify client credentials missing")
    redirect_uri = shopify_callback_url(request)
    state = auth_store.new_state(redirect_uri)
    params = {
        "client_id": settings.shopify_client_id,
        "scope": settings.shopify_scopes,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if settings.shopify_access_mode == "online":
        params["grant_options[]"] = "per-user"
    return f"https://{settings.shopify_shop}/admin/oauth/authorize?{urlencode(params)}"


def exchange_shopify_code(shop: str, code: str) -> dict:
    url = f"https://{shop}/admin/oauth/access_token"
    data = {
        "client_id": settings.shopify_client_id,
        "client_secret": settings.shopify_client_secret,
        "code": code,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=30.0, headers=headers) as client:
        resp = client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "shopify_configured": settings.has_shopify_credentials,
        "shopify_connected": bool(current_shopify_token()),
        "cargoboard_configured": settings.has_cargoboard,
        "billing_configured": settings.has_billing_address,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    drafts = draft_store.all()
    orders = []
    error = None
    shopify_connected = bool(current_shopify_token())
    if shopify_connected:
        try:
            orders = shopify_client().list_orders(limit=20)
        except Exception as exc:
            error = f"Shopify konnte nicht geladen werden: {exc}"
    return templates.TemplateResponse(request, "index.html", {
        "settings": settings,
        "drafts": drafts,
        "orders": orders,
        "error": error,
        "shopify_connected": shopify_connected,
        "shopify_shop": settings.shopify_shop,
        "shopify_connect_url": str(request.url_for('shopify_connect')) if settings.has_shopify_credentials and not shopify_connected else "",
        "oauth_pending": bool(auth_store.load().pending_state),
    })


@app.get("/shopify/connect")
def shopify_connect(request: Request):
    auth_url = build_shopify_auth_url(request)
    return RedirectResponse(url=auth_url, status_code=303)


@app.get("/shopify/callback")
def shopify_callback(request: Request):
    params = dict(request.query_params)
    shop = normalize_shop_domain(params.get("shop", settings.shopify_shop))
    code = params.get("code", "")
    state = params.get("state", "")
    hmac_ok = auth_store.verify_hmac(params, settings.shopify_client_secret)
    state_ok = auth_store.verify_state(state)
    if not (shop and code and state_ok and hmac_ok):
        raise HTTPException(status_code=400, detail="Shopify OAuth validation failed")
    token_response = exchange_shopify_code(shop, code)
    auth_store.store_token(
        shop=shop,
        access_token=token_response.get("access_token", ""),
        scope=token_response.get("scope", ""),
    )
    return RedirectResponse(url="/?shopify=connected", status_code=303)


@app.post("/sync")
def sync_orders():
    client = shopify_client()
    orders = client.list_orders(limit=20)
    existing_ids = {draft.shopify_order_id for draft in draft_store.all()}
    created = 0
    for order in orders:
        if order.id in existing_ids:
            continue
        draft_store.upsert(order_to_draft(order, settings, draft_store.new_id()))
        created += 1
    return RedirectResponse(url=f"/?synced={created}", status_code=303)


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_detail(request: Request, draft_id: str):
    draft = draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return templates.TemplateResponse(request, "draft.html", {
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
    draft = draft_store.get(draft_id)
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
    draft_store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/quote")
def create_quote(draft_id: str):
    if not settings.has_cargoboard:
        raise HTTPException(status_code=400, detail="Cargoboard API key missing")
    draft = draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.quotation = cargoboard_client().place_quotation(draft)
    draft.status = "quoted"
    draft_store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/confirm")
def confirm_order(draft_id: str):
    if not settings.has_cargoboard:
        raise HTTPException(status_code=400, detail="Cargoboard API key missing")
    draft = draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.order = cargoboard_client().place_order(draft, draft.quotation)
    draft.status = "ordered"
    draft_store.upsert(draft)
    return RedirectResponse(url=f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/delete")
def delete_draft(draft_id: str):
    draft_store.delete(draft_id)
    return RedirectResponse(url="/", status_code=303)
