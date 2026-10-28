from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import shopify_cargoboard_bridge.app as appmod
from shopify_cargoboard_bridge.clients import CargoboardClient, CargoboardError
from shopify_cargoboard_bridge.models import PickupAddress
from shopify_cargoboard_bridge.shipping import next_pickup_date
from shopify_cargoboard_bridge.store import DraftStore
from test_shipment_flow import setup_app, PickupAddressStoreStub


def test_client_uses_documented_envelope_and_booking_endpoint(monkeypatch):
    requests = []
    real_client = httpx.Client
    def handle(request):
        requests.append(request)
        return httpx.Response(201, json={'data': {'id': 'q-1', 'price': {'amount': 89.5, 'currency': 'EUR'}}})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = CargoboardClient(SimpleNamespace(cargoboard_base_url='https://api.cargoboard.com', cargoboard_api_key='test-key'))
    quote = client.place_quotation({'product': 'STANDARD'})
    client.place_order({'product': 'STANDARD'}, quote)
    assert requests[0].headers['X-API-KEY'] == 'test-key'
    assert str(requests[1].url) == 'https://api.cargoboard.com/v1/quotations/q-1/book'
    assert json.loads(requests[1].content) == {'product': 'STANDARD'}


@pytest.mark.parametrize('status', [403, 409, 422, 429, 502])
def test_client_errors_do_not_expose_remote_body_or_key(monkeypatch, status):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text='test-secret')), **kw))
    client = CargoboardClient(SimpleNamespace(cargoboard_base_url='https://api.cargoboard.com', cargoboard_api_key='test-secret'))
    with pytest.raises(CargoboardError) as err:
        client.place_quotation({})
    assert 'test-secret' not in str(err.value)


@pytest.fixture
def flow(monkeypatch, tmp_path):
    store = DraftStore(tmp_path / 'state.json')
    setup_app(monkeypatch, store, PickupAddressStoreStub([PickupAddress(id='p1', name='Lager', street='Hafen 1', city='Hamburg', post_code='20457')]))
    calls = []
    def quote(payload):
        calls.append('quote')
        return {'id': 'q1', 'product': 'STANDARD', 'price': {'amount': 100, 'currency': 'EUR'}, 'costItems': [], 'pickupOn': payload['shipper']['pickupOn']}
    def book(payload, quotation):
        calls.append('book')
        return {'id': 'o1', 'reference': 'ref1', 'product': 'STANDARD', 'price': {'amount': 100, 'currency': 'EUR'}}
    api = SimpleNamespace(place_quotation=quote, place_order=book)
    monkeypatch.setattr(appmod, 'cargoboard_client', lambda: api)
    payload = dict(action='quote', pickup_address_id='p1', delivery_name='Empfänger', delivery_street='Weg 1',
                   delivery_city='Berlin', delivery_post_code='10115', delivery_country_code='DE',
                   package_length_cm='120', package_width_cm='80', package_height_cm='80', package_weight_kg='20',
                   package_count='1', package_type='FP', package_content='Ware', service_level='standard', pickup_date=next_pickup_date())
    return TestClient(appmod.app), payload, store, api, calls


def quote_then_confirm(flow):
    client, payload, store, api, calls = flow
    assert client.post('/orders/123/shipment', data=payload).status_code == 200
    draft = store.all()[0]
    return dict(payload, action='create', quotation_token=draft.quotation_token)


@pytest.mark.parametrize('change', [{'package_weight_kg': '21'}, {'quotation_token': 'wrong'}, {'delivery_street': 'Andere Straße 2'}])
def test_changed_or_unconfirmed_data_never_books(flow, change):
    confirmation = quote_then_confirm(flow)
    confirmation.update(change)
    client, _, store, _, calls = flow
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote']
    assert store.all()[0].status == 'quoted'


def test_missing_quote_and_legacy_endpoint_never_book(flow):
    client, payload, _, _, calls = flow
    assert client.post('/orders/123/shipment', data=dict(payload, action='create')).status_code == 409
    assert client.post('/drafts/any/confirm').status_code == 409
    assert not calls


