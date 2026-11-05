from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from mailboxes import views

urlpatterns = [
    path('healthz', views.health),
    path('staff-gate/', views.staff_gate),
    path('manage/login/', views.PortalLoginView.as_view(), name='admin_login'),
    path('manage/', admin.site.urls),
    path('login/', views.PortalLoginView.as_view(), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('invite/<uidb64>/<token>/', auth.PasswordResetConfirmView.as_view(
        template_name='registration/invite.html', success_url='/login/'), name='password_reset_confirm'),
    path('password/', auth.PasswordChangeView.as_view(
        template_name='registration/password.html', success_url='/'), name='password_change'),
    path('', views.home, name='home'),
    path('connect/', views.connect, name='connect'),
    path('boxes/<int:box_id>/disconnect/', views.disconnect, name='disconnect'),
    path('boxes/<int:box_id>/', views.inbox, name='inbox'),
    path('boxes/<int:box_id>/compose/', views.compose, name='compose'),
    path('boxes/<int:box_id>/<str:folder>/', views.inbox, name='inbox'),
    path('boxes/<int:box_id>/<str:folder>/<int:uid>/', views.message, name='message'),
    path('boxes/<int:box_id>/<str:folder>/<int:uid>/attachment/<int:part>/', views.attachment, name='attachment'),
]
