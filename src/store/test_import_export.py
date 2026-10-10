from __future__ import annotations

import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import tablib
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings

from store.models import Category, DeliveryOption, Product
from store.resources import CategoryResource, DeliveryOptionResource, ProductResource

User = get_user_model()

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "import"


@contextmanager
def like_d1():
    """D1 has no transactions; import the way the Worker does."""
    with (
        patch.object(connection.features, "supports_transactions", False),
        override_settings(IMPORT_EXPORT_USE_TRANSACTIONS=False),
    ):
        yield


def dataset(headers, *rows):
    return tablib.Dataset(*rows, headers=headers)


def import_rows(resource, data, dry_run=False):
    with like_d1():
        return resource.import_data(data, dry_run=dry_run)


def row_errors(result):
    messages = []
    for row in result.invalid_rows:
        for errors in row.error_dict.values():
            messages.extend(str(e) for e in errors)
    for row in result.rows:
        messages.extend(str(e.error) for e in row.errors)
    return messages


@override_settings(STRIPE_SYNC_ENABLED=False)
class StoreResourceTests(TestCase):
    def test_sample_files_import_in_order(self):
        for resource, name in (
            (CategoryResource(), "categories.csv"),
            (DeliveryOptionResource(), "delivery-options.csv"),
            (ProductResource(), "products.csv"),
        ):
            data = tablib.Dataset().load((SAMPLES / name).read_text(), format="csv")
            result = import_rows(resource, data)
            self.assertFalse(result.has_errors() or result.has_validation_errors(), row_errors(result))

        kit = Product.objects.get(slug="vgc-zero-kit")
        self.assertEqual(kit.category.slug, "handhelds")
        self.assertEqual(kit.price_cents, 4999)
        self.assertIn("- Buttons", kit.long_description)
        self.assertEqual(
            sorted(kit.delivery_options.values_list("slug", flat=True)),
            ["eu-standard", "pickup", "pl-express"],
        )
        self.assertFalse(Product.objects.get(slug="spare-buttons").delivery_options.exists())
        pickup = DeliveryOption.objects.get(slug="pickup")
        self.assertFalse(pickup.requires_address)
        self.assertEqual(pickup.countries, "PL")
        self.assertIsNone(pickup.free_over_cents)

    def test_xlsx_imports(self):
        sheet = dataset(["name", "slug", "sort_order"], ("Parts", "parts", 3))
        data = tablib.Dataset().load(sheet.export("xlsx"), format="xlsx")
        result = import_rows(CategoryResource(), data)
        self.assertFalse(result.has_validation_errors(), row_errors(result))
        self.assertEqual(Category.objects.get().sort_order, 3)

    def test_reimport_updates_by_slug(self):
        Category.objects.create(name="Parts", slug="parts", sort_order=5)
        result = import_rows(
            CategoryResource(),
            dataset(["name", "slug", "sort_order"], ("Spare parts", "parts", "2")),
        )
        self.assertEqual(result.totals["update"], 1)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(Category.objects.get().name, "Spare parts")

    def test_unchanged_rows_are_skipped(self):
        Category.objects.create(name="Parts", slug="parts", sort_order=2)
        result = import_rows(
            CategoryResource(),
            dataset(["name", "slug", "sort_order"], ("Parts", "parts", "2")),
        )
        self.assertEqual(result.totals["skip"], 1)

    def test_blank_slug_comes_from_name_so_reimport_matches(self):
        data = dataset(["name", "slug"], ("Shells & Cases", ""))
        import_rows(CategoryResource(), data)
        import_rows(CategoryResource(), dataset(["name", "slug"], ("Shells & Cases", "")))
        self.assertEqual(list(Category.objects.values_list("slug", flat=True)), ["shells-cases"])

    def test_missing_columns_keep_defaults(self):
        import_rows(
            ProductResource(),
            dataset(["name", "price_cents"], ("Sticker", "150")),
        )
        sticker = Product.objects.get(slug="sticker")
        self.assertEqual((sticker.stock, sticker.currency, sticker.is_active), (0, "eur", True))

    def test_blank_currency_uses_store_currency(self):
        import_rows(
            ProductResource(),
            dataset(["name", "price_cents", "currency"], ("Sticker", "150", "")),
        )
        self.assertEqual(Product.objects.get().currency, "eur")

    def test_unknown_category_fails_the_row(self):
        result = import_rows(
            ProductResource(),
            dataset(["name", "price_cents", "category"], ("Kit", "100", "nope")),
        )
        self.assertTrue(result.has_validation_errors())
        self.assertIn("Unknown slug: nope.", row_errors(result))
        self.assertFalse(Product.objects.exists())

    def test_unknown_delivery_option_fails_the_row(self):
        DeliveryOption.objects.create(name="EU", slug="eu-standard", price_cents=500)
        result = import_rows(
            ProductResource(),
            dataset(
                ["name", "price_cents", "delivery_options"],
                ("Kit", "100", "eu-standard, eu-express"),
            ),
        )
        self.assertTrue(result.has_validation_errors())
        self.assertIn("Unknown slug: eu-express.", row_errors(result))
        self.assertFalse(Product.objects.exists())

    def test_preview_reports_unknown_delivery_option(self):
        result = import_rows(
            ProductResource(),
            dataset(["name", "price_cents", "delivery_options"], ("Kit", "100", "nope")),
            dry_run=True,
        )
        self.assertIn("Unknown slug: nope.", row_errors(result))

    def test_price_must_be_positive(self):
        result = import_rows(ProductResource(), dataset(["name", "price_cents"], ("Kit", "0")))
        self.assertIn("Price must be at least 1 cent.", row_errors(result))

    def test_delivery_countries_are_limited_to_shipping_countries(self):
        result = import_rows(
            DeliveryOptionResource(),
            dataset(["name", "countries"], ("Far away", "pl, us")),
        )
        self.assertIn("The store does not ship to: US.", row_errors(result))
        ok = import_rows(DeliveryOptionResource(), dataset(["name", "countries"], ("Near", "pl, de")))
        self.assertFalse(ok.has_validation_errors())
        self.assertEqual(DeliveryOption.objects.get().countries, "PL,DE")

    def test_dry_run_saves_nothing_without_transactions(self):
        DeliveryOption.objects.create(name="EU", slug="eu-standard", price_cents=500)
        result = import_rows(
            ProductResource(),
            dataset(["name", "price_cents", "delivery_options"], ("Kit", "100", "eu-standard")),
            dry_run=True,
        )
        self.assertEqual(result.totals["new"], 1)
        self.assertFalse(Product.objects.exists())

    def test_export_round_trips(self):
        category = Category.objects.create(name="Parts", slug="parts")
        option = DeliveryOption.objects.create(name="EU", slug="eu-standard", price_cents=500)
        product = Product.objects.create(
            name="Kit",
            slug="kit",
            category=category,
            price_cents=999,
            stripe_product_id="prod_123",
        )
        product.delivery_options.add(option)

        exported = ProductResource().export()
        self.assertEqual(exported.headers[:3], ["name", "slug", "category"])
        row = dict(zip(exported.headers, exported[0]))
        self.assertEqual(row["category"], "parts")
        self.assertEqual(row["delivery_options"], "eu-standard")
        self.assertEqual(row["stripe_product_id"], "prod_123")

        row["stripe_product_id"] = "prod_overwritten"
        row["price_cents"] = "1099"
        result = import_rows(ProductResource(), dataset(list(row), tuple(row.values())))
        self.assertFalse(result.has_validation_errors(), row_errors(result))
        product.refresh_from_db()
        self.assertEqual(product.price_cents, 1099)
        self.assertEqual(product.stripe_product_id, "prod_123")


