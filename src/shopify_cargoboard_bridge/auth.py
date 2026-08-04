
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ShopifyAuthRecord:
    shop: str = ""
    access_token: str = ""
    scope: str = ""
    created_at: str = ""
    updated_at: str = ""
    pending_state: str = ""
    pending_redirect_uri: str = ""
    pending_created_at: str = ""


class ShopifyAuthStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ShopifyAuthRecord:
        if not self.path.exists():
            return ShopifyAuthRecord()
        raw = json.loads(self.path.read_text())
        return ShopifyAuthRecord(**raw)

    def save(self, record: ShopifyAuthRecord) -> None:
        self.path.write_text(json.dumps(asdict(record), indent=2, ensure_ascii=False, sort_keys=True))

    def access_token(self) -> str:
        return self.load().access_token

    def is_connected(self) -> bool:
        return bool(self.access_token())

    def new_state(self, redirect_uri: str) -> str:
        record = self.load()
        record.pending_state = secrets.token_urlsafe(24)
        record.pending_redirect_uri = redirect_uri
        record.pending_created_at = datetime.now(timezone.utc).isoformat()
        self.save(record)
        return record.pending_state

    def clear_pending(self) -> None:
        record = self.load()
        record.pending_state = ""
        record.pending_redirect_uri = ""
        record.pending_created_at = ""
        self.save(record)

    def store_token(self, *, shop: str, access_token: str, scope: str = "") -> ShopifyAuthRecord:
        record = self.load()
        now = datetime.now(timezone.utc).isoformat()
        if not record.created_at:
            record.created_at = now
        record.shop = shop
        record.access_token = access_token
        record.scope = scope
        record.updated_at = now
        record.pending_state = ""
        record.pending_redirect_uri = ""
        record.pending_created_at = ""
        self.save(record)
        return record

    def verify_state(self, state: str) -> bool:
        record = self.load()
        return bool(record.pending_state and state and hmac.compare_digest(record.pending_state, state))

    def verify_hmac(self, params: dict[str, str], client_secret: str) -> bool:
        provided = params.get("hmac", "")
        if not provided:
            return False
        filtered = {k: v for k, v in params.items() if k not in {"hmac", "signature"}}
        message = "&".join(f"{k}={filtered[k]}" for k in sorted(filtered))
        digest = hmac.new(client_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided)
