
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import Settings, normalize_shop_domain
from .models import Address, ShopifyOrderSummary


def delivery_phone_from_shopify(order: dict[str, Any]) -> str:
    shipping = order.get("shipping_address") or {}
    customer = order.get("customer") or {}
    default_address = customer.get("default_address") or {}
    for candidate in (shipping.get("phone"), order.get("phone"), customer.get("phone"), default_address.get("phone")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


class ShopifyClient:
    def __init__(self, settings: Settings, access_token: str):
        self.settings = settings
        self.access_token = access_token.strip()
        self.shop = normalize_shop_domain(settings.shopify_shop)
        self.base = f"https://{self.shop}/admin/api/{settings.shopify_api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Accept": "application/json",
        }

    @property
    def ready(self) -> bool:
        return bool(self.shop and self.access_token)

    def list_orders(self, limit: int = 20) -> list[ShopifyOrderSummary]:
        if not self.ready:
            return []
        url = f"{self.base}/orders.json"
        params = {
            "status": "any",
            "limit": limit,
            "fields": "id,name,order_number,created_at,financial_status,fulfillment_status,shipping_address,billing_address,total_weight,line_items,customer,email,phone",
        }
        with httpx.Client(timeout=30.0, headers=self.headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json().get("orders", [])
        return [self._parse_order(order) for order in payload]

    def get_order(self, order_id: str) -> ShopifyOrderSummary:
        if not self.ready:
            raise ValueError("Shopify client is not ready")
        url = f"{self.base}/orders/{order_id}.json"
        params = {
            "fields": "id,name,order_number,created_at,financial_status,fulfillment_status,shipping_address,billing_address,total_weight,line_items,customer,email,phone,note,tags,cancel_reason,cancelled_at,closed_at,fulfillments",
        }
        with httpx.Client(timeout=30.0, headers=self.headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            order = resp.json().get("order") or {}
        return self._parse_order(order)

    def _parse_order(self, order: dict[str, Any]) -> ShopifyOrderSummary:
        shipping = order.get("shipping_address") or {}
        customer = order.get("customer") or {}
        delivery = None
        if shipping:
            delivery = Address(
                name=" ".join(filter(None, [shipping.get("first_name", ""), shipping.get("last_name", "")])).strip(),
                street=" ".join(filter(None, [shipping.get("address1", ""), shipping.get("address2", "")])).strip(),
                city=shipping.get("city", ""),
                post_code=shipping.get("zip", ""),
                country_code=shipping.get("country_code", "DE"),
                phone=delivery_phone_from_shopify(order),
            )
        customer_name = " ".join(filter(None, [customer.get("first_name", ""), customer.get("last_name", "")])).strip()
        created_at = str(order.get("created_at", "")).replace("Z", "+00:00")
        return ShopifyOrderSummary(
            id=str(order.get("id")),
            name=order.get("name", ""),
            number=int(order.get("order_number") or 0),
            created_at=datetime.fromisoformat(created_at),
            financial_status=order.get("financial_status") or "",
            fulfillment_status=order.get("fulfillment_status") or "",
            customer_name=customer_name,
            delivery_address=delivery,
            source=order,
        )


class CargoboardError(Exception):
    """Safe, user-facing error without credentials or remote response bodies."""

    def __init__(self, message: str, *, status_code: int | None = None, validation_errors: list[str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.validation_errors = validation_errors or []


def validation_messages(response: httpx.Response, api_key: str) -> list[str]:
    """Read only documented validation messages; never expose full responses."""
    try:
        body = response.json()
    except ValueError:
        return []
    messages = body.get("message") if isinstance(body, dict) else None
    if isinstance(messages, str):
        messages = [messages]
    if not isinstance(messages, list):
        return []
    labels = {
        "shipper.address.postCode": "PLZ der Abholadresse",
        "shipper.address.countryCode": "Land der Abholadresse",
        "shipper.address.street": "Straße der Abholadresse",
        "shipper.address.city": "Ort der Abholadresse",
        "shipper.pickupOn": "Abholdatum",
        "consignee.address.postCode": "PLZ der Lieferadresse",
        "consignee.address.countryCode": "Land der Lieferadresse",
        "consignee.contactPerson.phone": "Telefon des Empfängers",
        "consignee.contactPerson.email": "E-Mail des Empfängers",
    }
    result = []
    for message in messages[:10]:
        if not isinstance(message, str):
            continue
        if api_key:
            message = message.replace(api_key, "[API-Key entfernt]")
        message = " ".join(message.split())[:500]
        for field, label in labels.items():
            message = message.replace(field, label)
        if message:
            result.append(message)
    return result


class CargoboardClient:
    def __init__(self, settings: Settings):
        self.base = settings.cargoboard_base_url.rstrip("/")
        if self.base not in {"https://api.cargoboard.com", "https://api-sandbox.cargoboard.com"}:
            raise CargoboardError("Unbekannte Cargoboard-API-Adresse.")
        self.headers = {"X-API-KEY": settings.cargoboard_api_key, "Accept": "application/json"}

    def check_connection(self) -> None:
        self._request("GET", "/v1/quotations?take=1")

    def place_quotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/quotations", payload)

    def place_order(self, payload: dict[str, Any], quotation: dict[str, Any]) -> dict[str, Any]:
        from urllib.parse import quote
        quotation_id = quotation.get("id")
        if not isinstance(quotation_id, str) or not quotation_id:
            raise CargoboardError("Bitte zuerst ein neues Angebot berechnen.")
        return self._request("POST", f"/v1/quotations/{quote(quotation_id, safe='')}/book", payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=15.0 if method == "GET" else 60.0, headers=self.headers) as client:
                resp = client.request(method, f"{self.base}{path}", json=payload)
                resp.raise_for_status()
                body = resp.json()
            data = body.get("data") if isinstance(body, dict) else None
            if data is None:
                raise ValueError("Missing response data")
            if method == "GET":
                return {}
            if not isinstance(data, dict) or not data.get("id") or not isinstance(data.get("price"), dict):
                raise ValueError("Invalid response data")
            price = data["price"]
            if not isinstance(price.get("amount"), (int, float)) or price.get("currency") != "EUR":
                raise ValueError("Invalid price")
            return data
        except httpx.HTTPStatusError as exc:
            messages = {
                401: "Cargoboard hat die Anmeldung abgelehnt. Bitte API-Key und Zielsystem prüfen.",
                403: "API-Key ungültig oder für diese Umgebung/Aktion nicht freigeschaltet.",
                404: "Cargoboard-Angebot nicht gefunden. Bitte neu berechnen.",
                409: "API-Key ist keinem freigeschalteten Kundenkonto zugeordnet.",
                422: "Cargoboard hat die Versanddaten abgelehnt. Bitte Adressen, Datum und Packstücke prüfen.",
                429: "Zu viele Cargoboard-Anfragen. Bitte später erneut versuchen.",
            }
            status = exc.response.status_code
            message = messages.get(status, "Cargoboard meldet einen Server- oder Schnittstellenfehler.")
            details = validation_messages(exc.response, self.headers["X-API-KEY"]) if status == 422 else []
            raise CargoboardError(f"{message} (HTTP {status})", status_code=status, validation_errors=details) from None
        except httpx.TimeoutException:
            raise CargoboardError(
                "Zeitüberschreitung: Cargoboard hat nicht rechtzeitig geantwortet. "
                "Der API-Key konnte dadurch noch nicht bestätigt werden."
            ) from None
        except httpx.RequestError:
            raise CargoboardError("Netzwerkfehler: Die Verbindung zu Cargoboard konnte nicht abgeschlossen werden.") from None
        except (ValueError, TypeError):
            raise CargoboardError("Cargoboard hat eine unerwartete Antwort gesendet. Die Verbindung konnte nicht bestätigt werden.") from None
