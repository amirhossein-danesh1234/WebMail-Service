from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.html import format_html
from django.conf import settings

from . import mail
from .forms import ConnectForm
from .models import Mailbox, MailboxGrant, SendAttempt
from .views import MAIL_ERRORS

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


class MailboxGrantInline(admin.TabularInline):
    model = MailboxGrant
    extra = 1
    fields = ('mailbox', 'can_read', 'can_send')


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    actions = [invitation]
    if settings.MANAGED_KAPPA:
        inlines = [MailboxGrantInline]


class ManagedMailboxForm(forms.ModelForm):
    password = forms.CharField(
        label='رمز صندوق یا رمز برنامه', widget=forms.PasswordInput, required=False,
        help_text='برای نگه داشتن رمز قبلی، در زمان ویرایش خالی بگذارید.',
    )

    class Meta:
        model = Mailbox
        fields = ('address',)

    def clean(self):
        cleaned = super().clean()
        address = self.instance.address if self.instance.pk else cleaned.get('address')
        password = cleaned.get('password')
        if self.instance.pk and not password:
            return cleaned
        if not address:
            return cleaned
        form = ConnectForm({'address': address, 'password': password})
        if not form.is_valid():
            for field, errors in form.errors.items():
                self.add_error(field if field in self.fields else None, errors)
            return cleaned
        address = form.cleaned_data['address']
        try:
            with mail.connection(address, password):
                pass
        except MAIL_ERRORS:
            self.add_error('password', 'احراز هویت صندوق انجام نشد.')
        else:
            if not self.instance.pk:
                cleaned['address'] = address
            self.instance.encrypted_credential = mail.seal(address, password)
        return cleaned


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ['address', 'owner', 'created_at']
    readonly_fields = ['address', 'created_at']
    fields = ['address', 'owner', 'created_at']

    if settings.MANAGED_KAPPA:
        form = ManagedMailboxForm
        list_display = ['address', 'created_at']
        fields = ['address', 'password', 'created_at']

        def get_readonly_fields(self, request, obj=None):
            return ['address', 'created_at'] if obj else ['created_at']

    def has_add_permission(self, request):
        return settings.MANAGED_KAPPA and super().has_add_permission(request)


@admin.register(SendAttempt)
class SendAttemptAdmin(admin.ModelAdmin):
    list_display = ['mailbox', 'state', 'created_at']
    fields = ['mailbox', 'state', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
