
from types import SimpleNamespace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import shopify_cargoboard_bridge.app as appmod
from shopify_cargoboard_bridge.models import Address, ShopifyOrderSummary


def test_order_detail_page_shows_fulfillment_status(monkeypatch):
    fake_settings = SimpleNamespace(
        app_title='Test App',
        has_shopify_credentials=True,
        shopify_shop='caae00.myshopify.com',
        shopify_client_id='client',
        shopify_client_secret='secret',
        shopify_scopes='read_orders',
        shopify_access_mode='offline',
        has_cargoboard=False,
        has_billing_address=False,
    )
    fake_order = ShopifyOrderSummary(
        id='123',
        name='#1001',
        number=1001,
        created_at=datetime.now(timezone.utc),
        financial_status='paid',
        fulfillment_status='fulfilled',
        customer_name='Alice Example',
        delivery_address=Address(name='Alice Example', street='Main St 1', city='Berlin', post_code='10115', country_code='DE'),
        source={'id': 123, 'name': '#1001'},
    )
    monkeypatch.setattr(appmod, 'settings', fake_settings)
    monkeypatch.setattr(appmod, 'current_shopify_token', lambda: 'token')
    monkeypatch.setattr(appmod, 'draft_store', SimpleNamespace(all=lambda: []))
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: fake_order))
    client = TestClient(appmod.app)

    response = client.get('/orders/123')

    assert response.status_code == 200
    assert 'Fulfillment' in response.text
    assert 'fulfilled' in response.text
    assert '#1001' in response.text
    assert 'Speditionsauftrag erstellen' in response.text
