import hashlib
import hmac
import imaplib
import smtplib
import uuid
from datetime import timedelta

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters

from . import mail
from .forms import ComposeForm, ConnectForm
from .models import LoginWindow, Mailbox, SendAttempt

MAIL_ERRORS = (OSError, ValueError, LookupError, imaplib.IMAP4.error, smtplib.SMTPException, InvalidToken)


def limited(request, purpose):
    # Reverse proxy supplies a validated client address. No raw IPs in the database.
    address = request.META.get('HTTP_X_REAL_IP', request.META.get('REMOTE_ADDR', ''))
    identity = request.POST.get('username', '').strip().casefold() if purpose == 'login' else str(request.user.pk)
    key = hmac.new(settings.SECRET_KEY.encode(), (purpose + ':' + address + ':' + identity).encode(), hashlib.sha256).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        row, _ = LoginWindow.objects.get_or_create(key=key, defaults={'started_at': now})
        if row.started_at < now - timedelta(minutes=10):
            row.count = 0
            row.started_at = now
            row.save(update_fields=['count', 'started_at'])
        LoginWindow.objects.filter(pk=key).update(count=F('count') + 1)
        row.refresh_from_db()
        return row.count > 15


@method_decorator(sensitive_post_parameters('password'), name='dispatch')
class PortalLoginView(LoginView):
    template_name = 'registration/login.html'

    def post(self, request, *args, **kwargs):
        if limited(request, 'login'):
            return HttpResponse('تلاش‌های ورود زیاد است؛ ده دقیقه بعد دوباره تلاش کنید.', status=429)
        return super().post(request, *args, **kwargs)


def health(request):
    return HttpResponse('ok', content_type='text/plain')


def staff_gate(request):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return HttpResponse(status=200 if request.user.is_staff else 403)


@login_required
def home(request):
    if request.get_host().split(':')[0] == settings.ADMIN_HOST:
        if not request.user.is_staff:
            return HttpResponseNotFound()
        return render(request, 'admin_home.html')
    return render(request, 'home.html', {'boxes': Mailbox.objects.filter(owner=request.user)})


@sensitive_post_parameters('password')
@login_required
def connect(request):
    form = ConnectForm(request.POST or None)
    if request.method == 'POST' and limited(request, 'connect'):
        return HttpResponse('تلاش‌های اتصال زیاد است؛ ده دقیقه بعد دوباره تلاش کنید.', status=429)
    if request.method == 'POST' and form.is_valid():
        address = form.cleaned_data['address']
        password = form.cleaned_data['password']
        try:
            with mail.connection(address, password):
                pass
        except MAIL_ERRORS:
            form.add_error(None, 'اتصال تأیید نشد؛ آدرس، رمز و دسترس‌پذیری صندوق را بررسی کنید.')
        else:
            try:
                with transaction.atomic():
                    box, created = Mailbox.objects.get_or_create(address=address, defaults={
                        'owner': request.user, 'encrypted_credential': mail.seal(address, password)})
                    if box.owner_id != request.user.pk:
                        form.add_error(None, 'این صندوق قابل اتصال به حساب شما نیست؛ با ادمین تماس بگیرید.')
                    else:
                        box.encrypted_credential = mail.seal(address, password)
                        box.save(update_fields=['encrypted_credential'])
                        messages.success(request, 'صندوق پس از احراز هویت متصل شد.')
                        return redirect('home')
            except IntegrityError:
                form.add_error(None, 'اتصال هم‌زمان انجام شده است؛ صفحه را دوباره باز کنید.')
    return render(request, 'form.html', {'form': form, 'title': 'اتصال صندوق موجود',
        'hint': 'ساخت آدرس جدید فقط توسط ادمین انجام می‌شود. رمز اتصال به‌صورت رمزنگاری‌شده روی سرور نگهداری می‌شود.'})


def owned(request, box_id):
    return get_object_or_404(Mailbox, pk=box_id, owner=request.user)


@login_required
@require_POST
def disconnect(request, box_id):
    owned(request, box_id).delete()
    messages.success(request, 'اتصال حذف شد؛ صندوق و ایمیل‌های Stalwart حذف نشده‌اند.')
    return redirect('home')


