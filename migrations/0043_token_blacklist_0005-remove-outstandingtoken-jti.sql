CREATE TABLE "new__token_blacklist_outstandingtoken" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "token" text NOT NULL, "created_at" datetime NOT NULL, "expires_at" datetime NOT NULL, "user_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED, "jti_hex" varchar(255) NOT NULL UNIQUE);
INSERT INTO "new__token_blacklist_outstandingtoken" ("id", "token", "created_at", "expires_at", "user_id", "jti_hex") SELECT "id", "token", "created_at", "expires_at", "user_id", "jti_hex" FROM "token_blacklist_outstandingtoken";
DROP TABLE "token_blacklist_outstandingtoken";
ALTER TABLE "new__token_blacklist_outstandingtoken" RENAME TO "token_blacklist_outstandingtoken";
CREATE INDEX "token_blacklist_outstandingtoken_user_id_83bc629a" ON "token_blacklist_outstandingtoken" ("user_id");
