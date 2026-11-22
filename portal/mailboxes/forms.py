from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from email.utils import getaddresses


class ConnectForm(forms.Form):
    address = forms.EmailField(label='آدرس کامل صندوق')
    password = forms.CharField(label='رمز صندوق یا رمز اختصاصی برنامه', widget=forms.PasswordInput)

    def clean_address(self):
        value = self.cleaned_data['address'].lower()
        if value.rsplit('@', 1)[-1] not in settings.MAIL_DOMAINS:
            raise ValidationError('فقط صندوق‌های سه دامنهٔ میزبانی‌شده قابل اتصال‌اند.')
        return value


def recipients(value):
    if '\r' in value or '\n' in value:
        raise ValidationError('گیرنده نامعتبر است.')
    values = getaddresses([value])
    result = []
    for name, address in values:
        forms.EmailField().clean(address)
        result.append(address)
    if not result or len(result) > 20:
        raise ValidationError('بین ۱ تا ۲۰ گیرنده وارد کنید.')
    return result


class ComposeForm(forms.Form):
    to = forms.CharField(label='گیرنده‌ها (با کاما جدا شوند)', max_length=2000)
    subject = forms.CharField(label='موضوع', max_length=200)
    body = forms.CharField(label='متن پیام', widget=forms.Textarea, max_length=200000)
    attachment = forms.FileField(label='پیوست (حداکثر ۱۰ مگابایت)', required=False)
    nonce = forms.UUIDField(widget=forms.HiddenInput)

    def clean_to(self):
        return recipients(self.cleaned_data['to'])

    def clean_subject(self):
        value = self.cleaned_data['subject']
        if '\r' in value or '\n' in value:
            raise ValidationError('موضوع نامعتبر است.')
        return value

    def clean_attachment(self):
        value = self.cleaned_data.get('attachment')
        if value and value.size > 10 * 1024 * 1024:
            raise ValidationError('اندازهٔ پیوست بیش از ۱۰ مگابایت است.')
        return value
class WorkspaceComposeForm(ComposeForm):
    cc = forms.CharField(label='رونوشت', max_length=2000, required=False)
    bcc = forms.CharField(label='رونوشت پنهان', max_length=2000, required=False)

    def __init__(self, *args, saving_draft=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['to', 'cc', 'bcc', 'subject', 'body', 'attachment', 'nonce'])
        for name in ('to', 'subject', 'body'):
            self.fields[name].required = not saving_draft

    def clean_to(self):
        value = self.cleaned_data['to']
        return recipients(value) if value else []

    def clean_cc(self):
        value = self.cleaned_data['cc']
        return recipients(value) if value else []

    def clean_bcc(self):
        value = self.cleaned_data['bcc']
        return recipients(value) if value else []

    def clean(self):
        data = super().clean()
        if sum(len(data.get(name, [])) for name in ('to', 'cc', 'bcc')) > 20:
            raise ValidationError('حداکثر ۲۰ گیرنده وارد کنید.')
        return data
