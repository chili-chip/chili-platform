"""Spreadsheet import and export for the store catalog (django-import-export).

Every resource matches rows to existing records by ``slug``, so importing the
same file twice updates rows instead of adding duplicates. A blank slug is
filled from the name. Products refer to their category and delivery options by
slug. Import categories and delivery options before the products that use them.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from import_export import fields, resources, widgets

from store.models import Category, DeliveryOption, Product


class SlugMatchedResource(resources.ModelResource):
    def before_import_row(self, row, **kwargs):
        if not (row.get("slug") or "").strip() and (row.get("name") or "").strip():
            row["slug"] = slugify(row["name"])

    def import_instance(self, instance, row, **kwargs):
        # The package only reads many-to-many columns after saving the row, and
        # not at all in a preview on D1. Check them up front so a bad value
        # shows in the preview and stops the row before it is saved.
        errors = {}
        try:
            super().import_instance(instance, row, **kwargs)
        except ValidationError as exc:
            errors = exc.update_error_dict(errors)
        for field in self.get_import_fields():
            if not isinstance(field.widget, widgets.ManyToManyWidget):
                continue
            if field.column_name not in row:
                continue
            try:
                field.clean(row, **kwargs)
            except ValueError as exc:
                errors[field.attribute] = [ValidationError(str(exc), code="invalid")]
        if errors:
            raise ValidationError(errors)


class StrictManyToManyWidget(widgets.ManyToManyWidget):
    """Like ManyToManyWidget, but an unknown value fails the row instead of being dropped."""

    def clean(self, value, row=None, **kwargs):
        queryset = super().clean(value, row=row, **kwargs)
        if not value or isinstance(value, (int, float)):
            return queryset
        wanted = {part.strip() for part in str(value).split(self.separator) if part.strip()}
        found = {str(v) for v in queryset.values_list(self.field, flat=True)}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError(f"Unknown {self.field}: {', '.join(missing)}.")
        return queryset


class StrictForeignKeyWidget(widgets.ForeignKeyWidget):
    """Names the missing value instead of raising a bare DoesNotExist."""

    def clean(self, value, row=None, **kwargs):
        try:
            return super().clean(value, row=row, **kwargs)
        except self.model.DoesNotExist:
            raise ValueError(f"Unknown {self.field}: {value}.") from None


class PriceWidget(widgets.IntegerWidget):
    def clean(self, value, row=None, **kwargs):
        cents = super().clean(value, row=row, **kwargs)
        if cents is not None and cents < 1:
            raise ValueError("Price must be at least 1 cent.")
        return cents


class CountriesWidget(widgets.CharWidget):
    """Comma-separated ISO codes, limited to the countries the store ships to."""

    def clean(self, value, row=None, **kwargs):
        text = super().clean(value, row=row, **kwargs) or ""
        codes = [code.strip().upper() for code in text.split(",") if code.strip()]
        allowed = set(getattr(settings, "STORE_SHIPPING_COUNTRIES", []))
        unknown = sorted(set(codes) - allowed)
        if unknown:
            raise ValueError(f"The store does not ship to: {', '.join(unknown)}.")
        return ",".join(codes)


class CategoryResource(SlugMatchedResource):
    class Meta:
        model = Category
        fields = ("name", "slug", "description", "sort_order")
        export_order = fields
        import_id_fields = ("slug",)
        skip_unchanged = True
        clean_model_instances = True


class DeliveryOptionResource(SlugMatchedResource):
    countries = fields.Field(
        attribute="countries", column_name="countries", widget=CountriesWidget()
    )

    class Meta:
        model = DeliveryOption
        fields = (
            "name",
            "slug",
            "description",
            "estimate",
            "price_cents",
            "free_over_cents",
            "countries",
            "requires_address",
            "is_active",
            "sort_order",
        )
        export_order = fields
        import_id_fields = ("slug",)
        skip_unchanged = True
        clean_model_instances = True


class ProductResource(SlugMatchedResource):
    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if "currency" in row and not (row["currency"] or "").strip():
            row["currency"] = getattr(settings, "STORE_CURRENCY", "eur")

    category = fields.Field(
        attribute="category",
        column_name="category",
        widget=StrictForeignKeyWidget(Category, field="slug"),
    )
    delivery_options = fields.Field(
        attribute="delivery_options",
        column_name="delivery_options",
        widget=StrictManyToManyWidget(DeliveryOption, field="slug"),
    )
    price_cents = fields.Field(
        attribute="price_cents", column_name="price_cents", widget=PriceWidget()
    )
    # Export only: Stripe ids belong to the Stripe sync, not to the spreadsheet.
    stripe_product_id = fields.Field(
        attribute="stripe_product_id", column_name="stripe_product_id", readonly=True
    )
    stripe_price_id = fields.Field(
        attribute="stripe_price_id", column_name="stripe_price_id", readonly=True
    )

    class Meta:
        model = Product
        fields = (
            "name",
            "slug",
            "category",
            "sku",
            "price_cents",
            "currency",
            "stock",
            "is_digital",
            "delivery_options",
            "is_active",
            "short_description",
            "long_description",
            "stripe_product_id",
            "stripe_price_id",
        )
        export_order = fields
        import_id_fields = ("slug",)
        skip_unchanged = True
        clean_model_instances = True