@override_settings(STRIPE_SYNC_ENABLED=False)
class StoreAdminImportTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        self.staff = User.objects.create_superuser(
            username="admin", email="admin@chili.example", password="supersecret"
        )
        self.client.force_login(self.staff)

    def preview(self, model: str, name: str, content: bytes):
        response = self.client.post(
            f"/admin/store/{model}/import/",
            {
                "format": "0",  # CSV
                "import_file": SimpleUploadedFile(name, content, content_type="text/csv"),
            },
        )
        self.assertEqual(response.status_code, 200)
        return response

    def confirm(self, model: str, response):
        html = response.content.decode()
        fields = dict(re.findall(r'name="(\w+)"[^>]*value="([^"]*)"', html))
        fields.update(
            {
                k: v
                for v, k in re.findall(r'value="([^"]*)"[^>]*name="(\w+)"', html)
                if k not in fields
            }
        )
        return self.client.post(f"/admin/store/{model}/process_import/", fields)

    def test_preview_then_confirm_imports_through_media_storage(self):
        with like_d1():
            response = self.preview(
                "category", "categories.csv", (SAMPLES / "categories.csv").read_bytes()
            )
            self.assertContains(response, "Handhelds")
            self.assertFalse(Category.objects.exists())
            stored = list(Path(self.media, "django-import-export").iterdir())
            self.assertEqual(len(stored), 1)

            # The upload waits in media storage, but /media/ does not serve it.
            leak = self.client.get(f"/media/django-import-export/{stored[0].name}")
            self.assertEqual(leak.status_code, 404)

            done = self.confirm("category", response)
        self.assertEqual(done.status_code, 302)
        self.assertEqual(
            list(Category.objects.values_list("slug", flat=True)), ["handhelds", "parts"]
        )
        self.assertEqual(list(Path(self.media, "django-import-export").iterdir()), [])

    def test_confirmed_product_import_syncs_to_stripe(self):
        csv = b"name,slug,price_cents\nKit,kit,999\n"
        with like_d1(), patch("store.admin.sync_product_to_stripe") as sync:
            response = self.preview("product", "products.csv", csv)
            sync.assert_not_called()
            self.confirm("product", response)
        sync.assert_called_once()
        self.assertEqual(sync.call_args.args[0].slug, "kit")

    def test_export_downloads_csv(self):
        Category.objects.create(name="Parts", slug="parts")
        response = self.client.post(
            "/admin/store/category/export/",
            {"format": "0", "export_items": [], "categoryresource_name": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("Parts", response.content.decode())

    def test_formats_are_csv_xlsx_json(self):
        response = self.client.get("/admin/store/product/import/")
        self.assertContains(response, "csv")
        self.assertContains(response, "xlsx")
        self.assertContains(response, "json")
        self.assertNotContains(response, ">yaml<")
