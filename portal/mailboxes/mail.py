"""Fixed-host, TLS-verified IMAP/SMTP. No user-controlled server/SSRF target."""
import email
import imaplib
import re
import smtplib
import ssl
from contextlib import contextmanager
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from cryptography.fernet import Fernet
from django.conf import settings

MAX_MESSAGE = 20 * 1024 * 1024


def seal(address, password):
    # Bind the ciphertext to the mailbox address as well as the server key.
    return Fernet(settings.MAILBOX_KEY).encrypt((address + '\0' + password).encode()).decode()


def unseal(box):
    address, password = Fernet(settings.MAILBOX_KEY).decrypt(box.encrypted_credential.encode()).decode().split('\0', 1)
    if address != box.address:
        raise ValueError('Credential binding mismatch')
    return password


@contextmanager
def connection(address, password):
    client = imaplib.IMAP4_SSL(settings.MAIL_SERVER, 993,
                              ssl_context=ssl.create_default_context(), timeout=20)
    try:
        client.login(address, password)
        yield client
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def folder_name(client, kind):
    if kind == 'inbox':
        return 'INBOX'
    flags = {'sent': b'\\Sent', 'junk': b'\\Junk', 'trash': b'\\Trash'}
    if kind not in flags:
        raise ValueError('Unknown folder')
    status, rows = client.list()
    for row in rows or []:
        match = re.match(rb'\(([^)]*)\) "[^"]*" (.+)$', row)
        if match and flags[kind].lower() in match.group(1).lower().split():
            return match.group(2).decode('ascii')
    raise ValueError('Folder unavailable')


def select(client, kind):
    name = folder_name(client, kind)
    if client.select(name, readonly=True)[0] != 'OK':
        raise ValueError('Folder unavailable')
    return name


def raw_part(rows):
    return next((row[1] for row in rows if isinstance(row, tuple)), None)


def listing(box, kind, page):
    with connection(box.address, unseal(box)) as client:
        select(client, kind)
        status, rows = client.uid('search', None, 'ALL')
        if status != 'OK':
            raise ValueError('Search failed')
        ids = list(reversed(rows[0].split()))
        selected = ids[(page - 1) * 25:page * 25]
        messages = {}
        if selected:
            status, data = client.uid('fetch', b','.join(selected), '(UID BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] FLAGS)')
            if status != 'OK':
                raise ValueError('Fetch failed')
            for row in data:
                if not isinstance(row, tuple):
                    continue
                match = re.search(rb'\bUID (\d+)', row[0])
                if not match or match.group(1) not in selected:
                    continue
                msg = email.message_from_bytes(row[1], policy=policy.default)
                uid = match.group(1)
                messages[uid] = {'uid': uid.decode(), 'subject': str(msg.get('Subject', '(بدون موضوع)')),
                                 'sender': str(msg.get('From', '')), 'date': str(msg.get('Date', ''))}
        return [messages[uid] for uid in selected if uid in messages], len(ids) > page * 25


def read(box, kind, uid):
    with connection(box.address, unseal(box)) as client:
        select(client, kind)
        status, data = client.uid('fetch', str(uid), '(RFC822.SIZE)')
        sizes = re.findall(rb'RFC822.SIZE (\d+)', b' '.join(x for x in data if isinstance(x, bytes)))
        if status != 'OK' or not sizes or int(sizes[0]) > MAX_MESSAGE:
            raise ValueError('Message unavailable or larger than 20 MB')
        status, data = client.uid('fetch', str(uid), '(BODY.PEEK[])')
        raw = raw_part(data)
        if status != 'OK' or not raw or len(raw) > MAX_MESSAGE:
            raise ValueError('Message unavailable')
        return email.message_from_bytes(raw, policy=policy.default)


def text_body(msg):
    part = msg.get_body(preferencelist=('plain',))
    if part:
        return part.get_content()
    # HTML is not executed or loaded; the full existing client is available separately.
    return 'این پیام فقط نسخهٔ HTML دارد؛ برای نمایش کامل، وب‌میل کامل را باز کنید.'


def send(box, to, subject, body, attachment=None):
    msg = EmailMessage()
    msg['From'] = box.address
    msg['To'] = ', '.join(to)
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = make_msgid(domain=box.address.rsplit('@', 1)[1])
    msg.set_content(body)
    if attachment:
        # Never trust the upload's declared MIME type; downloads are attachments too.
        msg.add_attachment(attachment.read(), maintype='application', subtype='octet-stream',
                           filename=attachment.name)
    raw = msg.as_bytes(policy=policy.SMTP)
    password = unseal(box)
    with smtplib.SMTP(settings.MAIL_SERVER, 587, timeout=30) as client:
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        client.login(box.address, password)
        refused = client.sendmail(box.address, to, raw)
    # SMTP acceptance is not proof of final delivery. Do not retry automatically.
    sent_copy = False
    try:
        with connection(box.address, password) as client:
            target = folder_name(client, 'sent')
            sent_copy = client.append(target, '\\Seen', None, raw)[0] == 'OK'
    except (OSError, ValueError, imaplib.IMAP4.error):
        pass
    return {'all_accepted': not refused, 'sent_copy': sent_copy}
