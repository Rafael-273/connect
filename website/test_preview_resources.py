from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from website.external_media.preview import ProjectProxyService


def revision(pk, video_id=None):
    return SimpleNamespace(pk=pk, timeline={'review_video_revision_id': video_id} if video_id else {})


class PreviewResourceTests(SimpleTestCase):
    def project(self, current, approved=None):
        return SimpleNamespace(current_timeline_revision=current, approved_timeline_revision=approved)

    def test_obsolete_picture_is_skipped_but_current_and_approved_are_kept(self):
        project = self.project(revision(3), revision(1))
        self.assertFalse(ProjectProxyService.review_video_is_needed(project, revision(2)))
        self.assertTrue(ProjectProxyService.review_video_is_needed(project, revision(3)))
        self.assertTrue(ProjectProxyService.review_video_is_needed(project, revision(1)))

    def test_audio_only_revisions_keep_the_shared_parent_video_needed(self):
        project = self.project(revision(5, video_id=2))
        self.assertTrue(ProjectProxyService.review_video_is_needed(project, revision(2)))
        self.assertTrue(ProjectProxyService.review_video_is_needed(project, revision(4, video_id=2)))
        self.assertFalse(ProjectProxyService.review_video_is_needed(project, revision(3)))

    def test_undo_can_make_a_historical_picture_needed_again(self):
        project = self.project(revision(2))
        self.assertTrue(ProjectProxyService.review_video_is_needed(project, revision(2)))
        self.assertFalse(ProjectProxyService.review_video_is_needed(project, revision(3)))

    @patch('website.external_media.preview.ProjectProxyService._profile')
    @patch('website.external_media.preview.VideoAssemblyService')
    @patch('website.external_media.preview.TimelineRevision.objects')
    @patch('website.external_media.preview.ExternalMediaProject.objects')
    def test_stale_task_exits_before_allocating_a_proxy_or_video_processor(self, projects, revisions, assembly, profile):
        projects.select_related.return_value.get.return_value = self.project(revision(3))
        revisions.get.return_value = revision(2)
        self.assertIsNone(ProjectProxyService.build_timeline_review_proxy(1, 2))
        assembly.assert_not_called()
        profile.assert_not_called()
