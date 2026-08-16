
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
from .models import Address, PackageInput, PickupAddress
from .store import DraftStore, PickupAddressStore


settings = Settings()
draft_store = DraftStore(settings.state_path)
pickup_address_store = PickupAddressStore(settings.pickup_addresses_path)
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


def pickup_addresses() -> list[PickupAddress]:
    return pickup_address_store.all()


def get_pickup_address(address_id: str) -> PickupAddress | None:
    if not address_id:
        return None
    return pickup_address_store.get(address_id)


def default_pickup_address() -> PickupAddress | None:
    addresses = pickup_addresses()
    return addresses[0] if addresses else None


def pickup_address_option_label(address: PickupAddress) -> str:
    parts = [address.label or address.name or "Abholadresse", address.street, f"{address.post_code} {address.city}".strip()]
    return " · ".join([part for part in parts if part])


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


def forwarding_defaults(order, draft=None) -> dict:
    delivery = draft.delivery_address if draft else order.delivery_address
    package = draft.package if draft else None
    return {
        "delivery_name": (delivery.name if delivery else order.customer_name) or "",
        "delivery_street": (delivery.street if delivery else "") or "",
        "delivery_post_code": (delivery.post_code if delivery else "") or "",
        "delivery_city": (delivery.city if delivery else "") or "",
        "delivery_country_code": (delivery.country_code if delivery else "DE") or "DE",
        "package_length_cm": package.length_cm if package else 120,
        "package_width_cm": package.width_cm if package else 80,
        "package_height_cm": package.height_cm if package else 80,
        "package_weight_kg": package.weight_kg if package else 25.0,
        "package_count": package.quantity if package else 1,
        "package_type": package.package_type if package else "PA",
        "package_content": package.content if package else (order.name or ""),
        "service_level": "standard",
        "pickup_date": order.created_at.date().isoformat(),
        "insurance_value_eur": 0.0,
        "liftgate_required": False,
        "notes": "",
    }


def mock_forwarding_quote(order, payload: dict[str, object]) -> dict[str, object]:
    count = max(int(payload.get("package_count") or 1), 1)
    weight = max(float(payload.get("package_weight_kg") or 0.0) * count, 0.0)
    length = max(float(payload.get("package_length_cm") or 0.0), 0.0)
    width = max(float(payload.get("package_width_cm") or 0.0), 0.0)
    height = max(float(payload.get("package_height_cm") or 0.0), 0.0)
    volume = (length * width * height * count) / 5000.0
    base = 39.0
    weight_surcharge = round(weight * 0.38, 2)
    volume_surcharge = round(volume * 1.65, 2)
    service_level = str(payload.get("service_level") or "standard")
    service_surcharge = {"standard": 0.0, "express": 19.0, "priority": 34.0}.get(service_level, 0.0)
    insurance_surcharge = round(max(float(payload.get("insurance_value_eur") or 0.0), 0.0) * 0.012, 2)
    liftgate_surcharge = 14.0 if bool(payload.get("liftgate_required")) else 0.0
    total = round(base + weight_surcharge + volume_surcharge + service_surcharge + insurance_surcharge + liftgate_surcharge, 2)
    return {
        "id": f"mock-quote-{order.id}",
        "currency": "EUR",
        "base": base,
        "weight_surcharge": weight_surcharge,
        "volume_surcharge": volume_surcharge,
        "service_surcharge": service_surcharge,
        "insurance_surcharge": insurance_surcharge,
        "liftgate_surcharge": liftgate_surcharge,
        "total": total,
        "valid_until": order.created_at.date().isoformat(),
        "mode": "mock",
    }


def mock_forwarding_order(order, payload: dict[str, object], quote: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"mock-order-{order.id}",
        "reference": order.name,
        "status": "created",
        "currency": quote.get("currency", "EUR"),
        "total": quote.get("total"),
        "service_level": payload.get("service_level", "standard"),
        "mode": "mock",
    }


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


@app.get("/pickup-addresses", response_class=HTMLResponse)
def pickup_addresses_page(request: Request):
    return templates.TemplateResponse(request, "pickup_addresses.html", {
        "settings": settings,
        "addresses": pickup_addresses(),
    })


@app.post("/pickup-addresses")
def create_pickup_address(
    label: str = Form(""),
    name: str = Form(""),
    street: str = Form(""),
    city: str = Form(""),
    post_code: str = Form(""),
    country_code: str = Form("DE"),
):
    address = PickupAddress(
        id=pickup_address_store.new_id(),
        label=label.strip() or name.strip() or "Abholadresse",
        name=name.strip(),
        street=street.strip(),
        city=city.strip(),
        post_code=post_code.strip(),
        country_code=country_code.strip() or "DE",
    )
    pickup_address_store.upsert(address)
    return RedirectResponse(url="/pickup-addresses", status_code=303)


