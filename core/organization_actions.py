from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.access import ADMIN, require_role
from organization.deletion import ENTITIES, delete_entity, has_dependencies


class DeleteConfirmationForm(forms.Form):
    confirm = forms.BooleanField(label="Подтверждаю удаление записи")


@login_required
@require_http_methods(["GET", "POST"])
def delete(request, pk, kind):
    require_role(request.user, ADMIN)
    model, label, tab = ENTITIES[kind]
    instance = get_object_or_404(model, pk=pk, organization_id=1)
    back = reverse("organization-card") + "?tab=" + tab
    form = DeleteConfirmationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            delete_entity(request.user, kind, pk)
        except (ValidationError, ProtectedError):
            form.add_error(None, "Запись используется в других данных. Удаление запрещено.")
        else:
            messages.success(request, "Запись удалена. Действие сохранено в журнале.")
            return redirect(back)
    return render(
        request,
        "workspace/delete_entity.html",
        {
            "instance": instance,
            "label": label,
            "form": form,
            "blocked": has_dependencies(instance),
            "back": back,
        },
    )
