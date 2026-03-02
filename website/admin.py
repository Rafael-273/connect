from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event
from .models.schedule import ScaleDivision, DivisionMember

admin.site.register(ministry.Ministry)
admin.site.register(evangelism.Evangelized)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)

@admin.register(event.Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_date', 'location', 'is_recurring', 'recurrence_pattern', 'is_visible')
    list_filter = ('is_recurring', 'recurrence_pattern', 'event_date')
    search_fields = ('title', 'description', 'location')
    prepopulated_fields = {'slug': ('title',)}
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'banner', 'slug')
        }),
        ('Data e Horário', {
            'fields': ('event_date', 'event_time', 'display_start', 'display_end')
        }),
        ('Localização e Links', {
            'fields': ('location', 'link_more_info', 'link_type')
        }),
        ('Recorrência', {
            'fields': ('is_recurring', 'recurrence_pattern', 'recurrence_description'),
            'classes': ('collapse',),
            'description': 'Configure aqui se o evento se repete regularmente'
        }),
    )


class ScaleDivisionInline(admin.TabularInline):
    model = ScaleDivision
    fk_name = 'parent'
    extra = 0
    fields = ('name', 'order', 'is_active')
    verbose_name = 'Subdivisão'
    verbose_name_plural = 'Subdivisões'


@admin.register(ScaleDivision)
class ScaleDivisionAdmin(admin.ModelAdmin):
    list_display = ('name', 'ministry', 'parent', 'order', 'is_active')
    list_filter = ('ministry', 'is_active')
    search_fields = ('name', 'ministry__name')
    list_editable = ('order', 'is_active')
    inlines = [ScaleDivisionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('ministry', 'parent')


class DivisionMemberInline(admin.TabularInline):
    model = DivisionMember
    extra = 0
    autocomplete_fields = ['member']


@admin.register(DivisionMember)
class DivisionMemberAdmin(admin.ModelAdmin):
    list_display = ('member', 'division', 'schedule_day')
    list_filter = ('division__ministry', 'division')
    search_fields = ('member__name', 'division__name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'member', 'division', 'division__ministry', 'schedule_day'
        )
