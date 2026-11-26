from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .access import ADMIN, EMPLOYEE, REVIEWER

ROLES = [(r, r) for r in [ADMIN, EMPLOYEE, REVIEWER]]


class CreateUserForm(UserCreationForm):
    roles = forms.MultipleChoiceField(
        label="Роли (совмещение — отдельное решение администратора)",
        choices=ROLES,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = get_user_model()
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "password1",
            "password2",
            "roles",
        ]


class RolesForm(forms.Form):
    user = forms.ModelChoiceField(
        label="Пользователь", queryset=get_user_model().objects.order_by("username")
    )
    roles = forms.MultipleChoiceField(
        label="Роли", choices=ROLES, widget=forms.CheckboxSelectMultiple
    )
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)
