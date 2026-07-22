import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode, urlparse

from flask import current_app

from app.extensions import db
from app.models import PRODUCT_METAFIELD_DEFINITIONS
from app.services.credential_service import decrypt_credentials, encrypt_credentials


SHOPIFY_API_VERSION = "2026-07"
SHOPIFY_PRODUCT_METAFIELD_NAMESPACE = "custom"
SHOPLAZZA_API_VERSION = "2026-01"


class StoreAPIError(RuntimeError):
    pass


def test_store_connection(store):
    result = adapter_for(store).test_connection()
    store.currency = result.get("currency") or store.currency
    store.default_location_id = result.get("default_location_id") or store.default_location_id
    store.connection_status = "connected"
    store.last_error = None
    store.last_tested_at = datetime.utcnow()
    db.session.commit()
    return result


def sync_store_product(draft, publish):
    errors = validate_draft(draft)
    if errors:
        raise StoreAPIError("；".join(errors))
    return adapter_for(draft.store).sync_product(draft, publish=publish)


def adapter_for(store):
    if store.platform == "shopify":
        return ShopifyAdapter(store)
    if store.platform == "shoplazza":
        return ShoplazzaAdapter(store)
    raise StoreAPIError(f"暂不支持店铺平台：{store.platform}")


def _draft_option_names(draft):
    return [item.get("name") for item in draft.options if item.get("name")]


def _variant_options_for_draft(draft, variant):
    values = variant.option_values
    return {name: str(values.get(name) or "").strip() for name in _draft_option_names(draft)}


def validate_draft(draft):
    errors = []
    if not (draft.title or "").strip():
        errors.append("产品标题不能为空")
    if not draft.images:
        errors.append("至少需要一张产品图片")
    for image in draft.images:
        if image.local_path and not (image.public_url or "").lower().startswith("https://"):
            errors.append("上传的产品图片必须通过 PUBLIC_BASE_URL 暴露为 HTTPS 地址")
    if not draft.variants:
        errors.append("至少需要一个变体")
    seen_skus = set()
    seen_options = set()
    for index, variant in enumerate(draft.variants, start=1):
        sku = (variant.sku or "").strip()
        if not sku:
            errors.append(f"第 {index} 个变体缺少 SKU")
        elif sku.lower() in seen_skus:
            errors.append(f"SKU 重复：{sku}")
        seen_skus.add(sku.lower())
        normalized_options = _variant_options_for_draft(draft, variant)
        signature = json.dumps(normalized_options, ensure_ascii=False, sort_keys=True)
        if signature in seen_options:
            errors.append(f"第 {index} 个变体选项组合重复")
        seen_options.add(signature)
        if any(not value for value in normalized_options.values()):
            errors.append(f"第 {index} 个变体的选项组合不完整")
        if variant.local_image_path and not (variant.image_url or "").lower().startswith("https://"):
            errors.append(f"第 {index} 个变体上传图片必须通过 PUBLIC_BASE_URL 暴露为 HTTPS 地址")
        if variant.price is None or Decimal(variant.price) < 0:
            errors.append(f"第 {index} 个变体价格无效")
        if (variant.inventory_quantity or 0) < 0:
            errors.append(f"第 {index} 个变体库存不能为负数")
    if not draft.store.is_active:
        errors.append("目标店铺已停用")
    if draft.store.connection_status != "connected":
        errors.append("目标店铺连接尚未测试成功")
    return errors


