from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.html import format_html
from django.conf import settings

from .models import Mailbox, SendAttempt

admin.site.site_header = 'مدیریت کاربران وب‌میل'
admin.site.site_title = 'مدیریت ایمیل'
admin.site.index_title = 'حساب‌ها و اتصال صندوق‌ها'
admin.site.unregister(User)


@admin.action(description='نمایش لینک دعوت / تعیین رمز (اعتبار ۲۴ ساعت)')
def invitation(modeladmin, request, queryset):
    for user in queryset[:20]:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url = 'https://' + settings.MAIL_HOSTS[0] + '/invite/' + uid + '/' + token + '/'
        modeladmin.message_user(request, format_html('دعوت {}: <a href="{}">لینک خصوصی تعیین رمز</a> — فقط برای همان کاربر ارسال شود.', user.username, url))


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    actions = [invitation]


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ['address', 'owner', 'created_at']
    readonly_fields = ['address', 'created_at']
    fields = ['address', 'owner', 'created_at']

    def has_add_permission(self, request):
        return False  # Only successful mailbox authentication may create a link.


@admin.register(SendAttempt)
class SendAttemptAdmin(admin.ModelAdmin):
    list_display = ['mailbox', 'state', 'created_at']
    fields = ['mailbox', 'state', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
