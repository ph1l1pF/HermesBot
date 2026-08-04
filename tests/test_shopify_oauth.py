
from shopify_cargoboard_bridge.auth import ShopifyAuthStore
from shopify_cargoboard_bridge.config import normalize_shop_domain


def test_normalize_shop_domain_adds_myshopify_suffix():
    assert normalize_shop_domain('caae00') == 'caae00.myshopify.com'
    assert normalize_shop_domain('https://caae00.myshopify.com') == 'caae00.myshopify.com'


def test_auth_store_state_roundtrip(tmp_path):
    store = ShopifyAuthStore(tmp_path / 'auth.json')
    state = store.new_state('https://example.com/shopify/callback')
    assert store.verify_state(state)
    assert not store.verify_state('wrong')


def test_auth_store_token_persistence(tmp_path):
    store = ShopifyAuthStore(tmp_path / 'auth.json')
    store.store_token(shop='caae00.myshopify.com', access_token='shpat_123', scope='read_orders')
    assert store.access_token() == 'shpat_123'
