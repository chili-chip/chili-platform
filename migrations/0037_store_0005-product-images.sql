CREATE TABLE "store_productimage" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "image" varchar(100) NOT NULL, "alt" varchar(120) NOT NULL, "sort_order" smallint unsigned NOT NULL CHECK ("sort_order" >= 0), "created_at" datetime NOT NULL, "product_id" bigint NOT NULL REFERENCES "store_product" ("id") DEFERRABLE INITIALLY DEFERRED);
ALTER TABLE store_product DROP COLUMN image_url;
CREATE INDEX "store_productimage_product_id_e50e4046" ON "store_productimage" ("product_id");
