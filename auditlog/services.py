from .models import AuditEvent


def record(actor, action, instance, *, previous=None, new=None):
    """Записать значимое действие без содержимого файлов, паролей и секретов."""
    details = {}
    if previous is not None:
        details["previous"] = previous
    if new is not None:
        details["new"] = new
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity=instance._meta.label_lower,
        entity_id=str(instance.pk),
        details=details,
    )
