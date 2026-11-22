import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest import skipIf, skipUnless
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import mail
from .forms import ComposeForm, ConnectForm
from .models import LoginWindow, Mailbox, MailboxGrant, PendingMailbox, SendAttempt


@contextmanager
def authenticated_mailbox(*args):
    yield object()


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
@skipIf(settings.MANAGED_KAPPA, "Legacy portal mode")
class PortalSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('invited-user', password='test-only-password-493!')
        self.other = User.objects.create_user('other-user', password='test-only-password-672!')
        self.box = Mailbox.objects.create(owner=self.user, address='controlled@kappaut.ir',
            encrypted_credential=mail.seal('controlled@kappaut.ir', 'test-only-mail-password'))
        self.client.force_login(self.user)

    def test_no_public_registration(self):
        for path in ['/signup/', '/register/', '/accounts/signup/']:
            self.assertEqual(self.client.get(path, secure=True).status_code, 404)

    def test_admin_is_only_on_admin_host_and_requires_staff(self):
        self.assertEqual(self.client.get('/manage/', secure=True, HTTP_HOST='mail.kappaut.ir').status_code, 404)
        response = self.client.get('/manage/', secure=True, HTTP_HOST='mail-admin.amirhossein-danesh.ir')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)
        self.assertEqual(self.client.get('/', secure=True, HTTP_HOST='mail-admin.amirhossein-danesh.ir').status_code, 404)

    def test_native_admin_gate_requires_authenticated_staff(self):
        host = {'secure': True, 'HTTP_HOST': 'mail-admin.amirhossein-danesh.ir'}
        self.client.logout()
        self.assertEqual(self.client.get('/staff-gate/', **host).status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/staff-gate/', **host).status_code, 403)
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.get('/staff-gate/', **host).status_code, 200)
        self.assertEqual(self.client.get('/staff-gate/', secure=True, HTTP_HOST='mail.kappaut.ir').status_code, 404)

    def test_cross_user_access_cannot_read_download_send_or_disconnect(self):
        self.client.force_login(self.other)
        for path in [f'/boxes/{self.box.pk}/', f'/boxes/{self.box.pk}/compose/',
                     f'/boxes/{self.box.pk}/inbox/1/',
                     f'/boxes/{self.box.pk}/inbox/1/attachment/1/']:
            self.assertEqual(self.client.get(path, secure=True).status_code, 404)
        self.assertEqual(self.client.post(f'/boxes/{self.box.pk}/disconnect/', secure=True).status_code, 404)
        self.assertTrue(Mailbox.objects.filter(pk=self.box.pk).exists())

    def test_mailbox_password_is_encrypted_bound_and_hidden(self):
        self.assertNotIn('test-only-mail-password', self.box.encrypted_credential)
        self.assertEqual(mail.unseal(self.box), 'test-only-mail-password')
        response = self.client.get('/', secure=True)
        self.assertNotContains(response, self.box.encrypted_credential)
        self.box.address = 'other@kappaut.ir'
        with self.assertRaises(ValueError):
            mail.unseal(self.box)

    def test_csrf_required_to_connect_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/connect/', {'address': 'x@kappaut.ir', 'password': 'x'}, secure=True).status_code, 403)
        self.assertEqual(client.post('/logout/', secure=True).status_code, 403)
        self.assertEqual(self.client.get('/logout/', secure=True).status_code, 405)

    @patch('mailboxes.mail.connection', authenticated_mailbox)
    def test_connect_authenticates_and_preserves_other_owner(self):
        self.client.force_login(self.other)
        response = self.client.post('/connect/', {'address': self.box.address, 'password': 'valid-test'}, secure=True)
        self.assertContains(response, 'قابل اتصال')
        self.box.refresh_from_db()
        self.assertEqual(self.box.owner, self.user)
        response = self.client.post('/connect/', {'address': 'new@unilrn.ir', 'password': 'valid-test'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mailbox.objects.get(address='new@unilrn.ir').owner, self.other)

    @patch('mailboxes.mail.connection', side_effect=OSError('synthetic failure'))
    def test_failed_mailbox_login_never_creates_link(self, connection):
        response = self.client.post('/connect/', {'address': 'new@unilrn.ir', 'password': 'invalid'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Mailbox.objects.filter(address='new@unilrn.ir').exists())

    def test_fixed_domains_and_header_injection_validation(self):
        self.assertFalse(ConnectForm({'address': 'x@evil.invalid', 'password': 'x'}).is_valid())
        form = ComposeForm({'to': 'a@example.com\r\nBcc: b@example.com', 'subject': 'test', 'body': 'x', 'nonce': uuid.uuid4()})
        self.assertFalse(form.is_valid())

    @patch('mailboxes.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    def test_duplicate_send_form_never_resubmits(self, send):
        data = {'to': 'controlled@example.com', 'subject': 'test', 'body': 'test', 'nonce': str(uuid.uuid4())}
        for _ in range(2):
            self.assertEqual(self.client.post(f'/boxes/{self.box.pk}/compose/', data, secure=True).status_code, 302)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(SendAttempt.objects.get().state, 'accepted')

    @patch('mailboxes.mail.send', side_effect=OSError('ambiguous timeout'))
    def test_ambiguous_send_is_not_retried(self, send):
        data = {'to': 'controlled@example.com', 'subject': 'test', 'body': 'test', 'nonce': str(uuid.uuid4())}
        self.client.post(f'/boxes/{self.box.pk}/compose/', data, secure=True)
        self.client.post(f'/boxes/{self.box.pk}/compose/', data, secure=True)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(SendAttempt.objects.get().state, 'uncertain')

    def test_disconnect_only_removes_portal_link(self):
        with patch('mailboxes.mail.connection') as connection:
            self.client.post(f'/boxes/{self.box.pk}/disconnect/', secure=True)
            connection.assert_not_called()
        self.assertFalse(Mailbox.objects.filter(pk=self.box.pk).exists())

    @patch('mailboxes.mail.read')
    def test_message_html_and_subject_are_escaped(self, read):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['Subject'] = '<script>alert(1)</script>'
        msg.set_content('<img src="https://tracker.invalid/x">')
        read.return_value = msg
        response = self.client.get(f'/boxes/{self.box.pk}/inbox/1/', secure=True)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>')
        self.assertNotContains(response, '<img src="https://tracker')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])

    def test_invite_one_time_and_expiration(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        self.assertTrue(default_token_generator.check_token(self.user, token))
        client = Client()
        response = client.get(f'/invite/{uid}/{token}/', secure=True)
        self.assertEqual(response.status_code, 302)
        response = client.post(response.url, {'new_password1': 'new-secret-password-129!', 'new_password2': 'new-secret-password-129!'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('new-secret-password-129!'))
        self.assertFalse(default_token_generator.check_token(self.user, token))
        fresh = default_token_generator.make_token(self.user)
        with patch.object(default_token_generator, '_now', return_value=timezone.now().replace(tzinfo=None) + timedelta(days=2)):
            self.assertFalse(default_token_generator.check_token(self.user, fresh))

    def test_rate_limit_blocks_repeated_login(self):
        self.client.logout()
        for _ in range(15):
            self.client.post('/login/', {'username': 'none', 'password': 'invalid'}, secure=True)
        self.assertEqual(self.client.post('/login/', {'username': 'none', 'password': 'invalid'}, secure=True).status_code, 429)
        self.assertNotIn('127.0.0.1', LoginWindow.objects.get().key)


@skipUnless(settings.MANAGED_KAPPA, 'Managed Kappa mode')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class KappaPermissionsTests(TestCase):
    user_host = {'secure': True, 'HTTP_HOST': 'mail.kappaut.ir'}
    admin_host = {'secure': True, 'HTTP_HOST': 'mail-admin.kappaut.ir'}

    def setUp(self):
        self.admin = User.objects.create_superuser('tenant-admin', password='admin-only-test-password')
        self.user = User.objects.create_user('regular-user', password='user-only-test-password')
        self.other = User.objects.create_user('another-user')
        self.box = Mailbox.objects.create(address='controlled@kappaut.ir',
            encrypted_credential=mail.seal('controlled@kappaut.ir', 'test-only-mail-password'))
        self.grant = MailboxGrant.objects.create(user=self.user, mailbox=self.box)
        MailboxGrant.objects.create(user=self.other, mailbox=self.box, can_read=True)
        self.client.force_login(self.user)
        self.base = f'/boxes/{self.box.pk}/'

    @patch('mailboxes.mail.listing', return_value=([], False))
    @patch('mailboxes.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    def test_permissions_are_independent_and_revocation_takes_effect(self, send, listing):
        paths = ('', 'sent/', 'inbox/1/', 'inbox/1/attachment/1/', 'compose/')
        for path in paths:
            self.assertEqual(self.client.get(self.base + path, **self.user_host).status_code, 404)
        self.grant.can_read = True
        self.grant.save()
        page = self.client.get(self.base, **self.user_host)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'compose/')
        form = {'to': 'test@example.com', 'subject': 'Notice', 'body': 'Text', 'nonce': str(uuid.uuid4())}
        self.assertEqual(self.client.post(self.base + 'compose/', form, **self.user_host).status_code, 404)
        send.assert_not_called()
        self.grant.can_read = False
        self.grant.can_send = True
        self.grant.save()
        self.assertContains(self.client.get('/', **self.user_host), 'compose/')
        response = self.client.post(self.base + 'compose/', form, **self.user_host)
        self.assertEqual(response.url, '/')
        send.assert_called_once()
        self.assertEqual(self.client.get(self.base + 'sent/', **self.user_host).status_code, 404)
        listing.assert_called_once()
        self.grant.delete()
        self.assertEqual(self.client.get(self.base + 'compose/', **self.user_host).status_code, 404)
        self.assertTrue(MailboxGrant.objects.filter(user=self.other, mailbox=self.box).exists())

    def test_user_cannot_self_connect_disconnect_or_use_admin(self):
        for path in ('/connect/', '/manage/', '/webmail/', '/admin/'):
            self.assertEqual(self.client.get(path, **self.user_host).status_code, 404)
        self.assertEqual(self.client.post(self.base + 'disconnect/', **self.user_host).status_code, 404)
        self.assertEqual(self.client.get('/', **self.admin_host).status_code, 404)
        self.assertEqual(self.client.get('/manage/', **self.admin_host).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/', **self.admin_host), 'مدیریت ایمیل کاپا')
        self.assertEqual(self.client.get(f'/people/{self.user.pk}/', **self.admin_host).status_code, 200)
        self.assertEqual(self.client.get('/manage/', **self.admin_host).url, '/')
        self.assertEqual(self.client.get(self.base, **self.admin_host).status_code, 404)


    @patch('mailboxes.mail.connection', authenticated_mailbox)
    @patch('mailboxes.provision.request_creation', side_effect=[OSError('temporary'), 'exists'])
    def test_mailbox_local_part_only_and_retry_keeps_generated_credential(self, create):
        path = '/mailboxes/new/'
        self.assertEqual(self.client.get(path, **self.user_host).status_code, 404)
        self.assertEqual(self.client.post(path, {'local_part': 'session'}, **self.admin_host).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(path, {'local_part': 'new@unilrn.ir'}, **self.admin_host).status_code, 200)
        create.assert_not_called()
        self.assertEqual(self.client.post(path, {'local_part': 'New__Box@KAPPAUT.IR'}, **self.admin_host).status_code, 200)
        self.assertFalse(Mailbox.objects.filter(address='new__box@kappaut.ir').exists())
        pending = PendingMailbox.objects.get(address='new__box@kappaut.ir')
        self.assertEqual(self.client.post(path, {'local_part': 'new__box'}, **self.admin_host).status_code, 302)
        box = Mailbox.objects.get(address=pending.address)
        self.assertEqual(box.encrypted_credential, pending.encrypted_credential)
        self.assertFalse(PendingMailbox.objects.filter(pk=pending.pk).exists())
        self.assertEqual(create.call_args_list[0], create.call_args_list[1])
        self.assertEqual(create.call_args.args[0], 'new__box')
        self.assertEqual(self.client.post(path, {'local_part': 'new__box'}, **self.admin_host).status_code, 200)
        self.assertEqual(create.call_count, 2)

    def test_create_and_edit_user_from_single_form(self):
        self.client.force_login(self.admin)
        path = '/people/new/'
        field = f'box_{self.box.pk}'
        values = {'full_name': 'امیرحسین دانش', 'username': 'simple-user',
                  'password': '1234', field: 'read'}
        page = self.client.get(path, **self.admin_host)
        self.assertContains(page, 'kappa.css')
        self.assertContains(page, 'خواندن صندوق و ارسال')
        self.assertNotContains(page, 'فقط ارسال')
        response = self.client.post(path, {**values, 'password': ''}, **self.admin_host)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username=values['username']).exists())
        self.assertEqual(self.client.post(path, values, **self.admin_host).status_code, 302)
        person = User.objects.get(username=values['username'])
        self.assertEqual(person.get_full_name(), values['full_name'])
        self.assertTrue(person.check_password(values['password']))
        grant = MailboxGrant.objects.get(user=person, mailbox=self.box)
        self.assertTrue(grant.can_read)
        self.assertFalse(grant.can_send)
        values.update(password='', **{field: 'send'})
        path = f'/people/{person.pk}/'
        self.assertEqual(self.client.post(path, values, **self.admin_host).status_code, 302)
        grant.refresh_from_db()
        self.assertTrue(grant.can_read and grant.can_send)
        self.client.force_login(person)
        self.assertContains(self.client.get('/', **self.user_host), 'kappa.css')
        self.assertContains(self.client.get('/', **self.user_host), 'compose/')
        self.client.force_login(self.admin)
        values[field] = ''
        self.assertEqual(self.client.post(path, values, **self.admin_host).status_code, 302)
        self.assertFalse(MailboxGrant.objects.filter(pk=grant.pk).exists())

    def test_kappa_user_can_change_password_to_short_letters_only(self):
        path = '/password/'
        old = 'user-only-test-password'
        self.client.force_login(self.user)
        response = self.client.post(path, {'old_password': old,
            'new_password1': 'abc', 'new_password2': 'abc'}, **self.user_host)
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('abc'))
        self.assertFalse(self.user.check_password(old))
