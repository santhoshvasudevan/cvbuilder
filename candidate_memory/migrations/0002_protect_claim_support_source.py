# Protective deletion policy for claim-support provenance (AUDIT-002).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidate_memory", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="memoryclaimsupport",
            name="source_document",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="claim_supports",
                to="candidate_memory.memorysourcedocument",
            ),
        ),
    ]
