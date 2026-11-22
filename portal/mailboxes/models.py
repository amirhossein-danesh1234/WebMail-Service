import uuid

from django.conf import settings
from django.db import models


class Mailbox(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    address = models.EmailField(unique=True)
    encrypted_credential = models.TextField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.address


class PendingMailbox(models.Model):
    address = models.EmailField(unique=True)
    encrypted_credential = models.TextField(editable=False)


class MailboxGrant(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE, related_name='grants', verbose_name='صندوق')
    can_read = models.BooleanField('خواندن پیام‌ها', default=False)
    can_send = models.BooleanField('ارسال از این آدرس', default=False)

    class Meta:
        verbose_name = 'دسترسی صندوق'
        verbose_name_plural = 'دسترسی صندوق‌ها'
        constraints = [
            models.UniqueConstraint(fields=['user', 'mailbox'], name='unique_mailbox_grant'),
        ]


class SendAttempt(models.Model):
    """A submitted form can never silently resend after a network ambiguity."""
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE)
    nonce = models.UUIDField(unique=True)
    state = models.CharField(max_length=16, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)


class LoginWindow(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()


class PortalDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE)
    encrypted_content = models.TextField(editable=False)
    updated_at = models.DateTimeField(auto_now=True)


class DraftAttachment(models.Model):
    draft = models.ForeignKey(PortalDraft, on_delete=models.CASCADE, related_name='attachments')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    encrypted_data = models.BinaryField(editable=False)


class SeminarCampaign(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.PROTECT)
    source = models.CharField(max_length=12)
    encrypted_message = models.BinaryField(editable=False)
    total_rows = models.PositiveIntegerField()
    invalid_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=12, default='prepared', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True)

    class Meta:
        permissions = [('send_seminar_campaign', 'Can email registered seminar attendees')]


class SeminarRecipient(models.Model):
    campaign = models.ForeignKey(SeminarCampaign, on_delete=models.CASCADE, related_name='recipients')
    encrypted_address = models.BinaryField(editable=False)
    state = models.CharField(max_length=12, default='pending')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['campaign', 'state'])]
