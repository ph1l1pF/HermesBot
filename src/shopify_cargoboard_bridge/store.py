from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .models import Address, PackageInput, PickupAddress, ShippingDraft


class DraftStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ShippingDraft]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        drafts: list[ShippingDraft] = []
        for item in raw:
            item["created_at"] = datetime.fromisoformat(item["created_at"])
            item["pickup_address"] = Address(**item["pickup_address"])
            item["delivery_address"] = Address(**item["delivery_address"])
            item["billing_address"] = Address(**item["billing_address"])
            item["package"] = PackageInput(**item["package"])
            drafts.append(ShippingDraft(**item))
        return drafts

    def save(self, drafts: list[ShippingDraft]) -> None:
        payload = [d.model_dump(mode="json") for d in drafts]
        import os
        import tempfile
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def upsert(self, draft: ShippingDraft) -> ShippingDraft:
        drafts = self.load()
        for idx, existing in enumerate(drafts):
            if existing.id == draft.id:
                drafts[idx] = draft
                self.save(drafts)
                return draft
        drafts.append(draft)
        self.save(drafts)
        return draft

    def get(self, draft_id: str) -> ShippingDraft | None:
        for draft in self.load():
            if draft.id == draft_id:
                return draft
        return None

    def all(self) -> list[ShippingDraft]:
        return sorted(self.load(), key=lambda d: d.created_at, reverse=True)

    def delete(self, draft_id: str) -> None:
        self.save([d for d in self.load() if d.id != draft_id])

    def new_id(self) -> str:
        return uuid4().hex[:12]

class PickupAddressStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[PickupAddress]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [PickupAddress(**item) for item in raw]

    def save(self, addresses: list[PickupAddress]) -> None:
        payload = [address.model_dump(mode="json") for address in addresses]
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))

    def all(self) -> list[PickupAddress]:
        return self.load()

    def get(self, address_id: str) -> PickupAddress | None:
        for address in self.load():
            if address.id == address_id:
                return address
        return None

    def upsert(self, address: PickupAddress) -> PickupAddress:
        addresses = self.load()
        for idx, existing in enumerate(addresses):
            if existing.id == address.id:
                addresses[idx] = address
                self.save(addresses)
                return address
        addresses.append(address)
        self.save(addresses)
        return address

    def delete(self, address_id: str) -> None:
        self.save([address for address in self.load() if address.id != address_id])

    def new_id(self) -> str:
        return uuid4().hex[:12]