class ShopifyAdapter:
    def __init__(self, store):
        self.store = store

    def test_connection(self):
        payload = self._graphql(
            """
            query ProductHelperConnectionTest {
              shop { name currencyCode primaryDomain { url } }
              locations(first: 20) { nodes { id name isActive } }
            }
            """
        )
        shop = payload.get("shop") or {}
        locations = (payload.get("locations") or {}).get("nodes") or []
        active = next((item for item in locations if item.get("isActive")), locations[0] if locations else {})
        return {
            "name": shop.get("name") or self.store.name,
            "currency": shop.get("currencyCode"),
            "default_location_id": active.get("id"),
            "primary_url": (shop.get("primaryDomain") or {}).get("url"),
        }

    def sync_product(self, draft, publish):
        self._ensure_product_metafield_definitions()
        was_update = bool(draft.remote_product_id)
        product_input = self._product_input(draft, publish)
        variables = {"synchronous": True, "productSet": product_input}
        identifier_declaration = ""
        identifier_argument = ""
        if draft.remote_product_id:
            variables["identifier"] = {"id": draft.remote_product_id}
            identifier_declaration = ", $identifier: ProductSetIdentifiers"
            identifier_argument = ", identifier: $identifier"
        query = f"""
        mutation ProductHelperSync($productSet: ProductSetInput!, $synchronous: Boolean!{identifier_declaration}) {{
          productSet(synchronous: $synchronous, input: $productSet{identifier_argument}) {{
            product {{
              id handle status onlineStoreUrl
              media(first: 250, sortKey: POSITION) {{
                nodes {{ id status ... on MediaImage {{ image {{ url }} }} }}
              }}
              variants(first: 250) {{
                nodes {{
                  sku
                  media(first: 10) {{
                    nodes {{ id status ... on MediaImage {{ image {{ url }} }} }}
                  }}
                }}
              }}
            }}
            userErrors {{ field message code }}
          }}
        }}
        """
        data = self._graphql(query, variables)
        result = data.get("productSet") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise StoreAPIError(_format_user_errors(user_errors))
        product = result.get("product") or {}
        remote_id = product.get("id")
        if not remote_id:
            raise StoreAPIError("Shopify 未返回商品 ID。")
        handle = product.get("handle") or draft.remote_handle or ""
        remote_url = product.get("onlineStoreUrl") or (
            f"https://{self.store.shop_domain}/products/{handle}" if handle else ""
        )
        if was_update:
            self._delete_blank_product_metafields(remote_id, draft)
        media = self._wait_for_media(remote_id, draft, product)
        return {
            "remote_product_id": remote_id,
            "remote_handle": handle,
            "remote_url": remote_url,
            **media,
        }

    def _ensure_product_metafield_definitions(self):
        data = self._graphql(
            """
            query ProductHelperMetafieldDefinitions($namespace: String!) {
              metafieldDefinitions(
                first: 100
                ownerType: PRODUCT
                namespace: $namespace
              ) {
                nodes {
                  id name namespace key pinnedPosition
                  type { name }
                }
              }
            }
            """,
            {"namespace": SHOPIFY_PRODUCT_METAFIELD_NAMESPACE},
        )
        existing = {
            node.get("key"): node
            for node in ((data.get("metafieldDefinitions") or {}).get("nodes") or [])
            if node.get("key")
        }
        for definition in PRODUCT_METAFIELD_DEFINITIONS:
            current = existing.get(definition["key"])
            if current:
                current_type = ((current.get("type") or {}).get("name"))
                if current_type != "single_line_text_field":
                    raise StoreAPIError(
                        f'Shopify 字段 custom.{definition["key"]} 的类型是 '
                        f'{current_type or "未知"}，需要 single_line_text_field。'
                    )
                if current.get("pinnedPosition") is None:
                    self._pin_product_metafield_definition(definition["key"])
                continue
            result = self._graphql(
                """
                mutation ProductHelperCreateMetafieldDefinition(
                  $definition: MetafieldDefinitionInput!
                ) {
                  metafieldDefinitionCreate(definition: $definition) {
                    createdDefinition { id name namespace key pinnedPosition }
                    userErrors { field message code }
                  }
                }
                """,
                {"definition": {
                    "name": definition["label"],
                    "namespace": SHOPIFY_PRODUCT_METAFIELD_NAMESPACE,
                    "key": definition["key"],
                    "description": "Synced by Product Development Helper.",
                    "type": "single_line_text_field",
                    "ownerType": "PRODUCT",
                    "pin": True,
                }},
            )
            payload = result.get("metafieldDefinitionCreate") or {}
            user_errors = payload.get("userErrors") or []
            if user_errors:
                raise StoreAPIError(_format_user_errors(user_errors))

    def _pin_product_metafield_definition(self, key):
        data = self._graphql(
            """
            mutation ProductHelperPinMetafieldDefinition(
              $identifier: MetafieldDefinitionIdentifierInput!
            ) {
              metafieldDefinitionPin(identifier: $identifier) {
                pinnedDefinition { id key pinnedPosition }
                userErrors { field message code }
              }
            }
            """,
            {"identifier": {
                "ownerType": "PRODUCT",
                "namespace": SHOPIFY_PRODUCT_METAFIELD_NAMESPACE,
                "key": key,
            }},
        )
        payload = data.get("metafieldDefinitionPin") or {}
        user_errors = payload.get("userErrors") or []
        if user_errors:
            raise StoreAPIError(_format_user_errors(user_errors))

    def _delete_blank_product_metafields(self, remote_id, draft):
        blank = [
            {
                "ownerId": remote_id,
                "namespace": SHOPIFY_PRODUCT_METAFIELD_NAMESPACE,
                "key": definition["key"],
            }
            for definition in PRODUCT_METAFIELD_DEFINITIONS
            if not (draft.product_metafields.get(definition["key"]) or "").strip()
        ]
        if not blank:
            return
        data = self._graphql(
            """
            mutation ProductHelperDeleteBlankMetafields(
              $metafields: [MetafieldIdentifierInput!]!
            ) {
              metafieldsDelete(metafields: $metafields) {
                deletedMetafields { ownerId namespace key }
                userErrors { field message }
              }
            }
            """,
            {"metafields": blank},
        )
        payload = data.get("metafieldsDelete") or {}
        user_errors = payload.get("userErrors") or []
        if user_errors:
            raise StoreAPIError(_format_user_errors(user_errors))
    def _wait_for_media(self, remote_id, draft, product):
        snapshot = _shopify_media_snapshot(draft, product)
        attempts = max(1, int(current_app.config.get("SHOPIFY_MEDIA_POLL_ATTEMPTS", 15)))
        for attempt in range(attempts):
            if snapshot["all_ready"] or snapshot["has_failed"] or attempt == attempts - 1:
                break
            interval = max(0, float(current_app.config.get("SHOPIFY_MEDIA_POLL_INTERVAL", 2)))
            if interval:
                time.sleep(interval)
            data = self._graphql(
                """
                query ProductHelperMedia($id: ID!) {
                  product(id: $id) {
                    media(first: 250, sortKey: POSITION) {
                      nodes { id status ... on MediaImage { image { url } } }
                    }
                    variants(first: 250) {
                      nodes {
                        sku
                        media(first: 10) {
                          nodes { id status ... on MediaImage { image { url } } }
                        }
                      }
                    }
                  }
                }
                """,
                {"id": remote_id},
            )
            product = data.get("product") or {}
            snapshot = _shopify_media_snapshot(draft, product)
        return {
            "remote_images": snapshot["images"],
            "remote_variant_images": snapshot["variant_images"],
            "remote_media_ready": snapshot["all_ready"],
            "remote_media_failed": snapshot["has_failed"],
        }

    def _product_input(self, draft, publish):
        options = []
        for position, option in enumerate(draft.options, start=1):
            name = (option.get("name") or "").strip()
            values = [str(value).strip() for value in option.get("values") or [] if str(value).strip()]
            if name and values:
                options.append({
                    "name": name,
                    "position": position,
                    "values": [{"name": value} for value in values],
                })

        files = []
        file_keys = set()
        for image in draft.images:
            file_key = image.remote_media_id or image.public_url
            if file_key and file_key not in file_keys:
                files.append(_shopify_file_reference(
                    image.public_url, image.alt_text or draft.title, image.remote_media_id
                ))
                file_keys.add(file_key)

        variants = []
        for variant in draft.variants:
            item = {
                "sku": variant.sku,
                "price": _money(variant.price),
                "optionValues": [
                    {"optionName": name, "name": value}
                    for name, value in _variant_options_for_draft(draft, variant).items()
                    if name and value
                ],
            }
            if variant.compare_at_price is not None:
                item["compareAtPrice"] = _money(variant.compare_at_price)
            if variant.weight_kg is not None:
                item["inventoryItem"] = {"measurement": {"weight": {
                    "value": float(variant.weight_kg),
                    "unit": "KILOGRAMS",
                }}}
            if self.store.default_location_id:
                item["inventoryQuantities"] = [{
                    "locationId": self.store.default_location_id,
                    "name": "available",
                    "quantity": int(variant.inventory_quantity or 0),
                }]
            if variant.image_url or variant.remote_media_id:
                file_reference = _shopify_file_reference(
                    variant.image_url, variant.sku, variant.remote_media_id
                )
                item["file"] = file_reference
                file_key = variant.remote_media_id or variant.image_url
                if file_key not in file_keys:
                    files.append(file_reference)
                    file_keys.add(file_key)
            variants.append(item)

        return {
            "title": draft.title.strip(),
            "descriptionHtml": draft.description_html or "",
            "productType": draft.product_type or "",
            "status": "ACTIVE" if publish else "DRAFT",
            "tags": draft.tags,
            "files": files,
            "productOptions": options,
            "variants": variants,
            "metafields": _shopify_product_metafields(draft),
        }
    def _graphql(self, query, variables=None):
        token = self._access_token()
        url = f"https://{self.store.shop_domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
        payload = _request_json(
            url,
            method="POST",
            headers={"X-Shopify-Access-Token": token},
            body={"query": query, "variables": variables or {}},
        )
        if payload.get("errors"):
            raise StoreAPIError(_format_graphql_errors(payload["errors"]))
        return payload.get("data") or {}

    def _access_token(self):
        now = datetime.utcnow()
        if self.store.oauth_access_token_encrypted and self.store.token_expires_at:
            if self.store.token_expires_at > now + timedelta(minutes=5):
                return decrypt_credentials(self.store.oauth_access_token_encrypted).get("access_token")
        credentials = decrypt_credentials(self.store.credentials_encrypted)
        if not credentials.get("client_id") or not credentials.get("client_secret"):
            raise StoreAPIError("Shopify 缺少 Client ID 或 Client Secret。")
        payload = _request_json(
            f"https://{self.store.shop_domain}/admin/oauth/access_token",
            method="POST",
            body={
                "grant_type": "client_credentials",
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
            },
            form=True,
        )
        token = payload.get("access_token")
        if not token:
            raise StoreAPIError("Shopify 未返回访问令牌。")
        expires_in = int(payload.get("expires_in") or 86400)
        self.store.oauth_access_token_encrypted = encrypt_credentials({"access_token": token})
        self.store.token_expires_at = now + timedelta(seconds=max(300, expires_in))
        db.session.commit()
        return token

