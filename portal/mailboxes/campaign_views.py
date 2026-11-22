"""Administrator-only seminar email screens."""
import uuid

from cryptography.fernet import InvalidToken
from django import forms
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import campaigns, seminar_source
from .managed import kappa_admin
from .models import Mailbox, SeminarCampaign

SOURCES = [(key, name) for key, (name, _) in seminar_source.SOURCES.items()]
STATES = {'prepared': 'آمادهٔ تأیید', 'queued': 'در صف ارسال', 'paused': 'متوقف', 'finished': 'پایان‌یافته'}


class SeminarForm(forms.Form):
    source = forms.ChoiceField(label='سمینار', choices=SOURCES, initial='nobel')
    mailbox = forms.ModelChoiceField(label='صندوق فرستنده', queryset=Mailbox.objects.none())
    subject = forms.CharField(label='موضوع ایمیل', max_length=200)
    body = forms.CharField(label='متن ایمیل', max_length=200000, widget=forms.Textarea)
    nonce = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mailbox'].queryset = Mailbox.objects.filter(address__iendswith='@kappaut.ir').order_by('address')

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if '\r' in subject or '\n' in subject:
            raise forms.ValidationError('موضوع نامعتبر است.')
        return subject


def summary(campaign):
    return {
        'campaign': campaign,
        'source_label': seminar_source.SOURCES[campaign.source][0],
        'state_label': STATES[campaign.state],
    }


@kappa_admin
@require_http_methods(['GET', 'POST'])
def seminar_mail(request):
    posted = request.method == 'POST'
    form = SeminarForm(request.POST if posted else None, initial={'nonce': uuid.uuid4()})
    if posted and form.is_valid():
        try:
            rows = seminar_source.read_emails(form.cleaned_data['source'])
        except (OSError, ValueError):
            form.add_error('source', 'خواندن ثبت‌نام‌های این سمینار ممکن نشد. دوباره تلاش کنید.')
        else:
            try:
                campaign = campaigns.prepare_campaign(request.user, form, rows)
            except IntegrityError:
                return HttpResponseNotFound()
            if campaign is not None:
                return redirect('kappa_seminar_detail', campaign_id=campaign.pk)
            form.add_error('source', 'در این سمینار هیچ نشانی معتبر برای ارسال پیدا نشد.')
    recent = SeminarCampaign.objects.filter(owner=request.user).select_related('mailbox').order_by('-created_at')[:20]
    return render(request, 'kappa_seminar_mail.html', {'form': form, 'recent': [summary(item) for item in recent]})


@kappa_admin
@require_http_methods(['GET'])
def campaign_detail(request, campaign_id):
    campaign = get_object_or_404(SeminarCampaign.objects.select_related('mailbox'), pk=campaign_id, owner=request.user)
    counts = {row['state']: row['count'] for row in campaign.recipients.values('state').annotate(count=Count('pk'))}
    try:
        payload = campaigns.contents(campaign)
        exceptions = [(campaigns.address_of(item), item.state)
                      for item in campaign.recipients.filter(state__in=('rejected', 'uncertain')).order_by('pk')[:30]]
    except (ValueError, InvalidToken):
        messages.error(request, 'خواندن اطلاعات این ارسال ممکن نشد.')
        payload, exceptions = {}, []
    return render(request, 'kappa_seminar_detail.html', {
        **summary(campaign), 'payload': payload, 'counts': counts,
        'recipient_count': sum(counts.values()), 'exceptions': exceptions,
    })


@kappa_admin
@require_POST
def campaign_update(request, campaign_id):
    campaign = get_object_or_404(SeminarCampaign, pk=campaign_id, owner=request.user)
    matching = SeminarCampaign.objects.filter(pk=campaign.pk)
    action = request.POST.get('action')
    updated = 0
    if action == 'queue':
        updated = matching.filter(state='prepared').update(state='queued', queued_at=timezone.now())
    elif action == 'pause':
        updated = matching.filter(state='queued').update(state='paused')
    elif action == 'resume':
        updated = matching.filter(state='paused').update(state='queued')
    else:
        return HttpResponseNotFound()
    if updated and action in ('queue', 'resume'):
        messages.success(request, 'ارسال در صف قرار گرفت.')
    return redirect('kappa_seminar_detail', campaign_id=campaign.pk)
