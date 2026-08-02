
# Shopify → Cargoboard Bridge

Internal web app for one Shopify store.

## What it does
- Pulls Shopify orders
- Pre-fills delivery address, pickup address and billing address
- Lets you manually edit delivery address, dimensions and weight
- Creates a Cargoboard quotation
- Sends the order only after confirmation

## MVP rules
- 1 order = 1 package
- Billing address is constant
- Delivery address comes from Shopify but is editable
- Dimensions and weight are entered manually
- No fully automatic shipping

## Run locally
```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn shopify_cargoboard_bridge.app:app --reload
```

## Environment variables
- `SHOPIFY_SHOP_DOMAIN`
- `SHOPIFY_ADMIN_TOKEN`
- `SHOPIFY_API_VERSION` (optional, default `2024-10`)
- `CARGOBOARD_API_KEY`
- `CARGOBOARD_BASE_URL` (optional, default `https://api-sandbox.cargoboard.com`)
- `BILLING_NAME`
- `BILLING_STREET`
- `BILLING_CITY`
- `BILLING_POST_CODE`
- `BILLING_COUNTRY`

## Status
This repo now contains the new Python MVP scaffold.