@app.post("/pickup-addresses/{address_id}/delete")
def delete_pickup_address(address_id: str):
    pickup_address_store.delete(address_id)
    return RedirectResponse(url="/pickup-addresses", status_code=303)


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


@app.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: str):
    if not settings.has_shopify_credentials:
        raise HTTPException(status_code=400, detail="Shopify client credentials missing")
    client = shopify_client()
    try:
        order = client.get_order(order_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Order not found")
        raise HTTPException(status_code=exc.response.status_code, detail=f"Shopify konnte nicht geladen werden: {exc.response.text}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    draft = next((d for d in draft_store.all() if d.shopify_order_id == order_id), None)
    return templates.TemplateResponse(request, "order.html", {
        "settings": settings,
        "order": order,
        "draft": draft,
    })


@app.get("/orders/{order_id}/shipment", response_class=HTMLResponse)
def shipment_flow(request: Request, order_id: str, pickup_address_id: str = ""):
    try:
        order = shopify_client().get_order(order_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Order not found")
        raise HTTPException(status_code=exc.response.status_code, detail=f"Shopify konnte nicht geladen werden: {exc.response.text}")
    draft = next((d for d in draft_store.all() if d.shopify_order_id == order_id), None)
    selected_pickup_address = get_pickup_address(pickup_address_id) or default_pickup_address()
    form = forwarding_defaults(order, draft)
    return templates.TemplateResponse(request, "shipment.html", {
        "settings": settings,
        "order": order,
        "draft": draft,
        "form": form,
        "quote": None,
        "created": None,
        "pickup_addresses": pickup_addresses(),
        "selected_pickup_address": selected_pickup_address,
        "selected_pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
    })


@app.post("/orders/{order_id}/shipment", response_class=HTMLResponse)
def shipment_flow_submit(
    request: Request,
    order_id: str,
    action: str = Form("quote"),
    pickup_address_id: str = Form(""),
    delivery_name: str = Form(""),
    delivery_street: str = Form(""),
    delivery_post_code: str = Form(""),
    delivery_city: str = Form(""),
    delivery_country_code: str = Form("DE"),
    package_length_cm: float = Form(120),
    package_width_cm: float = Form(80),
    package_height_cm: float = Form(80),
    package_weight_kg: float = Form(25.0),
    package_count: int = Form(1),
    package_type: str = Form("PA"),
    package_content: str = Form(""),
    service_level: str = Form("standard"),
    pickup_date: str = Form(""),
    insurance_value_eur: float = Form(0.0),
    liftgate_required: bool = Form(False),
    notes: str = Form(""),
):
    order = shopify_client().get_order(order_id)
    draft = next((d for d in draft_store.all() if d.shopify_order_id == order_id), None)
    selected_pickup_address = get_pickup_address(pickup_address_id) or default_pickup_address()
    if not selected_pickup_address:
        raise HTTPException(status_code=400, detail="Es ist noch keine Abholadresse gespeichert.")
    form = {
        "pickup_address_id": selected_pickup_address.id,
        "delivery_name": delivery_name,
        "delivery_street": delivery_street,
        "delivery_post_code": delivery_post_code,
        "delivery_city": delivery_city,
        "delivery_country_code": delivery_country_code,
        "package_length_cm": package_length_cm,
        "package_width_cm": package_width_cm,
        "package_height_cm": package_height_cm,
        "package_weight_kg": package_weight_kg,
        "package_count": package_count,
        "package_type": package_type,
        "package_content": package_content,
        "service_level": service_level,
        "pickup_date": pickup_date,
        "insurance_value_eur": insurance_value_eur,
        "liftgate_required": liftgate_required,
        "notes": notes,
    }
    if action == "quote":
        quote = mock_forwarding_quote(order, form)
        return templates.TemplateResponse(request, "shipment.html", {
            "settings": settings,
            "order": order,
            "draft": draft,
            "form": form,
            "quote": quote,
            "created": None,
            "pickup_addresses": pickup_addresses(),
            "selected_pickup_address": selected_pickup_address,
            "selected_pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
        })
    if action == "create":
        quote = mock_forwarding_quote(order, form)
        created = mock_forwarding_order(order, form, quote)
        if draft:
            draft.order = created
            draft.status = "ordered"
            draft_store.upsert(draft)
        return templates.TemplateResponse(request, "shipment_created.html", {
            "settings": settings,
            "order": order,
            "draft": draft,
            "form": form,
            "quote": quote,
            "created": created,
            "selected_pickup_address": selected_pickup_address,
        })
    raise HTTPException(status_code=400, detail="Unknown shipment action")


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
