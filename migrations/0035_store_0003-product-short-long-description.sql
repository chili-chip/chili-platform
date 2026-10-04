ALTER TABLE store_product ADD COLUMN short_description varchar(280) NOT NULL DEFAULT '';
ALTER TABLE store_product ADD COLUMN long_description text NOT NULL DEFAULT '';
UPDATE store_product SET short_description = substr(description, 1, 280) WHERE ifnull(description, '') != '' AND ifnull(short_description, '') = '';