def test_ambiguous_booking_persists_block_and_cannot_be_retried(flow):
    confirmation = quote_then_confirm(flow)
    client, payload, store, api, calls = flow
    def fail(*args):
        calls.append('book')
        raise CargoboardError('Keine eindeutige Antwort')
    api.place_order = fail
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 502
    assert store.all()[0].status == 'booking_unknown'
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert client.post('/orders/123/shipment', data=payload).status_code == 409
    assert client.post(f'/drafts/{store.all()[0].id}/delete').status_code == 409
    assert calls == ['quote', 'book']


def test_stale_quote_cannot_book(flow):
    confirmation = quote_then_confirm(flow)
    client, _, store, _, calls = flow
    draft = store.all()[0]
    draft.quoted_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    store.upsert(draft)
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote']


@pytest.mark.parametrize('change', [{'pickup_date': '2020-01-01'}, {'package_type': 'PL'}, {'package_weight_kg': 'nan'}, {'package_count': '0'}, {'private_customer': 'on'}])
def test_invalid_input_does_not_reach_api(flow, change):
    client, payload, _, _, calls = flow
    assert client.post('/orders/123/shipment', data=dict(payload, **change)).status_code == 400
    assert not calls


def test_private_recipient_requests_notification(flow):
    client, payload, store, _, calls = flow
    response = client.post('/orders/123/shipment', data=dict(payload, private_customer='on', delivery_phone='+4930123456'))
    assert response.status_code == 200
    consignee = store.all()[0].cargoboard_payload['consignee']
    assert consignee['isPrivateCustomer'] is True
    assert consignee['wantsContactBeforeDelivery'] is True
    assert consignee['contactPerson']['phone'] == '+4930123456'


def test_connection_check_only_reads_api(flow):
    client, _, _, api, calls = flow
    api.check_connection = lambda: calls.append('check')
    response = client.post('/cargoboard/check')
    assert response.status_code == 200
    assert response.json() == {'connected': True, 'environment': 'Sandbox'}
    assert calls == ['check']


@pytest.mark.parametrize('error_type, expected', [
    (httpx.ReadTimeout, 'Zeitüberschreitung'),
    (httpx.ConnectError, 'Netzwerkfehler'),
])
def test_network_errors_have_distinct_safe_messages(monkeypatch, error_type, expected):
    real_client = httpx.Client
    def fail(request):
        raise error_type('sensitive transport information test-secret', request=request)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw))
    client = CargoboardClient(SimpleNamespace(cargoboard_base_url='https://api.cargoboard.com', cargoboard_api_key='test-secret'))
    with pytest.raises(CargoboardError) as err:
        client.check_connection()
    assert expected in str(err.value)
    assert 'test-secret' not in str(err.value)


def test_unexpected_http_status_is_visible_without_remote_content(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(lambda req: httpx.Response(504, text='test-secret')), **kw))
    client = CargoboardClient(SimpleNamespace(cargoboard_base_url='https://api.cargoboard.com', cargoboard_api_key='test-secret'))
    with pytest.raises(CargoboardError, match='HTTP 504') as err:
        client.check_connection()
    assert 'test-secret' not in str(err.value)


def test_browser_connection_error_is_html_with_utf8(flow):
    client, _, _, api, _ = flow
    def fail():
        raise CargoboardError('Zeitüberschreitung: Verbindung nicht bestätigt.')
    api.check_connection = fail
    response = client.post('/cargoboard/check', headers={'Accept': 'text/html'})
    assert response.status_code == 502
    assert response.headers['content-type'] == 'text/html; charset=utf-8'
    assert 'Zeitüberschreitung' in response.text
    assert 'Zurück zu HermesBot' in response.text
    response = client.post('/cargoboard/check', headers={'Accept': 'application/json'})
    assert response.headers['content-type'] == 'application/json; charset=utf-8'
    assert response.json()['detail'] == 'Zeitüberschreitung: Verbindung nicht bestätigt.'


def test_browser_connection_success_is_html(flow):
    client, _, _, api, _ = flow
    api.check_connection = lambda: None
    response = client.post('/cargoboard/check', headers={'Accept': 'text/html'})
    assert response.status_code == 200
    assert 'Verbindung erfolgreich' in response.text


