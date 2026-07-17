import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import register_blueprints
from app.blueprints.competitor.routes import parse_product_urls
from app.extensions import db, login_manager
from app.models import CompetitorProduct, CompetitorTask, User
from app.services.competitor_scraper import CompetitorScraper, extract_structured_price


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
            user.set_permissions(["competitor.view", "competitor.create_task", "competitor.manage_sites"])
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
                "collection_cycle": "instant",
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

if __name__ == "__main__":
    unittest.main()
