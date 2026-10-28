from datetime import date, timedelta
from math import isfinite
import re

from .models import PickupAddress


def next_pickup_date() -> str:
    day = date.today() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day.isoformat()


def validate_post_code(post_code: str, country_code: str, label: str) -> None:
    if country_code.strip().upper() == "DE" and not re.fullmatch(r"[0-9]{5}", post_code.strip()):
        raise ValueError(f"{label}: Eine deutsche Postleitzahl muss aus genau fünf Ziffern bestehen.")


def build_payload(order, pickup: PickupAddress, form: dict) -> dict:
    def text(key):
        return str(form.get(key) or "").strip()

    def number(key, maximum, integer=False):
        value = float(form[key])
        if not isfinite(value) or value <= 0 or value > maximum or (integer and not value.is_integer()):
            raise ValueError(f"Ungültiger Wert: {key}")
        return int(value) if integer else value

    day = date.fromisoformat(text("pickup_date"))
    if day < date.today() or day.weekday() > 4:
        raise ValueError("Bitte einen Abholtag ab heute von Montag bis Freitag wählen.")
    product = {"standard": "STANDARD", "express": "EXPRESS"}.get(text("service_level"))
    if not product:
        raise ValueError("Bitte Standard oder Express wählen.")
    package_type = text("package_type")
    if package_type not in {"PA", "EP", "FP", "GP", "KI", "KT"}:
        raise ValueError("Bitte einen gültigen Packstück-Typ wählen.")
    shipper = {"name": pickup.name.strip(), "address": {
        "street": pickup.street.strip(), "city": pickup.city.strip(),
        "postCode": pickup.post_code.strip(), "countryCode": pickup.country_code.strip().upper(),
    }, "pickupOn": day.isoformat()}
    if pickup.phone.strip():
        shipper["contactPerson"] = {"name": pickup.name.strip(), "phone": pickup.phone.strip()}
    consignee = {"name": text("delivery_name"), "address": {
        "street": text("delivery_street"), "city": text("delivery_city"),
        "postCode": text("delivery_post_code"), "countryCode": text("delivery_country_code").upper(),
    }, "wantsTailLiftTruck": bool(form.get("liftgate_required")), "freeTextForDelivery": text("notes")}
    for label, party in (("Abholadresse", shipper), ("Lieferadresse", consignee)):
        validate_post_code(party["address"]["postCode"], party["address"]["countryCode"], label)
        if not party["name"] or not all(party["address"].values()) or len(party["address"]["countryCode"]) != 2:
            raise ValueError("Bitte Abhol- und Lieferadresse vollständig ausfüllen.")
    if shipper["address"]["countryCode"] == "RO":
        raise ValueError("Abholung in Rumänien erfordert einen Abholkontakt und wird noch nicht unterstützt.")
    contact = {"name": text("delivery_name")}
    if text("delivery_phone"):
        contact["phone"] = text("delivery_phone")
    if text("delivery_email"):
        contact["email"] = text("delivery_email")
    if len(contact) > 1:
        consignee["contactPerson"] = contact
    if form.get("private_customer"):
        if len(contact) == 1:
            raise ValueError("Für Privatkunden ist eine Telefonnummer oder E-Mail zur Lieferavisierung erforderlich.")
        consignee.update(isPrivateCustomer=True, wantsContactBeforeDelivery=True)
    insurance = float(form.get("insurance_value_eur") or 0)
    if not isfinite(insurance) or insurance < 0:
        raise ValueError("Ungültiger Versicherungswert.")
    payload = {"product": product, "shipper": shipper, "consignee": consignee,
               "customerOrderCode": order.name, "incoterm": "STANDARD", "lines": [{
                   "unitQuantity": number("package_count", 100, True), "unitPackageType": package_type,
                   "unitLength": number("package_length_cm", 1360, True),
                   "unitWidth": number("package_width_cm", 240, True),
                   "unitHeight": number("package_height_cm", 300, True),
                   "unitWeight": number("package_weight_kg", 40000), "content": text("package_content") or order.name,
               }], "wantsInsurance": insurance > 0}
    if insurance:
        payload.update(valueOfGoodsAmount=insurance, valueOfGoodsCurrency="EUR")
    return payload
