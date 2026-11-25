import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from website.ai import TranscriptionSegment
from website.external_media.broll import BrollTimelineService
from website.external_media.take_suggestions import cached_project_transcript


def asset(identifier, description='Pessoas adorando'):
    return SimpleNamespace(public_id=identifier, original_filename='broll.mp4', description=description, media_type='VIDEO',
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

    def test_defaults_fill_each_block_with_or_without_semantic_suggestions(self):
        first, second = self.assets
        second.duration_ms = 700
        third = asset('c')
        third.block = SimpleNamespace(key='testemunho', name='Testemunho')
        unassembled = asset('d')
        unassembled.block = SimpleNamespace(key='ausente', name='Ausente')
        project = SimpleNamespace(configuration={'block_ranges': [
            {'block_key': 'convite', 'start_ms': 0, 'end_ms': 6000},
            {'block_key': 'testemunho', 'start_ms': 6000, 'end_ms': 12000},
        ]})
        chosen = BrollTimelineService._decision(first, 2500, 5000, 'FULLSCREEN', .9, BrollTimelineService.DEFAULTS)
        result = BrollTimelineService._fill_block_defaults(
            project, [first, second, third, unassembled], BrollTimelineService.DEFAULTS, [chosen], [],
        )
        self.assertEqual([(item['asset_id'], item['start_ms'], item['end_ms']) for item in result], [
            ('a', 2500, 5000), ('b', 0, 700), ('c', 6000, 9000),
        ])

    def test_defaults_fit_when_ai_selected_entire_block(self):
        project = SimpleNamespace(configuration={'block_ranges': [
            {'block_key': 'convite', 'start_ms': 0, 'end_ms': 4000},
        ]})
        chosen = BrollTimelineService._decision(self.assets[0], 0, 4000, 'FULLSCREEN', .9, BrollTimelineService.DEFAULTS)
        result = BrollTimelineService._fill_block_defaults(
            project, self.assets, BrollTimelineService.DEFAULTS, [chosen], [],
        )
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in result], [(0, 2000), (2000, 4000)])

    def test_edited_broll_timestamps_are_not_shifted_by_speech_cuts_again(self):
        first, second = asset(uuid4()), asset(uuid4())
        assets = Mock()
        assets.filter.return_value.select_related.return_value = [first, second]
        project = SimpleNamespace(public_id=uuid4(), broll_assets=assets, configuration={'broll_decisions': [
            {'asset_id': str(first.public_id), 'start_ms': 6000, 'end_ms': 7000, 'time_basis': 'edited'},
            {'asset_id': str(second.public_id), 'start_ms': 8500, 'end_ms': 9500},
        ]})
        clips = [{'block': {'key': 'convite'}, 'timeline_in_ms': 5000, 'timeline_out_ms': 10000}]
        with patch.object(BrollTimelineService, 'configuration', return_value=BrollTimelineService.DEFAULTS):
            placements, _ = BrollTimelineService.compose(project, clips, 10000, time_mapper=lambda value: value - 1000)
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in placements], [(6000, 7000), (7500, 8500)])

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

    def test_described_images_use_assembled_audio_when_cached_speech_is_from_another_block(self):
        images = [
            asset('a', 'Construção do novo lugar, área para as crianças'),
            asset('b', 'Construção do novo lugar, 1000 lugares'),
        ]
        for image in images:
            image.media_type = 'IMAGE'
        project = SimpleNamespace(
            public_id=uuid4(), render_job_id=1, render_job=SimpleNamespace(original_language='pt', original_video=Mock()),
            configuration={'block_ranges': [
                {'block_key': 'intro', 'start_ms': 0, 'end_ms': 2000},
                {'block_key': 'convite', 'start_ms': 2000, 'end_ms': 12000},
            ]},
        )
        query = Mock()
        query.values.return_value.order_by.return_value = []
        ai = Mock()
        ai.generate_text.return_value = json.dumps({'placements': [
            {'asset_id': 'a', 'start_ms': 3000, 'end_ms': 5300, 'confidence': .9},
            {'asset_id': 'b', 'start_ms': 7100, 'end_ms': 9500, 'confidence': .9},
        ]})
        cached = [TranscriptionSegment(start_ms=500, end_ms=1700, text='Sejam bem-vindos')]
        assembled = cached + [
            TranscriptionSegment(start_ms=3000, end_ms=5300, text='O novo sítio terá um espaço para as crianças'),
            TranscriptionSegment(start_ms=7100, end_ms=9500, text='O auditório vai comportar mil pessoas'),
        ]
        with (
            patch('website.external_media.broll.SubtitleCue.objects.filter', return_value=query),
            patch('website.external_media.take_suggestions.cached_project_transcript', return_value=cached),
            patch('website.external_media.broll.media_input_factory'),
            patch('website.external_media.broll.JobWorkspace'),
            patch('website.external_media.services.AudioExtractor') as extractor,
            patch('website.external_media.services.TranscriptionService.transcribe_detailed', return_value=assembled) as transcribe,
        ):
            placements = BrollTimelineService._generate(project, images, BrollTimelineService.DEFAULTS, ai)
        transcribe.assert_called_once_with(extractor.return_value.extract.return_value, 'pt', allow_empty=True)
        prompt = json.loads(ai.generate_text.call_args.args[0])
        self.assertEqual([item['description'] for item in prompt['assets']], [image.description for image in images])
        self.assertEqual([item['text'] for item in prompt['transcript']], [item.text for item in assembled])
        self.assertEqual([(item['asset_id'], item['source'], item['start_ms']) for item in placements], [
            ('a', 'AI', 3000), ('b', 'AI', 7100),
        ])
