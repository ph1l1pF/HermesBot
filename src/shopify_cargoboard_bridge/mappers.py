
from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .models import Address, PackageInput, ShopifyOrderSummary, ShippingDraft


def order_to_draft(order: ShopifyOrderSummary, settings: Settings, draft_id: str) -> ShippingDraft:
    billing = Address(
        name=settings.billing_name,
        street=settings.billing_street,
        city=settings.billing_city,
        post_code=settings.billing_post_code,
        country_code=settings.billing_country,
    )
    delivery = order.delivery_address or Address()
    return ShippingDraft(
        id=draft_id,
        shopify_order_id=order.id,
        shopify_order_name=order.name,
        shopify_order_number=order.number,
        created_at=datetime.now(timezone.utc),
        customer_name=order.customer_name,
        pickup_address=billing,
        delivery_address=delivery,
        billing_address=billing,
        package=PackageInput(quantity=1, package_type="PA", content=order.name),
        notes="",
        status="draft",
    )
