import hashlib
import hmac
import imaplib
import smtplib
import uuid
from datetime import timedelta
from email import policy
from email.message import Message
from urllib.parse import urlencode

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters

from . import mail, workspace
from .forms import ComposeForm, ConnectForm, WorkspaceComposeForm
from .models import LoginWindow, Mailbox, MailboxGrant, PortalDraft, SendAttempt

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
        if settings.MANAGED_KAPPA:
            if not request.user.is_superuser:
                return HttpResponseNotFound()
            return render(request, 'kappa_admin.html', {
                'people': User.objects.filter(is_staff=False, is_active=True).order_by('first_name', 'username'),
                'mailboxes': Mailbox.objects.order_by('address'),
            })
        return render(request, 'admin_home.html')
    if settings.MANAGED_KAPPA:
        grant = workspace.grants_for(request.user).filter(can_read=True).first()
        if grant:
            return render_workspace(request, grant, 'inbox')
        grant = workspace.grants_for(request.user).filter(can_send=True).first()
        return render(request, 'workspace.html', workspace.context(request, grant))
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


def authorized_box(request, box_id, permission):
    if settings.MANAGED_KAPPA:
        grant = get_object_or_404(
            MailboxGrant.objects.select_related('mailbox'), mailbox_id=box_id,
            user=request.user, **{permission: True}
        )
        return grant.mailbox, grant
    return get_object_or_404(Mailbox, pk=box_id, owner=request.user), None


@login_required
@require_POST
def disconnect(request, box_id):
    if settings.MANAGED_KAPPA:
        return HttpResponseNotFound()
    get_object_or_404(Mailbox, pk=box_id, owner=request.user).delete()
    messages.success(request, 'اتصال حذف شد؛ صندوق و ایمیل‌های Stalwart حذف نشده‌اند.')
    return redirect('home')


@login_required
def inbox(request, box_id, folder='inbox'):
    box, grant = authorized_box(request, box_id, 'can_read')
    if grant is not None:
        return render_workspace(request, grant, folder)
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
        'page': page, 'can_send': grant is None or grant.can_send,
        'legacy_webmail': grant is None, 'next_page': page + 1 if more else None,
        'previous_page': page - 1 if page > 1 else None})


def render_workspace(request, grant, folder, uid=None):
    if folder not in mail.FOLDERS:
        return HttpResponseNotFound()
    data = workspace.context(request, grant, folder)
    box = grant.mailbox
    try:
        page = max(1, min(100000, int(request.GET.get('page', '1'))))
    except ValueError:
        page = 1
    try:
        items, more = mail.listing(box, folder, page, query=data['query'], filter_by=data['filter_by'])
    except MAIL_ERRORS:
        messages.error(request, 'باز کردن پوشه یا جست‌وجو انجام نشد.')
        items, more = [], False
    data.update(items=items, selected=uid, next_page=page + 1 if more else None,
                previous_page=page - 1 if page > 1 else None)
    if uid is not None:
        try:
            msg, version, flags = mail.read(box, folder, uid, with_flags=True)
            data.update(
                opened=workspace_message(msg, uid, flags), body=mail.text_body(msg, html_as_text=True), version=version,
                attachments=[{'index': i, 'name': p.get_filename() or 'پیوست'}
                             for i, p in enumerate(msg.walk()) if p.get_content_disposition() == 'attachment' or p.get_filename()],
                reply_urls=[(label, reverse('compose', args=[box.pk]) + '?' + urlencode({
                    'source': f'{folder}/{uid}/{mode}/{version}'})) for mode, label in workspace.MODES.items()],
            )
        except MAIL_ERRORS:
            messages.error(request, 'پیام در دسترس نیست یا بیش از ۲۰ مگابایت است.')
    return render(request, 'workspace.html', data)


def workspace_message(msg, uid, flags):
    data = mail.message_summary(msg, uid, flags)
    data.update(to=str(msg.get('To', '')), cc=str(msg.get('Cc', '')), bcc=str(msg.get('Bcc', '')))
    return data


@login_required
@require_POST
def message_update(request, box_id, folder, uid):
    if not settings.MANAGED_KAPPA or folder not in mail.FOLDERS:
        return HttpResponseNotFound()
    box, grant = authorized_box(request, box_id, 'can_send')
    if not grant.can_read:
        return HttpResponseNotFound()
    action = request.POST.get('action', '')
    try:
        mail.update_message(box, folder, uid, request.POST.get('version'), action)
    except MAIL_ERRORS:
        messages.error(request, 'تغییر ذخیره نشد. صفحه را تازه کنید و دوباره امتحان کنید.')
    if action in mail.MESSAGE_FLAGS:
        return redirect('message', box_id=box_id, folder=folder, uid=uid)
    return redirect('inbox', box_id=box_id, folder=folder)


