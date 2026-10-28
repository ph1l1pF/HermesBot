
from __future__ import annotations

import secrets
import json
from datetime import datetime, timezone, timedelta
from threading import RLock
from functools import wraps
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import ShopifyAuthStore
from .clients import CargoboardClient, CargoboardError, ShopifyClient, delivery_phone_from_shopify
from .config import Settings, normalize_shop_domain, CARGOBOARD_URLS
from .environments import load_mode, save_mode
from .mappers import order_to_draft
from .models import Address, PackageInput, PickupAddress, ShippingDraft
from .shipping import build_payload, next_pickup_date, validate_post_code
from .store import DraftStore, PickupAddressStore


booking_lock = RLock()


def serialized_booking_state(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with booking_lock:
            return func(*args, **kwargs)
    return wrapped

configured_settings = Settings()
environment_path = configured_settings.state_path.with_name("cargoboard_environment.json")
initial_mode = configured_settings.cargoboard_initial_mode or configured_settings.cargoboard_mode
if initial_mode not in CARGOBOARD_URLS:
    initial_mode = "test"
settings = configured_settings.for_cargoboard_environment(load_mode(environment_path, initial_mode))
environment_switch_token = secrets.token_urlsafe(32)
draft_store = DraftStore(settings.state_path)
pickup_address_store = PickupAddressStore(settings.pickup_addresses_path)
auth_store = ShopifyAuthStore(settings.shopify_auth_path)
app = FastAPI(title=settings.app_title)


@app.exception_handler(StarletteHTTPException)
async def http_error_utf8(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                        headers=exc.headers, media_type="application/json; charset=utf-8")


templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
templates.env.globals["cargoboard_switch_token"] = environment_switch_token
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


def active_shipping_draft(order_id: str):
    drafts = [d for d in draft_store.all() if d.shopify_order_id == order_id]
    target = settings.cargoboard_base_url.rstrip("/")
    return next((d for d in drafts if getattr(d, "cargoboard_environment", "") == target),
                next((d for d in drafts if not getattr(d, "cargoboard_environment", "")), None))


@app.post("/cargoboard/environment")
@serialized_booking_state
def switch_cargoboard_environment(environment: str = Form(...), switch_token: str = Form("")):
    global settings
    if not switch_token or not secrets.compare_digest(switch_token, environment_switch_token):
        raise HTTPException(status_code=403, detail="Bitte die Seite neu laden und die Umgebung dort wechseln.")
    if environment not in CARGOBOARD_URLS:
        raise HTTPException(status_code=400, detail="Unbekannte Cargoboard-Umgebung.")
    target = CARGOBOARD_URLS[environment]
    if target != settings.cargoboard_base_url.rstrip("/"):
        for draft in draft_store.all():
            if draft.status == "quoted":
                draft.status = "draft"
                draft.quotation = None
                draft.quotation_token = ""
                draft.production_confirmation_token = ""
                draft.quoted_at = None
                draft.cargoboard_payload = None
                draft_store.upsert(draft)
        save_mode(environment_path, environment)
        settings = configured_settings.for_cargoboard_environment(environment)
    return RedirectResponse(url="/", status_code=303)


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
    if package and not any((package.length_cm, package.width_cm, package.height_cm, package.weight_kg)):
        package = None
    delivery_phone = delivery_phone_from_shopify(order.source) or (order.delivery_address.phone if order.delivery_address else "")
    if draft:
        saved_payload = getattr(draft, "cargoboard_payload", None)
        saved_contact = (saved_payload.get("consignee", {}).get("contactPerson") or {}) if saved_payload else {}
        if getattr(draft, "delivery_phone_saved", False):
            delivery_phone = draft.delivery_address.phone
        elif "phone" in saved_contact:
            delivery_phone = saved_contact["phone"] or ""
        elif draft.delivery_address.phone:
            delivery_phone = draft.delivery_address.phone
    return {
        "delivery_name": (delivery.name if delivery else order.customer_name) or "",
        "delivery_street": (delivery.street if delivery else "") or "",
        "delivery_post_code": (delivery.post_code if delivery else "") or "",
        "delivery_city": (delivery.city if delivery else "") or "",
        "delivery_country_code": (delivery.country_code if delivery else "DE") or "DE",
        "package_length_cm": package.length_cm if package else 120,
        "package_width_cm": package.width_cm if package else 80,
        "package_height_cm": package.height_cm if package else 10,
        "package_weight_kg": package.weight_kg if package else 10.0,
        "package_count": package.quantity if package else 1,
        "package_type": package.package_type if package else "KT",
        "package_content": package.content if package else (order.name or ""),
        "service_level": "standard",
        "pickup_date": next_pickup_date(),
        "private_customer": not bool((order.source.get("shipping_address") or {}).get("company")),
        "delivery_phone": delivery_phone,
        "delivery_email": order.source.get("email") or (order.source.get("customer") or {}).get("email") or "",
        "insurance_value_eur": 0.0,
        "liftgate_required": False,
        "notes": "",
    }


def validate_booking(draft, token: str):
    if draft.status != "quoted" or not draft.quotation or not draft.cargoboard_payload:
        raise HTTPException(status_code=409, detail="Bitte zuerst ein aktuelles Angebot berechnen. Bereits gebuchte oder unklare Aufträge nicht erneut senden.")
    if not token or not secrets.compare_digest(token, draft.quotation_token):
        raise HTTPException(status_code=409, detail="Bestätigung gehört nicht zum aktuellen Angebot.")
    if draft.cargoboard_environment != settings.cargoboard_base_url.rstrip("/"):
        raise HTTPException(status_code=409, detail="API-Umgebung geändert. Bitte neu berechnen.")
    if not draft.quoted_at or datetime.now(timezone.utc) - draft.quoted_at > timedelta(minutes=15):
        raise HTTPException(status_code=409, detail="Angebot älter als 15 Minuten. Bitte neu berechnen.")


def book_draft(draft, token: str, production_token: str = ""):
    validate_booking(draft, token)
    if settings.cargoboard_base_url.rstrip("/") == CARGOBOARD_URLS["production"]:
        if not production_token or not draft.production_confirmation_token or not secrets.compare_digest(production_token, draft.production_confirmation_token):
            raise HTTPException(status_code=409, detail="Bitte den kostenpflichtigen Auftrag auf der Sicherheitsabfrage ausdrücklich bestätigen.")
    draft.production_confirmation_token = ""
    draft.status = "booking"
    draft_store.upsert(draft)
    try:
        draft.order = cargoboard_client().place_order(draft.cargoboard_payload, draft.quotation)
    except CargoboardError as exc:
        draft.status = "booking_unknown"
        draft_store.upsert(draft)
        raise HTTPException(status_code=502, detail=f"{exc} Buchungsstatus unklar: vor weiteren Aktionen im Cargoboard-Portal prüfen.") from None
    draft.status = "ordered"
    draft.quotation_token = ""
    draft_store.upsert(draft)


@app.post("/cargoboard/check")
@serialized_booking_state
def check_cargoboard(request: Request):
    status_code = 200
    error = None
    if not settings.has_cargoboard:
        status_code = 400
        error = "Cargoboard API-Key fehlt."
    else:
        try:
            cargoboard_client().check_connection()
        except CargoboardError as exc:
            status_code = 502
            error = str(exc)
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "connection_result.html", {
            "settings": settings, "error": error,
        }, status_code=status_code)
    payload = {"detail": error} if error else {"connected": True, "environment": settings.cargoboard_environment}
    return JSONResponse(payload, status_code=status_code, media_type="application/json; charset=utf-8")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "shopify_configured": settings.has_shopify_credentials,
        "shopify_connected": bool(current_shopify_token()),
        "cargoboard_configured": settings.has_cargoboard,
        "cargoboard_environment": settings.cargoboard_environment,
        "cargoboard_base_url": settings.cargoboard_base_url,
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
@serialized_booking_state
def create_pickup_address(
    label: str = Form(""),
    name: str = Form(""),
    street: str = Form(""),
    city: str = Form(""),
    post_code: str = Form(""),
    country_code: str = Form("DE"),
    phone: str = Form(""),
):
    try:
        validate_post_code(post_code, country_code, "Abholadresse")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    address = PickupAddress(
        id=pickup_address_store.new_id(),
        label=label.strip() or name.strip() or "Abholadresse",
        name=name.strip(),
        street=street.strip(),
        city=city.strip(),
        post_code=post_code.strip(),
        country_code=country_code.strip() or "DE",
        phone=phone.strip(),
    )
    pickup_address_store.upsert(address)
    return RedirectResponse(url="/pickup-addresses", status_code=303)


