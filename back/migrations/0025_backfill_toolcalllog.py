from django.db import migrations


class RunIfPostgres(migrations.RunSQL):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class RunIfSqlite(migrations.RunSQL):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "sqlite":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "sqlite":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    """Backfill back_toolcalllog — 0021 only recorded model state (SeparateDatabaseAndState
    with empty database_operations), so the table was never created on fresh databases."""

    dependencies = [
        ("back", "0024_conversation_customer_address"),
    ]

    operations = [
        RunIfPostgres(
            sql=[
                (
                    "CREATE TABLE IF NOT EXISTS back_toolcalllog ("
                    "id bigserial NOT NULL PRIMARY KEY, "
                    "reply_id varchar(255) NOT NULL, "
                    "iteration integer NOT NULL, "
                    "tool_name varchar(100) NOT NULL, "
                    "arguments jsonb NOT NULL, "
                    "result_summary text NOT NULL, "
                    "execution_time_ms integer NOT NULL, "
                    "timestamp timestamptz NOT NULL, "
                    "conversation_id bigint NOT NULL, "
                    "user_id integer NOT NULL)"
                ),
                (
                    "ALTER TABLE back_toolcalllog "
                    "DROP CONSTRAINT IF EXISTS back_toolcalllog_conversation_id_13331510_fk_back_conv, "
                    "ADD CONSTRAINT back_toolcalllog_conversation_id_13331510_fk_back_conv "
                    "FOREIGN KEY (conversation_id) REFERENCES back_conversation(id) "
                    "ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED"
                ),
                (
                    "ALTER TABLE back_toolcalllog "
                    "DROP CONSTRAINT IF EXISTS back_toolcalllog_user_id_3aaef43f_fk_auth_user_id, "
                    "ADD CONSTRAINT back_toolcalllog_user_id_3aaef43f_fk_auth_user_id "
                    "FOREIGN KEY (user_id) REFERENCES auth_user(id) DEFERRABLE INITIALLY DEFERRED"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS back_toolcalllog_reply_id_72acaa5d "
                    "ON back_toolcalllog (reply_id)"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS back_toolcalllog_conversation_id_13331510 "
                    "ON back_toolcalllog (conversation_id)"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS back_toolcalllog_user_id_3aaef43f "
                    "ON back_toolcalllog (user_id)"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS back_toolca_convers_356644_idx "
                    "ON back_toolcalllog (conversation_id, timestamp)"
                ),
                (
                    "CREATE INDEX IF NOT EXISTS back_toolca_tool_na_1071c2_idx "
                    "ON back_toolcalllog (tool_name)"
                ),
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
        RunIfSqlite(
            sql=[
                (
                    "CREATE TABLE IF NOT EXISTS back_toolcalllog ("
                    "id integer NOT NULL PRIMARY KEY AUTOINCREMENT, "
                    "reply_id varchar(255) NOT NULL, "
                    "iteration integer NOT NULL, "
                    "tool_name varchar(100) NOT NULL, "
                    "arguments text NOT NULL, "
                    "result_summary text NOT NULL, "
                    "execution_time_ms integer NOT NULL, "
                    "timestamp datetime NOT NULL, "
                    "conversation_id bigint NOT NULL, "
                    "user_id integer NOT NULL)"
                ),
                "CREATE INDEX IF NOT EXISTS back_toolcalllog_reply_id_72acaa5d ON back_toolcalllog (reply_id)",
                "CREATE INDEX IF NOT EXISTS back_toolcalllog_conversation_id_13331510 ON back_toolcalllog (conversation_id)",
                "CREATE INDEX IF NOT EXISTS back_toolcalllog_user_id_3aaef43f ON back_toolcalllog (user_id)",
                "CREATE INDEX IF NOT EXISTS back_toolca_convers_356644_idx ON back_toolcalllog (conversation_id, timestamp)",
                "CREATE INDEX IF NOT EXISTS back_toolca_tool_na_1071c2_idx ON back_toolcalllog (tool_name)",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
