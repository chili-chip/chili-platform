ALTER TABLE store_product ADD COLUMN stripe_product_id varchar(255) NOT NULL DEFAULT '';
ALTER TABLE store_product ADD COLUMN stripe_price_id varchar(255) NOT NULL DEFAULT '';
ALTER TABLE store_order ADD COLUMN shipping_status varchar(24) NOT NULL DEFAULT 'awaiting_payment';
ALTER TABLE store_order ADD COLUMN stripe_customer_id varchar(255) NOT NULL DEFAULT '';
