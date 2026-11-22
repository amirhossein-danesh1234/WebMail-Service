import re
from email.utils import getaddresses

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError


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

ATTACHMENT_LIMIT = 10 * 1024 * 1024
ATTACHMENT_COUNT = 20
ADDRESS_SEPARATORS = re.compile(r'"(?:[^"\\]|\\.)*"|[;،]')


class AttachmentInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class AttachmentsField(forms.FileField):
    widget = AttachmentInput

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        clean_file = super().clean
        return [clean_file(file, initial) for file in files]


def workspace_recipients(value):
    if not value:
        return []
    separated = ADDRESS_SEPARATORS.sub(lambda match: ',' if len(match[0]) == 1 else match[0], value)
    return recipients(separated.rstrip(' ,'))


class WorkspaceComposeForm(ComposeForm):
    to = forms.CharField(label='گیرنده (To)', max_length=2000, required=False)
    cc = forms.CharField(label='رونوشت (CC)', max_length=2000, required=False)
    bcc = forms.CharField(label='رونوشت پنهان (BCC)', max_length=2000, required=False)
    attachment = AttachmentsField(label='پیوست‌ها', required=False,
                                  help_text='چند فایل را می‌توانید هم‌زمان انتخاب کنید. مجموع حجم حداکثر ۱۰ مگابایت.')

    def __init__(self, *args, saving_draft=False, saved_files=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.saving_draft = saving_draft
        self.saved_files = saved_files
        self.order_fields(['to', 'cc', 'bcc', 'subject', 'body', 'attachment', 'nonce'])
        for name in ('subject', 'body'):
            self.fields[name].required = False

    def clean_to(self):
        return workspace_recipients(self.cleaned_data['to'])

    def clean_cc(self):
        return workspace_recipients(self.cleaned_data['cc'])

    def clean_bcc(self):
        return workspace_recipients(self.cleaned_data['bcc'])

    def clean_attachment(self):
        uploads = self.cleaned_data['attachment']
        if len(uploads) + len(self.saved_files) > ATTACHMENT_COUNT:
            raise ValidationError('حداکثر ۲۰ فایل پیوست کنید.')
        if sum(file.size for file in [*uploads, *self.saved_files]) > ATTACHMENT_LIMIT:
            raise ValidationError('مجموع حجم پیوست‌ها باید حداکثر ۱۰ مگابایت باشد.')
        return uploads

    def clean(self):
        data = super().clean()
        if not any(self.has_error(field) for field in ('to', 'cc', 'bcc')):
            addresses = {address.lower() for field in ('to', 'cc', 'bcc') for address in data.get(field, [])}
            if not addresses and not self.saving_draft:
                self.add_error('to', 'دست‌کم یک نشانی در To، CC یا BCC وارد کنید.')
            elif len(addresses) > 20:
                raise ValidationError('حداکثر ۲۰ گیرنده وارد کنید.')
        return data
