BEGIN;
--
-- Create model Junction
--
CREATE TABLE "traffic_junction" ("id" varchar(32) NOT NULL PRIMARY KEY, "name" varchar(100) NOT NULL, "config" text NOT NULL CHECK ((JSON_VALID("config") OR "config" IS NULL)), "auto_ack" bool NOT NULL, "created_at" datetime NOT NULL);
--
-- Create model JunctionState
--
CREATE TABLE "traffic_junctionstate" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "data" text NOT NULL CHECK ((JSON_VALID("data") OR "data" IS NULL)), "version" integer unsigned NOT NULL CHECK ("version" >= 0), "updated_at" datetime NOT NULL, "junction_id" varchar(32) NOT NULL UNIQUE REFERENCES "traffic_junction" ("id") DEFERRABLE INITIALLY DEFERRED);
--
-- Create model ControllerCommand
--
CREATE TABLE "traffic_controllercommand" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "command_id" varchar(64) NOT NULL, "direction" varchar(10) NOT NULL, "requested_state" varchar(10) NOT NULL, "status" varchar(12) NOT NULL, "attempts" smallint unsigned NOT NULL CHECK ("attempts" >= 0), "issued_at" datetime NOT NULL, "acked_at" datetime NULL, "junction_id" varchar(32) NOT NULL REFERENCES "traffic_junction" ("id") DEFERRABLE INITIALLY DEFERRED, CONSTRAINT "uniq_cmd_per_junction" UNIQUE ("junction_id", "command_id"));
--
-- Create model AuditEvent
--
CREATE TABLE "traffic_auditevent" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "event_type" varchar(40) NOT NULL, "payload" text NOT NULL CHECK ((JSON_VALID("payload") OR "payload" IS NULL)), "created_at" datetime NOT NULL, "junction_id" varchar(32) NOT NULL REFERENCES "traffic_junction" ("id") DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX "traffic_controllercommand_junction_id_f4d9b7d0" ON "traffic_controllercommand" ("junction_id");
CREATE INDEX "traffic_auditevent_event_type_d05ff66a" ON "traffic_auditevent" ("event_type");
CREATE INDEX "traffic_auditevent_junction_id_f716df97" ON "traffic_auditevent" ("junction_id");
CREATE INDEX "traffic_aud_junctio_7c84f3_idx" ON "traffic_auditevent" ("junction_id", "id" DESC);
COMMIT;
