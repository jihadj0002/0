from django.db import migrations


class RunIfPostgres(migrations.RunSQL):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    dependencies = [
        ("back", "0021_toolcalllog"),
    ]

    operations = [
        RunIfPostgres(
            sql=(
                "ALTER TABLE back_toolcalllog "
                "DROP CONSTRAINT IF EXISTS back_toolcalllog_conversation_id_13331510_fk_back_conv, "
                "ADD CONSTRAINT back_toolcalllog_conversation_id_13331510_fk_back_conv "
                "FOREIGN KEY (conversation_id) REFERENCES back_conversation(id) "
                "ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
