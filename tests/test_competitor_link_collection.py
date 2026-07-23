import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import register_blueprints
from app.blueprints.competitor.routes import is_collection_url, parse_product_urls
from app.extensions import db, login_manager
from app.models import CompetitorProduct, CompetitorTask, User
from app.services.competitor_scraper import (
    CompetitorScraper,
    extract_structured_price,
    parse_product_detail,
    parse_standard_variants,
)


class CompetitorLinkCollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_root = Path(__file__).resolve().parents[1] / "app"
        cls.app = Flask("app", root_path=str(app_root), template_folder="templates", static_folder="static")
        cls.app.config.update(
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)
        login_manager.init_app(cls.app)
        register_blueprints(cls.app)
        with cls.app.app_context():
            db.create_all()
            user = User(
                username="link-tester",
                password_hash="unused",
                role="employee",
                created_at=datetime(2026, 1, 1),
            )
            user.set_permissions(["competitor.view", "competitor.detail", "competitor.create_task", "competitor.manage_sites"])
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def setUp(self):
        with self.app.app_context():
            CompetitorProduct.query.delete()
            CompetitorTask.query.delete()
            db.session.commit()

    def test_parse_product_urls_keeps_valid_unique_lines(self):
        urls = parse_product_urls(
            "https://example.com/products/one\ninvalid\nhttps://example.com/products/one\nhttp://shop.test/p/two"
        )

        self.assertEqual(
            urls,
            ["https://example.com/products/one", "http://shop.test/p/two"],
        )

    @patch("app.blueprints.competitor.routes.enqueue_competitor_task", return_value=True)
    def test_link_mode_creates_task_with_product_urls(self, enqueue_task):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

        response = client.post(
            "/competitor/tasks",
            data={
                "collection_mode": "product_links",
                "product_urls": "https://example.com/products/one\nhttps://shop.test/items/two",
                "collection_cycle": "6h",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["task"]["category_label"], "链接采集")
        self.assertEqual(payload["task"]["sites"], ["https://example.com/products/one", "https://shop.test/items/two"])
        self.assertEqual(payload["task"]["condition"], "逐链接采集 · 2 条")
        enqueue_task.assert_called_once()
        with self.app.app_context():
            task = CompetitorTask.query.one()
            self.assertTrue(task.is_product_link_collection)
            self.assertEqual(task.product_url_list, payload["task"]["sites"])
            self.assertEqual(task.target_sites, "")
            self.assertEqual(task.collection_cycle, "instant")

    @patch("app.blueprints.competitor.routes.enqueue_competitor_task", return_value=True)
    def test_collection_url_in_link_mode_becomes_category_task(self, enqueue_task):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

        category_url = "https://www.foryourcustom.com/collections/best-sellers"
        response = client.post(
            "/competitor/tasks",
            data={
                "collection_mode": "product_links",
                "product_urls": category_url,
                "collection_cycle": "instant",
                "category_scope": "pages",
                "category_page_count": "3",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["task"]
        self.assertEqual(payload["category_label"], "分类采集")
        self.assertEqual(payload["sites"], [category_url])
        self.assertEqual(payload["condition"], "分类前 3 页")
        with self.app.app_context():
            task = CompetitorTask.query.one()
            self.assertTrue(task.is_category_collection)
            self.assertEqual(task.category_url, category_url)
            self.assertEqual(task.product_urls, "")
        enqueue_task.assert_called_once()

    @patch("app.blueprints.competitor.routes.enqueue_competitor_task", return_value=True)
    def test_category_mode_trims_collection_product_suffix(self, enqueue_task):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
        response = client.post(
            "/competitor/tasks",
            data={
                "collection_mode": "category",
                "category_url": (
                    "https://www.petfiestas.com/collections/whats-new/products/"
                    "custom-pet-portrait-embroidered-sweatshirt"
                ),
                "category_scope": "pages",
                "category_page_count": "1",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            task = CompetitorTask.query.one()
            self.assertTrue(task.is_category_collection)
            self.assertEqual(task.category_url, "https://www.petfiestas.com/collections/whats-new")
        enqueue_task.assert_called_once()

    @patch("app.blueprints.competitor.routes.enqueue_competitor_task", return_value=True)
    def test_category_mode_with_direct_product_url_becomes_link_task(self, enqueue_task):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
        product_url = "https://www.petfiestas.com/products/custom-pet-portrait"
        response = client.post(
            "/competitor/tasks",
            data={
                "collection_mode": "category",
                "category_url": product_url,
                "collection_cycle": "1d",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["task"]
        self.assertEqual(payload["category_label"], "链接采集")
        with self.app.app_context():
            task = CompetitorTask.query.one()
            self.assertTrue(task.is_product_link_collection)
            self.assertEqual(task.product_url_list, [product_url])
            self.assertEqual(task.collection_cycle, "instant")
        enqueue_task.assert_called_once()
    def test_category_form_uses_text_url_and_expandable_task_targets(self):
        with self.app.app_context():
            db.session.add(CompetitorTask(
                target_sites="",
                collection_mode="category",
                category_url="https://example.com/collections/very-long-category-url",
                category_scope="all",
                category_page_count=1,
                collection_cycle="instant",
                status="completed",
            ))
            db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
        page = client.get("/competitor").get_data(as_text=True)
        self.assertIn('name="category_url" type="text"', page)
        self.assertIn('placeholder="请填写店铺内分类的网址"', page)
        self.assertIn("该分类下的全部产品", page)
        self.assertIn('class="task-sites-disclosure"', page)
        self.assertIn('data-competitor-field="media_count"', page)
        script_response = client.get("/static/js/app.js")
        script = script_response.get_data(as_text=True)
        script_response.close()
        self.assertIn('cfield("media_count").textContent = images.length;', script)

    def test_category_pagination_stops_after_last_product_page(self):
        scraper = CompetitorScraper()
        pages = {
            "https://example.com/collections/best": '<a href="/products/one">One</a>',
            "https://example.com/collections/best?page=2": '<a href="/products/two">Two</a>',
            "https://example.com/collections/best?page=3": "<html>No products</html>",
        }
        with patch.object(scraper, "fetch_page_html", side_effect=lambda url: pages.get(url, "")) as fetch:
            products = scraper.fetch_category_products(
                "https://example.com/collections/best", collect_all=True, platform="shopify"
            )
        self.assertEqual([item["product_url"] for item in products], [
            "https://example.com/products/one", "https://example.com/products/two"
        ])
        self.assertEqual(fetch.call_count, 3)

    def test_category_products_deduplicate_collection_context_urls(self):
        scraper = CompetitorScraper()
        html = """
        <a href="/collections/whats-new/products/pet-card">Pet card</a>
        <a href="/products/pet-card">Pet card price</a>
        """
        with patch.object(scraper, "fetch_page_html", return_value=html):
            products = scraper.fetch_category_products(
                "https://www.petfiestas.com/collections/whats-new",
                page_count=1,
                platform="shoplazza",
            )
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product_url"], "https://www.petfiestas.com/products/pet-card")
    def test_site_and_category_duplicates_only_refresh_price_and_collection_time(self):
        old_time = datetime.utcnow() - timedelta(days=1)
        with self.app.app_context():
            existing = CompetitorProduct(
                source_domain="example.com",
                source_type="shopify_json",
                title="Original title",
                price="$10.00",
                product_media='{"main": "https://cdn.example/original.jpg"}',
                description="Original description",
                product_url="https://example.com/products/one",
                reviews_count=17,
                collected_at=old_time,
            )
            site_task = CompetitorTask(
                target_sites="example.com",
                collection_mode="competitor_sites",
                products_per_site=20,
                collection_cycle="instant",
                status="collecting",
            )
            db.session.add_all([existing, site_task])
            db.session.commit()

            scraper = CompetitorScraper()
            site_raw = {
                "source_type": "shopify_json",
                "title": "Changed title that must not be saved",
                "price": "$12.00",
                "description": "Changed description",
                "product_media": {"main": "https://cdn.example/changed.jpg"},
                "product_url": "https://example.com/products/one?variant=2",
            }
            with patch.object(scraper, "fetch_products", return_value=[site_raw]), patch(
                "app.services.competitor_scraper.collect_fb_ads", return_value=[]
            ):
                self.assertEqual(scraper.run_collection(site_task), 1)

            product = CompetitorProduct.query.one()
            site_refresh_time = product.collected_at
            self.assertEqual(product.price, "$12.00")
            self.assertEqual(product.previous_price, "$10.00")
            self.assertEqual(product.previous_collected_at, old_time)
            self.assertGreater(site_refresh_time, old_time)
            self.assertEqual(product.title, "Original title")
            self.assertEqual(product.description, "Original description")
            self.assertIn("original.jpg", product.product_media)
            self.assertEqual(product.reviews_count, 17)

            category_task = CompetitorTask(
                target_sites="",
                collection_mode="category",
                category_url="https://example.com/collections/best",
                category_scope="pages",
                category_page_count=1,
                collection_cycle="instant",
                status="collecting",
            )
            db.session.add(category_task)
            db.session.commit()
            category_raw = {**site_raw, "price": "$14.00", "product_url": "https://example.com/products/one"}
            with patch.object(scraper, "fetch_category_products", return_value=[category_raw]):
                self.assertEqual(scraper.run_collection(category_task), 1)

            product = CompetitorProduct.query.one()
            self.assertEqual(product.price, "$14.00")
            self.assertEqual(product.previous_price, "$12.00")
            self.assertEqual(product.previous_collected_at, site_refresh_time)
            self.assertGreaterEqual(product.collected_at, site_refresh_time)
            product_id = product.id

        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
        detail = client.get(f"/competitor/products/{product_id}").get_json()
        self.assertEqual(detail["price"], "$14.00")
        self.assertEqual(detail["previous_price"], "$12.00")
        self.assertTrue(detail["previous_collected_at"])

    def test_duplicate_collection_repairs_only_missing_description_and_invalid_media(self):
        with self.app.app_context():
            existing = CompetitorProduct(
                source_domain="petfiestas.com",
                source_type="direct_product_link",
                platform="shoplazza",
                title="Keep this title",
                price="$12.99",
                product_media=json.dumps({
                    "main": "https://img.staticdj.com/pet_{width}x.jpeg",
                    "carousel": ["https://img.staticdj.com/pet_{width}x.jpeg"],
                }),
                description='<div class="description"></div>',
                variants='[{"sku": "KEEP-VARIANT"}]',
                product_url="https://petfiestas.com/products/pet-card",
                collected_at=datetime.utcnow() - timedelta(days=1),
            )
            db.session.add(existing)
            db.session.commit()
            scraper = CompetitorScraper()
            repaired = {
                "price": "$13.99",
                "description": "<p>Recovered product description.</p>",
                "product_media": {
                    "main": "https://img.staticdj.com/pet.jpeg",
                    "carousel": [
                        "https://img.staticdj.com/pet.jpeg",
                        "https://img.staticdj.com/pet-side.webp",
                    ],
                },
                "variants": [{"sku": "REPLACEMENT-MUST-NOT-BE-SAVED"}],
            }

            with patch.object(scraper, "enrich_product_detail", return_value=repaired) as enrich:
                scraper.save_or_refresh_product(
                    None,
                    "petfiestas.com",
                    {"product_url": existing.product_url, "price": "$13.99"},
                    "shoplazza",
                    [],
                )
            db.session.commit()

            product = CompetitorProduct.query.one()
            self.assertEqual(enrich.call_count, 1)
            self.assertEqual(product.title, "Keep this title")
            self.assertEqual(product.price, "$13.99")
            self.assertIn("Recovered product description", product.description)
            self.assertNotIn("{width}", product.product_media)
            self.assertIn("pet-side.webp", product.product_media)
            self.assertIn("KEEP-VARIANT", product.variants)
            self.assertNotIn("REPLACEMENT-MUST-NOT-BE-SAVED", product.variants)
    def test_collection_url_detection(self):
        self.assertTrue(is_collection_url("https://example.com/collections/best-sellers"))
        self.assertFalse(is_collection_url("https://example.com/products/one"))
        self.assertFalse(is_collection_url("https://example.com/collections/best-sellers/products/one"))
    def test_link_collection_saves_one_product_per_url(self):
        with self.app.app_context():
            task = CompetitorTask(
                target_sites="",
                collection_mode="product_links",
                product_urls="https://example.com/products/one\nhttps://shop.test/items/two",
                collection_cycle="instant",
                status="collecting",
            )
            db.session.add(task)
            db.session.commit()

            scraper = CompetitorScraper()
            with patch.object(
                scraper,
                "enrich_product_detail",
                side_effect=lambda raw: {
                    **raw,
                    "title": "Parsed product",
                    "price": "$20.00",
                    "description": "<p>Product description</p>",
                },
            ):
                saved = scraper.run_collection(task)

            products = CompetitorProduct.query.order_by(CompetitorProduct.product_url).all()
            self.assertEqual(saved, 2)
            self.assertEqual([product.source_domain for product in products], ["example.com", "shop.test"])
            self.assertTrue(all(product.source_type == "direct_product_link" for product in products))

    @patch("app.blueprints.competitor.routes.add_competitor", return_value=True)
    def test_manual_site_route_uses_validated_form_values(self, add_competitor_mock):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

        response = client.post(
            "/competitor/sites/add",
            data={
                "domain": "https://manual.example/products/test",
                "category": "comprehensive",
                "platform": "shopify",
                "description": "Manual competitor",
                "scrape_reason": "Monitor prices",
            },
        )

        self.assertEqual(response.status_code, 302)
        add_competitor_mock.assert_called_once_with(
            "https://manual.example/products/test",
            "comprehensive",
            "Manual competitor",
            "Monitor prices",
            platform="shopify",
            source="manual",
        )

    def test_shoplazza_product_detail_payload_fills_empty_description_and_gallery(self):
        html = """
        <div class="description"></div>
        <script>
        $('.product').product_detail({ product: {
            "description": "<p>Personalized pet Christmas card description.</p>",
            "images": [
                {"src": "//img.staticdj.com/pet-a_{width}x.jpeg"},
                {"src": "http://img.staticdj.com/pet-b.webp"}
            ]
        }});
        </script>
        """

        detail = parse_product_detail("https://www.petfiestas.com/products/pet-card", html)

        self.assertIn("Personalized pet Christmas card description", detail["description"])
        self.assertEqual(detail["images"], [
            "https://img.staticdj.com/pet-a.jpeg",
            "https://img.staticdj.com/pet-b.webp",
        ])

    def test_shoplazza_ymq_product_fills_empty_description_without_replacing_valid_dom(self):
        ymq_payload = """
        <script>
        window.ymq_option = window.ymq_option || {};
        ymq_option.product = {
            "description": "<h5>Custom 3D puff embroidery description.</h5>",
            "images": [{"src": "//img.staticdj.com/puff.webp"}]
        };
        ymq_option.other = {};
        </script>
        """
        empty_detail = parse_product_detail(
            "https://www.foryourcustom.com/products/puff",
            '<div class="description"></div>' + ymq_payload,
        )
        existing_detail = parse_product_detail(
            "https://www.foryourcustom.com/products/puff",
            '<div class="product__description"><p>Existing DOM description.</p></div>' + ymq_payload,
        )

        self.assertIn("Custom 3D puff embroidery description", empty_detail["description"])
        self.assertEqual(empty_detail["images"], ["https://img.staticdj.com/puff.webp"])
        self.assertIn("Existing DOM description", existing_detail["description"])
        self.assertNotIn("Custom 3D puff", existing_detail["description"])

    def test_image_only_payload_description_continues_to_json_ld_text(self):
        html = """
        <script>
        $('.product').product_detail({ product: {
            "description": "<p><img src='//img.staticdj.com/description.webp'></p>"
        }});
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "description": "Complete product description from JSON-LD."}
        </script>
        """

        detail = parse_product_detail("https://www.petfiestas.com/products/pet-card", html)

        self.assertEqual(detail["description"], "Complete product description from JSON-LD.")

    def test_empty_dynamic_fields_continue_to_static_page_without_overwriting_valid_data(self):
        scraper = CompetitorScraper()
        raw = {
            "product_url": "https://www.foryourcustom.com/products/puff",
            "platform": "shoplazza",
            "title": "Collection title",
            "price": "$32.99",
            "description": "",
            "product_media": {"main": "", "carousel": []},
            "variants": [],
            "reviews_count": 0,
        }
        dynamic_html = """
        <meta property="og:title" content="Dynamic product title">
        <meta property="og:price:amount" content="36.99">
        <script>window.SHOPLAZZA = {};</script>
        <div class="description"></div>
        """
        static_html = """
        <meta property="og:title" content="Static product title">
        <meta property="og:price:amount" content="99.99">
        <meta property="og:image" content="https://img.staticdj.com/puff.webp">
        <script>window.SHOPLAZZA = {};</script>
        <script type="application/ld+json">
        {"@type":"Product","description":"Fallback product description."}
        </script>
        <select name="options[Style]"><option>Sweatshirt</option><option>Hoodie</option></select>
        """

        with (
            patch.object(scraper, "fetch_dynamic_html", return_value=dynamic_html),
            patch.object(scraper, "fetch_page_html", return_value=static_html) as fetch_static,
        ):
            enriched = scraper.enrich_product_detail(raw)

        fetch_static.assert_called_once_with(raw["product_url"])
        self.assertEqual(enriched["title"], "Collection title")
        self.assertEqual(enriched["price"], "$36.99")
        self.assertEqual(enriched["description"], "Fallback product description.")
        self.assertEqual(
            enriched["product_media"]["carousel"],
            ["https://img.staticdj.com/puff.webp"],
        )
        self.assertEqual(len(enriched["variants"]), 2)
    def test_shoplazza_structured_integer_price_is_converted_from_cents(self):
        html = """
        <script>window.SHOPLAZZA = {};</script>
        <script type="application/ld+json">
        {"offers": {"price": "7500", "priceCurrency": "USD"}}
        </script>
        """

        self.assertEqual(extract_structured_price(html), "$75.00")
        self.assertEqual(
            extract_structured_price(html.replace('"7500"', '"75.50"')),
            "$75.50",
        )

    def test_standard_variants_ignores_combination_id_select_when_named_options_exist(self):
        html = """
        <select name="options[Material]"><option>Stainless steel</option><option>Sterling silver</option></select>
        <select name="options[Chain Length]"><option>14 inch</option><option>16 inch</option></select>
        <select name="id"><option>Stainless steel / 14 inch - Sold Out</option></select>
        <select name="properties[Engraving]"><option>No</option><option>Yes +$4.95</option></select>
        <select name="options[Language]"><option>English</option><option>French</option></select>
        """

        variants = parse_standard_variants(html)

        self.assertEqual(len(variants), 4)
        self.assertEqual(variants[0]["option_values"], {"Material": "Stainless steel", "Chain Length": "14 inch"})
        self.assertTrue(all("Variant" not in item["option_values"] for item in variants))

    def test_standard_variants_keeps_simple_id_select_as_generic_variant(self):
        html = '<select name="id"><option value="1">Small</option><option value="2">Large</option></select>'

        variants = parse_standard_variants(html)

        self.assertEqual([item["option_values"] for item in variants], [{"Variant": "Small"}, {"Variant": "Large"}])
if __name__ == "__main__":
    unittest.main()
