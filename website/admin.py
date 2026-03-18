from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event, testimony, prayer_request
from .models.schedule import ScaleDivision, DivisionMember

admin.site.register(ministry.Ministry)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)


@admin.register(prayer_request.PrayerRequest)
class PrayerRequestAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name', 'content')
    readonly_fields = ('created_at',)


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


class ScaleDivisionInline(admin.TabularInline):
    model = ScaleDivision
    fk_name = 'parent'
    extra = 0
    fields = ('name', 'order', 'is_active')
    verbose_name = 'Subdivisão'
    verbose_name_plural = 'Subdivisões'


@admin.register(ScaleDivision)
class ScaleDivisionAdmin(admin.ModelAdmin):
    list_display = ('name', 'schedule', 'parent', 'order', 'is_active')
    list_filter = ('schedule', 'is_active')
    search_fields = ('name', 'schedule__title')
    list_editable = ('order', 'is_active')
    inlines = [ScaleDivisionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('schedule', 'parent')


class DivisionMemberInline(admin.TabularInline):
    model = DivisionMember
    extra = 0
    autocomplete_fields = ['member']


@admin.register(DivisionMember)
class DivisionMemberAdmin(admin.ModelAdmin):
    list_display = ('member', 'division', 'schedule_day')
    list_filter = ('division__schedule', 'division')
    search_fields = ('member__name', 'division__name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'member', 'division', 'division__schedule', 'schedule_day'
        )


@admin.register(testimony.Testimony)
class TestimonyAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'category', 'is_approved', 'show_on_home', 'created_at')
    list_filter = ('is_approved', 'show_on_home', 'category')
    search_fields = ('author_name', 'content')
    list_editable = ('is_approved', 'show_on_home')
    fieldsets = (
        ('Informações do Testemunho', {
            'fields': ('author_name', 'member', 'photo', 'content', 'category', 'testimony_date')
        }),
        ('Instagram', {
            'fields': ('instagram_url',),
            'description': 'Cole a URL de um post ou reel do Instagram para exibir o vídeo na página de testemunhos'
        }),
        ('Exibição', {
            'fields': ('is_approved', 'show_on_home')
        }),
    )
