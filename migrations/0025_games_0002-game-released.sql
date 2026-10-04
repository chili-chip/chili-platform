CREATE TABLE "new__games_game" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "released" bool NOT NULL, "title" varchar(160) NOT NULL, "slug" varchar(180) NOT NULL UNIQUE, "cover" varchar(255) NOT NULL, "data" text NOT NULL, "created_at" datetime NOT NULL, "updated_at" datetime NOT NULL, "owner_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED);
INSERT INTO "new__games_game" ("id", "title", "slug", "cover", "data", "created_at", "updated_at", "owner_id", "released") SELECT "id", "title", "slug", "cover", "data", "created_at", "updated_at", "owner_id", 0 FROM "games_game";
DROP TABLE "games_game";
ALTER TABLE "new__games_game" RENAME TO "games_game";
CREATE INDEX "games_game_owner_id_c554a59d" ON "games_game" ("owner_id");
