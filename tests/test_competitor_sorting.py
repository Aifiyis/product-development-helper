import unittest
import warnings
from datetime import datetime
from pathlib import Path

from flask import Flask
from sqlalchemy.exc import LegacyAPIWarning

from app import register_blueprints
from app.blueprints.competitor.routes import resolve_product_sort
from app.extensions import db, login_manager
from app.models import CompetitorProduct, User


class ResolveProductSortTest(unittest.TestCase):
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
                username="sort-tester",
                password_hash="unused",
                role="employee",
                created_at=datetime(2026, 1, 1),
            )
            user.set_permissions(["competitor.view"])
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
            db.session.add_all(
                [
                    CompetitorProduct(
                        source_domain="one.example",
                        title="few",
                        product_created_at=datetime(2024, 1, 1),
                        reviews_count=2,
                        fb_ad_count=None,
                        collected_at=datetime(2026, 1, 1),
                    ),
                    CompetitorProduct(
                        source_domain="two.example",
                        title="many",
                        product_created_at=datetime(2025, 1, 1),
                        reviews_count=20,
                        fb_ad_count=8,
                        collected_at=datetime(2026, 1, 2),
                    ),
                ]
            )
            db.session.commit()

    def test_accepts_supported_sort_fields_and_directions(self):
        for field in ("product_created_at", "reviews_count", "fb_ad_count"):
            with self.subTest(field=field):
                ordering, selected_field, selected_direction = resolve_product_sort(field, "asc")

                self.assertEqual(selected_field, field)
                self.assertEqual(selected_direction, "asc")
                self.assertIn(field, str(ordering[0]))
                self.assertIn("ASC", str(ordering[0]))

    def test_defaults_to_collection_time_for_invalid_parameters(self):
        ordering, selected_field, selected_direction = resolve_product_sort("title", "sideways")

        self.assertIsNone(selected_field)
        self.assertEqual(selected_direction, "desc")
        self.assertIn("collected_at", str(ordering[0]))
        self.assertIn("DESC", str(ordering[0]))

    def test_defaults_direction_to_descending(self):
        ordering, selected_field, selected_direction = resolve_product_sort("reviews_count", "sideways")

        self.assertEqual(selected_field, "reviews_count")
        self.assertEqual(selected_direction, "desc")
        self.assertIn("DESC", str(ordering[0]))

    def test_database_sort_places_missing_values_last(self):
        with self.app.app_context():
            ordering, _, _ = resolve_product_sort("fb_ad_count", "desc")
            titles = [product.title for product in CompetitorProduct.query.order_by(*ordering).all()]

        self.assertEqual(titles, ["many", "few"])

    def test_competitor_page_applies_sort_and_toggles_direction(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyAPIWarning)
            response = client.get("/competitor?sort=reviews_count&direction=asc")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("few"), html.index("many"))
        self.assertIn("sort=reviews_count&amp;direction=desc", html)

    def test_unsorted_page_links_to_descending_sort_first(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LegacyAPIWarning)
            html = client.get("/competitor").get_data(as_text=True)

        self.assertIn("sort=product_created_at&amp;direction=desc", html)


if __name__ == "__main__":
    unittest.main()