def test_validation_details_are_extracted_and_secret_is_redacted(monkeypatch):
    real_client = httpx.Client
    def fail(request):
        return httpx.Response(422, json={'message': ['shipper.address.postCode must be a valid postal code',
                                                   'rejected test-secret'], 'debug': 'test-secret'})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw))
    client = CargoboardClient(SimpleNamespace(cargoboard_base_url='https://api.cargoboard.com', cargoboard_api_key='test-secret'))
    with pytest.raises(CargoboardError) as err:
        client.place_quotation({})
    assert err.value.status_code == 422
    assert err.value.validation_errors[0] == 'PLZ der Abholadresse must be a valid postal code'
    assert 'test-secret' not in str(err.value.validation_errors)


def test_quote_error_preserves_form_and_escapes_validation_message(flow):
    client, payload, _, api, _ = flow
    def fail(payload):
        raise CargoboardError('Versanddaten abgelehnt (HTTP 422)', status_code=422,
                             validation_errors=['PLZ der Abholadresse ungültig', '<script>alert(1)</script>'])
    api.place_quotation = fail
    response = client.post('/orders/123/shipment', data=payload, headers={'Accept': 'text/html'})
    assert response.status_code == 422
    assert response.headers['content-type'] == 'text/html; charset=utf-8'
    assert 'PLZ der Abholadresse ungültig' in response.text
    assert 'value="Empfänger"' in response.text
    assert 'value="120.0"' in response.text
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;' in response.text
    assert 'name="quotation_token"' not in response.text
    response = client.post('/orders/123/shipment', data=payload)
    assert response.json()['validation_errors'][0] == 'PLZ der Abholadresse ungültig'


def test_invalid_pickup_postcode_is_caught_before_quote(flow):
    client, payload, _, _, calls = flow
    pickup = appmod.pickup_address_store.get('p1')
    pickup.post_code = '336154'
    response = client.post('/orders/123/shipment', data=payload, headers={'Accept': 'text/html'})
    assert response.status_code == 400
    assert 'Abholadresse: Eine deutsche Postleitzahl muss aus genau fünf Ziffern bestehen.' in response.text
    assert not calls


@pytest.mark.parametrize('contact', [{'delivery_phone': '+4930123456'}, {'delivery_email': 'receiver@example.com'}])
def test_empty_optional_contact_fields_are_not_sent(flow, contact):
    client, payload, store, _, _ = flow
    assert client.post('/orders/123/shipment', data=dict(payload, private_customer='on', **contact)).status_code == 200
    person = store.all()[0].cargoboard_payload['consignee']['contactPerson']
    assert all(person.values())
    assert ('phone' in person) == ('delivery_phone' in contact)
    assert ('email' in person) == ('delivery_email' in contact)


def test_bad_pickup_postcode_cannot_be_saved(flow):
    client, _, _, _, _ = flow
    response = client.post('/pickup-addresses', data={'name': 'Lager', 'post_code': '336154', 'country_code': 'DE'})
    assert response.status_code == 400
    assert response.headers['content-type'] == 'application/json; charset=utf-8'
    assert 'fünf Ziffern' in response.json()['detail']


def test_packaging_choice_is_explicit_and_transmitted_unchanged(flow):
    client, payload, store, _, _ = flow
    page = client.get('/orders/123/shipment')
    assert 'Sonstiges Packstück' in page.text
    assert 'Für eine Sendung im Karton' in page.text
    assert client.post('/orders/123/shipment', data=dict(payload, package_type='KT')).status_code == 200
    assert store.all()[0].cargoboard_payload['lines'][0]['unitPackageType'] == 'KT'
    assert client.post('/orders/123/shipment', data=dict(payload, package_type='PA')).status_code == 200
    assert store.all()[0].cargoboard_payload['lines'][0]['unitPackageType'] == 'PA'


