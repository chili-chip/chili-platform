CREATE TABLE "new__token_blacklist_blacklistedtoken" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "blacklisted_at" datetime NOT NULL, "token_id" bigint NOT NULL UNIQUE REFERENCES "token_blacklist_outstandingtoken" ("id") DEFERRABLE INITIALLY DEFERRED);
INSERT INTO "new__token_blacklist_blacklistedtoken" ("blacklisted_at", "token_id", "id") SELECT "blacklisted_at", "token_id", "id" FROM "token_blacklist_blacklistedtoken";
DROP TABLE "token_blacklist_blacklistedtoken";
ALTER TABLE "new__token_blacklist_blacklistedtoken" RENAME TO "token_blacklist_blacklistedtoken";
CREATE TABLE "new__token_blacklist_outstandingtoken" ("token" text NOT NULL, "created_at" datetime NULL, "expires_at" datetime NOT NULL, "user_id" bigint NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED, "jti" varchar(255) NOT NULL UNIQUE, "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT);
INSERT INTO "new__token_blacklist_outstandingtoken" ("token", "created_at", "expires_at", "user_id", "jti", "id") SELECT "token", "created_at", "expires_at", "user_id", "jti", "id" FROM "token_blacklist_outstandingtoken";
DROP TABLE "token_blacklist_outstandingtoken";
ALTER TABLE "new__token_blacklist_outstandingtoken" RENAME TO "token_blacklist_outstandingtoken";
CREATE INDEX "token_blacklist_outstandingtoken_user_id_83bc629a" ON "token_blacklist_outstandingtoken" ("user_id");
