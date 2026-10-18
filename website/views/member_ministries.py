"""Member ministry pages, independent from the personal dashboard."""

import unicodedata

from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .member_context import MemberPersonalContextMixin
from .mixins import MemberRequiredMixin


# Explicit template registry: add new ministry pages here without growing Home.
# Stable ministry codes take precedence; names support existing legacy records.
MINISTRY_PAGES = {
    'midia_externa': 'media',
    'midia externa': 'media',
    'boas vindas': 'welcome',
    'boas_vindas': 'welcome',
    'ministracao': 'ministration',
    'moderacao': 'moderation',
    'louvor': 'worship',
    'ministerio de louvor': 'worship',
}


def ministry_page_template(ministry):
    name = ''.join(
        char for char in unicodedata.normalize('NFKD', ministry.name.lower())
        if not unicodedata.combining(char)
    ).strip()
    page = MINISTRY_PAGES.get(ministry.code) or MINISTRY_PAGES.get(name, 'generic')
    return f'member/ministries/{page}.html'


class MemberMinistriesView(MemberRequiredMixin, View):
    def get(self, request):
        memberships = self.member.ministry_memberships.filter(
            is_active=True, ministry__is_active=True,
        ).select_related('ministry').order_by('ministry__name')
        # Preserve links from the previous query-string route.
        selected = request.GET.get('ministry', '')
        if selected.isdigit():
            membership = get_object_or_404(memberships, ministry_id=int(selected))
            return redirect('member_ministry_detail', ministry_id=membership.ministry_id)
        return render(request, 'member/ministries/index.html', {
            'member': self.member,
            'member_ministries': memberships,
            'ministry_page': True,
            'member_nav_active': 'ministries',
        })


class MemberMinistryDetailView(MemberRequiredMixin, MemberPersonalContextMixin, View):
    def get(self, request, ministry_id):
        membership = get_object_or_404(
            self.member.ministry_memberships.select_related('ministry'),
            ministry_id=ministry_id, is_active=True, ministry__is_active=True,
        )
        context = self._build_base_context(self.member)
        context.update({
            'membership': membership,
            'ministry': membership.ministry,
            'ministry_page': True,
            'member_nav_active': 'ministries',
            'member_schedules': [
                schedule for schedule in context['member_schedules']
                if schedule.ministry_id == ministry_id
            ],
        })
        return render(request, ministry_page_template(membership.ministry), context)
