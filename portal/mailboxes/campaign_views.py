"""Administrator-only seminar email screens."""
import uuid
from functools import wraps

from cryptography.fernet import InvalidToken
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import campaigns, seminar_source, workspace
from .models import SeminarCampaign

SOURCE_LABELS = {'nobel': 'Nobel Seminar', 'apply': 'Apply Seminar', 'quantum': 'Quantum Seminar'}
SOURCES = [(key, SOURCE_LABELS[key]) for key in seminar_source.SOURCES]
STATES = {'prepared': 'Ready for review', 'queued': 'Queued', 'paused': 'Paused', 'finished': 'Finished'}


class SeminarForm(forms.Form):
    source = forms.ChoiceField(label='Seminar', choices=SOURCES, initial='nobel')
    mailbox = forms.ModelChoiceField(label='Sender', queryset=None)
    subject = forms.CharField(label='Subject', max_length=200)
    body = forms.CharField(label='Message', max_length=200000, widget=forms.Textarea)
    nonce = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mailbox'].queryset = campaigns.sender_mailboxes(owner)

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if '\r' in subject or '\n' in subject:
            raise forms.ValidationError('Enter a subject on a single line.')
        return subject


def summary(campaign):
    return {
        'campaign': campaign,
        'source_label': SOURCE_LABELS[campaign.source],
        'state_label': STATES[campaign.state],
    }


def campaign_access(view):
    @wraps(view)
    @login_required
    def checked(request, *args, **kwargs):
        host = request.get_host().split(':')[0]
        if not settings.MANAGED_KAPPA or not campaigns.can_use_campaigns(request.user):
            return HttpResponseNotFound()
        if host == settings.ADMIN_HOST and not request.user.is_superuser:
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)
    return checked


def campaign_layout(request):
    if request.get_host().split(':')[0] == settings.ADMIN_HOST:
        return {'layout': 'kappa_campaign_base.html'}
    grant = workspace.grants_for(request.user).filter(can_read=True).first()
    grant = grant or workspace.grants_for(request.user).filter(can_send=True).first()
    return {**workspace.context(request, grant), 'layout': 'workspace.html'}


@campaign_access
@require_http_methods(['GET', 'POST'])
def seminar_mail(request):
    posted = request.method == 'POST'
    form = SeminarForm(request.POST if posted else None, owner=request.user, initial={'nonce': uuid.uuid4()})
    if posted and form.is_valid():
        try:
            rows = seminar_source.read_emails(form.cleaned_data['source'])
        except (OSError, ValueError):
            form.add_error('source', 'Unable to load registrations. Try again.')
        else:
            try:
                campaign = campaigns.prepare_campaign(request.user, form, rows)
            except IntegrityError:
                return HttpResponseNotFound()
            if campaign is not None:
                return redirect('kappa_seminar_detail', campaign_id=campaign.pk)
            form.add_error('source', 'This seminar has no valid email addresses.')
    recent = SeminarCampaign.objects.filter(owner=request.user).select_related('mailbox').order_by('-created_at')[:20]
    return render(request, 'kappa_seminar_mail.html', {**campaign_layout(request), 'form': form, 'recent': [summary(item) for item in recent]})


@campaign_access
@require_http_methods(['GET'])
def campaign_detail(request, campaign_id):
    campaign = get_object_or_404(SeminarCampaign.objects.select_related('mailbox'), pk=campaign_id, owner=request.user)
    counts = {row['state']: row['count'] for row in campaign.recipients.values('state').annotate(count=Count('pk'))}
    try:
        payload = campaigns.contents(campaign)
        exceptions = [(campaigns.address_of(item), item.state)
                      for item in campaign.recipients.filter(state__in=('rejected', 'uncertain')).order_by('pk')[:30]]
    except (ValueError, InvalidToken):
        messages.error(request, 'Unable to load this campaign.')
        payload, exceptions = {}, []
    return render(request, 'kappa_seminar_detail.html', {
        **campaign_layout(request), **summary(campaign), 'payload': payload, 'counts': counts,
        'recipient_count': sum(counts.values()), 'exceptions': exceptions,
    })


@campaign_access
@require_POST
def campaign_update(request, campaign_id):
    campaign = get_object_or_404(SeminarCampaign, pk=campaign_id, owner=request.user)
    matching = SeminarCampaign.objects.filter(pk=campaign.pk)
    action = request.POST.get('action')
    if action in ('queue', 'resume') and not campaigns.sender_mailboxes(request.user).filter(pk=campaign.mailbox_id).exists():
        messages.error(request, 'You no longer have permission to send from this mailbox.')
        return redirect('kappa_seminar_detail', campaign_id=campaign.pk)
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
        messages.success(request, 'Campaign queued.')
    return redirect('kappa_seminar_detail', campaign_id=campaign.pk)
