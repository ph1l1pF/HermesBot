
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import Settings
from .models import Address, ShopifyOrderSummary, ShippingDraft


class ShopifyClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = f"https://{settings.shopify_shop}/admin/api/{settings.shopify_api_version}"
        self.headers = {
            "X-Shopify-Access-Token": settings.shopify_token,
            "Accept": "application/json",
        }

    def list_orders(self, limit: int = 20) -> list[ShopifyOrderSummary]:
        if not self.settings.has_shopify:
            return []
        url = f"{self.base}/orders.json"
        params = {
            "status": "any",
            "limit": limit,
            "fields": "id,name,order_number,created_at,financial_status,fulfillment_status,shipping_address,billing_address,total_weight,line_items,customer",
        }
        with httpx.Client(timeout=30.0, headers=self.headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json().get("orders", [])
        return [self._parse_order(order) for order in payload]

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


class CargoboardClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.cargoboard_base_url.rstrip("/")
        self.headers = {
            "X-API-KEY": settings.cargoboard_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def place_quotation(self, draft: ShippingDraft) -> dict[str, Any]:
        return self._post("/v1/quotations", self._build_payload(draft))

    def place_order(self, draft: ShippingDraft, quotation: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._build_payload(draft)
        if quotation:
            payload["quotationId"] = quotation.get("id") or quotation.get("quotationId")
        return self._post("/v1/orders", payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=60.0, headers=self.headers) as client:
            resp = client.post(f"{self.base}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()

    def _build_payload(self, draft: ShippingDraft) -> dict[str, Any]:
        shipper = {
            "name": self.settings.billing_name,
            "address": {
                "street": self.settings.billing_street,
                "city": self.settings.billing_city,
                "postCode": self.settings.billing_post_code,
                "countryCode": self.settings.billing_country,
            },
            "pickupOn": draft.created_at.date().isoformat(),
        }
        consignee = {
            "name": draft.delivery_address.name or draft.customer_name or "Shopify Customer",
            "address": {
                "street": draft.delivery_address.street,
                "city": draft.delivery_address.city,
                "postCode": draft.delivery_address.post_code,
                "countryCode": draft.delivery_address.country_code,
            },
        }
        lines = [{
            "unitQuantity": draft.package.quantity,
            "unitPackageType": draft.package.package_type,
            "unitLength": draft.package.length_cm,
            "unitWidth": draft.package.width_cm,
            "unitHeight": draft.package.height_cm,
            "unitWeight": draft.package.weight_kg,
            "content": draft.package.content or draft.shopify_order_name,
            "isStackable": draft.package.stackable,
            "wantsPalletExchange": draft.package.pallet_exchange,
        }]
        return {"shipper": shipper, "consignee": consignee, "lines": lines, "reference": draft.shopify_order_name}