def test_pickup_phone_is_saved_and_passed_through_to_booking(flow, monkeypatch, tmp_path):
    from shopify_cargoboard_bridge.store import PickupAddressStore
    client, payload, drafts, api, _ = flow
    store = PickupAddressStore(tmp_path / 'pickups.json')
    store.upsert(PickupAddress(id='p1', name='Lager', street='Hafen 1', city='Hamburg', post_code='20457'))
    monkeypatch.setattr(appmod, 'pickup_address_store', store)
    response = client.post('/pickup-addresses/p1/phone', data={'phone': '  +49 661 123456  '})
    assert response.status_code == 200
    assert 'value="+49 661 123456"' in response.text
    assert PickupAddressStore(store.path).get('p1').phone == '+49 661 123456'
    confirmation = quote_then_confirm(flow)
    draft = drafts.all()[0]
    expected = {'name': 'Lager', 'phone': '+49 661 123456'}
    assert draft.cargoboard_payload['shipper']['contactPerson'] == expected
    assert draft.pickup_address.phone == '+49 661 123456'
    assert not draft.cargoboard_payload['shipper'].get('wantsContactBeforePickup')
    sent = []
    original = api.place_order
    def book(body, quote):
        sent.append(body)
        return original(body, quote)
    api.place_order = book
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 200
    assert sent[0]['shipper']['contactPerson'] == expected


def test_phone_changes_require_new_quote_and_can_be_cleared(flow):
    client, _, store, _, calls = flow
    confirmation = quote_then_confirm(flow)
    assert 'contactPerson' not in store.all()[0].cargoboard_payload['shipper']
    assert client.post('/pickup-addresses/p1/phone', data={'phone': '+49 661 123456'}).status_code == 200
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote']
    assert client.post('/pickup-addresses/p1/phone', data={'phone': ''}).status_code == 200
    assert appmod.pickup_address_store.get('p1').phone == ''
    assert client.post('/pickup-addresses/missing/phone', data={'phone': '123'}).status_code == 404


def test_new_pickup_phone_and_old_address_compatibility(flow, tmp_path):
    from shopify_cargoboard_bridge.store import PickupAddressStore
    client, _, _, _, _ = flow
    response = client.post('/pickup-addresses', data={'name': 'Neues Lager', 'post_code': '36154', 'city': 'Hosenfeld',
                          'street': 'Weg 1', 'country_code': 'DE', 'phone': '+49 661 123456'})
    assert response.status_code == 200
    assert any(address.phone == '+49 661 123456' for address in appmod.pickup_address_store.all())
    path = tmp_path / 'legacy-pickups.json'
    path.write_text('[{"id":"legacy","name":"Altes Lager","post_code":"36154"}]')
    assert PickupAddressStore(path).get('legacy').phone == ''


@pytest.mark.parametrize('shipping_phone, order_phone, customer_phone, expected', [
    (' +49 30 111 ', '+49 30 222', '+49 30 333', '+49 30 111'),
    (None, '+49 30 222', '+49 30 333', '+49 30 222'),
    ('  ', None, '+49 30 333', '+49 30 333'),
    (None, None, None, ''),
])
def test_shopify_phone_is_requested_and_mapped(monkeypatch, shipping_phone, order_phone, customer_phone, expected):
    from shopify_cargoboard_bridge.clients import ShopifyClient
    order = {'id': 123, 'name': '#123', 'order_number': 123, 'created_at': '2026-10-19T10:00:00Z',
             'shipping_address': {'first_name': 'Receiver', 'phone': shipping_phone},
             'phone': order_phone, 'customer': {'phone': customer_phone}}
    real_client = httpx.Client
    def handle(request):
        assert 'phone' in request.url.params['fields'].split(',')
        return httpx.Response(200, json={'order': order} if request.url.path.endswith('/123.json') else {'orders': [order]})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = ShopifyClient(SimpleNamespace(shopify_shop='test.myshopify.com', shopify_api_version='2026-07'), 'test-token')
    assert client.get_order('123').delivery_address.phone == expected
    assert client.list_orders()[0].delivery_address.phone == expected


