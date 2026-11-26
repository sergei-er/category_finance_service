import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("organization", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_name", models.CharField(max_length=100, verbose_name="Фамилия")),
                ("first_name", models.CharField(max_length=100, verbose_name="Имя")),
                ("middle_name", models.CharField(blank=True, max_length=100, verbose_name="Отчество")),
                ("position", models.CharField(max_length=180, verbose_name="Должность")),
                ("department", models.CharField(blank=True, max_length=180, verbose_name="Подразделение")),
                ("contact", models.CharField(blank=True, max_length=250, verbose_name="Контактные данные")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employees", to="organization.organizationprofile")),
            ],
            options={"ordering": ["last_name", "first_name", "middle_name", "pk"]},
        ),
        migrations.AddField(model_name="organizationprofile", name="short_name", field=models.CharField(blank=True, max_length=120, verbose_name="Сокращённое наименование")),
        migrations.AddField(model_name="organizationprofile", name="inn", field=models.CharField(blank=True, max_length=12, verbose_name="ИНН")),
        migrations.AddField(model_name="organizationprofile", name="legal_address", field=models.CharField(blank=True, max_length=300, verbose_name="Юридический адрес")),
        migrations.AddField(model_name="organizationprofile", name="organization_type", field=models.CharField(blank=True, max_length=150, verbose_name="Тип финансовой организации")),
        migrations.AddField(model_name="organizationprofile", name="questionnaire_owner", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="questionnaire_organizations", to="organization.employee")),
        migrations.AddField(model_name="organizationprofile", name="result_approver", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approval_organizations", to="organization.employee")),
        migrations.AddField(model_name="protectioncontour", name="code", field=models.CharField(blank=True, max_length=40, verbose_name="Идентификатор")),
        migrations.CreateModel(
            name="InformationSystem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="Наименование")),
                ("purpose", models.TextField(blank=True, verbose_name="Назначение")),
                ("developer", models.CharField(blank=True, max_length=200, verbose_name="Разработчик")),
                ("responsible_department", models.CharField(blank=True, max_length=180, verbose_name="Ответственное подразделение")),
                ("archived", models.BooleanField(default=False, verbose_name="В архиве")),
                ("contour", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="information_systems", to="organization.protectioncontour")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="information_systems", to="organization.organizationprofile")),
                ("responsible_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="systems", to="organization.employee")),
            ],
            options={"ordering": ["name", "pk"]},
        ),
        migrations.AddConstraint(model_name="informationsystem", constraint=models.UniqueConstraint(fields=("organization", "name"), name="system_name_org_unique")),
    ]
