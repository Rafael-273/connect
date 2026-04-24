from .dashboard import DashboardView
from .members import MembersListView, MemberDetailApiView, MemberEditView
from .visitors import VisitorsListView, VisitorDetailApiView, VisitorEditView
from .events import EventsListView, EventEditView
from .ministries import MinistriesListView, MinistryCreateEditApiView, MinistryEditView
from .neighborhoods import NeighborhoodsListView, NeighborhoodCreateEditApiView, NeighborhoodEditView
from .api import ApiDeleteItemView
from .followups import (
    FollowUpListView, FollowUpEditView, FollowUpDeleteView,
    FollowUpReportView, FollowUpDetailView,
)
from .canteen import (
    CanteenListView, CanteenEditView, CanteenDetailView,
    CanteenDeleteView, CanteenTogglePaidView, CanteenApiView,
)
from .templates import (
    TemplatesListView, TemplateCreateView, TemplateEditView,
    TemplateDetailView, TemplateDeleteView,
)
from .reports import ReportsView
from .testimonies import (
    TestimonyListView, TestimonyEditView, TestimonyDeleteView, TestimonyToggleView,
)
from .roteiro import (
    RoteiroView, AnuncioCreateView, AnuncioEditView, AnuncioDeleteView,
    AnuncioReorderView, RoteiroPrintView,
)

__all__ = [
    'DashboardView',
    'MembersListView', 'MemberDetailApiView', 'MemberEditView',
    'VisitorsListView', 'VisitorDetailApiView', 'VisitorEditView',
    'EventsListView', 'EventEditView',
    'MinistriesListView', 'MinistryCreateEditApiView', 'MinistryEditView',
    'NeighborhoodsListView', 'NeighborhoodCreateEditApiView', 'NeighborhoodEditView',
    'ApiDeleteItemView',
    'FollowUpListView', 'FollowUpEditView', 'FollowUpDeleteView',
    'FollowUpReportView', 'FollowUpDetailView',
    'CanteenListView', 'CanteenEditView', 'CanteenDetailView',
    'CanteenDeleteView', 'CanteenTogglePaidView', 'CanteenApiView',
    'TemplatesListView', 'TemplateCreateView', 'TemplateEditView',
    'TemplateDetailView', 'TemplateDeleteView',
    'ReportsView',
    'TestimonyListView', 'TestimonyEditView', 'TestimonyDeleteView', 'TestimonyToggleView',
    'RoteiroView', 'AnuncioCreateView', 'AnuncioEditView', 'AnuncioDeleteView',
    'AnuncioReorderView', 'RoteiroPrintView',
]