@login_required
def drafts(request, box_id):
    if not settings.MANAGED_KAPPA:
        return HttpResponseNotFound()
    box, grant = authorized_box(request, box_id, 'can_send')
    items = PortalDraft.objects.filter(mailbox=box, owner=request.user).order_by('-updated_at', 'pk')
    page = Paginator(items, 25).get_page(request.GET.get('page'))
    entries = []
    for draft in page:
        try:
            subject = workspace.decode_draft(draft).get('subject')
        except (ValueError, InvalidToken):
            subject = ''
        entries.append((draft, subject or '(بدون موضوع)'))
    data = workspace.context(request, grant, 'drafts')
    return render(request, 'workspace.html', {**data, 'draft_entries': entries, 'draft_page': page})


@login_required
def message(request, box_id, folder, uid):
    box, grant = authorized_box(request, box_id, 'can_read')
    if grant is not None:
        return render_workspace(request, grant, folder, uid)
    try:
        msg = mail.read(box, folder, uid)
        attachments = [{'index': i, 'name': p.get_filename() or 'attachment'}
                       for i, p in enumerate(msg.walk()) if p.get_content_disposition() == 'attachment' or p.get_filename()]
        body = mail.text_body(msg)
    except MAIL_ERRORS:
        messages.error(request, 'پیام در دسترس نیست یا بیش از ۲۰ مگابایت است.')
        return redirect('inbox', box_id=box.pk)
    return render(request, 'message.html', {'box': box, 'folder': folder, 'uid': uid,
        'subject': str(msg.get('Subject', '')), 'sender': str(msg.get('From', '')),
        'date': str(msg.get('Date', '')), 'body': body, 'attachments': attachments})


@login_required
def attachment(request, box_id, folder, uid, part):
    box, _ = authorized_box(request, box_id, 'can_read')
    try:
        msg = mail.read(box, folder, uid)
        pieces = list(msg.walk())
        if part >= len(pieces):
            return HttpResponseNotFound()
        piece = pieces[part]
        if not piece.get_filename() and piece.get_content_disposition() != 'attachment':
            return HttpResponseNotFound()
        data = piece.get_payload(decode=True)
        if data is None and piece.get_content_type() == 'message/rfc822':
            nested = piece.get_content()
            if isinstance(nested, Message):
                data = nested.as_bytes(policy=policy.SMTP)
        if not isinstance(data, bytes):
            return HttpResponseNotFound()
        from django.http import FileResponse
        import io
        return FileResponse(io.BytesIO(data), as_attachment=True,
                            filename=piece.get_filename() or 'attachment',
                            content_type='application/octet-stream')
    except MAIL_ERRORS:
        return HttpResponseNotFound()


def compose_source(raw, box, grant):
    if not raw:
        return None, None
    folder, uid, mode, version = raw.split('/')
    if not grant.can_read or folder not in mail.FOLDERS or mode not in workspace.MODES:
        raise ValueError('Invalid source')
    if not uid.isascii() or not uid.isdigit() or len(uid) > 10:
        raise ValueError('Invalid UID')
    msg, actual, _ = mail.read(box, folder, int(uid), with_flags=True)
    if not version or version != actual:
        raise ValueError('Folder changed')
    return msg, mode


