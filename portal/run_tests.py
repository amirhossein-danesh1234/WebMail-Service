"""Isolated offline tests. Never connects to production mail."""
import os
import secrets
import sys
import tempfile
from cryptography.fernet import Fernet

with tempfile.TemporaryDirectory() as directory:
    os.environ['PORTAL_DATA_DIR'] = directory
    os.environ['PORTAL_SECRET_KEY'] = secrets.token_urlsafe(48)
    os.environ['PORTAL_MAILBOX_KEY'] = Fernet.generate_key().decode()
    os.environ['DJANGO_SETTINGS_MODULE'] = 'mailportal.settings'
    import django
    django.setup()
    from django.test.utils import get_runner
    from django.conf import settings
    result = get_runner(settings)(verbosity=2).run_tests(['mailboxes'])
    sys.exit(bool(result))
