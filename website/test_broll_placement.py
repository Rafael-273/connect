from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from website.external_media.broll import BrollTimelineService
from website.external_media.take_suggestions import cached_project_transcript


def asset(identifier, description='Pessoas adorando'):
    return SimpleNamespace(public_id=identifier, description=description, media_type='VIDEO',
                           duration_ms=6000, trim_start_ms=0, trim_end_ms=None,
                           defaults={}, block_id=1, custom_block_id=None,
                           block=SimpleNamespace(key='convite', name='Convite'))


class BrollPlacementTests(SimpleTestCase):
    def setUp(self):
        self.assets = [asset('a'), asset('b')]
        self.ranges = {'convite': (0, 20000)}
        self.requested = [{'asset_id': item.public_id, 'allowed_range_ms': [0, 20000]} for item in self.assets]
        self.cues = [{'start_ms': 2000, 'end_ms': 6000, 'text': 'Buscaremos juntos a presença de Deus'},
                     {'start_ms': 10000, 'end_ms': 15000, 'text': 'Venha participar'}]

    def select(self, placements, occupied=None):
        return BrollTimelineService._select_candidates(
            self.assets, self.requested, self.cues, {'placements': placements}, self.ranges,
            BrollTimelineService.DEFAULTS, occupied or [],
        )

    def test_compares_candidates_and_avoids_conflicts(self):
        results = self.select([
            {'asset_id': 'a', 'candidates': [
                {'start_ms': 2000, 'end_ms': 5000, 'confidence': .8},
                {'start_ms': 10000, 'end_ms': 13000, 'confidence': .9}]},
            {'asset_id': 'b', 'candidates': [
                {'start_ms': 10000, 'end_ms': 13000, 'confidence': .95},
                {'start_ms': 2000, 'end_ms': 5000, 'confidence': .7}]},
        ])
        self.assertEqual([(item['asset_id'], item['start_ms']) for item in results], [('b', 10000), ('a', 2000)])

    def test_rejects_invalid_unrelated_and_out_of_bounds(self):
        results = self.select([
            {'asset_id': 'a', 'candidates': [None, {'start_ms': -1, 'end_ms': 3000, 'confidence': .9},
                                             {'start_ms': 6000, 'end_ms': 9000, 'confidence': .99}]},
            {'asset_id': 'b', 'candidates': [{'start_ms': 2000, 'end_ms': 5000, 'confidence': .3}]},
            {'asset_id': 'unknown', 'candidates': []},
        ])
        self.assertEqual(results, [])

    def test_existing_placements_are_respected(self):
        results = self.select([{'asset_id': 'a', 'start_ms': 2000, 'end_ms': 5000, 'confidence': .9}],
                              [{'start_ms': 1000, 'end_ms': 4000}])
        self.assertEqual(results, [])

    def test_prompt_includes_description_and_timestamps(self):
        project = SimpleNamespace(configuration={'block_ranges': [{'block_key': 'convite', 'start_ms': 0, 'end_ms': 20000}]},
                                  render_job_id=1, render_job=object(), public_id='project')
        query = Mock()
        query.values.return_value.order_by.return_value = self.cues
        ai = Mock()
        ai.generate_text.return_value = '{"placements": []}'
        with patch('website.external_media.broll.SubtitleCue.objects.filter', return_value=query):
            self.assertEqual(BrollTimelineService._generate(project, self.assets, BrollTimelineService.DEFAULTS, ai), [])
        prompt = ai.generate_text.call_args.args[0]
        self.assertIn('Pessoas adorando', prompt)
        self.assertIn('Buscaremos juntos', prompt)
        self.assertIn('2000', prompt)

    def test_broll_uses_uploaded_speech_when_no_subtitle_track_exists(self):
        files = Mock()
        files.select_related.return_value = [
            SimpleNamespace(pk=2, file=SimpleNamespace(name='take.mp4'), take_analysis=SimpleNamespace(
                transcript_ready=True, language='pt', version=1, duration_ms=9000,
                transcript=[{'start_ms': 3000, 'end_ms': 4600, 'text': 'Buscaremos juntos', 'granularity': 'segment'}],
            )),
        ]
        job = SimpleNamespace(original_language='pt')
        project = SimpleNamespace(
            render_job_id=1, render_job=job, public_id='project', block_media=files, template_version=None,
            current_timeline_revision_id=None, approved_timeline_revision_id=None,
            configuration={
                'block_ranges': [{'block_key': 'convite', 'start_ms': 5000, 'end_ms': 10000}],
                'analysis_source_block_ranges': [
                    {'label': 'fixed', 'start_ms': 0, 'end_ms': 5000},
                    {'label': 'take', 'start_ms': 5000, 'end_ms': 10000},
                ],
            },
        )
        manifest = {'sources': [
            {'id': 'fixed', 'source_reference': 'fixed'},
            {'id': 'take', 'source_reference': '2', 'storage_name': 'take.mp4',
             'trim': {'start_ms': 2000, 'end_ms': 7000}},
        ]}
        query = Mock()
        query.values.return_value.order_by.return_value = []
        ai = Mock()
        ai.generate_text.return_value = '{"placements":[{"asset_id":"a","start_ms":6000,"end_ms":7600,"confidence":0.9}]}'
        with (
            patch('website.external_media.canonical.ProjectProcessingState.get_source_manifest', return_value=manifest),
            patch('website.external_media.broll.SubtitleCue.objects.filter', return_value=query),
        ):
            self.assertIsNone(cached_project_transcript(project, 'pt', 10000))
            placements = BrollTimelineService._generate(project, self.assets, BrollTimelineService.DEFAULTS, ai)
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in placements], [(6000, 7600)])
        self.assertIn('Buscaremos juntos', ai.generate_text.call_args.args[0])
