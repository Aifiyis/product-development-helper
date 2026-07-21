import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import register_blueprints
from app.blueprints.competitor.routes import is_collection_url, parse_product_urls
from app.extensions import db, login_manager
from app.models import CompetitorProduct, CompetitorTask, User
from app.services.competitor_scraper import CompetitorScraper, extract_structured_price, parse_standard_variants


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

    def test_collection_url_detection(self):
        self.assertTrue(is_collection_url("https://example.com/collections/best-sellers"))
        self.assertFalse(is_collection_url("https://example.com/products/one"))
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
