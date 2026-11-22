"""Fixed-host, TLS-verified IMAP/SMTP. No user-controlled server/SSRF target."""
import email
import imaplib
import re
import smtplib
import ssl
from copy import deepcopy
from contextlib import contextmanager
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from html.parser import HTMLParser

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


FOLDER_FLAGS = {'sent': b'\\Sent', 'junk': b'\\Junk', 'trash': b'\\Trash', 'archive': b'\\Archive'}
FOLDERS = ('inbox', *FOLDER_FLAGS)
SEARCH_FLAGS = {'unread': 'UNSEEN', 'starred': 'FLAGGED'}
MESSAGE_FLAGS = {'read': ('+FLAGS.SILENT', r'(\Seen)'), 'unread': ('-FLAGS.SILENT', r'(\Seen)'),
                 'star': ('+FLAGS.SILENT', r'(\Flagged)'), 'unstar': ('-FLAGS.SILENT', r'(\Flagged)')}


class FolderUnavailable(ValueError):
    pass


def folder_name(client, kind):
    if kind == 'inbox':
        return 'INBOX'
    if kind not in FOLDER_FLAGS:
        raise ValueError('Unknown folder')
    status, rows = client.list()
    if status != 'OK':
        raise ValueError('Folder list failed')
    archive = None
    for row in rows or []:
        if not isinstance(row, bytes):
            continue
        match = re.match(rb'\(([^)]*)\) (?:"[^"]*"|NIL) (.+)$', row)
        if match:
            name = match.group(2)
            if FOLDER_FLAGS[kind].lower() in match.group(1).lower().split():
                return name.decode('ascii')
            if kind == 'archive' and name.strip(b'"').lower() == b'archive':
                archive = name.decode('ascii')
    if archive:
        return archive
    raise FolderUnavailable('Folder unavailable')


def select(client, kind, *, readonly=True):
    name = folder_name(client, kind)
    if client.select(name, readonly=readonly)[0] != 'OK':
        raise FolderUnavailable('Folder unavailable')
    return name


def uid_validity(client):
    _, rows = client.response('UIDVALIDITY')
    return rows[0].decode('ascii') if rows and isinstance(rows[0], bytes) and rows[0].isdigit() else ''


def update_message(box, kind, uid, version, action):
    with connection(box.address, unseal(box)) as client:
        select(client, kind, readonly=False)
        if not version or version != uid_validity(client):
            raise ValueError('Folder changed')
        if action in MESSAGE_FLAGS:
            status, _ = client.uid('store', str(uid), *MESSAGE_FLAGS[action])
        elif action in ('inbox', 'archive', 'trash', 'junk') and action != kind:
            try:
                target = folder_name(client, action)
            except FolderUnavailable:
                if action != 'archive':
                    raise
                client.create('Archive')
                target = folder_name(client, action)
            status, _ = client.uid('move', str(uid), target)
        else:
            raise ValueError('Unknown message action')
        if status != 'OK':
            raise ValueError('IMAP update failed')


def raw_part(rows):
    return next((row[1] for row in rows if isinstance(row, tuple)), None)


def listing(box, kind, page, *, query='', filter_by=''):
    with connection(box.address, unseal(box)) as client:
        try:
            select(client, kind)
        except FolderUnavailable:
            if kind == 'archive':
                return [], False
            raise
        search_keys = [SEARCH_FLAGS.get(filter_by, 'ALL')]
        if query:
            # Use an IMAP literal so that user text is never interpreted as a command.
            search_keys = ['CHARSET', 'UTF-8', *search_keys, 'TEXT']
            client.literal = query.encode('utf-8')
        status, rows = client.uid('search', *search_keys)
        if status != 'OK':
            raise ValueError('Search failed')
        ids = list(reversed((rows[0] or b'').split()))
        selected = ids[(page - 1) * 25:page * 25]
        messages = {}
        if selected:
            status, data = client.uid('fetch', b','.join(selected), '(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])')
            if status != 'OK':
                raise ValueError('Fetch failed')
            for index, row in enumerate(data):
                if not isinstance(row, tuple):
                    continue
                match = re.search(rb'\bUID (\d+)', row[0])
                if not match or match.group(1) not in selected:
                    continue
                msg = email.message_from_bytes(row[1], policy=policy.default)
                uid = match.group(1)
                remainder = data[index + 1] if index + 1 < len(data) else None
                flags = imaplib.ParseFlags(row[0] + (remainder if isinstance(remainder, bytes) else b''))
                messages[uid] = message_summary(msg, uid.decode(), flags)
        return [messages[uid] for uid in selected if uid in messages], len(ids) > page * 25


