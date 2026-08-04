
# Shopify → Cargoboard Bridge

Internal web app for one Shopify store.

## What it does
- Pulls Shopify orders
- Lets you connect Shopify via OAuth using client id/secret
- Pre-fills delivery address, pickup address and billing address
- Lets you manually edit delivery address, dimensions and weight
- Creates a Cargoboard quotation
- Sends the order only after confirmation

## Shopify OAuth setup
1. Create or open the app in the Shopify Dev Dashboard.
2. Copy the **Client ID** and **Client Secret** into environment variables.
3. Set the shop domain, for example:
   - `SHOPIFY_SHOP_DOMAIN=caae00` or `SHOPIFY_SHOP_DOMAIN=caae00.myshopify.com`
4. Add the app's callback URL to the allowed redirection URLs.
   - For this repo, the callback is `/shopify/callback` on the current public URL.
5. Open the app and click **Shopify verbinden**.
6. Finish the Shopify consent screen.

## Environment variables
- `SHOPIFY_SHOP_DOMAIN`
- `SHOPIFY_CLIENT_ID`
- `SHOPIFY_CLIENT_SECRET`
- `SHOPIFY_SCOPES` (optional, default `read_orders`)
- `SHOPIFY_ACCESS_MODE` (optional, `offline` default, `online` optional)
- `SHOPIFY_API_VERSION` (optional, default `2024-10`)
- `SHOPIFY_AUTH_PATH` (optional, default `~/.hermes/shopify_auth.json`)
- `CARGOBOARD_API_KEY`
- `CARGOBOARD_BASE_URL` (optional, default `https://api-sandbox.cargoboard.com`)
- `BILLING_NAME`
- `BILLING_STREET`
- `BILLING_CITY`
- `BILLING_POST_CODE`
- `BILLING_COUNTRY`

## MVP rules
- 1 order = 1 package
- Billing address is constant
- Delivery address comes from Shopify but is editable
- Dimensions and weight are entered manually
- No fully automatic shipping
