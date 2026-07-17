import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class CredentialError(ValueError):
    pass


def encrypt_credentials(payload):
    if not isinstance(payload, dict) or not payload:
        raise CredentialError("店铺凭据不能为空。")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _cipher().encrypt(raw).decode("ascii")


def decrypt_credentials(value):
    if not value:
        raise CredentialError("店铺尚未配置凭据。")
    try:
        raw = _cipher().decrypt(value.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise CredentialError("店铺凭据无法解密，请重新配置。") from exc
    if not isinstance(payload, dict):
        raise CredentialError("店铺凭据格式无效。")
    return payload


def normalize_shop_domain(value):
    domain = (value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.strip("/").split("/")[0]


def _cipher():
    configured = current_app.config.get("STORE_CREDENTIAL_ENCRYPTION_KEY") or ""
    if not configured:
        raise CredentialError("缺少 STORE_CREDENTIAL_ENCRYPTION_KEY 配置。")
    digest = hashlib.sha256(str(configured).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
