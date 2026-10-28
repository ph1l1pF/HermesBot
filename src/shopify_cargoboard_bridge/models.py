from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Address(BaseModel):
    name: str = ""
    street: str = ""
    city: str = ""
    post_code: str = ""
    country_code: str = "DE"
    phone: str = ""


class PickupAddress(BaseModel):
    id: str
    label: str = ""
    name: str = ""
    street: str = ""
    city: str = ""
    post_code: str = ""
    country_code: str = "DE"
    phone: str = ""


class PackageInput(BaseModel):
    quantity: int = 1
    package_type: str = "KT"
    length_cm: int = 0
    width_cm: int = 0
    height_cm: int = 10
    weight_kg: float = 10.0
    content: str = ""
    stackable: bool = False
    pallet_exchange: bool = False


class ShippingDraft(BaseModel):
    id: str
    shopify_order_id: str
    shopify_order_name: str
    shopify_order_number: int = 0
    created_at: datetime
    customer_name: str = ""
    pickup_address: Address
    delivery_address: Address
    billing_address: Address
    package: PackageInput
    notes: str = ""
    quotation: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    status: str = "draft"
    cargoboard_payload: dict[str, Any] | None = None
    quotation_token: str = ""
    production_confirmation_token: str = ""
    delivery_phone_saved: bool = False
    quoted_at: datetime | None = None
    cargoboard_environment: str = ""
    errors: list[str] = Field(default_factory=list)


class ShopifyOrderSummary(BaseModel):
    id: str
    name: str
    number: int
    created_at: datetime
    financial_status: str = ""
    fulfillment_status: str = ""
    customer_name: str = ""
    delivery_address: Address | None = None
    source: dict[str, Any] = Field(default_factory=dict)
