from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


MARKETPLACE_MODE_ENV = "FVS_MARKETPLACE_MODE"
MARKETPLACE_ACCOUNTS_ENV = "FVS_MARKETPLACE_ACCOUNT_KEYS_JSON"
MARKETPLACE_SECRET_ENV = "FVS_MARKETPLACE_SHARED_SECRET"
MARKETPLACE_REQUIRE_SIGNATURE_ENV = "FVS_MARKETPLACE_REQUIRE_SIGNATURE"
MARKETPLACE_MAX_SKEW_ENV = "FVS_MARKETPLACE_SIGNATURE_MAX_SKEW_SECONDS"

ACCOUNT_HEADER = "x-fvs-marketplace-account"
ENTITLEMENT_HEADER = "x-fvs-marketplace-entitlement"
PLAN_HEADER = "x-fvs-marketplace-plan"
TIMESTAMP_HEADER = "x-fvs-marketplace-timestamp"
SIGNATURE_HEADER = "x-fvs-marketplace-signature"

DEFAULT_SIGNATURE_MAX_SKEW_SECONDS = 300


class MarketplaceAuthError(ValueError):
    def __init__(self, message: str, *, status_code: int = 403, code: str = "marketplace_forbidden") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class MarketplaceCredential:
    account_id: str
    entitlement_id: str | None
    plan: str | None
    api_key: str
    display_name: str | None = None


def marketplace_mode() -> str:
    return (os.getenv(MARKETPLACE_MODE_ENV) or "optional").strip().lower() or "optional"


def bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_marketplace_credential(headers: Any, *, now: float | None = None) -> MarketplaceCredential | None:
    account_id = header_value(headers, ACCOUNT_HEADER)
    if not account_id:
        return None

    if marketplace_mode() == "disabled":
        raise MarketplaceAuthError("Marketplace-linked account mode is disabled.", status_code=403)

    account_map = load_marketplace_accounts()
    account = account_map.get(account_id)
    if account is None:
        raise MarketplaceAuthError("Marketplace account is not linked to Future Video Studio.", status_code=403)
    if account.get("status", "active").lower() not in {"active", "trialing"}:
        raise MarketplaceAuthError("Marketplace entitlement is not active.", status_code=402, code="marketplace_inactive")

    entitlement_id = header_value(headers, ENTITLEMENT_HEADER) or None
    expected_entitlement = str(account.get("entitlement_id") or "").strip()
    if expected_entitlement and entitlement_id != expected_entitlement:
        raise MarketplaceAuthError("Marketplace entitlement does not match the linked account.", status_code=403)

    secret = (os.getenv(MARKETPLACE_SECRET_ENV) or "").strip()
    require_signature = bool_env(MARKETPLACE_REQUIRE_SIGNATURE_ENV, bool(secret))
    if require_signature:
        if not secret:
            raise MarketplaceAuthError("Marketplace signature verification is not configured.", status_code=503, code="marketplace_not_configured")
        verify_marketplace_signature(
            headers,
            secret=secret,
            account_id=account_id,
            entitlement_id=entitlement_id,
            now=now,
        )

    api_key = str(account.get("api_key") or "").strip()
    if not api_key:
        raise MarketplaceAuthError("Marketplace account is linked without an agent API key.", status_code=503, code="marketplace_not_configured")

    return MarketplaceCredential(
        account_id=account_id,
        entitlement_id=entitlement_id,
        plan=header_value(headers, PLAN_HEADER) or str(account.get("plan") or "").strip() or None,
        api_key=api_key,
        display_name=str(account.get("display_name") or "").strip() or None,
    )


def load_marketplace_accounts() -> dict[str, dict[str, str]]:
    raw = (os.getenv(MARKETPLACE_ACCOUNTS_ENV) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarketplaceAuthError("FVS marketplace account mapping is not valid JSON.", status_code=503, code="marketplace_not_configured") from exc
    if not isinstance(parsed, Mapping):
        raise MarketplaceAuthError("FVS marketplace account mapping must be a JSON object.", status_code=503, code="marketplace_not_configured")

    accounts: dict[str, dict[str, str]] = {}
    for account_id, value in parsed.items():
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            continue
        if isinstance(value, str):
            accounts[normalized_account_id] = {"api_key": value.strip(), "status": "active"}
        elif isinstance(value, Mapping):
            accounts[normalized_account_id] = {
                str(key): str(item).strip()
                for key, item in value.items()
                if item is not None
            }
        else:
            raise MarketplaceAuthError("FVS marketplace account entries must be strings or objects.", status_code=503, code="marketplace_not_configured")
    return accounts


def verify_marketplace_signature(
    headers: Any,
    *,
    secret: str,
    account_id: str,
    entitlement_id: str | None,
    now: float | None = None,
) -> None:
    timestamp = header_value(headers, TIMESTAMP_HEADER)
    signature = normalize_signature(header_value(headers, SIGNATURE_HEADER))
    if not timestamp or not signature:
        raise MarketplaceAuthError("Marketplace account calls require timestamp and signature headers.", status_code=401, code="marketplace_unauthenticated")
    try:
        timestamp_seconds = int(timestamp)
    except ValueError as exc:
        raise MarketplaceAuthError("Marketplace signature timestamp must be a Unix timestamp.", status_code=401, code="marketplace_unauthenticated") from exc
    max_skew = max(1, int_env(MARKETPLACE_MAX_SKEW_ENV, DEFAULT_SIGNATURE_MAX_SKEW_SECONDS))
    current = int(now if now is not None else time.time())
    if abs(current - timestamp_seconds) > max_skew:
        raise MarketplaceAuthError("Marketplace signature timestamp is outside the allowed window.", status_code=401, code="marketplace_unauthenticated")

    expected = marketplace_signature(
        secret=secret,
        timestamp=timestamp,
        account_id=account_id,
        entitlement_id=entitlement_id,
    )
    if not hmac.compare_digest(signature, expected):
        raise MarketplaceAuthError("Marketplace signature is invalid.", status_code=401, code="marketplace_unauthenticated")


def marketplace_signature(*, secret: str, timestamp: str, account_id: str, entitlement_id: str | None) -> str:
    payload = marketplace_signature_payload(
        timestamp=timestamp,
        account_id=account_id,
        entitlement_id=entitlement_id,
    )
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def marketplace_signature_payload(*, timestamp: str, account_id: str, entitlement_id: str | None) -> str:
    return f"{timestamp}.{account_id}.{entitlement_id or ''}"


def normalize_signature(value: str) -> str:
    cleaned = value.strip()
    if cleaned.lower().startswith("sha256="):
        cleaned = cleaned[7:].strip()
    return cleaned


def header_value(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "").strip()
    except Exception:
        return ""


def credential_metadata(credential: MarketplaceCredential) -> dict[str, str | None]:
    return {
        "accountId": credential.account_id,
        "entitlementId": credential.entitlement_id,
        "plan": credential.plan,
        "displayName": credential.display_name,
    }
