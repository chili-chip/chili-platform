CREATE TABLE "community_forumcategory" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(80) NOT NULL UNIQUE, "slug" varchar(80) NOT NULL UNIQUE, "description" text NOT NULL);
CREATE TABLE "community_forumpost" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "title" varchar(160) NOT NULL, "slug" varchar(180) NOT NULL UNIQUE, "content" text NOT NULL, "created_at" datetime NOT NULL, "author_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED, "category_id" bigint NOT NULL REFERENCES "community_forumcategory" ("id") DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE "community_forumcomment" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "content" text NOT NULL, "created_at" datetime NOT NULL, "author_id" bigint NOT NULL REFERENCES "accounts_user" ("id") DEFERRABLE INITIALLY DEFERRED, "post_id" bigint NOT NULL REFERENCES "community_forumpost" ("id") DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX "community_forumpost_author_id_26754b49" ON "community_forumpost" ("author_id");
CREATE INDEX "community_forumpost_category_id_53946a48" ON "community_forumpost" ("category_id");
CREATE INDEX "community_forumcomment_author_id_e37f2bc2" ON "community_forumcomment" ("author_id");
CREATE INDEX "community_forumcomment_post_id_ab62cdf6" ON "community_forumcomment" ("post_id");
