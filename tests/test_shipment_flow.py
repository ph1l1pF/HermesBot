from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import shopify_cargoboard_bridge.app as appmod
from shopify_cargoboard_bridge.models import Address, PackageInput, PickupAddress, ShopifyOrderSummary


class DraftStoreStub:
    def __init__(self, drafts):
        self._drafts = drafts
        self.saved = []

    def all(self):
        return list(self._drafts)

    def upsert(self, draft):
        self.saved.append(draft)
        return draft


class PickupAddressStoreStub:
    def __init__(self, addresses):
        self._addresses = list(addresses)
        self.saved = []
        self.deleted = []
        self.counter = 1

    def all(self):
        return list(self._addresses)

    def get(self, address_id):
        for address in self._addresses:
            if address.id == address_id:
                return address
        return None

    def upsert(self, address):
        self.saved.append(address)
        for idx, existing in enumerate(self._addresses):
            if existing.id == address.id:
                self._addresses[idx] = address
                return address
        self._addresses.append(address)
        return address

    def delete(self, address_id):
        self.deleted.append(address_id)
        self._addresses = [address for address in self._addresses if address.id != address_id]

    def new_id(self):
        value = f'pickup-{self.counter}'
        self.counter += 1
        return value


def make_order():
    return ShopifyOrderSummary(
        id='123',
        name='#1001',
        number=1001,
        created_at=datetime.now(timezone.utc),
        financial_status='paid',
        fulfillment_status='partial',
        customer_name='Alice Example',
        delivery_address=Address(name='Alice Example', street='Main St 1', city='Berlin', post_code='10115', country_code='DE'),
        source={'id': 123, 'name': '#1001'},
    )


def make_draft():
    return SimpleNamespace(
        shopify_order_id='123',
        delivery_address=Address(name='Alice Example', street='Main St 1', city='Berlin', post_code='10115', country_code='DE'),
        package=PackageInput(quantity=1, package_type='PA', length_cm=120, width_cm=80, height_cm=80, weight_kg=25.0, content='Test', stackable=False, pallet_exchange=False),
        order=None,
        status='draft',
        id='draft-1',
    )


def setup_app(monkeypatch, draft_store=None, pickup_store=None):
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
    monkeypatch.setattr(appmod, 'settings', fake_settings)
    monkeypatch.setattr(appmod, 'current_shopify_token', lambda: 'token')
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: make_order()))
    monkeypatch.setattr(appmod, 'draft_store', draft_store or DraftStoreStub([]))
    monkeypatch.setattr(appmod, 'pickup_address_store', pickup_store or PickupAddressStoreStub([]))


def test_shipment_flow_page_shows_calculation_form(monkeypatch):
    pickup_store = PickupAddressStoreStub([
        PickupAddress(id='pickup-1', label='Lager', name='Lager GmbH', street='Hafenstraße 1', city='Hamburg', post_code='20457', country_code='DE')
    ])
    setup_app(monkeypatch, DraftStoreStub([make_draft()]), pickup_store)
    client = TestClient(appmod.app)

    response = client.get('/orders/123/shipment')

    assert response.status_code == 200
    assert 'Speditionsauftrag' in response.text
    assert 'Kosten berechnen' in response.text
    assert 'Paket & Service' in response.text
    assert 'Gespeicherte Abholadresse' in response.text
    assert 'Lager' in response.text


def test_shipment_flow_quote_and_create(monkeypatch):
    pickup_store = PickupAddressStoreStub([
        PickupAddress(id='pickup-1', label='Lager', name='Lager GmbH', street='Hafenstraße 1', city='Hamburg', post_code='20457', country_code='DE')
    ])
    store = DraftStoreStub([make_draft()])
    setup_app(monkeypatch, store, pickup_store)
    client = TestClient(appmod.app)
    payload = {
        'action': 'quote',
        'pickup_address_id': 'pickup-1',
        'delivery_name': 'Alice Example',
        'delivery_street': 'Main St 1',
        'delivery_post_code': '10115',
        'delivery_city': 'Berlin',
        'delivery_country_code': 'DE',
        'package_length_cm': '120',
        'package_width_cm': '80',
        'package_height_cm': '80',
        'package_weight_kg': '25',
        'package_count': '1',
        'package_type': 'PA',
        'package_content': 'Test',
        'service_level': 'express',
        'pickup_date': '2026-01-01',
        'insurance_value_eur': '1000',
        'liftgate_required': 'on',
        'notes': 'Handle with care',
    }

    quote_response = client.post('/orders/123/shipment', data=payload)
    assert quote_response.status_code == 200
    assert 'Berechnete Frachtkosten' in quote_response.text
    assert 'Gesamt:' in quote_response.text
    assert 'Lager GmbH' in quote_response.text

    create_payload = dict(payload)
    create_payload['action'] = 'create'
    create_response = client.post('/orders/123/shipment', data=create_payload)
    assert create_response.status_code == 200
    assert 'Auftrag erstellt' in create_response.text
    assert 'mock-order-123' in create_response.text
    assert 'Verwendete Abholadresse' in create_response.text
    assert store.saved
    assert store.saved[-1].status == 'ordered'
    assert store.saved[-1].order['status'] == 'created'


def test_pickup_address_page_creates_and_lists_addresses(monkeypatch):
    pickup_store = PickupAddressStoreStub([])
    setup_app(monkeypatch, DraftStoreStub([]), pickup_store)
    client = TestClient(appmod.app)

    create_response = client.post('/pickup-addresses', data={
        'label': 'Lager Heepen',
        'name': 'Firma GmbH',
        'street': 'Industriestraße 5',
        'city': 'Bielefeld',
        'post_code': '33719',
        'country_code': 'DE',
    })
    assert create_response.status_code in (200, 303)
    page = client.get('/pickup-addresses')
    assert page.status_code == 200
    assert 'Lager Heepen' in page.text
    assert 'Industriestraße 5' in page.text
