CREATE TABLE "new__auth_group" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "name" varchar(150) NOT NULL UNIQUE);
INSERT INTO "new__auth_group" ("id", "name") SELECT "id", "name" FROM "auth_group";
DROP TABLE "auth_group";
ALTER TABLE "new__auth_group" RENAME TO "auth_group";
