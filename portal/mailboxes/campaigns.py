"""Snapshot, queue and deliver messages to registered seminar attendees."""
import json
import smtplib
import time

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from . import mail
from .models import Mailbox, SeminarCampaign, SeminarRecipient


SEMINAR_PERMISSION = 'mailboxes.send_seminar_campaign'


def can_use_campaigns(user):
    return user.is_authenticated and user.is_active and user.has_perm(SEMINAR_PERMISSION)


def sender_mailboxes(user):
    boxes = Mailbox.objects.filter(address__iendswith='@kappaut.ir').order_by('address')
    if user.is_superuser and user.is_active:
        return boxes
    if can_use_campaigns(user):
        return boxes.filter(grants__user=user, grants__can_send=True)
    return boxes.none()


def cipher():
    return Fernet(settings.MAILBOX_KEY)


def normalize_addresses(rows):
    seen, recipients = set(), []
    invalid = duplicates = 0
    for value in rows:
        address = value.strip()
        try:
            validate_email(address)
            if not address.isascii() or len(address) > 254:
                raise ValidationError('Unsupported email address')
        except ValidationError:
            invalid += 1
            continue
        key = address.casefold()
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
            recipients.append(address)
    return recipients, invalid, duplicates


@transaction.atomic
def prepare_campaign(owner, form, rows):
    recipients, invalid, duplicates = normalize_addresses(rows)
    if not recipients:
        return None
    data = form.cleaned_data
    crypto = cipher()
    payload = json.dumps({'subject': data['subject'], 'body': data['body']}).encode()
    campaign, created = SeminarCampaign.objects.get_or_create(
        pk=data['nonce'], owner=owner,
        defaults={'mailbox': data['mailbox'], 'source': data['source'], 'encrypted_message': crypto.encrypt(payload),
                  'total_rows': len(rows), 'invalid_rows': invalid, 'duplicate_rows': duplicates})
    if created:
        SeminarRecipient.objects.bulk_create([
            SeminarRecipient(campaign=campaign, encrypted_address=crypto.encrypt(address.encode()))
            for address in recipients
        ], batch_size=200)
    return campaign


def contents(campaign):
    return json.loads(cipher().decrypt(bytes(campaign.encrypted_message)))


def address_of(recipient):
    return cipher().decrypt(bytes(recipient.encrypted_address)).decode()


@transaction.atomic
def claim_recipient():
    queued = SeminarCampaign.objects.filter(state='queued').select_related('owner', 'mailbox').order_by('queued_at', 'pk')
    campaign = queued.first()
    if not campaign:
        return None
    if not sender_mailboxes(campaign.owner).filter(pk=campaign.mailbox_id).exists():
        SeminarCampaign.objects.filter(pk=campaign.pk, state='queued').update(state='paused')
        return None
    recipient = campaign.recipients.filter(state='pending').order_by('pk').first()
    if recipient is None:
        SeminarCampaign.objects.filter(pk=campaign.pk, state='queued').update(state='finished')
        return None
    recipient.state = 'sending'
    recipient.save(update_fields=['state', 'updated_at'])
    return campaign, recipient


@transaction.atomic
def record_delivery(campaign, recipient, state, *, pause=False):
    SeminarRecipient.objects.filter(pk=recipient.pk, state='sending').update(state=state, updated_at=timezone.now())
    running = SeminarCampaign.objects.filter(pk=campaign.pk, state='queued')
    if pause:
        running.update(state='paused')
    elif not campaign.recipients.filter(state__in=('pending', 'sending')).exists():
        running.update(state='finished')


@transaction.atomic
def recover_interrupted():
    interrupted = SeminarRecipient.objects.filter(state='sending')
    campaigns = set(interrupted.values_list('campaign_id', flat=True))
    interrupted.update(state='uncertain', updated_at=timezone.now())
    SeminarCampaign.objects.filter(pk__in=campaigns, state='queued').update(state='paused')


def deliver_one(campaign, recipient):
    try:
        payload = contents(campaign)
        address = address_of(recipient)
    except (ValueError, InvalidToken):
        record_delivery(campaign, recipient, 'pending', pause=True)
        return
    try:
        result = mail.send(campaign.mailbox, [address], payload['subject'], payload['body'])
    except smtplib.SMTPRecipientsRefused as error:
        if any(400 <= code < 500 for code, _ in error.recipients.values()):
            record_delivery(campaign, recipient, 'pending', pause=True)
        else:
            record_delivery(campaign, recipient, 'rejected')
    except smtplib.SMTPDataError as error:
        if 400 <= error.smtp_code < 500:
            record_delivery(campaign, recipient, 'pending', pause=True)
        else:
            record_delivery(campaign, recipient, 'rejected')
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused, smtplib.SMTPConnectError, InvalidToken):
        record_delivery(campaign, recipient, 'pending', pause=True)
    except (OSError, ValueError, smtplib.SMTPException):
        # SMTP may have accepted DATA before the connection was interrupted.
        record_delivery(campaign, recipient, 'uncertain', pause=True)
    else:
        record_delivery(campaign, recipient, 'accepted' if result['all_accepted'] else 'rejected')


def run_batch(batch=12, delay=2):
    recover_interrupted()
    for index in range(batch):
        if index:
            time.sleep(delay)
        pair = claim_recipient()
        if pair is None:
            break
        deliver_one(*pair)
