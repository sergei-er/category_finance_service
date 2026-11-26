from django.shortcuts import redirect, render
from django.views.decorators.http import require_safe


@require_safe
def home(request):
    """Без приветственного экрана: сразу рабочая область либо вход."""
    return redirect("workspace" if request.user.is_authenticated else "login")


@require_safe
def about(request):
    return render(request, "core/about.html")
