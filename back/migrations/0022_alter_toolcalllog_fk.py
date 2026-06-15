from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("back", "0021_toolcalllog"),
    ]

    operations = [
        migrations.RunSQL(
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
