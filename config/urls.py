from django.contrib.auth.views import LogoutView
from django.urls import include, path

from accounts.views import SessionLoginView

urlpatterns = [
    path("", include("core.urls")),
    path("", include("core.workspace_urls")),
    path("accounts/login/", SessionLoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
]
