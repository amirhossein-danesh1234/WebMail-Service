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
from .models import LoginWindow, Mailbox, MailboxGrant, PendingMailbox, PortalDraft, SendAttempt


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
        self.assertContains(self.client.get('/', **self.admin_host), 'Kappa Mail Admin')
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

    @patch('mailboxes.mail.listing', return_value=([], False))
    def test_create_and_edit_user_from_single_form(self, listing):
        self.client.force_login(self.admin)
        path = '/people/new/'
        field = f'box_{self.box.pk}'
        values = {'full_name': 'امیرحسین دانش', 'username': 'simple-user',
                  'password': '1234', field: 'read'}
        page = self.client.get(path, **self.admin_host)
        self.assertContains(page, 'kappa.css')
        self.assertContains(page, 'Read and send')
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

    @patch('mailboxes.mail.update_message')
    @patch('mailboxes.mail.listing', return_value=([], False))
    def test_workspace_filters_and_forbids_message_mutation_for_read_only(self, listing, update):
        self.grant.can_read = True
        self.grant.save()
        page = self.client.get(self.base+'inbox/', {'q': 'کاوش', 'filter': 'unread'}, **self.user_host)
        self.assertContains(page, 'workspace.css')
        listing.assert_called_once_with(self.box, 'inbox', 1, query='کاوش', filter_by='unread')
        route = self.base+'inbox/77/update/'
        self.assertEqual(self.client.post(route, {'version': '4', 'action': 'trash'}, **self.user_host).status_code, 404)
        update.assert_not_called()
        self.grant.can_send = True
        self.grant.save()
        self.assertEqual(self.client.get(route, **self.user_host).status_code, 405)
        self.assertEqual(self.client.post(route, {'version': '4', 'action': 'archive'}, **self.user_host).status_code, 302)
        update.assert_called_once_with(self.box, 'inbox', 77, '4', 'archive')

    @patch('mailboxes.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    def test_workspace_drafts_are_private_encrypted_and_retired_on_send(self, send):
        self.grant.can_read = self.grant.can_send = True
        self.grant.save()
        compose = self.base+'compose/'
        data = {'intent': 'draft', 'nonce': str(uuid.uuid4()), 'body': 'Only the author may see this'}
        self.assertEqual(self.client.post(compose, data, **self.user_host).status_code, 302)
        draft = PortalDraft.objects.get()
        self.assertNotIn(data['body'], draft.encrypted_content)
        route = compose+'?draft='+str(draft.pk)
        self.assertContains(self.client.get(route, **self.user_host), data['body'])
        MailboxGrant.objects.filter(user=self.other).update(can_send=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(route, **self.user_host).status_code, 404)
        self.assertEqual(self.client.post(route, {'intent': 'discard'}, **self.user_host).status_code, 404)
        self.client.force_login(self.user)
        data.update(intent='send', to='first@example.com', cc='cc@example.com', bcc='private@example.com', subject='hello')
        self.assertEqual(self.client.post(route, data, **self.user_host).status_code, 302)
        send.assert_called_once_with(self.box, ['first@example.com'], 'hello', data['body'], None,
                                     cc=['cc@example.com'], bcc=['private@example.com'], related=None, forward=None, files=[])
        self.assertFalse(PortalDraft.objects.exists())
        self.assertEqual(self.client.post(route, data, **self.user_host).status_code, 404)

    @patch('mailboxes.mail.read')
    @patch('mailboxes.mail.listing', return_value=([], False))
    def test_workspace_reply_uses_source_and_safe_html_text(self, listing, read):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['Subject'] = 'Fwd: test'
        msg['From'] = 'author@example.com'
        msg['Reply-To'] = 'reply@example.com'
        msg['To'] = 'controlled@kappaut.ir, other@example.com'
        msg['Message-ID'] = '<letter@example.com>'
        msg.set_content('<p>متن پیام</p><script>alert(1)</script><img src="https://remote.invalid/">', subtype='html')
        read.return_value = (msg, '42', ())
        self.grant.can_read = self.grant.can_send = True
        self.grant.save()
        page = self.client.get(self.base+'inbox/7/', **self.user_host)
        self.assertContains(page, 'متن پیام')
        self.assertNotContains(page, 'alert(1)')
        self.assertNotContains(page, 'remote.invalid')
        response = self.client.get(self.base+'compose/', {'source': 'inbox/7/reply-all/42'}, **self.user_host)
        self.assertContains(response, 'value="reply@example.com"')
        self.assertContains(response, 'value="other@example.com"')
        self.assertContains(response, 'Re: Fwd: test')
        response = self.client.get(self.base+'compose/', {'source': 'inbox/7/reply-all/99'}, **self.user_host)
        self.assertEqual(response.status_code, 302)

    @patch('mailboxes.mail.read')
    def test_download_forwards_original_message_as_attachment(self, read):
        from email.message import EmailMessage
        original = EmailMessage()
        original['Subject'] = 'Original content'
        original.set_content('Original body')
        outer = EmailMessage()
        outer.set_content('Forwarded')
        outer.add_attachment(original, filename='original.eml')
        read.return_value = outer
        self.grant.can_read = True
        self.grant.save()
        response = self.client.get(self.base+'inbox/5/attachment/2/', **self.user_host)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment; filename="original.eml"', response['Content-Disposition'])
        self.assertIn(b'Original body', b''.join(response.streaming_content))

    @patch('mailboxes.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    def test_cc_bcc_only_with_multiple_files_and_saved_draft(self, send):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.grant.can_read = self.grant.can_send = True
        self.grant.save()
        compose = self.base+'compose/'
        page = self.client.get(compose, **self.user_host)
        for label in ('To', 'CC', 'BCC', 'multiple'):
            self.assertContains(page, label)
        data = {'intent': 'draft', 'nonce': str(uuid.uuid4()),
                'cc': '"کاپا; دانشگاه" <cc@example.com>; cc2@example.com',
                'bcc': 'one@example.com، two@example.com',
                'attachment': [SimpleUploadedFile('first.txt', b'alpha'), SimpleUploadedFile('second.txt', b'beta')]}
        self.assertEqual(self.client.post(compose, data, **self.user_host).status_code, 302)
        draft = PortalDraft.objects.get()
        one, two = draft.attachments.order_by('pk')
        self.assertNotIn(b'alpha', one.encrypted_data)
        route = compose+'?draft='+str(draft.pk)
        self.assertContains(self.client.get(route, **self.user_host), two.name)
        data.update(intent='send', remove_attachment=str(two.pk),
                    attachment=[SimpleUploadedFile('last.txt', b'gamma')])
        self.assertEqual(self.client.post(route, data, **self.user_host).status_code, 302)
        send.assert_called_once_with(self.box, [], '', '', None, cc=['cc@example.com', 'cc2@example.com'],
            bcc=['one@example.com', 'two@example.com'], related=None, forward=None,
            files=[('first.txt', b'alpha'), ('last.txt', b'gamma')])
        self.assertFalse(PortalDraft.objects.exists())

    @patch('mailboxes.mail.connection')
    @patch('mailboxes.mail.smtplib.SMTP')
    def test_bcc_envelope_and_sent_copy_and_forward_privacy(self, smtp, imap):
        from email import policy
        from email.message import EmailMessage
        from email.parser import BytesParser
        transport = smtp.return_value.__enter__.return_value
        transport.sendmail.return_value = {}
        sent = imap.return_value.__enter__.return_value
        sent.list.return_value = ('OK', [b'(\\Sent) "/" Sent'])
        sent.append.return_value = ('OK', [b''])
        forwarded = EmailMessage()
        forwarded['Bcc'] = 'private-original@example.com'
        forwarded.set_content('Old message')
        summary = mail.send(self.box, [], '', '', bcc=['blind@example.com'],
                            forward=forwarded, files=[('one.txt', b'abc'), ('two.txt', b'def')])
        self.assertEqual(summary, {'all_accepted': True, 'sent_copy': True})
        email_parser = BytesParser(policy=policy.default)
        sender, envelope, wire = transport.sendmail.call_args.args
        self.assertEqual(sender, self.box.address)
        self.assertEqual(envelope, ['blind@example.com'])
        incoming = email_parser.parsebytes(wire)
        self.assertIsNone(incoming['Bcc'])
        self.assertNotIn(b'private-original@example.com', wire)
        self.assertEqual([part.get_filename() for part in incoming.iter_attachments()],
                         ['original.eml', 'one.txt', 'two.txt'])
        archive = email_parser.parsebytes(sent.append.call_args.args[-1])
        self.assertEqual(archive['Bcc'], 'blind@example.com')
        self.assertEqual(forwarded['Bcc'], 'private-original@example.com')

    def test_cc_and_bcc_accept_separators_but_not_a_header_line_or_missing_recipient(self):
        from .forms import WorkspaceComposeForm
        for data in ({}, {'cc': 'one@example.com\r\nBcc: hidden@example.com'}):
            form = WorkspaceComposeForm({'nonce': str(uuid.uuid4()), **data})
            self.assertFalse(form.is_valid())

    @patch('mailboxes.campaigns.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    @patch('mailboxes.seminar_source.read_emails', return_value=[
        'first@example.com', ' FIRST@example.com ', '', 'not-an-email', 'second@example.com'])
    def test_seminar_snapshot_requires_confirmation_and_sends_each_address_once(self, source, send):
        from .campaigns import run_batch
        from .models import SeminarCampaign
        path = '/seminar-mail/'
        data = {'source':'nobel', 'mailbox':self.box.pk, 'subject':'Seminar news',
                'body':'Registration notice', 'nonce':str(uuid.uuid4())}
        self.assertEqual(self.client.get(path, **self.user_host).status_code, 404)
        self.assertEqual(self.client.post(path, data, **self.admin_host).status_code, 404)
        self.client.force_login(self.admin)
        response = self.client.post(path, data, **self.admin_host)
        self.assertEqual(response.status_code, 302)
        campaign = SeminarCampaign.objects.get()
        self.assertEqual(campaign.state, 'prepared')
        self.assertEqual((campaign.total_rows, campaign.invalid_rows, campaign.duplicate_rows), (5, 2, 1))
        self.assertEqual(campaign.recipients.count(), 2)
        self.assertNotIn(b'first@example.com', bytes(campaign.recipients.first().encrypted_address))
        preview = self.client.get(response.url, **self.admin_host)
        self.assertContains(preview, 'Send to 2 recipients')
        self.assertEqual(preview.context['recipient_addresses'], ['first@example.com', 'second@example.com'])
        self.assertContains(preview, '<li><bdi>first@example.com</bdi></li>', count=1)
        self.assertContains(preview, '<li><bdi>second@example.com</bdi></li>', count=1)
        self.assertNotContains(preview, 'not-an-email')
        run_batch(delay=0)
        send.assert_not_called()
        self.assertEqual(self.client.post(path, data, **self.admin_host).url, response.url)
        for _ in range(2):
            self.assertEqual(self.client.post(response.url+'update/', {'action':'queue'}, **self.admin_host).status_code, 302)
        run_batch(delay=0)
        run_batch(delay=0)
        self.assertEqual([call.args[1] for call in send.call_args_list], [[], []])
        self.assertEqual([call.kwargs['bcc'] for call in send.call_args_list],
                         [['first@example.com'], ['second@example.com']])
        campaign.refresh_from_db()
        self.assertEqual(campaign.state, 'finished')
        self.assertEqual(campaign.recipients.filter(state='accepted').count(), 2)
        source.assert_called_with('nobel')

    @patch('mailboxes.seminar_source.read_emails', return_value=['one@example.com', 'two@example.com'])
    def test_seminar_does_not_repeat_uncertain_submission(self, source):
        from .campaigns import claim_recipient, run_batch
        from .models import SeminarCampaign
        self.client.force_login(self.admin)
        url = self.client.post('/seminar-mail/', {
            'source':'nobel', 'mailbox':self.box.pk, 'subject':'Test', 'body':'Test', 'nonce':str(uuid.uuid4()),
        }, **self.admin_host).url
        self.client.post(url+'update/', {'action':'queue'}, **self.admin_host)
        _, interrupted = claim_recipient()
        with patch('mailboxes.campaigns.mail.send', return_value={'all_accepted': True}) as send:
            run_batch(delay=0)
            send.assert_not_called()
            self.assertEqual(SeminarCampaign.objects.get().state, 'paused')
            interrupted.refresh_from_db()
            self.assertEqual(interrupted.state, 'uncertain')
            self.client.post(url+'update/', {'action':'resume'}, **self.admin_host)
            run_batch(delay=0)
            send.assert_called_once_with(self.box, [], 'Test', 'Test', bcc=['two@example.com'])

    def test_recipient_fields_accept_spaces_without_comma(self):
        from .forms import WorkspaceComposeForm
        form = WorkspaceComposeForm({'nonce': uuid.uuid4(), 'to': 'one@example.com two@example.com',
                                     'cc': 'cc@example.com next@example.com', 'bcc': 'b@example.com a@example.com'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['to'], ['one@example.com', 'two@example.com'])
        self.assertEqual(form.cleaned_data['cc'], ['cc@example.com', 'next@example.com'])
        self.assertEqual(form.cleaned_data['bcc'], ['b@example.com', 'a@example.com'])

    @patch('mailboxes.mail.listing', return_value=([], False))
    @patch('mailboxes.seminar_source.read_emails', return_value=['attendee@example.com'])
    def test_granted_seminar_access_in_mail_limits_sender_and_worker(self, rows, listing):
        from django.contrib.auth.models import Permission
        from .campaigns import claim_recipient
        from .models import SeminarCampaign
        path = '/seminar-mail/'
        self.grant.can_read = self.grant.can_send = True
        self.grant.save()
        self.assertNotContains(self.client.get('/', **self.user_host), 'Seminar mail')
        self.assertEqual(self.client.get(path, **self.user_host).status_code, 404)
        self.user.user_permissions.add(Permission.objects.get(codename='send_seminar_campaign'))
        self.assertContains(self.client.get('/', **self.user_host), 'Seminar mail')
        self.assertContains(self.client.get(path, **self.user_host), self.box.address)
        self.assertEqual(self.client.get(path, **self.admin_host).status_code, 404)
        other = Mailbox.objects.create(address='other-sender@kappaut.ir', encrypted_credential='test')
        data = {'source': 'nobel', 'mailbox': other.pk, 'subject': 'Update',
                'body': 'Registration notice', 'nonce': str(uuid.uuid4())}
        self.assertEqual(self.client.post(path, data, **self.user_host).status_code, 200)
        self.assertFalse(SeminarCampaign.objects.exists())
        data['mailbox'] = self.box.pk
        detail = self.client.post(path, data, **self.user_host).url
        self.client.post(detail+'update/', {'action': 'queue'}, **self.user_host)
        self.grant.can_send = False
        self.grant.save()
        self.assertIsNone(claim_recipient())
        self.assertEqual(SeminarCampaign.objects.get().state, 'paused')

    @patch('mailboxes.mail.listing', return_value=([], False))
    @patch('mailboxes.mail.send', return_value={'all_accepted': True, 'sent_copy': True})
    def test_rich_message_payload_retains_formatting_but_strips_active_markup(self, send, listing):
        self.grant.can_read = self.grant.can_send = True
        self.grant.save()
        editor = self.client.get(self.base+'compose/', **self.user_host)
        self.assertContains(editor, 'aria-label="Message formatting"')
        form = {'nonce': str(uuid.uuid4()), 'to': 'one@example.com', 'body': 'Word',
                'body_html': '<p onclick="evil()"><b>Word</b><script>evil()</script><img src="https://track.invalid"><a href="javascript:evil()">Text</a></p>'}
        self.assertEqual(self.client.post(self.base+'compose/', form, **self.user_host).status_code, 302)
        self.assertEqual(send.call_args.kwargs['html_body'], '<p><b>Word</b>Text</p>')
