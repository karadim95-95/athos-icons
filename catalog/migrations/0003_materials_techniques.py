from django.db import migrations, models


def text_to_materials(apps, schema_editor):
    """Turn the old free text ("Ξύλο, φύλλο χρυσού / Wood, gold leaf") into materials to tick."""
    Product = apps.get_model("catalog", "Product")
    Material = apps.get_model("catalog", "Material")
    for product in Product.objects.exclude(materials_text=""):
        greek, _sep, english = product.materials_text.partition(" / ")
        names = [n.strip() for n in greek.split(",") if n.strip()]
        names_en = [n.strip() for n in english.split(",") if n.strip()]
        for i, name in enumerate(names):
            name_en = names_en[i] if len(names_en) == len(names) else ""
            material, _created = Material.objects.get_or_create(
                name=name[:1].upper() + name[1:], defaults={"name_en": name_en[:1].upper() + name_en[1:]})
            product.materials.add(material)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_sizes"),
    ]

    operations = [
        migrations.RenameField("product", "materials", "materials_text"),
        migrations.CreateModel(
            name="Material",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="name (Greek)")),
                ("name_en", models.CharField(blank=True, max_length=80, verbose_name="name (English)")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="position")),
            ],
            options={"verbose_name": "material", "verbose_name_plural": "materials", "ordering": ["order", "name"],
                     "abstract": False},
        ),
        migrations.CreateModel(
            name="Technique",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="name (Greek)")),
                ("name_en", models.CharField(blank=True, max_length=80, verbose_name="name (English)")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="position")),
            ],
            options={"verbose_name": "technique", "verbose_name_plural": "techniques", "ordering": ["order", "name"],
                     "abstract": False},
        ),
        migrations.AddField(
            model_name="product",
            name="materials",
            field=models.ManyToManyField(blank=True, related_name="products", to="catalog.material",
                                         verbose_name="materials"),
        ),
        migrations.AddField(
            model_name="product",
            name="techniques",
            field=models.ManyToManyField(blank=True, related_name="products", to="catalog.technique",
                                         verbose_name="technique"),
        ),
        migrations.RunPython(text_to_materials, migrations.RunPython.noop),
        migrations.RemoveField("product", "materials_text"),
    ]
