import io
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy.exc import OperationalError

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
from app.services.store_publish_service import ShopifyAdapter, ShoplazzaAdapter, StoreAPIError, validate_draft


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

    def test_user_management_hides_super_admin_id_one(self):
        with self.app.app_context():
            hidden_user = db.session.get(User, self.user_id)
            hidden_user.username = "2011159843@qq.com"
            visible_user = User(
                username="visible-admin@example.com",
                password_hash="unused",
                role="admin",
                is_active=True,
            )
            db.session.add(visible_user)
            db.session.commit()

        page = self.client.get("/auth/users").get_data(as_text=True)

        self.assertNotIn("2011159843@qq.com", page)
        self.assertIn("visible-admin@example.com", page)
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
            self.assertEqual(item.base_sku, f"PDH-{product_id}")

    def test_move_retries_when_sqlite_is_temporarily_locked(self):
        product_id = self.create_product("Retry locked move")
        real_commit = db.session.commit
        commit_attempts = 0

        def commit_with_one_lock():
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise OperationalError("INSERT", {}, Exception("database is locked"))
            return real_commit()

        with patch.object(db.session, "commit", side_effect=commit_with_one_lock):
            response = self.client.post("/product-workflow/inbox/move", data={"product_id": product_id})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(commit_attempts, 2)
        with self.app.app_context():
            item = ProductInboxItem.query.filter_by(source_product_id=product_id).one()
            self.assertEqual(len(item.variants), 2)

    def test_move_lock_failure_tells_user_to_wait_for_collection(self):
        product_id = self.create_product("Locked until collection finishes")
        locked_error = OperationalError("INSERT", {}, Exception("database is locked"))

        with (
            patch.object(db.session, "commit", side_effect=locked_error),
            patch("app.blueprints.product_workflow.routes.time.sleep"),
            self.assertLogs("app", level="ERROR"),
        ):
            response = self.client.post(
                "/product-workflow/inbox/move",
                data={"product_id": product_id},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("认领操作需等待当前采集任务完成后再操作", response.get_data(as_text=True))

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
            self.assertTrue(all(draft.base_sku == draft.inbox_item.base_sku for draft in drafts))
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
                "title": "Edited shirt", "product_type": "Apparel", "category_id": "gid://shopify/TaxonomyCategory/aa-1-13-14", "category_name": "Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts", "base_sku": "SHIRT", "tags": "one, two",
                "description_html": '<p>Allowed</p><script>alert("x")</script>',
                "metafield_product_developer_name": "Liu XiaoJie",
                "metafield_product_specialist_name": "Ma RuiTing",
                "metafield_design": "Sport & Team Spirit",
                "metafield_holiday": "Game Day",
                "metafield_recipient": "Team",
                "metafield_hobby": "Sport",
                "options_json": json.dumps([{"name": "Color", "values": ["Black", "Blue"]}]),
                "variant_count": "2",
                "variant_id-0": str(variant_ids[0]), "variant_options-0": json.dumps({"Color": "Black", "Size": "M"}),
                "variant_sku-0": "BLACK", "variant_price-0": "30.50", "variant_compare_at-0": "40.00", "variant_inventory-0": "9",
                "variant_weight-0": "0.4", "variant_length-0": "20", "variant_width-0": "15", "variant_height-0": "3",
                "variant_id-1": str(variant_ids[1]), "variant_options-1": json.dumps({"Color": "Blue", "Size": "L"}),
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
            self.assertEqual([variant.option_values for variant in draft.variants], [{"Color": "Black"}, {"Color": "Blue"}])
            self.assertEqual(draft.base_sku, "SHIRT")
            self.assertEqual(draft.category_id, "gid://shopify/TaxonomyCategory/aa-1-13-14")
            self.assertEqual(draft.category_name, "Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts")
            self.assertEqual(str(draft.variants[0].price), "30.50")
            self.assertEqual(str(draft.variants[0].package_length_cm), "20.00")
            self.assertEqual(draft.product_metafields["product_developer_name"], "Liu XiaoJie")
            self.assertEqual(draft.product_metafields["hobby"], "Sport")
        page = self.client.get(f"/product-workflow/drafts/{draft_id}/edit").get_data(as_text=True)
        self.assertIn("变体信息", page)
        self.assertNotIn("变种", page)
        self.assertNotIn("编辑图片", page)
        self.assertIn("批量设置（待开发）", page)
        self.assertIn("Product Developer", page)
        self.assertIn("Liu XiaoJie", page)
        self.assertIn("Category", page)
        self.assertIn("产品类型(Type)", page)
        self.assertIn("custom.product_developer_name", page)
        self.assertIn("custom.product_specialist_name", page)
        self.assertIn("custom.design", page)
        self.assertIn("custom.holiday", page)
        self.assertNotIn("custom.product_developer</small>", page)
        self.assertIn("shopify-reference-data", page)
        self.assertIn("shopify-categories", page)
        self.assertIn('data-classic-source="product-types"', page)
        self.assertEqual(page.count('data-classic-source="metafield:'), 6)
        self.assertNotIn("<datalist", page)
        self.assertIn("基础 SKU", page)
        self.assertIn("智能 SKU 生成", page)
        script_response = self.client.get("/static/js/app.js")
        script = script_response.get_data(as_text=True)
        script_response.close()
        self.assertIn("data-sku-option-index", script)
        self.assertIn("if (exact) return { ...exact, options: values };", script)
        self.assertIn('categorySearch.addEventListener("blur"', script)
        self.assertIn('categorySearch.addEventListener("focus"', script)
        self.assertIn("hideCategorySuggestions", script)
        self.assertIn("renderClassicOptions", script)
        css_response = self.client.get("/static/css/custom.css")
        css = css_response.get_data(as_text=True)
        css_response.close()
        self.assertIn(".classic-search-options", css)
        self.assertIn("height: 220px", css)
        self.assertIn("overflow-y: scroll", css)

        invalid_category = self.client.post(
            f"/product-workflow/drafts/{draft_id}/edit",
            data={"title": "Edited shirt", "category_name": "Typed but not selected"},
            follow_redirects=True,
        )
        self.assertIn("不能只填写分类名称", invalid_category.get_data(as_text=True))
        with self.app.app_context():
            draft = db.session.get(StoreProductDraft, draft_id)
            self.assertEqual(draft.category_id, "gid://shopify/TaxonomyCategory/aa-1-13-14")

    def test_publish_uses_only_current_options_when_old_variant_values_remain(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft.options_json = json.dumps([{"name": "Color", "values": ["Black", "Blue"]}])
            self.assertTrue(all("Size" in variant.option_values for variant in draft.variants))
            errors = validate_draft(draft)
            self.assertFalse(any("选项组合不完整" in error for error in errors))
            payload = ShopifyAdapter(db.session.get(StoreConnection, store_id))._product_input(draft, publish=False)
            self.assertTrue(all(
                [item["optionName"] for item in variant["optionValues"]] == ["Color"]
                for variant in payload["variants"]
            ))

    def test_unclaimed_item_can_be_edited_with_variant_image_before_claim(self):
        item_id = self.move_product(self.create_product())
        with self.app.app_context():
            item = db.session.get(ProductInboxItem, item_id)
            variant_id = item.variants[0].id

        inbox_page = self.client.get("/product-workflow/inbox?tab=unclaimed").get_data(as_text=True)
        self.assertIn(f'/product-workflow/inbox/{item_id}/edit', inbox_page)
        editor_page = self.client.get(f"/product-workflow/inbox/{item_id}/edit").get_data(as_text=True)
        self.assertIn("认领前编辑", editor_page)
        script_response = self.client.get("/static/js/app.js")
        editor_script = script_response.get_data(as_text=True)
        script_response.close()
        self.assertIn("data-variant-image-input", editor_script)
        self.assertIn("editorHistory", editor_script)
        self.assertNotIn("Product metafields", editor_page)

        with patch(
            "app.blueprints.product_workflow.routes._save_upload",
            return_value=("https://public.test/variant-black.jpg", "inbox/variant-black.jpg"),
        ):
            response = self.client.post(
                f"/product-workflow/inbox/{item_id}/edit",
                data={
                    "title": "Edited before claim",
                    "product_type": "Apparel",
                    "tags": "edited, shirt",
                    "description_html": "<p>Edited description</p><script>bad()</script>",
                    "options_json": json.dumps([{"name": "Color", "values": ["Black"]}]),
                    "variant_count": "1",
                    "variant_id-0": str(variant_id),
                    "variant_options-0": json.dumps({"Color": "Black"}),
                    "variant_sku-0": "EDITED-BLACK",
                    "variant_price-0": "35.00",
                    "variant_compare_at-0": "45.00",
                    "variant_inventory-0": "12",
                    "variant_weight-0": "0.45",
                    "variant_length-0": "30",
                    "variant_width-0": "20",
                    "variant_height-0": "4",
                    "variant_image-0": (io.BytesIO(b"fake-image"), "black.jpg"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            item = db.session.get(ProductInboxItem, item_id)
            self.assertEqual(item.title, "Edited before claim")
            self.assertEqual(item.options, [{"name": "Color", "values": ["Black"]}])
            self.assertEqual(len(item.variants), 1)
            self.assertEqual(item.variants[0].sku, "EDITED-BLACK")
            self.assertEqual(item.variants[0].image_url, "https://public.test/variant-black.jpg")
            self.assertEqual(item.variants[0].local_image_path, "inbox/variant-black.jpg")
            self.assertNotIn("<script", item.description_html)

        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            self.assertEqual(draft.title, "Edited before claim")
            self.assertEqual(draft.options, [{"name": "Color", "values": ["Black"]}])
            self.assertEqual(draft.variants[0].sku, "EDITED-BLACK")
            self.assertEqual(draft.variants[0].local_image_path, "inbox/variant-black.jpg")

    def test_unclaimed_product_can_be_removed_but_claimed_product_is_protected(self):
        first_product_id = self.create_product("Removable product")
        first_item_id = self.move_product(first_product_id)
        scrape_page = self.client.get("/competitor").get_data(as_text=True)
        self.assertIn(f"/product-workflow/inbox/{first_item_id}/remove", scrape_page)
        response = self.client.post(f"/product-workflow/inbox/{first_item_id}/remove")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(ProductInboxItem, first_item_id))

        second_product_id = self.create_product("Claimed product")
        second_item_id = self.move_product(second_product_id)
        store_id = self.create_store()
        self.claim(second_item_id, [store_id])
        response = self.client.post(f"/product-workflow/inbox/{second_item_id}/remove")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(ProductInboxItem, second_item_id))
            self.assertEqual(StoreProductDraft.query.count(), 1)
    def test_navigation_order_and_product_scrape_toolbar(self):
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        labels = [
            "中控台", "用户管理", "店铺管理", "产品抓取", "产品认领箱",
            "产品扩展", "热门标签发现", "社媒监控", "产品趋势库",
        ]
        positions = [dashboard.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("平台采集", dashboard)

        scrape_page = self.client.get("/competitor").get_data(as_text=True)
        self.assertNotIn('class="btn btn-outline-warning" href="/product-workflow/inbox"', scrape_page)
        self.assertIn("移入产品认领箱", scrape_page)
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
            draft.category_id = "gid://shopify/TaxonomyCategory/aa-1-13-14"
            draft.category_name = "Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts"
            adapter = ShopifyAdapter(store)
            adapter._ensure_product_metafield_definitions = MagicMock()
            adapter._graphql = MagicMock(return_value={
                "productSet": {"product": product, "userErrors": []}
            })
            result = adapter.sync_product(draft, publish=False)
            first_variables = adapter._graphql.call_args.args[1]
            self.assertEqual(first_variables["productSet"]["status"], "DRAFT")
            self.assertEqual(first_variables["productSet"]["category"], "gid://shopify/TaxonomyCategory/aa-1-13-14")
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
                custom_metafields["product_developer_name"]["value"],
                "value-product_developer_name",
            )

            self.assertEqual(result["remote_product_id"], "gid://shopify/Product/9")
            draft.remote_product_id = result["remote_product_id"]
            for image, remote in zip(draft.images, result["remote_images"]):
                image.remote_media_id = remote["remote_media_id"]
            draft.variants[0].remote_media_id = result["remote_variant_images"][draft.variants[0].sku]["remote_media_id"]
            adapter.sync_product(draft, publish=True)
            second_variables = [
                call.args[1]
                for call in adapter._graphql.call_args_list
                if len(call.args) > 1 and "productSet" in call.args[1]
            ][-1]
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

    @patch("app.blueprints.product_workflow.routes.adapter_for")
    def test_shopify_editor_reference_and_category_routes(self, adapter_for):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft_id = StoreProductDraft.query.one().id
        adapter = adapter_for.return_value
        adapter.editor_reference_data.return_value = {
            "metafield_choices": {
                "product_developer_name": ["Nancy", "Liu XiaoJie"],
                "product_specialist_name": ["Nancy", "Ma RuiTing"],
                "design": ["Sport & Team Spirit"],
                "holiday": ["Game Day"],
                "recipient": ["Team"],
                "hobby": ["Sport"],
            },
            "product_types": ["College Sweatshirt", "Embroidered Sweatshirt"],
        }
        adapter.suggest_product_categories.return_value = [{
            "id": "gid://shopify/TaxonomyCategory/aa-1-13-14",
            "name": "Sweatshirts",
            "full_name": "Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts",
        }]
        adapter.search_product_categories.return_value = [{
            "id": "gid://shopify/TaxonomyCategory/aa-1-1-7-4",
            "name": "Sweatshirts",
            "full_name": "Apparel & Accessories > Clothing > Activewear > Activewear Sweatshirts & Hoodies > Sweatshirts",
        }]

        reference = self.client.get(f"/product-workflow/drafts/{draft_id}/shopify-reference-data")
        recommended = self.client.get(
            f"/product-workflow/drafts/{draft_id}/shopify-categories?recommend=1&title=College+Sweatshirt"
        )
        searched = self.client.get(
            f"/product-workflow/drafts/{draft_id}/shopify-categories?q=activewear+sweatshirt"
        )

        self.assertEqual(reference.status_code, 200)
        self.assertEqual(len(reference.get_json()["metafield_choices"]), 6)
        self.assertIn("College Sweatshirt", reference.get_json()["product_types"])
        self.assertEqual(recommended.get_json()["categories"][0]["name"], "Sweatshirts")
        self.assertIn("Activewear", searched.get_json()["categories"][0]["full_name"])
        adapter.suggest_product_categories.assert_called_once_with("College Sweatshirt", first=8)
        adapter.search_product_categories.assert_called_once_with("activewear sweatshirt", first=8)

    def test_shopify_reference_data_parses_choices_and_all_product_type_pages(self):
        store_id = self.create_store()
        with self.app.app_context():
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(side_effect=[
                {
                    "metafieldDefinitions": {"nodes": [
                        {
                            "key": "product_developer_name",
                            "type": {"name": "single_line_text_field"},
                            "validations": [{"name": "choices", "value": json.dumps(["Nancy", "Liu XiaoJie"])}],
                        },
                        {
                            "key": "recipient",
                            "type": {"name": "list.single_line_text_field"},
                            "validations": [{"name": "choices", "value": json.dumps(["Team", "Mom"])}],
                        },
                    ]},
                    "productTypes": {
                        "nodes": ["College Sweatshirt"],
                        "pageInfo": {"hasNextPage": True, "endCursor": "types-page-2"},
                    },
                },
                {
                    "productTypes": {
                        "nodes": ["Embroidered Sweatshirt", "College Sweatshirt"],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                },
            ])

            result = adapter.editor_reference_data()

            self.assertEqual(result["metafield_choices"]["product_developer_name"], ["Nancy", "Liu XiaoJie"])
            self.assertEqual(result["metafield_choices"]["recipient"], ["Team", "Mom"])
            self.assertEqual(result["metafield_choices"]["design"], [])
            self.assertEqual(result["product_types"], ["College Sweatshirt", "Embroidered Sweatshirt"])
            self.assertEqual(adapter._graphql.call_count, 2)
            self.assertEqual(adapter._graphql.call_args_list[1].args[1]["after"], "types-page-2")

    def test_shopify_category_recommendation_uses_product_title_keywords(self):
        store_id = self.create_store()
        with self.app.app_context():
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(return_value={
                "taxonomy": {"categories": {"nodes": [
                    {
                        "id": "gid://shopify/TaxonomyCategory/aa-1-13-14",
                        "name": "Sweatshirts",
                        "fullName": "Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts",
                        "isLeaf": True,
                        "isArchived": False,
                    },
                    {
                        "id": "gid://shopify/TaxonomyCategory/archived",
                        "name": "Archived",
                        "fullName": "Archived",
                        "isLeaf": True,
                        "isArchived": True,
                    },
                ]}}
            })

            categories = adapter.suggest_product_categories(
                "Personalization Texas State University Landmark Buildings Embroidery T Shirt Sweatshirt Hoodie Cs Mj260612006"
            )

            self.assertEqual(len(categories), 1)
            self.assertEqual(categories[0]["name"], "Sweatshirts")
            self.assertEqual(adapter._graphql.call_args.args[1]["search"], "Sweatshirt Hoodie")
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
            metadata = adapter._ensure_product_metafield_definitions()
            field_types = metadata["types"]

            self.assertEqual(
                field_types,
                {definition["key"]: "single_line_text_field" for definition in PRODUCT_METAFIELD_DEFINITIONS},
            )
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

    def test_shopify_uses_existing_text_and_list_metafield_types(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft.product_metafields_json = json.dumps({
                "design": "Sport & Team Spirit",
                "recipient": "Team",
                "hobby": "Sport",
            })
            types = {
                definition["key"]: (
                    "list.single_line_text_field"
                    if definition["key"] in {"recipient", "hobby"}
                    else "single_line_text_field"
                )
                for definition in PRODUCT_METAFIELD_DEFINITIONS
            }
            nodes = [
                {
                    "key": definition["key"],
                    "type": {"name": types[definition["key"]]},
                    "pinnedPosition": index,
                }
                for index, definition in enumerate(PRODUCT_METAFIELD_DEFINITIONS, start=1)
            ]
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(return_value={"metafieldDefinitions": {"nodes": nodes}})

            metadata = adapter._ensure_product_metafield_definitions()
            detected_types = metadata["types"]
            payload = adapter._product_input(draft, publish=False, metafield_types=detected_types)
            metafields = {
                item["key"]: item
                for item in payload["metafields"]
                if item["namespace"] == "custom"
            }

            self.assertEqual(detected_types, types)
            self.assertEqual(metafields["design"]["type"], "single_line_text_field")
            self.assertEqual(metafields["design"]["value"], "Sport & Team Spirit")
            self.assertEqual(metafields["recipient"]["type"], "list.single_line_text_field")
            self.assertEqual(json.loads(metafields["recipient"]["value"]), ["Team"])
            self.assertEqual(metafields["hobby"]["type"], "list.single_line_text_field")
            self.assertEqual(json.loads(metafields["hobby"]["value"]), ["Sport"])
            adapter._graphql.assert_called_once()

    def test_shopify_reports_all_incompatible_metafield_types(self):
        store_id = self.create_store()
        with self.app.app_context():
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(return_value={
                "metafieldDefinitions": {"nodes": [
                    {"key": "recipient", "type": {"name": "multi_line_text_field"}, "pinnedPosition": 1},
                    {"key": "hobby", "type": {"name": "number_integer"}, "pinnedPosition": 2},
                ]}
            })
            with self.assertRaises(StoreAPIError) as context:
                adapter._ensure_product_metafield_definitions()
            message = str(context.exception)
            self.assertIn("custom.recipient=multi_line_text_field", message)
            self.assertIn("custom.hobby=number_integer", message)
            adapter._graphql.assert_called_once()

    def test_shopify_rejects_values_outside_existing_choice_dataset(self):
        item_id = self.move_product(self.create_product())
        store_id = self.create_store()
        self.claim(item_id, [store_id])
        with self.app.app_context():
            draft = StoreProductDraft.query.one()
            draft.product_metafields_json = json.dumps({"recipient": "Unknown recipient"})
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._ensure_product_metafield_definitions = MagicMock(return_value={
                "types": {"recipient": "list.single_line_text_field"},
                "choices": {"recipient": ["Team", "Mom"]},
            })
            adapter._graphql = MagicMock()

            with self.assertRaises(StoreAPIError) as context:
                adapter.sync_product(draft, publish=False)

            self.assertIn("Recipient", str(context.exception))
            self.assertIn("不在店铺可选数据集中", str(context.exception))
            adapter._graphql.assert_not_called()

    def test_shopify_deletes_only_existing_legacy_metafield_values(self):
        store_id = self.create_store()
        with self.app.app_context():
            adapter = ShopifyAdapter(db.session.get(StoreConnection, store_id))
            adapter._graphql = MagicMock(side_effect=[
                {
                    "node": {"metafields": {"nodes": [
                        {"namespace": "custom", "key": "product_developer"},
                        {"namespace": "custom", "key": "elements"},
                        {"namespace": "custom", "key": "product_developer_name"},
                    ]}}
                },
                {"metafieldsDelete": {"deletedMetafields": [], "userErrors": []}},
            ])

            adapter._delete_legacy_product_metafields("gid://shopify/Product/9")

            identifiers = adapter._graphql.call_args_list[1].args[1]["metafields"]
            self.assertEqual({item["key"] for item in identifiers}, {"product_developer", "elements"})
            self.assertTrue(all(item["ownerId"] == "gid://shopify/Product/9" for item in identifiers))

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
                "product_specialist_name", "design", "holiday", "recipient", "hobby"
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
        self.assertNotIn('class="workflow-steps"', body)
        self.assertIn("前往认领箱", body)
        self.assertNotIn("返回认领箱", body)
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
