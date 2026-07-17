import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from app import register_blueprints
from app.extensions import db, login_manager
from app.models import (
    CompetitorProduct,
    DraftProductImage,
    DraftVariant,
    ProductInboxItem,
    PRODUCT_METAFIELD_DEFINITIONS,
    StoreConnection,
    StoreProductDraft,
    User,
)
from app.services.credential_service import CredentialError, decrypt_credentials, encrypt_credentials
from app.services.store_publish_queue import run_store_publish_by_id
from app.services.store_publish_service import ShopifyAdapter, ShoplazzaAdapter


class ProductWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_root = Path(__file__).resolve().parents[1] / "app"
        cls.app = Flask("app", root_path=str(app_root), template_folder="templates", static_folder="static")
        cls.app.config.update(
            SECRET_KEY="workflow-test-session-key",
            STORE_CREDENTIAL_ENCRYPTION_KEY="workflow-test-credential-key",
            PUBLIC_BASE_URL="https://public.test",
            PRODUCT_UPLOAD_EXTENSIONS={"jpg", "jpeg", "png", "webp"},
            SHOPIFY_MEDIA_POLL_ATTEMPTS=1,
            SHOPIFY_MEDIA_POLL_INTERVAL=0,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            TESTING=True,
        )
        db.init_app(cls.app)
        login_manager.init_app(cls.app)
        register_blueprints(cls.app)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def setUp(self):
        with self.app.app_context():
            self.assertEqual(str(db.engine.url), "sqlite://")
            db.drop_all()
            db.create_all()
            user = User(username="workflow-admin", password_hash="unused", role="super_admin", is_active=True)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

    def create_product(self, title="Collected shirt"):
        with self.app.app_context():
            product = CompetitorProduct(
                source_domain="source.example",
                source_type="shopify_json",
                title=title,
                price="29.90",
                product_url="https://source.example/products/shirt",
                product_media=json.dumps({"main": "https://cdn.example/main.jpg", "carousel": ["https://cdn.example/main.jpg", "https://cdn.example/side.jpg"]}),
                product_tags=json.dumps(["shirt", "custom"]),
                description="<p>Safe description</p>",
                variants=json.dumps([
                    {"title": "Black / M", "price": "29.90", "compare_at_price": "39.90", "inventory_quantity": 8, "option_values": {"Color": "Black", "Size": "M"}, "image": "https://cdn.example/black.jpg"},
                    {"title": "Blue / L", "sku": "SOURCE-BLUE-L", "price": "31.90", "inventory_quantity": 3, "option_values": {"Color": "Blue", "Size": "L"}},
                ]),
            )
            db.session.add(product)
            db.session.commit()
            return product.id

    def move_product(self, product_id):
        response = self.client.post("/product-workflow/inbox/move", data={"product_id": product_id})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            return ProductInboxItem.query.filter_by(source_product_id=product_id).one().id

    def create_store(self, platform="shopify", name="Test store", domain=None):
        domain = domain or ("test.myshopify.com" if platform == "shopify" else "test.myshoplaza.com")
        with self.app.app_context():
            credentials = {"client_id": "client-id", "client_secret": "client-secret"} if platform == "shopify" else {"access_token": "shoplazza-token"}
            store = StoreConnection(
                name=name,
                platform=platform,
                shop_domain=domain,
                credential_type="test",
                credentials_encrypted=encrypt_credentials(credentials),
                connection_status="connected",
                is_active=True,
                created_by=self.user_id,
            )
            db.session.add(store)
            db.session.commit()
            return store.id

    def claim(self, item_id, store_ids):
        response = self.client.post(f"/product-workflow/inbox/{item_id}/claim", data={"store_ids": store_ids})
        self.assertEqual(response.status_code, 302)

    def test_batch_move_is_idempotent_and_copies_variant_fields(self):
        product_id = self.create_product()
        item_id = self.move_product(product_id)
        self.move_product(product_id)
        with self.app.app_context():
            self.assertEqual(ProductInboxItem.query.count(), 1)
            item = db.session.get(ProductInboxItem, item_id)
            self.assertEqual(len(item.images), 2)
            self.assertEqual(len(item.variants), 2)
            self.assertEqual(item.variants[0].sku, f"PDH-{product_id}-001")
            self.assertEqual(item.variants[0].option_values, {"Color": "Black", "Size": "M"})
            self.assertEqual(str(item.variants[0].compare_at_price), "39.90")
            self.assertEqual(item.variants[0].inventory_quantity, 8)
            self.assertEqual(item.variants[1].sku, "SOURCE-BLUE-L")

    def test_claim_multiple_stores_and_prevent_duplicate_claims(self):
        item_id = self.move_product(self.create_product())
        first_store = self.create_store()
        second_store = self.create_store("shoplazza", "Second store")
        self.claim(item_id, [first_store, second_store])
        self.claim(item_id, [first_store])
        third_store = self.create_store("shopify", "Third store", "third.myshopify.com")
        self.claim(item_id, [third_store])
        with self.app.app_context():
            drafts = StoreProductDraft.query.order_by(StoreProductDraft.store_connection_id).all()
            self.assertEqual(len(drafts), 3)
            self.assertTrue(all(len(draft.variants) == 2 for draft in drafts))
            self.assertEqual(drafts[0].options, [{"name": "Color", "values": ["Black", "Blue"]}, {"name": "Size", "values": ["M", "L"]}])
        response = self.client.get(f"/product-workflow/inbox?tab=claimed&store_id={first_store}&sku=SOURCE-BLUE-L&status=local")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Collected shirt", response.get_data(as_text=True))
        self.assertIn("认领更多店铺", response.get_data(as_text=True))

    def test_editor_sanitizes_html_and_keeps_variant_values(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft_id = draft.id
            variant_ids = [variant.id for variant in draft.variants]
        response = self.client.post(
            f"/product-workflow/drafts/{draft_id}/edit",
            data={
                "title": "Edited shirt", "product_type": "Apparel", "tags": "one, two",
                "description_html": '<p>Allowed</p><script>alert("x")</script>',
                "metafield_product_developer": "Liu XiaoJie",
                "metafield_product_specialist": "Ma RuiTing",
                "metafield_elements": "Sport & Team Spirit",
                "metafield_occasion": "Game Day",
                "metafield_recipient": "Team",
                "metafield_hobby": "Sport",
                "options_json": json.dumps([{"name": "Color", "values": ["Black", "Blue"]}]),
                "variant_count": "2",
                "variant_id-0": str(variant_ids[0]), "variant_options-0": json.dumps({"Color": "Black"}),
                "variant_sku-0": "BLACK", "variant_price-0": "30.50", "variant_compare_at-0": "40.00", "variant_inventory-0": "9",
                "variant_weight-0": "0.4", "variant_length-0": "20", "variant_width-0": "15", "variant_height-0": "3",
                "variant_id-1": str(variant_ids[1]), "variant_options-1": json.dumps({"Color": "Blue"}),
                "variant_sku-1": "BLUE", "variant_price-1": "32.50", "variant_compare_at-1": "", "variant_inventory-1": "4",
                "variant_weight-1": "0.5", "variant_length-1": "21", "variant_width-1": "16", "variant_height-1": "4",
                "after_save": "save",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            draft = db.session.get(StoreProductDraft, draft_id)
            self.assertNotIn("<script", draft.description_html)
            self.assertIn("alert", draft.description_html)
            self.assertEqual([variant.sku for variant in draft.variants], ["BLACK", "BLUE"])
            self.assertEqual(str(draft.variants[0].price), "30.50")
            self.assertEqual(str(draft.variants[0].package_length_cm), "20.00")
            self.assertEqual(draft.product_metafields["product_developer"], "Liu XiaoJie")
            self.assertEqual(draft.product_metafields["hobby"], "Sport")
        page = self.client.get(f"/product-workflow/drafts/{draft_id}/edit").get_data(as_text=True)
        self.assertIn("变体信息", page)
        self.assertNotIn("变种", page)
        self.assertNotIn("编辑图片", page)
        self.assertIn("批量设置（待开发）", page)
        self.assertIn("Product Developer", page)
        self.assertIn("Liu XiaoJie", page)

    def test_shopify_create_update_draft_and_publish_use_same_remote_product(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            store = db.session.get(StoreConnection, store_id)
            store.default_location_id = "gid://shopify/Location/1"
            draft.variants[0].weight_kg = 0.4
            product = {
                "id": "gid://shopify/Product/9",
                "handle": "shirt",
                "status": "DRAFT",
                "onlineStoreUrl": None,
                "media": {"nodes": [
                    {"id": "gid://shopify/MediaImage/1", "status": "READY", "image": {"url": "https://cdn.shopify.com/main.jpg"}},
                    {"id": "gid://shopify/MediaImage/2", "status": "READY", "image": {"url": "https://cdn.shopify.com/side.jpg"}},
                    {"id": "gid://shopify/MediaImage/3", "status": "READY", "image": {"url": "https://cdn.shopify.com/black.jpg"}},
                ]},
                "variants": {"nodes": [{
                    "sku": draft.variants[0].sku,
                    "media": {"nodes": [{
                        "id": "gid://shopify/MediaImage/3",
                        "status": "READY",
                        "image": {"url": "https://cdn.shopify.com/black.jpg"},
                    }]},
                }]},
            }
            draft.product_metafields_json = json.dumps({
                definition["key"]: f'value-{definition["key"]}'
                for definition in PRODUCT_METAFIELD_DEFINITIONS
            })
            adapter = ShopifyAdapter(store)
            adapter._ensure_product_metafield_definitions = MagicMock()
            adapter._graphql = MagicMock(return_value={
                "productSet": {"product": product, "userErrors": []}
            })
            result = adapter.sync_product(draft, publish=False)
            first_variables = adapter._graphql.call_args.args[1]
            self.assertEqual(first_variables["productSet"]["status"], "DRAFT")
            self.assertNotIn("identifier", first_variables)
            self.assertEqual(
                first_variables["productSet"]["variants"][0]["inventoryItem"]["measurement"]["weight"]["unit"],
                "KILOGRAMS",
            )
            file_urls = [item["originalSource"] for item in first_variables["productSet"]["files"]]
            self.assertIn("https://cdn.example/black.jpg", file_urls)
            self.assertTrue(result["remote_media_ready"])
            custom_metafields = {
                item["key"]: item
                for item in first_variables["productSet"]["metafields"]
                if item["namespace"] == "custom"
            }
            self.assertEqual(set(custom_metafields), {
                definition["key"] for definition in PRODUCT_METAFIELD_DEFINITIONS
            })
            self.assertEqual(
                custom_metafields["product_developer"]["value"],
                "value-product_developer",
            )

            self.assertEqual(result["remote_product_id"], "gid://shopify/Product/9")
            draft.remote_product_id = result["remote_product_id"]
            for image, remote in zip(draft.images, result["remote_images"]):
                image.remote_media_id = remote["remote_media_id"]
            draft.variants[0].remote_media_id = result["remote_variant_images"][draft.variants[0].sku]["remote_media_id"]
            adapter.sync_product(draft, publish=True)
            second_variables = adapter._graphql.call_args.args[1]
            self.assertEqual(second_variables["productSet"]["status"], "ACTIVE")
            self.assertEqual(second_variables["identifier"]["id"], result["remote_product_id"])
            self.assertTrue(all("id" in item for item in second_variables["productSet"]["files"]))
            self.assertTrue(all("originalSource" not in item for item in second_variables["productSet"]["files"]))

    def test_publish_replaces_local_images_with_shopify_cdn_and_deletes_files(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        product_relative = f"workflow-tests/{uuid.uuid4().hex}.jpg"
        variant_relative = f"workflow-tests/{uuid.uuid4().hex}.jpg"
        upload_root = Path(self.app.instance_path) / "product_uploads"
        product_file = upload_root / product_relative
        variant_file = upload_root / variant_relative
        try:
            product_file.parent.mkdir(parents=True, exist_ok=True)
            product_file.write_bytes(b"product")
            variant_file.write_bytes(b"variant")
            with self.app.app_context():
                draft = StoreProductDraft.query.one()
                draft_id = draft.id
                draft.images[0].local_path = product_relative
                draft.images[0].public_url = f"https://public.test/product-workflow/uploads/{product_relative}"
                draft.variants[0].local_image_path = variant_relative
                draft.variants[0].image_url = f"https://public.test/product-workflow/uploads/{variant_relative}"
                first_sku = draft.variants[0].sku
                db.session.commit()

            result = {
                "remote_product_id": "gid://shopify/Product/9",
                "remote_handle": "shirt",
                "remote_url": "https://store.test/products/shirt",
                "remote_media_ready": True,
                "remote_images": [
                    {"remote_media_id": "gid://shopify/MediaImage/1", "status": "READY", "url": "https://cdn.shopify.com/main.jpg"},
                    {"remote_media_id": "gid://shopify/MediaImage/2", "status": "READY", "url": "https://cdn.shopify.com/side.jpg"},
                ],
                "remote_variant_images": {
                    first_sku: {
                        "remote_media_id": "gid://shopify/MediaImage/3",
                        "status": "READY",
                        "url": "https://cdn.shopify.com/variant.jpg",
                    },
                },
            }
            with patch("app.services.store_publish_queue.sync_store_product", return_value=result):
                run_store_publish_by_id(draft_id, self.app, publish=True)

            with self.app.app_context():
                draft = db.session.get(StoreProductDraft, draft_id)
                self.assertEqual(draft.images[0].public_url, "https://cdn.shopify.com/main.jpg")
                self.assertIsNone(draft.images[0].local_path)
                self.assertEqual(draft.images[0].remote_media_id, "gid://shopify/MediaImage/1")
                self.assertEqual(draft.variants[0].image_url, "https://cdn.shopify.com/variant.jpg")
                self.assertIsNone(draft.variants[0].local_image_path)
                self.assertEqual(draft.variants[0].remote_media_id, "gid://shopify/MediaImage/3")
            self.assertFalse(product_file.exists())
            self.assertFalse(variant_file.exists())
        finally:
            product_file.unlink(missing_ok=True)
            variant_file.unlink(missing_ok=True)

    def test_publish_keeps_local_file_until_shopify_media_is_ready(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        relative = f"workflow-tests/{uuid.uuid4().hex}.jpg"
        local_file = Path(self.app.instance_path) / "product_uploads" / relative
        try:
            local_file.parent.mkdir(parents=True, exist_ok=True)
            local_file.write_bytes(b"processing")
            with self.app.app_context():
                draft = StoreProductDraft.query.one()
                draft_id = draft.id
                draft.images[0].local_path = relative
                draft.images[0].public_url = f"https://public.test/product-workflow/uploads/{relative}"
                db.session.commit()

            result = {
                "remote_product_id": "gid://shopify/Product/9",
                "remote_images": [
                    {"remote_media_id": "gid://shopify/MediaImage/1", "status": "PROCESSING", "url": None},
                    {"remote_media_id": "gid://shopify/MediaImage/2", "status": "READY", "url": "https://cdn.shopify.com/side.jpg"},
                ],
                "remote_variant_images": {},
                "remote_media_ready": False,
            }
            with patch("app.services.store_publish_queue.sync_store_product", return_value=result):
                run_store_publish_by_id(draft_id, self.app, publish=True)

            with self.app.app_context():
                draft = db.session.get(StoreProductDraft, draft_id)
                self.assertEqual(draft.images[0].remote_media_id, "gid://shopify/MediaImage/1")
                self.assertEqual(
                    draft.images[0].public_url,
                    f"https://public.test/product-workflow/uploads/{relative}",
                )
                self.assertEqual(draft.images[0].local_path, relative)
            self.assertTrue(local_file.exists())
        finally:
            local_file.unlink(missing_ok=True)
    def test_publish_keeps_local_file_when_description_still_references_it(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        relative = f"workflow-tests/{uuid.uuid4().hex}.jpg"
        local_file = Path(self.app.instance_path) / "product_uploads" / relative
        old_url = f"https://public.test/product-workflow/uploads/{relative}"
        try:
            local_file.parent.mkdir(parents=True, exist_ok=True)
            local_file.write_bytes(b"shared")
            with self.app.app_context():
                draft = StoreProductDraft.query.one()
                draft_id = draft.id
                draft.images[0].local_path = relative
                draft.images[0].public_url = old_url
                draft.description_html = f'<p><img src="{old_url}"></p>'
                first_sku = draft.variants[0].sku
                db.session.commit()

            result = {
                "remote_product_id": "gid://shopify/Product/9",
                "remote_images": [
                    {"remote_media_id": "gid://shopify/MediaImage/1", "status": "READY", "url": "https://cdn.shopify.com/main.jpg"},
                    {"remote_media_id": "gid://shopify/MediaImage/2", "status": "READY", "url": "https://cdn.shopify.com/side.jpg"},
                ],
                "remote_variant_images": {
                    first_sku: {
                        "remote_media_id": "gid://shopify/MediaImage/3",
                        "status": "READY",
                        "url": "https://cdn.shopify.com/variant.jpg",
                    },
                },
            }
            with patch("app.services.store_publish_queue.sync_store_product", return_value=result):
                run_store_publish_by_id(draft_id, self.app, publish=True)
            self.assertTrue(local_file.exists())
        finally:
            local_file.unlink(missing_ok=True)

    def test_shopify_creates_pinned_product_metafield_definitions(self):
        store_id = self.create_store()
        with self.app.app_context():
            store = db.session.get(StoreConnection, store_id)
            adapter = ShopifyAdapter(store)
            responses = [{"metafieldDefinitions": {"nodes": []}}]
            responses.extend([
                {
                    "metafieldDefinitionCreate": {
                        "createdDefinition": {
                            "id": f"gid://shopify/MetafieldDefinition/{index}"
                        },
                        "userErrors": [],
                    }
                }
                for index, _definition in enumerate(
                    PRODUCT_METAFIELD_DEFINITIONS, start=1
                )
            ])
            adapter._graphql = MagicMock(side_effect=responses)
            adapter._ensure_product_metafield_definitions()

            self.assertEqual(
                adapter._graphql.call_count,
                1 + len(PRODUCT_METAFIELD_DEFINITIONS),
            )
            create_calls = adapter._graphql.call_args_list[1:]
            for definition, call in zip(
                PRODUCT_METAFIELD_DEFINITIONS, create_calls
            ):
                payload = call.args[1]["definition"]
                self.assertEqual(payload["namespace"], "custom")
                self.assertEqual(payload["key"], definition["key"])
                self.assertEqual(payload["name"], definition["label"])
                self.assertEqual(payload["type"], "single_line_text_field")
                self.assertEqual(payload["ownerType"], "PRODUCT")
                self.assertTrue(payload["pin"])

    def test_shopify_deletes_blank_product_metafields_on_update(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft.product_metafields_json = json.dumps({
                "product_developer": "Liu XiaoJie"
            })
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(return_value={
                "metafieldsDelete": {"deletedMetafields": [], "userErrors": []}
            })
            adapter._delete_blank_product_metafields(
                "gid://shopify/Product/9", draft
            )
            identifiers = adapter._graphql.call_args.args[1]["metafields"]
            deleted_keys = {item["key"] for item in identifiers}
            self.assertNotIn("product_developer", deleted_keys)
            self.assertEqual(deleted_keys, {
                "product_specialist", "elements", "occasion", "recipient", "hobby"
            })

    @patch("app.services.store_publish_service._request_json")
    def test_shopify_token_exchange_uses_form_encoding(self, request_json):
        store_id = self.create_store()
        request_json.return_value = {"access_token": "short-token", "expires_in": 86399}
        with self.app.app_context():
            store = db.session.get(StoreConnection, store_id)
            token = ShopifyAdapter(store)._access_token()
            self.assertEqual(token, "short-token")
            call = request_json.call_args
            self.assertTrue(call.kwargs["form"])
            self.assertEqual(call.kwargs["body"]["grant_type"], "client_credentials")
            self.assertEqual(decrypt_credentials(store.oauth_access_token_encrypted)["access_token"], "short-token")


    @patch("app.services.store_publish_service._request_json")
    def test_shoplazza_create_and_update_publish_payload(self, request_json):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store("shoplazza")
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            store = db.session.get(StoreConnection, store_id)
            adapter = ShoplazzaAdapter(store)
            request_json.return_value = {"product": {"id": "slz-7", "handle": "shirt"}}
            adapter.sync_product(draft, publish=False)
            create_call = request_json.call_args
            self.assertTrue(create_call.args[0].endswith("/products"))
            self.assertFalse(create_call.kwargs["body"]["product"]["published"])
            draft.remote_product_id = "slz-7"
            adapter.sync_product(draft, publish=True)
            update_call = request_json.call_args
            self.assertTrue(update_call.args[0].endswith("/products/slz-7"))
            self.assertTrue(update_call.kwargs["body"]["product"]["published"])

    def test_publish_worker_updates_status_and_redacts_secrets(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft_id = draft.id
        with patch("app.services.store_publish_queue.sync_store_product", return_value={"remote_product_id": "remote-1", "remote_handle": "shirt", "remote_url": "https://store.test/products/shirt"}):
            run_store_publish_by_id(draft_id, self.app, publish=True)
        with self.app.app_context():
            draft = db.session.get(StoreProductDraft, draft_id)
            self.assertEqual(draft.sync_status, "published")
            self.assertFalse(draft.has_pending_changes)
            self.assertEqual(draft.remote_product_id, "remote-1")
            self.assertTrue(draft.remote_published)
        with patch("app.services.store_publish_queue.sync_store_product", side_effect=RuntimeError("client_secret=top-secret")):
            run_store_publish_by_id(draft_id, self.app, publish=True)
        with self.app.app_context():
            draft = db.session.get(StoreProductDraft, draft_id)
            self.assertEqual(draft.sync_status, "failed")
            self.assertNotIn("top-secret", draft.last_error)
            self.assertIn("redacted", draft.last_error)
            self.assertTrue(draft.remote_published)
        editor_page = self.client.get(f"/product-workflow/drafts/{draft_id}/edit").get_data(as_text=True)
        self.assertNotIn("移入待发布", editor_page)
        self.assertIn("更新发布", editor_page)

    def test_publish_route_claims_status_atomically_and_blocks_draft_after_publish(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft_id = StoreProductDraft.query.one().id
        with patch("app.blueprints.product_workflow.routes.enqueue_store_publish", return_value=True) as enqueue:
            first = self.client.post(f"/product-workflow/drafts/{draft_id}/publish")
            second = self.client.post(f"/product-workflow/drafts/{draft_id}/publish")
            self.assertEqual(first.status_code, 302)
            self.assertEqual(second.status_code, 302)
            enqueue.assert_called_once()
            with self.app.app_context():
                draft = db.session.get(StoreProductDraft, draft_id)
                draft.sync_status = "failed"
                draft.remote_published = True
                db.session.commit()
            self.client.post(f"/product-workflow/drafts/{draft_id}/create-remote-draft")
            enqueue.assert_called_once()
    def test_store_route_encrypts_credentials_and_never_echoes_secret(self):
        response = self.client.post(
            "/product-workflow/stores",
            data={
                "platform": "shopify",
                "name": "Route store",
                "shop_domain": "route-store.myshopify.com",
                "client_id": "route-client",
                "client_secret": "route-super-secret",
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("route-super-secret", body)
        self.assertIn("••••••••（已加密保存）", body)
        self.assertIn("write_products", body)
        with self.app.app_context():
            store = StoreConnection.query.filter_by(shop_domain="route-store.myshopify.com").one()
            self.assertNotIn("route-super-secret", store.credentials_encrypted)
            self.assertEqual(decrypt_credentials(store.credentials_encrypted)["client_secret"], "route-super-secret")

    def test_employee_defaults_are_read_only_for_product_workflow(self):
        employee = User(username="employee-check", password_hash="unused", role="employee", is_active=True)
        self.assertTrue(employee.can("product_inbox.view"))
        self.assertTrue(employee.can("stores.view"))
        self.assertFalse(employee.can("product_inbox.move"))
        self.assertFalse(employee.can("product_inbox.publish"))
        self.assertFalse(employee.can("stores.manage"))
    def test_credentials_are_encrypted_and_require_independent_key(self):
        with self.app.app_context():
            encrypted = encrypt_credentials({"access_token": "never-plain"})
            self.assertNotIn("never-plain", encrypted)
            self.assertEqual(decrypt_credentials(encrypted)["access_token"], "never-plain")
            previous = self.app.config["STORE_CREDENTIAL_ENCRYPTION_KEY"]
            self.app.config["STORE_CREDENTIAL_ENCRYPTION_KEY"] = ""
            try:
                with self.assertRaises(CredentialError):
                    encrypt_credentials({"access_token": "x"})
            finally:
                self.app.config["STORE_CREDENTIAL_ENCRYPTION_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