class ShoplazzaAdapter:
    def __init__(self, store):
        self.store = store
        credentials = decrypt_credentials(store.credentials_encrypted)
        self.token = credentials.get("access_token")
        if not self.token:
            raise StoreAPIError("Shoplazza 缺少 Access Token。")

    @property
    def headers(self):
        return {"access-token": self.token}

    @property
    def base_url(self):
        return f"https://{self.store.shop_domain}/openapi/{SHOPLAZZA_API_VERSION}"

    def test_connection(self):
        payload = _request_json(f"{self.base_url}/shop", headers=self.headers)
        shop = _extract_nested(payload, "shop") or {}
        return {
            "name": shop.get("name") or shop.get("store_name") or self.store.name,
            "currency": shop.get("currency") or shop.get("currency_code"),
            "primary_url": shop.get("primary_domain") or shop.get("domain"),
        }

    def sync_product(self, draft, publish):
        product = self._product_payload(draft, publish)
        if draft.remote_product_id:
            payload = _request_json(
                f"{self.base_url}/products/{draft.remote_product_id}",
                method="PUT",
                headers=self.headers,
                body={"product": product},
            )
        else:
            payload = _request_json(
                f"{self.base_url}/products",
                method="POST",
                headers=self.headers,
                body={"product": product},
            )
        result = _extract_nested(payload, "product") or {}
        remote_id = result.get("id") or draft.remote_product_id
        if not remote_id:
            raise StoreAPIError("Shoplazza 未返回商品 ID。")
        handle = result.get("handle") or draft.remote_handle or ""
        remote_url = f"https://{self.store.shop_domain}/products/{handle}" if handle else ""
        return {"remote_product_id": str(remote_id), "remote_handle": handle, "remote_url": remote_url}

    def _product_payload(self, draft, publish):
        option_names = _draft_option_names(draft)
        variants = []
        for variant in draft.variants:
            option_values = _variant_options_for_draft(draft, variant)
            item = {
                "sku": variant.sku,
                "price": float(variant.price),
                "inventory_quantity": int(variant.inventory_quantity or 0),
                "position": variant.position + 1,
            }
            if variant.compare_at_price is not None:
                item["compare_at_price"] = float(variant.compare_at_price)
            for index, name in enumerate(option_names[:3], start=1):
                item[f"option{index}"] = option_values.get(name, "")
            if variant.image_url:
                item["image"] = {"src": variant.image_url}
            variants.append(item)
        has_options = bool(option_names)
        return {
            "title": draft.title.strip(),
            "body_html": draft.description_html or "",
            "product_type": draft.product_type or "",
            "tags": draft.tags,
            "published": bool(publish),
            "has_only_default_variant": not has_options,
            "need_variant_image": False,
            "inventory_tracking": True,
            "inventory_policy": "deny",
            "options": [
                {"name": item.get("name"), "values": item.get("values") or []}
                for item in draft.options if item.get("name")
            ],
            "images": [{"src": image.public_url} for image in draft.images],
            "variants": variants,
        }


