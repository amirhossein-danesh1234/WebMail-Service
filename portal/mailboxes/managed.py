"""The two managed Kappa forms and their tenant-scoped actions."""
import imaplib
import secrets
from functools import wraps

from cryptography.fernet import InvalidToken
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UsernameField
from django.contrib.auth.models import Permission, User
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import IntegrityError, transaction
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from django.conf import settings

from . import mail, provision
from .models import Mailbox, MailboxGrant, PendingMailbox

PERMISSIONS = [('', 'No access'), ('read', 'Read only'), ('send', 'Read and send')]


def kappa_admin(view):
    @wraps(view)
    @login_required
    def checked(request, *args, **kwargs):
        if (not settings.MANAGED_KAPPA or request.get_host().split(':')[0] != settings.ADMIN_HOST
                or not request.user.is_superuser):
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)
    return checked


class MailboxForm(forms.Form):
    local_part = forms.CharField(
        label='Mailbox name', max_length=64 + len('@' + provision.DOMAIN),
        widget=forms.TextInput(attrs={'placeholder': 'info', 'autocomplete': 'off', 'dir': 'ltr'}),
    )

    def clean_local_part(self):
        name = self.cleaned_data['local_part'].lower().removesuffix('@' + provision.DOMAIN)
        if not provision.valid_local_part(name):
            raise forms.ValidationError('Enter a valid Kappa mailbox name or address.')
        return name


class PersonForm(forms.Form):
    full_name = forms.CharField(label='Full name', max_length=150)
    username = UsernameField(label='Username', max_length=150, validators=[UnicodeUsernameValidator()],
                             widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    campaign_access = forms.BooleanField(label='Allow seminar mail', required=False)
    password = forms.CharField(label='Password', strip=False, required=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        self.fields['password'].required = person is None
        if person is not None:
            self.fields['password'].help_text = 'Leave blank to keep the current password.'
        grants = {}
        if person is not None:
            self.initial.update(full_name=person.get_full_name(), username=person.username,
                                campaign_access=person.has_perm('mailboxes.send_seminar_campaign'))
            grants = {grant.mailbox_id: grant for grant in MailboxGrant.objects.filter(user=person)}
        self.boxes = list(Mailbox.objects.order_by('address'))
        for box in self.boxes:
            grant = grants.get(box.pk)
            initial = ''
            if grant and grant.can_read:
                initial = 'send' if grant.can_send else 'read'
            self.fields[f'box_{box.pk}'] = forms.ChoiceField(
                label=box.address, choices=PERMISSIONS, required=False, initial=initial)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exclude(pk=self.person.pk if self.person else None).exists():
            raise forms.ValidationError('This username is already in use.')
        return username

    def save(self):
        with transaction.atomic():
            person = self.person or User()
            person.first_name = self.cleaned_data['full_name']
            person.last_name = ''
            person.username = self.cleaned_data['username']
            if self.cleaned_data['password']:
                person.set_password(self.cleaned_data['password'])
            person.save()
            permission = Permission.objects.get(content_type__app_label='mailboxes',
                                                codename='send_seminar_campaign')
            if self.cleaned_data['campaign_access']:
                person.user_permissions.add(permission)
            else:
                person.user_permissions.remove(permission)
            for box in self.boxes:
                field = f'box_{box.pk}'
                if field in self.data:
                    permission = self.cleaned_data[field]
                    grants = MailboxGrant.objects.filter(user=person, mailbox=box)
                    if permission:
                        grants.update_or_create(defaults={'can_read': True, 'can_send': permission == 'send'},
                                                user=person, mailbox=box)
                    else:
                        grants.delete()


@kappa_admin
@require_http_methods(['GET', 'POST'])
def mailbox_create(request):
    form = MailboxForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        name = form.cleaned_data['local_part']
        address = f'{name}@{provision.DOMAIN}'
        with transaction.atomic():
            box = None
            if not Mailbox.objects.filter(address=address).exists():
                box, _ = PendingMailbox.objects.get_or_create(address=address, defaults={
                    'encrypted_credential': lambda: mail.seal(address, secrets.token_urlsafe(40)),
                })
        if box is None:
            form.add_error('local_part', 'This mailbox already exists.')
        else:
            try:
                password = mail.unseal(box)
                result = provision.request_creation(name, password)
                if result not in ('created', 'exists'):
                    raise OSError('Mailbox creation failed')
                with mail.connection(address, password):
                    pass
            except imaplib.IMAP4.error:
                form.add_error('local_part', 'Unable to verify this mailbox.')
            except (OSError, ValueError, InvalidToken):
                form.add_error(None, 'Could not create the mailbox. Try the same name again.')
            else:
                with transaction.atomic():
                    Mailbox.objects.get_or_create(address=address, defaults={
                        'encrypted_credential': box.encrypted_credential,
                    })
                    PendingMailbox.objects.filter(pk=box.pk).delete()
                messages.success(request, f'Mailbox {address} created.')
                return redirect('home')
    return render(request, 'kappa_mailbox_form.html', {'form': form})


@sensitive_post_parameters('password')
@kappa_admin
@require_http_methods(['GET', 'POST'])
def person_edit(request, user_id=None):
    person = None
    if user_id is not None:
        person = get_object_or_404(User, pk=user_id, is_staff=False, is_superuser=False, is_active=True)
    form = PersonForm(request.POST if request.method == 'POST' else None, person=person)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error('username', 'This username is already in use.')
        else:
            messages.success(request, 'User and mailbox access saved.')
            return redirect('home')
    return render(request, 'kappa_person_form.html', {'form': form, 'person': person,
        'access_fields': [form[f'box_{box.pk}'] for box in form.boxes]})