def test_shopify_delivery_phone_is_editable_and_sent_for_business_recipient(flow, monkeypatch):
    from test_shipment_flow import make_order
    client, payload, drafts, api, calls = flow
    order = make_order()
    order.delivery_address.phone = '+49 30 111'
    order.source['shipping_address'] = {'company': 'Business', 'phone': '+49 30 111'}
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: order))
    page = client.get('/orders/123/shipment')
    assert 'name="delivery_phone" value="+49 30 111"' in page.text
    payload['delivery_phone'] = '+49 30 999'
    confirmation = quote_then_confirm(flow)
    draft = drafts.all()[0]
    assert draft.delivery_address.phone == '+49 30 999'
    contact = draft.cargoboard_payload['consignee']['contactPerson']
    assert contact['phone'] == '+49 30 999'
    assert not draft.cargoboard_payload['consignee'].get('wantsContactBeforeDelivery')
    sent = []
    original = api.place_order
    def book(body, quotation):
        sent.append(body)
        return original(body, quotation)
    api.place_order = book
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 200
    assert sent[0]['consignee']['contactPerson']['phone'] == '+49 30 999'


@pytest.mark.parametrize('manual_phone', ['+49 30 999', ''])
def test_saved_manual_delivery_phone_is_not_overwritten_on_reload(flow, monkeypatch, manual_phone):
    from test_shipment_flow import make_order
    client, payload, _, _, _ = flow
    order = make_order()
    order.source['shipping_address'] = {'phone': '+49 30 111'}
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: order))
    payload['delivery_phone'] = manual_phone
    quote_then_confirm(flow)
    page = client.get('/orders/123/shipment')
    assert f'name="delivery_phone" value="{manual_phone}"' in page.text


def test_old_quote_without_phone_still_imports_new_shopify_phone(flow, monkeypatch):
    from test_shipment_flow import make_order
    client, payload, drafts, _, _ = flow
    quote_then_confirm(flow)
    draft = drafts.all()[0]
    draft.delivery_phone_saved = False
    draft.delivery_address.phone = ''
    drafts.upsert(draft)
    order = make_order()
    order.source['phone'] = '+49 30 222'
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: order))
    page = client.get('/orders/123/shipment')
    assert 'name="delivery_phone" value="+49 30 222"' in page.text


def test_customer_default_address_phone_is_used_in_shipment_form(flow, monkeypatch):
    from shopify_cargoboard_bridge.clients import ShopifyClient
    client, payload, store, _, _ = flow
    source = {'id': 123, 'name': '#1001', 'order_number': 1001, 'created_at': '2026-10-19T10:00:00Z',
              'shipping_address': {'first_name': 'Receiver', 'phone': None, 'address1': 'Weg 1', 'city': 'Berlin', 'zip': '10115', 'country_code': 'DE'},
              'phone': None, 'customer': {'phone': None, 'default_address': {'phone': '+49 30 555'}}}
    shopify = ShopifyClient(SimpleNamespace(shopify_shop='test.myshopify.com', shopify_api_version='2026-07'), 'test-token')
    order = shopify._parse_order(source)
    assert order.delivery_address.phone == '+49 30 555'
    monkeypatch.setattr(appmod, 'shopify_client', lambda: SimpleNamespace(get_order=lambda order_id: order))
    page = client.get('/orders/123/shipment')
    assert 'name="delivery_phone" value="+49 30 555"' in page.text
    payload['delivery_phone'] = '+49 30 555'
    quote_then_confirm(flow)
    assert store.all()[0].cargoboard_payload['consignee']['contactPerson']['phone'] == '+49 30 555'


def test_customer_default_phone_is_last_fallback():
    from shopify_cargoboard_bridge.clients import delivery_phone_from_shopify
    source = {'shipping_address': {'phone': '+49 30 111'}, 'phone': '+49 30 222',
              'customer': {'phone': '+49 30 333', 'default_address': {'phone': ' +49 30 444 '}}}
    assert delivery_phone_from_shopify(source) == '+49 30 111'
    source['shipping_address']['phone'] = None
    assert delivery_phone_from_shopify(source) == '+49 30 222'
    source['phone'] = None
    assert delivery_phone_from_shopify(source) == '+49 30 333'
    source['customer']['phone'] = None
    assert delivery_phone_from_shopify(source) == '+49 30 444'
