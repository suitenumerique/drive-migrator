from django.db import migrations, models


def copy_integrity_check_passed(apps, schema_editor):
    """Keep the existing results on the new column."""
    ExtraTaskInfo = apps.get_model("core", "ExtraTaskInfo")
    ExtraTaskInfo.objects.update(
        is_integrity_check_passed=models.F("integrity_check_passed")
    )


def clear_is_integrity_check_passed(apps, schema_editor):
    ExtraTaskInfo = apps.get_model("core", "ExtraTaskInfo")
    ExtraTaskInfo.objects.update(is_integrity_check_passed=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_workspace_upload_errors"),
    ]

    operations = [
        migrations.AddField(
            model_name="extrataskinfo",
            name="is_integrity_check_passed",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.RunPython(
            copy_integrity_check_passed, clear_is_integrity_check_passed
        ),
    ]
