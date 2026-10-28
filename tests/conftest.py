"""Keep tests isolated from real local credentials and shipment records."""
import os
import tempfile
from pathlib import Path

_test_data = tempfile.TemporaryDirectory(prefix='hermesbot-tests-')
for name, filename in [('APP_STATE_PATH', 'state.json'), ('PICKUP_ADDRESSES_PATH', 'pickups.json'), ('SHOPIFY_AUTH_PATH', 'auth.json')]:
    os.environ[name] = str(Path(_test_data.name) / filename)
os.environ['CARGOBOARD_API_KEY'] = ''