@login_required
def inbox(request, box_id, folder='inbox'):
    box = owned(request, box_id)
    if folder not in ('inbox', 'sent', 'junk', 'trash'):
        return HttpResponseNotFound()
    try:
        page = max(1, min(100000, int(request.GET.get('page', '1'))))
    except ValueError:
        page = 1
    try:
        items, more = mail.listing(box, folder, page)
    except MAIL_ERRORS:
        messages.error(request, 'خواندن صندوق ممکن نشد؛ اتصال یا رمز صندوق را بررسی کنید.')
        items, more = [], False
    return render(request, 'inbox.html', {'box': box, 'items': items, 'folder': folder,
        'page': page, 'next_page': page + 1 if more else None,
        'previous_page': page - 1 if page > 1 else None})


@login_required
def message(request, box_id, folder, uid):
    box = owned(request, box_id)
    try:
        msg = mail.read(box, folder, uid)
        attachments = [{'index': i, 'name': p.get_filename() or 'attachment'}
                       for i, p in enumerate(msg.walk()) if p.get_content_disposition() == 'attachment' or p.get_filename()]
        body = mail.text_body(msg)
    except MAIL_ERRORS:
        messages.error(request, 'پیام در دسترس نیست یا بیش از ۲۰ مگابایت است؛ از وب‌میل کامل استفاده کنید.')
        return redirect('inbox', box_id=box.pk)
    return render(request, 'message.html', {'box': box, 'folder': folder, 'uid': uid,
        'subject': str(msg.get('Subject', '')), 'sender': str(msg.get('From', '')),
        'date': str(msg.get('Date', '')), 'body': body, 'attachments': attachments})


@login_required
def attachment(request, box_id, folder, uid, part):
    box = owned(request, box_id)
    try:
        msg = mail.read(box, folder, uid)
        pieces = list(msg.walk())
        if part >= len(pieces):
            return HttpResponseNotFound()
        piece = pieces[part]
        if not piece.get_filename() and piece.get_content_disposition() != 'attachment':
            return HttpResponseNotFound()
        data = piece.get_payload(decode=True)
        if not isinstance(data, bytes):
            return HttpResponseNotFound()
        from django.http import FileResponse
        import io
        return FileResponse(io.BytesIO(data), as_attachment=True,
                            filename=piece.get_filename() or 'attachment',
                            content_type='application/octet-stream')
    except MAIL_ERRORS:
        return HttpResponseNotFound()


@login_required
def compose(request, box_id):
    box = owned(request, box_id)
    form = ComposeForm(request.POST or None, request.FILES or None,
                       initial={'nonce': uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                attempt = SendAttempt.objects.create(mailbox=box, nonce=form.cleaned_data['nonce'])
        except IntegrityError:
            messages.error(request, 'این فرم قبلاً ثبت شده است؛ برای جلوگیری از ارسال تکراری دوباره ارسال نشد.')
            return redirect('inbox', box_id=box.pk)
        try:
            result = mail.send(box, form.cleaned_data['to'], form.cleaned_data['subject'],
                               form.cleaned_data['body'], form.cleaned_data.get('attachment'))
        except MAIL_ERRORS:
            attempt.state = 'uncertain'
            messages.error(request, 'ارسال تأیید نشد. پیش از ارسال دوباره، پوشهٔ ارسالی و وضعیت دریافت را بررسی کنید؛ تکرار خودکار انجام نشد.')
        else:
            attempt.state = 'accepted' if result['all_accepted'] else 'partial'
            if result['all_accepted']:
                messages.success(request, 'سرور پیام را برای ارسال پذیرفت؛ این به‌معنی تحویل نهایی نیست.')
            else:
                messages.warning(request, 'سرور بعضی گیرنده‌ها را نپذیرفت؛ پیام را برای همه دوباره ارسال نکنید.')
            if not result['sent_copy']:
                messages.warning(request, 'نسخهٔ پیام در پوشهٔ ارسالی ذخیره نشد؛ پیام را دوباره ارسال نکنید.')
        attempt.save(update_fields=['state'])
        return redirect('inbox', box_id=box.pk, folder='sent')
    return render(request, 'form.html', {'form': form, 'title': 'ارسال از ' + box.address,
                                         'hint': 'فرستنده فقط همین صندوق احراز هویت‌شده است.'})