@app.post("/pickup-addresses/{address_id}/phone")
@serialized_booking_state
def update_pickup_phone(address_id: str, phone: str = Form("")):
    address = pickup_address_store.get(address_id)
    if not address:
        raise HTTPException(status_code=404, detail="Abholadresse nicht gefunden.")
    address.phone = phone.strip()
    pickup_address_store.upsert(address)
    return RedirectResponse(url="/pickup-addresses", status_code=303)


@app.post("/pickup-addresses/{address_id}/edit")
@serialized_booking_state
def edit_pickup_address(
    address_id: str,
    label: str = Form(""),
    name: str = Form(""),
    street: str = Form(""),
    city: str = Form(""),
    post_code: str = Form(""),
    country_code: str = Form("DE"),
    phone: str = Form(""),
):
    address = pickup_address_store.get(address_id)
    if not address:
        raise HTTPException(status_code=404, detail="Abholadresse nicht gefunden.")
    country = country_code.strip().upper() or "DE"
    try:
        validate_post_code(post_code, country, "Abholadresse")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    address.label = label.strip() or name.strip() or "Abholadresse"
    address.name = name.strip()
    address.street = street.strip()
    address.city = city.strip()
    address.post_code = post_code.strip()
    address.country_code = country
    address.phone = phone.strip()
    pickup_address_store.upsert(address)
    return RedirectResponse(url="/pickup-addresses", status_code=303)


