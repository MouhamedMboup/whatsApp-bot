from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="contact_phone_number",
            field=models.CharField(
                blank=True,
                help_text="Numéro de téléphone au format E.164 (optionnel, utilisé pour recherche/contacts)",
                max_length=20,
                null=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Format E.164 requis (ex: +221771234567)",
                        regex="^\\+[1-9]\\d{1,14}$",
                    )
                ],
            ),
        ),
    ]

