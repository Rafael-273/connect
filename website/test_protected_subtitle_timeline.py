from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from website.external_media.exceptions import ExternalMediaError
from website.external_media.quality_control import MediaQualityService
from website.external_media.services import ExternalMediaProjectPipeline


class ProtectedSubtitleTimelineTests(SimpleTestCase):
    def project(self):
        revision = SimpleNamespace(
            revision=212,
            source_manifest={'sources': [
                {'id': 'talk', 'metadata': {'skip_extra_processing': False}},
                {'id': 'ending', 'block_key': 'ending', 'metadata': {'skip_extra_processing': True}},
            ]},
            timeline={'clips': [
                {'asset_id': 'talk', 'timeline_in_ms': 0, 'timeline_out_ms': 8000},
                {'asset_id': 'ending', 'timeline_in_ms': 8000, 'timeline_out_ms': 10000},
            ]},
        )
        return SimpleNamespace(
            public_id='test-project', render_job_id=1, render_job=SimpleNamespace(),
            configuration={'proxy_pipeline': True, 'protected_block_ranges': [{'start_ms': 5000, 'end_ms': 7000}]},
            approved_timeline_revision=revision, approved_timeline_revision_id=212,
            current_timeline_revision_id=212, preview_dirty=False,
            template_version=SimpleNamespace(interactive_preview_enabled=True),
        )

    def track(self, start, end):
        cue = SimpleNamespace(start_ms=start, end_ms=end, cue_index=3)
        return SimpleNamespace(language='pt', cues=SimpleNamespace(all=lambda: [cue]))

    def test_moved_protected_scene_uses_approved_positions_instead_of_analysis_ranges(self):
        project = self.project()
        tracks = [self.track(5500, 6200)]
        self.assertFalse(MediaQualityService.validate_subtitles(tracks, project.configuration['protected_block_ranges']).ok)
        ranges = ExternalMediaProjectPipeline._subtitle_protected_ranges(project)
        self.assertEqual(ranges, [{'start_ms': 8000, 'end_ms': 10000, 'source_id': 'ending', 'block_key': 'ending'}])
        self.assertTrue(MediaQualityService.validate_subtitles(tracks, ranges).ok)
        self.assertEqual(project.configuration['protected_block_ranges'], [{'start_ms': 5000, 'end_ms': 7000}])

    def test_actual_overlap_with_the_moved_protected_scene_is_still_rejected(self):
        ranges = ExternalMediaProjectPipeline._subtitle_protected_ranges(self.project())
        self.assertFalse(MediaQualityService.validate_subtitles([self.track(8500, 9000)], ranges).ok)
        self.assertTrue(MediaQualityService.validate_subtitles([self.track(7000, 8000)], ranges).ok)

    def test_a_removed_protected_scene_no_longer_reserves_its_old_position(self):
        project = self.project()
        project.approved_timeline_revision.timeline['clips'].pop()
        self.assertEqual(ExternalMediaProjectPipeline._subtitle_protected_ranges(project), [])

    def test_split_protected_scenes_do_not_protect_the_unrelated_take_between_them(self):
        project = self.project()
        project.approved_timeline_revision.timeline['clips'] = [
            {'asset_id': 'ending', 'timeline_in_ms': 0, 'timeline_out_ms': 1000},
            {'asset_id': 'talk', 'timeline_in_ms': 1000, 'timeline_out_ms': 9000},
            {'asset_id': 'ending', 'timeline_in_ms': 9000, 'timeline_out_ms': 10000},
        ]
        ranges = ExternalMediaProjectPipeline._subtitle_protected_ranges(project)
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in ranges], [(0, 1000), (9000, 10000)])
        self.assertTrue(MediaQualityService.validate_subtitles([self.track(2000, 3000)], ranges).ok)

    def test_approved_audio_protection_is_not_remapped_through_cuts_again(self):
        project = self.project()
        plan = Mock()
        self.assertEqual(ExternalMediaProjectPipeline._protected_ranges_ms(project, plan), [(8000, 10000)])
        plan.remap_time.assert_not_called()

    def test_unapproved_analysis_keeps_its_existing_protected_clock(self):
        project = self.project()
        project.approved_timeline_revision = None
        self.assertEqual(ExternalMediaProjectPipeline._subtitle_protected_ranges(project), project.configuration['protected_block_ranges'])
        plan = SimpleNamespace(remap_time=lambda ms: ms - 1000)
        self.assertEqual(ExternalMediaProjectPipeline._protected_ranges_ms(project, plan), [(4000, 6000)])

    def test_real_overlap_fails_before_allocating_workspace_or_assembling_video(self):
        project = self.project()
        pipeline = ExternalMediaProjectPipeline()
        with patch.object(pipeline, '_get_project', return_value=project), patch.object(pipeline, '_update'), \
                patch.object(pipeline, '_step'), patch.object(pipeline, '_fail'), \
                patch.object(pipeline, '_prepare_final_master') as assemble, \
                patch('website.external_media.services.JobWorkspace') as workspace, \
                patch('website.external_media.services.ExternalMediaPipeline._ordered_output_tracks', return_value=[self.track(8500, 9000)]):
            with self.assertRaisesMessage(ExternalMediaError, 'Há legendas sobre blocos'):
                pipeline.render(1)
            assemble.assert_not_called()
            workspace.assert_not_called()

    def test_stale_analysis_overlap_does_not_block_starting_final_assembly(self):
        project = self.project()
        pipeline = ExternalMediaProjectPipeline()
        with patch.object(pipeline, '_get_project', return_value=project), patch.object(pipeline, '_update'), \
                patch.object(pipeline, '_step'), patch.object(pipeline, '_fail'), \
                patch.object(pipeline, '_job_workspace_estimate', return_value=0), \
                patch.object(pipeline, '_prepare_final_master', side_effect=ExternalMediaError('assembly reached')) as assemble, \
                patch('website.external_media.services.JobWorkspace'), \
                patch('website.external_media.services.ExternalMediaPipeline._ordered_output_tracks', return_value=[self.track(5500, 6200)]):
            with self.assertRaisesMessage(ExternalMediaError, 'assembly reached'):
                pipeline.render(1)
            assemble.assert_called_once()
