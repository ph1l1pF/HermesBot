
from datetime import datetime, timezone

from shopify_cargoboard_bridge.config import Settings
from shopify_cargoboard_bridge.mappers import order_to_draft
from shopify_cargoboard_bridge.models import Address, ShopifyOrderSummary


def test_order_to_draft_uses_billing_as_pickup_and_delivery_is_editable():
    settings = Settings(
        shopify_shop="example.myshopify.com",
        shopify_token="token",
        billing_name="Billing GmbH",
        billing_street="Invoice St 1",
        billing_city="Berlin",
        billing_post_code="10115",
        billing_country="DE",
    )
    order = ShopifyOrderSummary(
        id="123",
        name="#1001",
        number=1001,
        created_at=datetime.now(timezone.utc),
        customer_name="Alice Example",
        delivery_address=Address(name="Alice Example", street="Delivery St 2", city="Hamburg", post_code="20095", country_code="DE"),
    )
    draft = order_to_draft(order, settings, "draft-1")
    assert draft.pickup_address.street == "Invoice St 1"
    assert draft.delivery_address.city == "Hamburg"
    assert draft.package.quantity == 1
    assert draft.package.package_type == "KT"
    assert draft.package.weight_kg == 10
    assert draft.package.height_cm == 10