def _request_json(url, method="GET", headers=None, body=None, form=False):
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
        "User-Agent": "ProductDevelopmentHelper/1.0",
    }
    request_headers.update(headers or {})
    if body is None:
        data = None
    else:
        data = (urlencode(body) if form else json.dumps(body, ensure_ascii=False)).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise StoreAPIError(f"店铺接口返回 HTTP {exc.code}：{_safe_remote_message(raw)}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StoreAPIError(f"店铺接口连接失败：{type(exc).__name__}") from exc
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise StoreAPIError("店铺接口返回了无法解析的响应。") from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, "Success", "success", 0, "0"):
        raise StoreAPIError(_safe_remote_message(json.dumps(payload, ensure_ascii=False)))
    return payload


def _extract_nested(payload, key):
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            return {}
        if key in current and isinstance(current[key], dict):
            return current[key]
        next_value = current.get("data")
        if not isinstance(next_value, dict):
            return {}
        current = next_value
    return {}


def _shopify_product_metafields(draft):
    metafields = [{
        "namespace": "product_helper",
        "key": "draft_id",
        "type": "single_line_text_field",
        "value": str(draft.id),
    }]
    values = draft.product_metafields
    for definition in PRODUCT_METAFIELD_DEFINITIONS:
        value = str(values.get(definition["key"]) or "").strip()
        if value:
            metafields.append({
                "namespace": SHOPIFY_PRODUCT_METAFIELD_NAMESPACE,
                "key": definition["key"],
                "type": "single_line_text_field",
                "value": value,
            })
    return metafields

