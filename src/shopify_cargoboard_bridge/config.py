
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def normalize_shop_domain(value: str) -> str:
    shop = value.strip().lower()
    if not shop:
        return ""
    shop = shop.removeprefix("https://").removeprefix("http://")
    shop = shop.split("/", 1)[0]
    if shop.endswith(".myshopify.com"):
        return shop
    if "." not in shop:
        return f"{shop}.myshopify.com"
    return shop


@dataclass(frozen=True)
class Settings:
    shopify_shop: str = normalize_shop_domain(env("SHOPIFY_SHOP_DOMAIN", default=""))
    shopify_token: str = env("SHOPIFY_ADMIN_TOKEN", "SHOPIFY_ACCESS_TOKEN")
    shopify_client_id: str = env("SHOPIFY_CLIENT_ID", "SHOPIFYCLIENTID", "shopifyclientid")
    shopify_client_secret: str = env("SHOPIFY_CLIENT_SECRET", "SHOPIFYCLIENTSECRET", "shopifyclientsecret")
    shopify_api_version: str = env("SHOPIFY_API_VERSION", default="2024-10")
    shopify_scopes: str = env("SHOPIFY_SCOPES", default="read_orders")
    shopify_access_mode: str = env("SHOPIFY_ACCESS_MODE", default="offline")
    shopify_auth_path: Path = Path(env("SHOPIFY_AUTH_PATH", default=str(Path.home() / ".hermes" / "shopify_auth.json")))
    cargoboard_api_key: str = env("CARGOBOARD_API_KEY")
    cargoboard_base_url: str = env("CARGOBOARD_BASE_URL", default="https://api-sandbox.cargoboard.com")
    billing_name: str = env("BILLING_NAME")
    billing_street: str = env("BILLING_STREET")
    billing_city: str = env("BILLING_CITY")
    billing_post_code: str = env("BILLING_POST_CODE")
    billing_country: str = env("BILLING_COUNTRY", default="DE")
    app_title: str = env("APP_TITLE", default="Shopify -> Cargoboard Bridge")
    state_path: Path = Path(env("APP_STATE_PATH", default=str(Path.home() / ".hermes" / "shopify_cargoboard_state.json")))

    @property
    def has_shopify_credentials(self) -> bool:
        return bool(self.shopify_shop and self.shopify_client_id and self.shopify_client_secret)

    @property
    def has_shopify_env_token(self) -> bool:
        return bool(self.shopify_shop and self.shopify_token)

    @property
    def has_cargoboard(self) -> bool:
        return bool(self.cargoboard_api_key)

    @property
    def has_billing_address(self) -> bool:
        return all([self.billing_name, self.billing_street, self.billing_city, self.billing_post_code, self.billing_country])
