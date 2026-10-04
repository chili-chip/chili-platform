CREATE TABLE "token_blacklist_blacklistedtoken" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "blacklisted_at" datetime NOT NULL);
CREATE TABLE "token_blacklist_outstandingtoken" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "jti" char(32) NOT NULL UNIQUE, "token" text NOT NULL, "created_at" datetime NOT NULL, "expires_at" datetime NOT NULL, "user_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE "new__token_blacklist_blacklistedtoken" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "blacklisted_at" datetime NOT NULL, "token_id" integer NOT NULL UNIQUE REFERENCES "token_blacklist_outstandingtoken" ("id") DEFERRABLE INITIALLY DEFERRED);
INSERT INTO "new__token_blacklist_blacklistedtoken" ("id", "blacklisted_at", "token_id") SELECT "id", "blacklisted_at", NULL FROM "token_blacklist_blacklistedtoken";
DROP TABLE "token_blacklist_blacklistedtoken";
ALTER TABLE "new__token_blacklist_blacklistedtoken" RENAME TO "token_blacklist_blacklistedtoken";
CREATE INDEX "token_blacklist_outstandingtoken_user_id_83bc629a" ON "token_blacklist_outstandingtoken" ("user_id");
