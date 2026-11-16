"""Representative announcement takes, with deterministic mocked semantic responses."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from website.ai import AIService, AIServiceError, TranscriptionSegment as Word
from website.external_media.take_suggestions import (
    analyze_source, cached_project_transcript, remap_source_transcript, select_suggestions,
)
from website.models import (
    ExternalMediaProject, MediaTemplate, MediaTemplateVersion, MediaTemplateBlock,
    MinistryMembership, ProjectBlockMedia, SourceTakeAnalysis,
)
from website.test_external_media import ExternalMediaFixtureMixin


def attempt(first, last, valid=True, group='anuncio', **extra):
    return {'group': group, 'valid': valid, 'ranges': [{'first': first, 'last': last}], 'reason': 'Fala aproveitável', **extra}


class SuggestionDecisionTests(SimpleTestCase):
    def setUp(self):
        self.words = [Word(1000 + i * 1000, 1500 + i * 1000, text, 'word') for i, text in enumerate(
            ['Um', 'dois', 'três', 'Neste', 'domingo', 'venha', 'Neste', 'domingo', 'venha', 'Neste', 'domingo', 'venha'])]

    def decide(self, *attempts):
        return select_suggestions({'attempts': list(attempts)}, self.words, 15000)

    def test_single_correct_speech(self):
        ranges, review = self.decide(attempt(3, 5))
        self.assertEqual(ranges[0]['start_ms'], 3820)
        self.assertEqual(ranges[0]['end_ms'], 6680)
        self.assertFalse(review)

    def test_preparation_count_is_excluded_but_intentional_count_can_be_kept(self):
        ranges, _ = self.decide(attempt(3, 5))
        self.assertGreater(ranges[0]['start_ms'], self.words[2].end_ms)
        intentional, _ = self.decide(attempt(0, 5))
        self.assertEqual(intentional[0]['start_ms'], 820)

    def test_initial_and_trailing_silence_not_selected(self):
        ranges, _ = self.decide(attempt(3, 5))
        self.assertGreater(ranges[0]['start_ms'], 0)
        self.assertLess(ranges[0]['end_ms'], 15000)

    def test_two_attempts_latest_valid_wins_even_if_response_is_reversed(self):
        ranges, _ = self.decide(attempt(6, 8), attempt(3, 5))
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]['start_ms'], 6820)

    def test_three_attempts_latest_correct_wins(self):
        ranges, _ = self.decide(attempt(3, 5, False), attempt(6, 8, False), attempt(9, 11))
        self.assertEqual(ranges[0]['start_ms'], 9820)

    def test_later_failed_attempt_cannot_replace_previous_valid(self):
        ranges, _ = self.decide(attempt(3, 5), attempt(9, 11, False))
        self.assertEqual(ranges[0]['start_ms'], 3820)

    def test_all_attempts_have_errors_compose_two_separate_spans(self):
        ranges, review = self.decide(attempt(3, 4, False, ranges=[{'first': 3, 'last': 4}, {'first': 10, 'last': 11}]))
        self.assertEqual(len(ranges), 2)
        self.assertLess(ranges[0]['end_ms'], ranges[1]['start_ms'])
        self.assertIn('incompletas', review)

    def test_no_speech_music_and_background_noise_have_no_automatic_cut(self):
        for name in ['silencio', 'musica', 'ruido']:
            with self.subTest(name=name):
                self.assertEqual(select_suggestions({'attempts': []}, [], 10000)[0], [])

    def test_reordering_is_rejected(self):
        with self.assertRaises(ValueError):
            self.decide(attempt(0, 1, False, ranges=[{'first': 9, 'last': 11}, {'first': 3, 'last': 5}]))

    def test_invalid_payloads_and_timestamps(self):
        for payload in [None, {}, {'attempts': [attempt(-1, 3)]}, {'attempts': [attempt(0, 999)]}, {'attempts': [attempt(True, 3)]}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                select_suggestions(payload, self.words, 15000)
        with self.assertRaises(ValueError):
            select_suggestions({'attempts': [attempt(0, 0)]}, [Word(-1, 300, 'oi')], 15000)
        with self.assertRaises(ValueError):
            select_suggestions({'attempts': [attempt(0, 0)]}, [Word(1, 16000, 'oi')], 15000)

    def test_padding_never_cuts_next_word_when_ranges_are_close(self):
        words = [Word(0, 500, 'Venha'), Word(550, 1000, 'domingo')]
        ranges, _ = select_suggestions({'attempts': [attempt(0, 0, group='a'), attempt(1, 1, group='b')]}, words, 1000)
        self.assertLessEqual(ranges[0]['end_ms'], ranges[1]['start_ms'])
        self.assertLessEqual(ranges[1]['start_ms'], 550)
        self.assertGreaterEqual(ranges[0]['end_ms'], 500)

    def test_editor_mapping_respects_offsets_and_multiple_source_ranges(self):
        data = [asdict(word) for word in self.words]
        first = remap_source_transcript(data, 3800, 6700, 0)
        second = remap_source_transcript(data, 9800, 12700, 2900)
        self.assertEqual(first[0].start_ms, 200)
        self.assertEqual(second[0].start_ms, 3100)
        self.assertEqual([word.text for word in first + second], ['Neste', 'domingo', 'venha'] * 2)
        self.assertIsNone(remap_source_transcript(data, 4200, 6700, 0))

    def test_silent_whisper_result_is_allowed_only_explicitly(self):
        client = Mock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(words=[], segments=[])
        service = AIService(client=client)
        self.assertEqual(service.transcribe_segments(b'audio', allow_empty=True), [])
        with self.assertRaises(AIServiceError):
            service.transcribe_segments(b'audio')


@override_settings(EXTERNAL_MEDIA_TAKE_SUGGESTIONS_ENABLED=True)
class SourceAnalysisIntegrationTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        template = MediaTemplate.objects.create(name='Anúncios', slug='anuncios')
        version = MediaTemplateVersion.objects.create(template=template, preset=self.preset, subtitle_style=self.style)
        self.project = ExternalMediaProject.objects.create(name='Domingo', template_version=version, created_by=self.member)
        self.blocks = [MediaTemplateBlock.objects.create(version=version, name=f'Cena {i}', key=f'cena-{i}', order=i) for i in [1, 2]]
        self.item = self.make_media(self.blocks[0])
        self.client.force_login(self.user)
        self.words = [Word(1000, 1500, 'Venha', 'word'), Word(1500, 2000, 'domingo.', 'word')]
        self.response = json.dumps({'attempts': [attempt(0, 1)]})
        self.patches = [
            patch('website.external_media.services.VideoAssemblyService._duration_ms', return_value=5000),
            patch('website.external_media.services.AudioExtractor.extract', return_value=[SimpleNamespace(path=Path('audio_0000.mp3'), offset_ms=0)]),
            patch('website.external_media.services.TranscriptionService.transcribe_detailed', return_value=self.words),
            patch('website.external_media.take_suggestions.get_ai_service'),
        ]
        self.duration, self.extract, self.transcribe, self.ai = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.ai.return_value.generate_text.return_value = self.response

    def make_media(self, block, content=b'same-source'):
        return ProjectBlockMedia.objects.create(project=self.project, block=block, original_filename='take.mp4', file=SimpleUploadedFile('take.mp4', content), file_size=len(content))

    def test_same_source_in_different_blocks_and_reprocessing_transcribe_once(self):
        second = self.make_media(self.blocks[1])
        analyze_source(self.item.pk)
        analyze_source(second.pk)
        analyze_source(self.item.pk)
        self.assertEqual(self.transcribe.call_count, 1)
        self.assertEqual(self.ai.return_value.generate_text.call_count, 1)
        self.assertEqual(SourceTakeAnalysis.objects.count(), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.trim_ranges, [])
        self.assertFalse(self.item.trim_reviewed)

    def test_different_blocks_and_content_have_independent_jobs(self):
        second = self.make_media(self.blocks[1], b'other-source')
        analyze_source(self.item.pk)
        analyze_source(second.pk)
        self.assertEqual(self.transcribe.call_count, 2)
        self.assertEqual(SourceTakeAnalysis.objects.count(), 2)

    def test_transcription_failure_is_nonblocking_and_retry_reuses_chunks(self):
        self.transcribe.side_effect = AIServiceError('temporário')
        with self.assertRaises(AIServiceError):
            analyze_source(self.item.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.take_analysis.status, 'error')
        self.transcribe.side_effect = None
        analyze_source(self.item.pk)
        self.item.take_analysis.refresh_from_db()
        self.assertEqual(self.item.take_analysis.status, 'ready')

    def test_ai_failure_keeps_source_transcript_for_retry(self):
        self.ai.return_value.generate_text.side_effect = AIServiceError('modelo indisponível')
        with self.assertRaises(AIServiceError):
            analyze_source(self.item.pk)
        self.ai.return_value.generate_text.side_effect = None
        analyze_source(self.item.pk)
        self.assertEqual(self.transcribe.call_count, 1)

    def test_active_lease_prevents_duplicate_transcription(self):
        analyze_source(self.item.pk)
        cache = SourceTakeAnalysis.objects.get()
        cache.status = 'running'
        cache.lease_until = timezone.now() + timedelta(hours=1)
        cache.save()
        analyze_source(self.item.pk)
        self.assertEqual(self.transcribe.call_count, 1)

    def test_expired_lease_is_recovered(self):
        analyze_source(self.item.pk)
        cache = SourceTakeAnalysis.objects.get()
        cache.status = 'running'
        cache.lease_until = timezone.now() - timedelta(seconds=1)
        cache.save()
        analyze_source(self.item.pk)
        cache.refresh_from_db()
        self.assertEqual(cache.status, 'ready')
        self.assertEqual(self.transcribe.call_count, 1)

    def test_manual_changes_during_analysis_are_preserved_and_status_exposes_suggestions(self):
        def manual_edit(*args, **kwargs):
            ProjectBlockMedia.objects.filter(pk=self.item.pk).update(trim_reviewed=True, trim_ranges=[{'start_ms': 0, 'end_ms': 4000}])
            return self.response
        self.ai.return_value.generate_text.side_effect = manual_edit
        analyze_source(self.item.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.trim_ranges, [{'start_ms': 0, 'end_ms': 4000}])
        response = self.client.get(reverse('external_media_project_media_status', args=[self.project.public_id, self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['take_analysis']['trim_reviewed'])
        self.assertEqual(len(response.json()['take_analysis']['suggestions']), 1)

    def test_saving_confirms_suggestions_using_existing_trim_endpoint(self):
        analyze_source(self.item.pk)
        response = self.client.post(reverse('external_media_project_media_trim', args=[self.project.public_id, self.item.pk]), {'trim_ranges': json.dumps([{'start_seconds': .82, 'end_seconds': 2.18}])})
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.trim_reviewed)
        self.assertEqual(self.item.trim_ranges, [{'start_ms': 820, 'end_ms': 2180}])

    def test_bad_client_timestamps_return_400(self):
        self.item.duration_ms = 5000
        self.item.save()
        for value in ['NaN', 'Infinity', 'bad', 8]:
            response = self.client.post(reverse('external_media_project_media_trim', args=[self.project.public_id, self.item.pk]), {'trim_ranges': json.dumps([{'start_seconds': 0, 'end_seconds': value}])})
            self.assertEqual(response.status_code, 400)

    def test_deleted_or_non_draft_media_are_skipped(self):
        analyze_source(999999)
        self.project.status = 'PROCESSING'
        self.project.save()
        analyze_source(self.item.pk)
        self.transcribe.assert_not_called()

    def test_full_editor_cache_uses_approved_ranges_and_falls_back_on_mismatch(self):
        from website.external_media.canonical import SourceManifestBuilder
        analyze_source(self.item.pk)
        self.item.trim_ranges = [{'start_ms': 800, 'end_ms': 2200}]
        self.item.save(update_fields=['trim_ranges'])
        manifest = SourceManifestBuilder.build(self.project)
        self.project.configuration = {
            'source_manifest': manifest,
            'analysis_source_block_ranges': [{'start_ms': 0, 'end_ms': 1400, 'label': manifest['sources'][0]['id']}],
        }
        words = cached_project_transcript(self.project, 'pt', 1400)
        self.assertIsNotNone(words)
        self.assertEqual(words[0].start_ms, 200)
        self.assertIsNone(cached_project_transcript(self.project, 'pt', 2000))
        self.assertIsNone(cached_project_transcript(self.project, 'en', 1400))

    def test_later_editor_pipeline_skips_extraction_and_whisper_when_cache_matches(self):
        from website.external_media.canonical import SourceManifestBuilder
        from website.external_media.services import ExternalMediaPipeline
        analyze_source(self.item.pk)
        self.item.trim_ranges = [{'start_ms': 800, 'end_ms': 2200}]
        self.item.save(update_fields=['trim_ranges'])
        manifest = SourceManifestBuilder.build(self.project)
        self.project.configuration = {'source_manifest': manifest, 'analysis_source_block_ranges': [
            {'start_ms': 0, 'end_ms': 1400, 'label': manifest['sources'][0]['id']},
        ]}
        job = SimpleNamespace(pk=77, public_id='test-job', original_video=self.item.file, project=self.project,
                              original_language='pt', output_languages=['pt'], translation_model='')
        pipeline = ExternalMediaPipeline()
        pipeline._get_job = Mock(return_value=job)
        pipeline._update = Mock()
        pipeline._apply_off_context_detection = Mock()
        pipeline._apply_background_voice_removal = Mock(side_effect=lambda j, v, w, d: (v, d))
        pipeline._apply_speech_edit = Mock(side_effect=lambda j, v, w, d: d)
        pipeline._apply_noise_analysis = Mock()
        pipeline._save_source_track = Mock()
        self.duration.return_value = 1400
        self.extract.reset_mock()
        self.transcribe.reset_mock()
        pipeline.prepare_subtitle_tracks(77)
        self.extract.assert_not_called()
        self.transcribe.assert_not_called()
        self.assertEqual(pipeline._save_source_track.call_args.args[1][0].start_ms, 200)

    def test_partial_chunk_checkpoint_resumes_without_repeating_successful_chunk(self):
        self.extract.return_value = [SimpleNamespace(path=Path('a.mp3'), offset_ms=0), SimpleNamespace(path=Path('b.mp3'), offset_ms=2500)]
        self.transcribe.side_effect = [self.words, AIServiceError('temporário')]
        with self.assertRaises(AIServiceError):
            analyze_source(self.item.pk)
        self.transcribe.reset_mock()
        self.transcribe.side_effect = [[Word(3000, 3500, 'Obrigado.', 'word')]]
        analyze_source(self.item.pk)
        self.assertEqual(self.transcribe.call_count, 1)
        cache = SourceTakeAnalysis.objects.get()
        self.assertEqual(len(cache.transcript), 3)

    def test_recovery_scheduler_requeues_expired_analysis(self):
        from website.external_media.tasks import recover_source_analysis
        analyze_source(self.item.pk)
        cache = SourceTakeAnalysis.objects.get()
        SourceTakeAnalysis.objects.filter(pk=cache.pk).update(status='running', lease_until=timezone.now() - timedelta(seconds=1))
        with patch('website.external_media.tasks.enqueue_source_analysis') as enqueue:
            recover_source_analysis.run()
        enqueue.assert_called_once_with(self.item.pk)

    def test_upload_dispatches_analysis_after_commit_independently_of_preview(self):
        from website.views.external_media import ExternalMediaProjectUploadView
        with patch('website.views.external_media.enqueue_source_analysis') as analysis, patch('website.views.external_media.create_project_preview.delay'):
            with self.captureOnCommitCallbacks(execute=True):
                ExternalMediaProjectUploadView._enqueue_preview(self.item)
                analysis.assert_not_called()
            analysis.assert_called_once_with(self.item.pk)

    def test_no_speech_finishes_without_calling_language_model(self):
        self.transcribe.return_value = []
        analyze_source(self.item.pk)
        cache = SourceTakeAnalysis.objects.get()
        self.assertTrue(cache.transcript_ready)
        self.assertEqual(cache.status, 'ready')
        self.assertEqual(cache.suggestions, [])
        self.ai.assert_not_called()

    def test_music_or_noise_without_usable_message_does_not_suggest_a_cut(self):
        self.transcribe.return_value = [Word(500, 1000, '♪ ruído ♪', 'segment')]
        self.ai.return_value.generate_text.return_value = json.dumps({'attempts': [], 'review_reason': 'Sem apresentação aproveitável.'})
        analyze_source(self.item.pk)
        cache = SourceTakeAnalysis.objects.get()
        self.assertEqual(cache.status, 'ready')
        self.assertEqual(cache.suggestions, [])
        self.item.refresh_from_db()
        self.assertEqual(self.item.trim_ranges, [])

    def test_invalid_model_output_never_becomes_approved_ranges(self):
        self.ai.return_value.generate_text.return_value = json.dumps({'attempts': [attempt(0, 200)]})
        with self.assertRaises(ValueError):
            analyze_source(self.item.pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.take_analysis.status, 'error')
        self.assertEqual(self.item.trim_ranges, [])
        self.assertFalse(self.item.trim_reviewed)

    def test_migration_preserves_legacy_cuts_without_scheduling_a_backfill(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        migration = importlib.import_module('website.migrations.0129_preserve_existing_take_markings')
        ProjectBlockMedia.objects.filter(pk=self.item.pk).update(trim_ranges=[{'start_ms': 100, 'end_ms': 3000}])
        migration.preserve_existing_media(apps, SimpleNamespace(connection=connection))
        self.item.refresh_from_db()
        self.assertTrue(self.item.trim_reviewed)
        self.assertEqual(self.item.take_analysis_status, 'unavailable')
        self.assertEqual(self.item.trim_ranges, [{'start_ms': 100, 'end_ms': 3000}])

    def test_scheduler_recovers_a_worker_killed_before_hash_was_saved(self):
        from website.external_media.tasks import recover_source_analysis
        ProjectBlockMedia.objects.filter(pk=self.item.pk).update(preview_status='READY', update_at=timezone.now() - timedelta(hours=3))
        with patch('website.external_media.tasks.enqueue_source_analysis') as enqueue:
            recover_source_analysis.run()
            recover_source_analysis.run()
        enqueue.assert_called_once_with(self.item.pk)


class TakeAnalysisMigrationTests(ExternalMediaFixtureMixin, TransactionTestCase):
    def test_existing_video_survives_separate_schema_and_data_transactions(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        template = MediaTemplate.objects.create(name='Migração', slug='migracao')
        version = MediaTemplateVersion.objects.create(template=template, preset=self.preset)
        project = ExternalMediaProject.objects.create(name='Projeto existente', template_version=version, created_by=self.member)
        media = ProjectBlockMedia.objects.create(
            project=project, original_filename='take.mp4', file='migration-test.mp4',
            trim_ranges=[{'start_ms': 100, 'end_ms': 3000}],
        )
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('website', '0127_sourcetakeanalysis_and_more')])
            # PostgreSQL executes the deferred FK index at schema-editor exit.
            # Updating this populated table before exit used to raise ObjectInUse.
            executor = MigrationExecutor(connection)
            executor.migrate([('website', '0128_projectblockmedia_take_analysis_and_more')])
            executor = MigrationExecutor(connection)
            executor.migrate([('website', '0129_preserve_existing_take_markings')])
            media.refresh_from_db()
            self.assertTrue(media.trim_reviewed)
            self.assertEqual(media.take_analysis_status, 'unavailable')
            self.assertEqual(media.trim_ranges, [{'start_ms': 100, 'end_ms': 3000}])
        finally:
            MigrationExecutor(connection).migrate(latest)
