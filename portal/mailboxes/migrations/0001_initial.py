from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='LoginWindow', fields=[
            ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
            ('count', models.PositiveIntegerField(default=0)),
            ('started_at', models.DateTimeField())]),
        migrations.CreateModel(name='Mailbox', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('address', models.EmailField(max_length=254, unique=True)),
            ('encrypted_credential', models.TextField(editable=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name='SendAttempt', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('nonce', models.UUIDField(unique=True)),
            ('state', models.CharField(default='pending', max_length=16)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('mailbox', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='mailboxes.mailbox'))]),
    ]
