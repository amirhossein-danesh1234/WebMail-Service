"""Presentation and draft helpers for the managed mail workspace."""
import json
import re
from email.utils import getaddresses

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from . import campaigns, mail
from .forms import ATTACHMENT_COUNT, ATTACHMENT_LIMIT
from .models import DraftAttachment, MailboxGrant, PortalDraft

FOLDERS = [('inbox', 'Inbox'), ('sent', 'Sent'), ('junk', 'Spam'), ('drafts', 'Drafts')]
FOLDER_NAMES = dict(FOLDERS + [('archive', 'Archive'), ('trash', 'Trash')])
MODES = {'reply': 'Reply', 'reply-all': 'Reply all', 'forward': 'Forward'}
DRAFT_FIELDS = ('to', 'cc', 'bcc', 'subject', 'body', 'body_html')
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
            'folder_title': FOLDER_NAMES.get(folder, 'Inbox'), 'seminar_access': campaigns.can_use_campaigns(request.user),
            'folders': [entry for entry in FOLDERS if entry[0] != 'drafts' or (grant and grant.can_send)],
            'query': search, 'filter_by': filter_by if filter_by in mail.SEARCH_FLAGS else ''}


def encode_draft(data, source):
    content = {field: ', '.join(data[field]) if isinstance(data[field], list) else data[field]
               for field in DRAFT_FIELDS}
    content['source'] = source
    return Fernet(settings.MAILBOX_KEY).encrypt(json.dumps(content).encode()).decode()


def decode_draft(draft):
    return json.loads(Fernet(settings.MAILBOX_KEY).decrypt(draft.encrypted_content.encode()))



class DraftAttachmentsChanged(ValueError):
    pass


@transaction.atomic
def save_draft(draft_id, owner, mailbox, content, uploads, removed, *, editing=False):
    drafts = PortalDraft.objects.filter(pk=draft_id, owner=owner, mailbox=mailbox)
    if editing:
        draft = drafts.first()
        if draft is None:
            raise DraftAttachmentsChanged('Draft was removed elsewhere')
    else:
        draft, created = drafts.get_or_create(pk=draft_id, owner=owner, mailbox=mailbox,
                                               defaults={'encrypted_content': content})
        if not created:
            return
    kept = list(draft.attachments.defer('encrypted_data').exclude(pk__in=removed))
    if len(kept) + len(uploads) > ATTACHMENT_COUNT or sum(
            file.size for file in [*kept, *uploads]) > ATTACHMENT_LIMIT:
        raise DraftAttachmentsChanged('Invalid total attachment size or count')
    draft.encrypted_content = content
    draft.save(update_fields=['encrypted_content', 'updated_at'])
    draft.attachments.filter(pk__in=removed).delete()
    if uploads:
        cipher = Fernet(settings.MAILBOX_KEY)
        DraftAttachment.objects.bulk_create([
            DraftAttachment(draft=draft, name=file.name, size=file.size,
                            encrypted_data=cipher.encrypt(file.read())) for file in uploads
        ])


def compose_files(draft, kept, uploads):
    result = []
    if draft and kept:
        cipher = Fernet(settings.MAILBOX_KEY)
        persisted = draft.attachments.filter(pk__in=[file.pk for file in kept]).order_by('pk')
        for name, data in persisted.values_list('name', 'encrypted_data'):
            result.append((name, cipher.decrypt(data)))
        if len(result) != len(kept):
            raise DraftAttachmentsChanged('Attachment was edited elsewhere')
    return [*result, *((file.name, file.read()) for file in uploads)]


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
