from django.conf import settings
from django.db import models


class Mailbox(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    address = models.EmailField(unique=True)
    encrypted_credential = models.TextField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.address


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
