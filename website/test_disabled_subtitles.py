from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from website.external_media.preview import PreviewCapabilityRegistry, PreviewCompositionService, TimelineRevisionService
from website.external_media.timeline import InternalTimelineBuilder
from website.models.external_media import SubtitleCue
from website.test_editor_persistence import EditorFixture


class DisabledSubtitleTests(EditorFixture, TestCase):
    def test_internal_transcript_is_not_exposed_as_captions(self):
        version = self.project.template_version
        version.subtitles_enabled = False
        version.preview_editable_capabilities = ['CUTS', 'SUBTITLES']
        version.save(update_fields=['subtitles_enabled', 'preview_editable_capabilities'])

        # Existing revisions may already contain captions generated from analysis.
        timeline = dict(self.project.current_timeline_revision.timeline)
        self.assertTrue(timeline['captions'])
        PreviewCompositionService.hydrate_legacy_caption_clock(self.project, timeline)
        self.assertEqual(timeline['captions'], [])
        self.assertNotIn('SUBTITLES', PreviewCapabilityRegistry.resolve(self.project)['available'])
        self.assertNotIn('SUBTITLES', PreviewCapabilityRegistry.resolve(self.project)['editable'])

        # New revisions must also exclude the transcript, while retaining it for analysis.
        revision = TimelineRevisionService.ensure_initial(self.project, self.member)
        from website.external_media.canonical import SourceManifestBuilder
        composed = PreviewCompositionService.compose(
            self.project, SourceManifestBuilder.build(self.project), {}, revision,
        )
        self.assertEqual(composed['captions'], [])
        self.assertTrue(SubtitleCue.objects.filter(pk=self.cue.pk).exists())
        with TemporaryDirectory() as directory:
            builder = InternalTimelineBuilder()
            self.assertEqual(builder._captions(self.project, Path(directory)), ([], []))
            self.assertEqual(builder._styled_caption_overlay(self.project, Path(directory), {}, 1000), (None, None))
