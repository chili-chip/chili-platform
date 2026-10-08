CREATE TABLE "store_category" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(80) NOT NULL, "slug" varchar(100) NOT NULL UNIQUE, "description" varchar(280) NOT NULL, "sort_order" smallint unsigned NOT NULL CHECK ("sort_order" >= 0), "created_at" datetime NOT NULL);
ALTER TABLE "store_product" ADD COLUMN "category_id" bigint NULL REFERENCES "store_category" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "store_product_category_id_574bae65" ON "store_product" ("category_id");
