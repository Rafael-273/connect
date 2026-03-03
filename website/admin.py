from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event

admin.site.register(ministry.Ministry)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)


@admin.register(evangelism.Evangelized)
class EvangelizedAdmin(admin.ModelAdmin):
    list_display = ('name', 'gender', 'phone', 'neighborhood', 'evangelism_date', 'evangelized_by', 'conversion')
    list_filter = ('conversion', 'gender', 'evangelism_date', 'neighborhood')
    search_fields = ('name', 'phone', 'address')
    date_hierarchy = 'evangelism_date'
    readonly_fields = ('evangelism_date',)

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
