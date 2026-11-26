"""Удаление неиспользуемых записей организации с сохранением журнала."""

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.access import ADMIN, require_role
from auditlog.services import record

from .models import Employee, InformationSystem, ProtectionContour

ENTITIES = {
    "employee": (Employee, "сотрудника", "employees"),
    "system": (InformationSystem, "информационной системы", "systems"),
    "contour": (ProtectionContour, "контура защиты", "systems"),
}


def has_dependencies(instance):
    # Проверяем также M2M: Django иначе удаляет связь с обследованием каскадом.
    return any(
        getattr(instance, relation.get_accessor_name()).exists()
        for relation in instance._meta.related_objects
        if relation.one_to_many or relation.many_to_many
    )


@transaction.atomic
def delete_entity(actor, kind, pk):
    require_role(actor, ADMIN)
    model, _, _ = ENTITIES[kind]
    instance = model.objects.select_for_update().get(pk=pk, organization_id=1)
    if has_dependencies(instance):
        raise ValidationError(
            "Удаление невозможно: запись используется в профиле организации, "
            "системах, обследованиях или рекомендациях. Связанные данные сохранены."
        )
    record(actor, "organization.entity_deleted", instance, previous={"name": str(instance)})
    instance.delete()