def message_summary(msg, uid, flags):
    date = str(msg.get('Date', ''))
    try:
        received_at = parsedate_to_datetime(date)
    except (ValueError, OverflowError):
        received_at = None
    sender_header = msg.get('From')
    addresses = getattr(sender_header, 'addresses', ())
    sender = addresses[0].display_name or addresses[0].addr_spec if addresses else str(sender_header or '')
    return {'uid': uid, 'subject': str(msg.get('Subject') or '(بدون موضوع)'), 'sender': sender, 'sender_detail': str(sender_header or ''),
            'date': date, 'received_at': received_at, 'seen': b'\\Seen' in flags, 'starred': b'\\Flagged' in flags}


def read(box, kind, uid, *, with_flags=False):
    with connection(box.address, unseal(box)) as client:
        select(client, kind)
        status, data = client.uid('fetch', str(uid), '(RFC822.SIZE)')
        sizes = re.findall(rb'RFC822.SIZE (\d+)', b' '.join(x for x in (data or []) if isinstance(x, bytes)))
        if status != 'OK' or not sizes or int(sizes[0]) > MAX_MESSAGE:
            raise ValueError('Message unavailable or larger than 20 MB')
        version = uid_validity(client) if with_flags else ''
        status, data = client.uid('fetch', str(uid), '(FLAGS BODY.PEEK[])' if with_flags else '(BODY.PEEK[])')
        raw = raw_part(data or [])
        if status != 'OK' or not raw or len(raw) > MAX_MESSAGE:
            raise ValueError('Message unavailable')
        msg = email.message_from_bytes(raw, policy=policy.default)
        if with_flags:
            response = b' '.join(row[0] if isinstance(row, tuple) else row for row in data if row)
            return msg, version, imaplib.ParseFlags(response)
        return msg


class VisibleText(HTMLParser):
    blocks = {'br', 'div', 'p', 'li', 'tr', 'table', 'blockquote', 'section', 'h1', 'h2', 'h3'}
    hidden = {'script', 'style', 'head', 'template'}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.suppressed.append(tag)
        elif not self.suppressed and tag in self.blocks:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
        elif tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def text_body(msg, *, html_as_text=False):
    part = msg.get_body(preferencelist=('plain', 'html') if html_as_text else ('plain',))
    if part is None:
        return '' if html_as_text else 'این پیام فقط نسخهٔ HTML دارد؛ برای نمایش کامل، وب‌میل کامل را باز کنید.'
    content = part.get_content()
    if part.get_content_subtype() == 'plain':
        return content
    parser = VisibleText()
    parser.feed(content[:500000])
    parser.close()
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def send(box, to, subject, body, attachment=None, *, cc=(), bcc=(), related=None, forward=None, files=()):
    msg = EmailMessage()
    msg['From'] = box.address
    if to:
        msg['To'] = ', '.join(to)
    elif not cc:
        msg['To'] = 'undisclosed-recipients:;'
    if cc:
        msg['Cc'] = ', '.join(cc)
    msg['Subject'] = subject
    if related:
        msg['In-Reply-To'], msg['References'] = related
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = make_msgid(domain=box.address.rsplit('@', 1)[1])
    msg.set_content(body)
    if forward is not None:
        safe_forward = deepcopy(forward)
        del safe_forward['Bcc']
        del safe_forward['Resent-Bcc']
        msg.add_attachment(safe_forward, filename='original.eml')
    if attachment:
        files = [(attachment.name, attachment.read()), *files]
    for name, data in files:
        msg.add_attachment(data, maintype='application', subtype='octet-stream', filename=name)
    raw = msg.as_bytes(policy=policy.SMTP)
    password = unseal(box)
    with smtplib.SMTP(settings.MAIL_SERVER, 587, timeout=30) as client:
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        client.login(box.address, password)
        envelope = {}
        for address in [*to, *cc, *bcc]:
            envelope.setdefault(address.lower(), address)
        refused = client.sendmail(box.address, list(envelope.values()), raw)
    # SMTP acceptance is not proof of final delivery. Do not retry automatically.
    sent_copy = False
    try:
        with connection(box.address, password) as client:
            target = folder_name(client, 'sent')
            if bcc:
                msg['Bcc'] = ', '.join(bcc)
                raw = msg.as_bytes(policy=policy.SMTP)
            sent_copy = client.append(target, '\\Seen', None, raw)[0] == 'OK'
    except (OSError, ValueError, imaplib.IMAP4.error):
        pass
    return {'all_accepted': not refused, 'sent_copy': sent_copy}
