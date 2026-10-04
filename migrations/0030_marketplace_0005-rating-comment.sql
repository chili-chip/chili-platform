CREATE TABLE "new__marketplace_rating" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "comment" varchar(500) NOT NULL, "stars" smallint unsigned NOT NULL CHECK ("stars" >= 0), "created_at" datetime NOT NULL, "game_id" bigint NOT NULL REFERENCES "games_game" ("id") DEFERRABLE INITIALLY DEFERRED, "user_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED, CONSTRAINT "uniq_game_rating" UNIQUE ("user_id", "game_id"), CONSTRAINT "rating_stars_1_to_5" CHECK (("stars" >= 1 AND "stars" <= 5)));
INSERT INTO "new__marketplace_rating" ("id", "stars", "created_at", "game_id", "user_id", "comment") SELECT "id", "stars", "created_at", "game_id", "user_id", '' FROM "marketplace_rating";
DROP TABLE "marketplace_rating";
ALTER TABLE "new__marketplace_rating" RENAME TO "marketplace_rating";
CREATE INDEX "marketplace_rating_game_id_86f2fbec" ON "marketplace_rating" ("game_id");
CREATE INDEX "marketplace_rating_user_id_0fc12f10" ON "marketplace_rating" ("user_id");
