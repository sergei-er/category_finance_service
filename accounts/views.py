import hashlib
from datetime import timedelta

from django.contrib.auth.views import LoginView
from django.utils import timezone

from .models import LoginAttempt


class SessionLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def identity(self):
        return hashlib.sha256(
            self.request.POST.get("username", "").strip().lower().encode()
        ).hexdigest()

    def post(self, request, *args, **kwargs):
        since = timezone.now() - timedelta(minutes=15)
        if (
            LoginAttempt.objects.filter(identity=self.identity(), created_at__gte=since).count()
            >= 5
        ):
            form = self.get_form()
            form.add_error(None, "Слишком много попыток входа. Повторите через 15 минут.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        LoginAttempt.objects.create(identity=self.identity())
        return super().form_invalid(form)

    def form_valid(self, form):
        LoginAttempt.objects.filter(identity=self.identity()).delete()
        return super().form_valid(form)
