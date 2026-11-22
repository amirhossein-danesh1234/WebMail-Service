"""Presentation and draft helpers for the managed mail workspace."""
import json
import re
from email.utils import getaddresses

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from . import mail
from .models import MailboxGrant

FOLDERS = [
    ('inbox', 'دریافتی', '↓'), ('sent', 'ارسالی', '↑'), ('drafts', 'پیش‌نویس', '✎'),
    ('archive', 'بایگانی', '▤'), ('junk', 'هرزنامه', '!'), ('trash', 'حذف‌شده', '×'),
]
MODES = {'reply': 'پاسخ', 'reply-all': 'پاسخ به همه', 'forward': 'ارسال مجدد'}
DRAFT_FIELDS = ('to', 'cc', 'bcc', 'subject', 'body')
MESSAGE_ID = re.compile(r'<[^\s<>]+>')


def grants_for(user):
    return MailboxGrant.objects.filter(user=user).select_related('mailbox').order_by('mailbox__address')


def context(request, grant=None, folder='inbox'):
    search = request.GET.get('q', '').strip()[:200]
    if any(char in search for char in '\r\n\0'):
        search = ''
    filter_by = request.GET.get('filter', '')
    return {'grants': [item for item in grants_for(request.user) if item.can_read or item.can_send],
            'active_grant': grant, 'box': grant.mailbox if grant else None, 'folder': folder,
            'folders': [entry for entry in FOLDERS if entry[0] != 'drafts' or (grant and grant.can_send)],
            'query': search, 'filter_by': filter_by if filter_by in mail.SEARCH_FLAGS else ''}


def encode_draft(data, source):
    content = {field: ', '.join(data[field]) if isinstance(data[field], list) else data[field]
               for field in DRAFT_FIELDS}
    content['source'] = source
    return Fernet(settings.MAILBOX_KEY).encrypt(json.dumps(content).encode()).decode()


def decode_draft(draft):
    return json.loads(Fernet(settings.MAILBOX_KEY).decrypt(draft.encrypted_content.encode()))


def reply_fields(box, msg, mode):
    subject = str(msg.get('Subject', ''))
    prefix = 'Fwd: ' if mode == 'forward' else 'Re: '
    initial = {'subject': (subject if subject.lower().startswith(prefix.lower()) else prefix + subject)[:200]}
    quoted = mail.text_body(msg, html_as_text=True)[:150000]
    initial['body'] = '\n\n' + '\n'.join('> ' + line for line in quoted.splitlines())
    if mode == 'forward':
        return initial
    seen = {box.address.casefold()}
    def addresses(headers):
        result = []
        for _, address in getaddresses([str(header) for header in headers if header]):
            try:
                validate_email(address)
            except ValidationError:
                continue
            key = address.casefold()
            if key not in seen and len(seen) <= 20:
                seen.add(key)
                result.append(address)
        return ', '.join(result)[:2000]
    initial['to'] = addresses([msg.get('Reply-To') or msg.get('From')]) or addresses([msg.get('To')])
    if mode == 'reply-all':
        initial['cc'] = addresses([msg.get('To'), msg.get('Cc')])
    return initial


def thread_headers(msg):
    ids = MESSAGE_ID.findall(str(msg.get('Message-ID') or ''))
    if len(ids) != 1 or len(ids[0]) > 250:
        return None
    parent = ids[0]
    references = [item for item in MESSAGE_ID.findall(str(msg.get('References') or '')) if len(item) <= 250 and item != parent][-9:]
    return parent, ' '.join(references + [parent])
