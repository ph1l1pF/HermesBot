from types import SimpleNamespace

import pytest

import shopify_cargoboard_bridge.app as appmod
from shopify_cargoboard_bridge.config import Settings, CARGOBOARD_URLS
from shopify_cargoboard_bridge.environments import load_mode, save_mode
from test_cargoboard import flow, quote_then_confirm


@pytest.fixture
def production_flow(flow):
    appmod.settings.cargoboard_environment = 'Produktion'
    appmod.settings.cargoboard_base_url = CARGOBOARD_URLS['production']
    return flow


def test_production_requires_preview_then_explicit_confirmation(production_flow):
    client, _, store, _, calls = production_flow
    create = quote_then_confirm(production_flow)
    # Merely knowing the quotation token cannot execute a production booking.
    assert client.post('/orders/123/shipment', data=dict(create, action='confirm')).status_code == 409
    assert calls == ['quote']
    preview = client.post('/orders/123/shipment', data=create)
    assert preview.status_code == 200
    assert 'Kostenpflichtigen Transport verbindlich buchen?' in preview.text
    assert 'Ja, kostenpflichtig buchen' in preview.text
    assert 'Gesamt: 100.00 EUR netto' in preview.text
    assert store.all()[0].status == 'quoted'
    assert calls == ['quote']
    token = store.all()[0].production_confirmation_token
    assert token
    assert client.post('/orders/123/shipment', data=dict(create, action='confirm', production_confirmation_token='wrong')).status_code == 409
    confirmation = dict(create, action='confirm', production_confirmation_token=token)
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 200
    assert store.all()[0].status == 'ordered'
    assert store.all()[0].production_confirmation_token == ''
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote', 'book']


def test_modified_production_confirmation_cannot_book(production_flow):
    client, _, store, _, calls = production_flow
    create = quote_then_confirm(production_flow)
    client.post('/orders/123/shipment', data=create)
    confirmation = dict(create, action='confirm', production_confirmation_token=store.all()[0].production_confirmation_token,
                        package_weight_kg='21')
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote']


def test_new_quote_invalidates_previous_production_confirmation(production_flow):
    client, payload, store, _, calls = production_flow
    create = quote_then_confirm(production_flow)
    client.post('/orders/123/shipment', data=create)
    confirmation = dict(create, action='confirm', production_confirmation_token=store.all()[0].production_confirmation_token)
    assert client.post('/orders/123/shipment', data=payload).status_code == 200
    assert not store.all()[0].production_confirmation_token
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote', 'quote']


def test_keys_are_bound_to_their_environment():
    s = Settings(cargoboard_base_url=CARGOBOARD_URLS['production'], cargoboard_api_key='legacy-production',
                 cargoboard_production_api_key='', cargoboard_sandbox_api_key='')
    assert s.for_cargoboard_environment('production').cargoboard_api_key == 'legacy-production'
    assert not s.for_cargoboard_environment('test').has_cargoboard
    s = Settings(cargoboard_api_key='', cargoboard_production_api_key='prod-key', cargoboard_sandbox_api_key='test-key')
    assert s.for_cargoboard_environment('production').cargoboard_api_key == 'prod-key'
    assert s.for_cargoboard_environment('test').cargoboard_api_key == 'test-key'
    assert s.for_cargoboard_environment('test').cargoboard_base_url == CARGOBOARD_URLS['test']


def configure_switching(monkeypatch, tmp_path, mode='production', sandbox_key='test-key'):
    configured = Settings(cargoboard_api_key='', cargoboard_production_api_key='prod-key', cargoboard_sandbox_api_key=sandbox_key)
    monkeypatch.setattr(appmod, 'configured_settings', configured)
    monkeypatch.setattr(appmod, 'settings', configured.for_cargoboard_environment(mode))
    monkeypatch.setattr(appmod, 'environment_path', tmp_path / 'environment.json')
    return configured


def switch(client, mode):
    return client.post('/cargoboard/environment', data={'environment': mode, 'switch_token': appmod.environment_switch_token}, follow_redirects=False)


def test_mode_changes_invalidate_quotes_and_confirmation_tokens(production_flow, monkeypatch, tmp_path):
    client, _, store, _, calls = production_flow
    configure_switching(monkeypatch, tmp_path)
    create = quote_then_confirm(production_flow)
    client.post('/orders/123/shipment', data=create)
    confirmation = dict(create, action='confirm', production_confirmation_token=store.all()[0].production_confirmation_token)
    assert switch(client, 'test').status_code == 303
    assert appmod.settings.cargoboard_api_key == 'test-key'
    assert store.all()[0].status == 'draft'
    assert not store.all()[0].quotation_token
    assert not store.all()[0].production_confirmation_token
    assert load_mode(appmod.environment_path, 'production') == 'test'
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert switch(client, 'production').status_code == 303
    assert appmod.settings.cargoboard_api_key == 'prod-key'
    assert client.post('/orders/123/shipment', data=confirmation).status_code == 409
    assert calls == ['quote']


def test_sandbox_booking_does_not_block_production(flow, monkeypatch, tmp_path):
    client, payload, store, _, calls = flow
    configure_switching(monkeypatch, tmp_path, mode='test')
    create = quote_then_confirm(flow)
    assert client.post('/orders/123/shipment', data=create).status_code == 200
    test_draft = store.all()[0]
    assert test_draft.status == 'ordered'
    assert switch(client, 'production').status_code == 303
    assert client.post('/orders/123/shipment', data=payload).status_code == 200
    assert len(store.all()) == 2
    production = appmod.active_shipping_draft('123')
    assert production.id != test_draft.id
    assert production.cargoboard_environment == CARGOBOARD_URLS['production']
    preview = client.post('/orders/123/shipment', data=dict(payload, action='create', quotation_token=production.quotation_token))
    assert 'Sicherheitsabfrage' in preview.text
    assert calls == ['quote', 'book', 'quote']


def test_switch_is_validated_and_missing_test_key_blocks_requests(flow, monkeypatch, tmp_path):
    client, payload, _, _, calls = flow
    configure_switching(monkeypatch, tmp_path, sandbox_key='')
    assert client.post('/cargoboard/environment', data={'environment': 'test', 'switch_token': 'wrong'}).status_code == 403
    assert appmod.settings.cargoboard_environment == 'Produktion'
    assert switch(client, 'https://example.com').status_code == 400
    assert switch(client, 'test').status_code == 303
    assert not appmod.settings.has_cargoboard
    response = client.post('/orders/123/shipment', data=payload)
    assert response.status_code == 400
    assert not calls
    assert appmod.settings.cargoboard_api_key == ''


def test_environment_persistence_validates_saved_values(tmp_path):
    path = tmp_path / 'environment.json'
    assert load_mode(path, 'test') == 'test'
    save_mode(path, 'production')
    assert load_mode(path, 'test') == 'production'
    path.write_text('{"environment":"https://example.com"}')
    assert load_mode(path, 'test') == 'test'
    path.write_text('invalid json')
    assert load_mode(path, 'test') == 'test'