def _shopify_media_snapshot(draft, product):
    media_nodes = ((product.get("media") or {}).get("nodes") or [])
    images = []
    for position, _image in enumerate(draft.images):
        node = media_nodes[position] if position < len(media_nodes) else {}
        images.append(_shopify_media_entry(node))

    variants_by_sku = {
        str(node.get("sku") or ""): node
        for node in ((product.get("variants") or {}).get("nodes") or [])
        if node.get("sku")
    }
    variant_images = {}
    for variant in draft.variants:
        if not (variant.image_url or variant.remote_media_id):
            continue
        remote_variant = variants_by_sku.get(variant.sku) or {}
        nodes = ((remote_variant.get("media") or {}).get("nodes") or [])
        variant_images[variant.sku] = _shopify_media_entry(nodes[0] if nodes else {})

    expected = images + list(variant_images.values())
    all_ready = bool(expected) and all(
        item.get("remote_media_id") and item.get("status") == "READY" and item.get("url")
        for item in expected
    )
    has_failed = any(item.get("status") == "FAILED" for item in expected)
    return {
        "images": images,
        "variant_images": variant_images,
        "all_ready": all_ready,
        "has_failed": has_failed,
    }


def _shopify_media_entry(node):
    return {
        "remote_media_id": node.get("id"),
        "status": node.get("status"),
        "url": ((node.get("image") or {}).get("url")),
    }


def _shopify_file_reference(url, alt, remote_media_id=None):
    if remote_media_id:
        return {"id": remote_media_id, "alt": (alt or "")[:255]}
    return _shopify_file(url, alt)

def _shopify_file(url, alt):
    filename = urlparse(url).path.rsplit("/", 1)[-1] or "product-image.jpg"
    return {
        "originalSource": url,
        "alt": (alt or "")[:255],
        "filename": filename.split("?")[0],
        "contentType": "IMAGE",
    }


def _format_user_errors(errors):
    return "；".join(
        f"{'.'.join(str(part) for part in item.get('field') or [])}: {item.get('message')}"
        for item in errors
    )


def _format_graphql_errors(errors):
    return "；".join(str(item.get("message") or "GraphQL 请求失败") for item in errors)

def redact_error_message(value):
    return _safe_remote_message(str(value or ""))



def _safe_remote_message(raw):
    text = re.sub(
        r'(?i)(access[_-]?token|client[_-]?secret|authorization)["\s:=]+[^,}\s"]+',
        r"\1=<redacted>",
        raw or "",
    )
    text = re.sub(r"(?i)shp(?:at|rt|ss)_[a-z0-9_-]+", "<redacted>", text)
    return text[:800]


def _money(value):
    return format(Decimal(value or 0), ".2f")
