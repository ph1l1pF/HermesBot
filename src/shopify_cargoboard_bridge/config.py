
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    shopify_shop: str = env("SHOPIFY_SHOP_DOMAIN")
    shopify_token: str = env("SHOPIFY_ADMIN_TOKEN")
    shopify_api_version: str = env("SHOPIFY_API_VERSION", "2024-10")
    cargoboard_api_key: str = env("CARGOBOARD_API_KEY")
    cargoboard_base_url: str = env("CARGOBOARD_BASE_URL", "https://api-sandbox.cargoboard.com")
    billing_name: str = env("BILLING_NAME")
    billing_street: str = env("BILLING_STREET")
    billing_city: str = env("BILLING_CITY")
    billing_post_code: str = env("BILLING_POST_CODE")
    billing_country: str = env("BILLING_COUNTRY", "DE")
    app_title: str = env("APP_TITLE", "Shopify -> Cargoboard Bridge")
    state_path: Path = Path(env("APP_STATE_PATH", str(Path.home() / ".hermes" / "shopify_cargoboard_state.json")))

    @property
    def has_shopify(self) -> bool:
        return bool(self.shopify_shop and self.shopify_token)

    @property
    def has_cargoboard(self) -> bool:
        return bool(self.cargoboard_api_key)

    @property
    def has_billing_address(self) -> bool:
        return all([self.billing_name, self.billing_street, self.billing_city, self.billing_post_code, self.billing_country])