@app.post("/pickup-addresses/{address_id}/delete")
@serialized_booking_state
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
@serialized_booking_state
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
    draft = active_shipping_draft(order_id)
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
    draft = active_shipping_draft(order_id)
    selected_pickup_address = get_pickup_address(pickup_address_id) or default_pickup_address()
    form = forwarding_defaults(order, draft)
    return templates.TemplateResponse(request, "shipment.html", {
        "settings": settings,
        "order": order,
        "draft": draft,
        "form": form,
        "quote": None,
        "created": None,
        "show_payload": False,
        "payload_json": "",
        "pickup_addresses": pickup_addresses(),
        "selected_pickup_address": selected_pickup_address,
        "selected_pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
    })


@app.post("/orders/{order_id}/shipment", response_class=HTMLResponse)
@serialized_booking_state
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
    package_height_cm: float = Form(10),
    package_weight_kg: float = Form(10.0),
    package_count: int = Form(1),
    package_type: str = Form("KT"),
    package_content: str = Form(""),
    service_level: str = Form("standard"),
    pickup_date: str = Form(""),
    insurance_value_eur: float = Form(0.0),
    liftgate_required: bool = Form(False),
    notes: str = Form(""),
    private_customer: bool = Form(False),
    delivery_phone: str = Form(""),
    delivery_email: str = Form(""),
    show_payload: bool = Form(False),
    quotation_token: str = Form(""),
    production_confirmation_token: str = Form(""),
):
    order = shopify_client().get_order(order_id)
    draft = active_shipping_draft(order_id)
    selected_pickup_address = get_pickup_address(pickup_address_id) or default_pickup_address()
    form = {
        "pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
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
        "private_customer": private_customer,
        "delivery_phone": delivery_phone,
        "delivery_email": delivery_email,
    }
    request_payload = None
    def payload_json_for_display(payload):
        return json.dumps(payload, ensure_ascii=False, indent=2) if payload else ""

    def failure(status_code, detail, validation_errors=None):
        if "text/html" in request.headers.get("accept", ""):
            return templates.TemplateResponse(request, "shipment.html", {
                "settings": settings, "order": order, "draft": draft, "form": form,
                "quote": None, "created": None, "error": detail,
                "show_payload": show_payload,
                "payload_json": payload_json_for_display(request_payload or (
                    draft.cargoboard_payload if draft and draft.status == "quoted" else None
                )),
                "validation_errors": validation_errors or [], "pickup_addresses": pickup_addresses(),
                "selected_pickup_address": selected_pickup_address,
                "selected_pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
            }, status_code=status_code)
        content = {"detail": detail}
        if validation_errors:
            content["validation_errors"] = validation_errors
        return JSONResponse(content, status_code=status_code, media_type="application/json; charset=utf-8")

    if not selected_pickup_address:
        return failure(400, "Es ist noch keine Abholadresse gespeichert.")
    if not settings.has_cargoboard:
        return failure(400, "Cargoboard API-Key fehlt. Bitte lokal konfigurieren.")
    if action == "quote":
        try:
            payload = build_payload(order, selected_pickup_address, form)
        except (ValueError, TypeError) as exc:
            return failure(400, str(exc))
        request_payload = payload
        with booking_lock:
            # Reload under the same lock used by confirmations.
            draft = active_shipping_draft(order_id)
            if draft and draft.status in {"ordered", "booking", "booking_unknown"}:
                return failure(409, "Für diese Bestellung wurde bereits gebucht oder der Buchungsstatus muss geprüft werden.")
            try:
                quote = cargoboard_client().place_quotation(payload)
            except CargoboardError as exc:
                return failure(422 if exc.status_code == 422 else 502, str(exc), exc.validation_errors)
            draft = ShippingDraft(
                id=draft.id if draft else uuid4().hex[:12], shopify_order_id=order.id,
                shopify_order_name=order.name, shopify_order_number=order.number,
                created_at=datetime.now(timezone.utc), customer_name=order.customer_name,
                pickup_address=Address(**selected_pickup_address.model_dump(exclude={"id", "label"})),
                delivery_address=Address(name=delivery_name, street=delivery_street, city=delivery_city,
                                         post_code=delivery_post_code, country_code=delivery_country_code, phone=delivery_phone.strip()),
                billing_address=Address(), package=PackageInput(quantity=package_count, package_type=package_type,
                    length_cm=int(package_length_cm), width_cm=int(package_width_cm), height_cm=int(package_height_cm),
                    weight_kg=package_weight_kg, content=package_content), notes=notes,
                quotation=quote, cargoboard_payload=payload, quotation_token=secrets.token_urlsafe(32),
                delivery_phone_saved=True,
                quoted_at=datetime.now(timezone.utc), cargoboard_environment=settings.cargoboard_base_url.rstrip("/"),
                status="quoted",
            )
            draft_store.upsert(draft)
        return templates.TemplateResponse(request, "shipment.html", {
            "settings": settings,
            "order": order,
            "draft": draft,
            "form": form,
            "quote": quote,
            "created": None,
            "show_payload": show_payload,
            "payload_json": payload_json_for_display(payload),
            "pickup_addresses": pickup_addresses(),
            "selected_pickup_address": selected_pickup_address,
            "selected_pickup_address_id": selected_pickup_address.id if selected_pickup_address else "",
        })
    if action in {"create", "confirm"}:
        with booking_lock:
            draft = active_shipping_draft(order_id)
            if not draft:
                return failure(409, "Bitte zuerst Kosten berechnen.")
            try:
                current_payload = build_payload(order, selected_pickup_address, form)
            except (ValueError, TypeError) as exc:
                return failure(400, str(exc))
            request_payload = current_payload
            if current_payload != draft.cargoboard_payload:
                return failure(409, "Versanddaten geändert. Bitte zuerst neu berechnen.")
            try:
                validate_booking(draft, quotation_token)
                if settings.cargoboard_base_url.rstrip("/") == CARGOBOARD_URLS["production"] and action == "create":
                    draft.production_confirmation_token = secrets.token_urlsafe(32)
                    draft_store.upsert(draft)
                    return templates.TemplateResponse(request, "booking_confirm.html", {
                        "settings": settings, "order": order, "draft": draft, "form": form,
                        "selected_pickup_address": selected_pickup_address,
                        "show_payload": show_payload,
                        "payload_json": payload_json_for_display(draft.cargoboard_payload),
                    })
                book_draft(draft, quotation_token, production_confirmation_token)
            except HTTPException as exc:
                return failure(exc.status_code, exc.detail)
        return templates.TemplateResponse(request, "shipment_created.html", {
            "settings": settings, "order": order, "draft": draft, "form": form,
            "quote": draft.quotation, "created": draft.order,
            "selected_pickup_address": selected_pickup_address,
        })
    return failure(400, "Unbekannte Versandaktion.")


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
@serialized_booking_state
def update_draft(
    draft_id: str,
    delivery_name: str = Form(""),
    delivery_street: str = Form(""),
    delivery_city: str = Form(""),
    delivery_post_code: str = Form(""),
    delivery_country_code: str = Form("DE"),
    package_length_cm: int = Form(0),
    package_width_cm: int = Form(0),
    package_height_cm: int = Form(10),
    package_weight_kg: float = Form(10.0),
    package_type: str = Form("KT"),
    package_content: str = Form(""),
    notes: str = Form(""),
):
    draft = draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status in {"ordered", "booking", "booking_unknown"}:
        raise HTTPException(status_code=409, detail="Gebuchte oder unklare Aufträge können nicht bearbeitet werden.")
    draft.quotation = None
    draft.quotation_token = ""
    draft.cargoboard_payload = None
    draft.status = "draft"
    draft.delivery_address = Address(
        name=delivery_name,
        street=delivery_street,
        city=delivery_city,
        post_code=delivery_post_code,
        country_code=delivery_country_code,
        phone=draft.delivery_address.phone,
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
    draft = draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return RedirectResponse(url=f"/orders/{draft.shopify_order_id}/shipment", status_code=303)


@app.post("/drafts/{draft_id}/confirm")
def confirm_order(draft_id: str):
    raise HTTPException(status_code=409, detail="Bitte im Versandformular Kosten berechnen und dort ausdrücklich bestätigen.")


@app.post("/drafts/{draft_id}/delete")
@serialized_booking_state
def delete_draft(draft_id: str):
    draft = draft_store.get(draft_id)
    if draft and draft.status in {"ordered", "booking", "booking_unknown"}:
        raise HTTPException(status_code=409, detail="Gebuchte oder unklare Aufträge können nicht gelöscht werden.")
    draft_store.delete(draft_id)
    return RedirectResponse(url="/", status_code=303)