@login_required
def compose(request, box_id):
    box, grant = authorized_box(request, box_id, 'can_send')
    return_to_inbox = grant is None or grant.can_read
    is_post = request.method == 'POST'
    draft = None
    source, original, mode = '', None, None
    initial = {'nonce': uuid.uuid4()}
    saving_draft = False
    saved_files, removed, keep, files = [], set(), [], []
    if grant is not None:
        draft_id = request.GET.get('draft')
        if draft_id is not None:
            try:
                draft_key = uuid.UUID(draft_id)
            except ValueError:
                return HttpResponseNotFound()
            draft = get_object_or_404(PortalDraft, pk=draft_key, mailbox=box, owner=request.user)
            if is_post and request.POST.get('intent') == 'discard':
                draft.delete()
                messages.success(request, 'پیش‌نویس حذف شد.')
                return redirect('drafts', box_id=box.pk)
            saved_files = list(draft.attachments.defer('encrypted_data').order_by('pk'))
            requested = set(request.POST.getlist('remove_attachment'))
            removed = {item.pk for item in saved_files if str(item.pk) in requested}
            keep = [item for item in saved_files if item.pk not in removed]
            try:
                initial.update(workspace.decode_draft(draft))
            except (InvalidToken, ValueError):
                return HttpResponseNotFound()
        source = initial.get('source', '') if draft else request.GET.get('source', '')
        if source and not grant.can_read:
            return HttpResponseNotFound()
        try:
            original, mode = compose_source(source, box, grant)
            if original is not None and not draft:
                initial.update(workspace.reply_fields(box, original, mode))
        except MAIL_ERRORS:
            messages.error(request, 'پیام اصلی جابه‌جا شده یا در دسترس نیست.')
            return redirect('drafts', box_id=box.pk) if draft else redirect('inbox', box_id=box.pk)
        saving_draft = is_post and request.POST.get('intent') == 'draft'
        form = WorkspaceComposeForm(request.POST if is_post else None, request.FILES if is_post else None,
                                    initial=initial, saving_draft=saving_draft, saved_files=keep)
    else:
        form = ComposeForm(request.POST if is_post else None, request.FILES if is_post else None, initial=initial)

    if is_post and form.is_valid() and saving_draft:
        content = workspace.encode_draft(form.cleaned_data, source)
        try:
            workspace.save_draft(draft.pk if draft else form.cleaned_data['nonce'], request.user, box,
                                 content, form.cleaned_data['attachment'], removed, editing=draft is not None)
        except IntegrityError:
            return HttpResponseNotFound()
        except workspace.DraftAttachmentsChanged:
            form.add_error('attachment', 'پیوست‌ها تغییر کرده‌اند. صفحه را تازه کنید و دوباره امتحان کنید.')
        else:
            messages.success(request, 'پیش‌نویس و پیوست‌هایش ذخیره شدند.')
            return redirect('drafts', box_id=box.pk)
    if is_post and form.is_valid() and not saving_draft and grant is not None:
        try:
            files = workspace.compose_files(draft, keep, form.cleaned_data['attachment'])
        except (OSError, InvalidToken, workspace.DraftAttachmentsChanged):
            form.add_error('attachment', 'خواندن پیوست‌ها ممکن نشد. دوباره فایل را انتخاب کنید.')
    if is_post and form.is_valid() and not saving_draft:
        try:
            with transaction.atomic():
                attempt = SendAttempt.objects.create(mailbox=box, nonce=draft.pk if draft else form.cleaned_data['nonce'])
        except IntegrityError:
            messages.error(request, 'این فرم قبلاً ثبت شده است؛ ارسال تکرار نشد.')
            return redirect('inbox', box_id=box.pk) if return_to_inbox else redirect('home')
        try:
            extra = {}
            attachment = form.cleaned_data['attachment']
            if grant is not None:
                attachment = None
                extra = {'cc': form.cleaned_data['cc'], 'bcc': form.cleaned_data['bcc'],
                         'related': workspace.thread_headers(original) if mode in ('reply', 'reply-all') else None,
                         'forward': original if mode == 'forward' else None, 'files': files}
            result = mail.send(box, form.cleaned_data['to'], form.cleaned_data['subject'],
                               form.cleaned_data['body'], attachment, **extra)
        except MAIL_ERRORS:
            attempt.state = 'uncertain'
            messages.error(request, 'ارسال تأیید نشد. پوشهٔ ارسالی و وضعیت دریافت را قبل از تلاش دوباره بررسی کنید.')
        else:
            attempt.state = 'accepted' if result['all_accepted'] else 'partial'
            if result['all_accepted']:
                messages.success(request, 'سرور پیام را برای ارسال پذیرفت.')
            else:
                messages.warning(request, 'سرور بعضی گیرنده‌ها را نپذیرفت؛ پیام را برای همه دوباره ارسال نکنید.')
            if not result['sent_copy']:
                messages.warning(request, 'نسخهٔ پیام در پوشهٔ ارسالی ذخیره نشد.')
        with transaction.atomic():
            attempt.save(update_fields=['state'])
            if draft and attempt.state == 'accepted':
                draft.delete()
        return redirect('inbox', box_id=box.pk, folder='sent') if return_to_inbox else redirect('home')
    if grant is not None:
        return render(request, 'workspace_compose.html', {
            **workspace.context(request, grant), 'form': form, 'draft': draft, 'mode': mode,
            'saved_files': saved_files, 'removed_files': removed,
            'title': workspace.MODES.get(mode, 'ایمیل جدید'),
        })
    return render(request, 'form.html', {'form': form, 'title': 'ارسال از ' + box.address,
                                         'hint': 'فرستنده فقط همین صندوق احراز هویت‌شده است.'})
