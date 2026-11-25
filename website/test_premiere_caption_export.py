from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from website.external_media.timeline import InternalTimelineBuilder
from website.models.external_media import SubtitleCue, SubtitleTrack
from website.test_editor_persistence import EditorFixture


class PremiereCaptionExportTests(EditorFixture, TestCase):
    def test_srt_exports_one_text_line_per_cue_in_all_languages(self):
        self.cue.text = 'Família\r\nIsso mesmo\n\n você e a\tfamília'
        self.cue.save(update_fields=['text'])
        english = SubtitleTrack.objects.create(job=self.project.render_job, language='en')
        SubtitleCue.objects.create(
            track=english, cue_index=1, start_ms=1000, end_ms=2000,
            text='Family<BR />Yes<br> you and\u2028your family',
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Captions').mkdir()
            captions, assets = InternalTimelineBuilder()._captions(self.project, root)
            for language, expected in [('pt', 'Família Isso mesmo você e a família'), ('en', 'Family Yes you and your family')]:
                content = (root / 'Captions' / f'{language}.srt').read_text()
                blocks = content.strip().split('\n\n')
                self.assertTrue(all(len(block.splitlines()) == 3 for block in blocks))
                self.assertEqual(blocks[0].splitlines(), ['1', '00:00:01,000 --> 00:00:02,000', expected])
        self.cue.refresh_from_db()
        self.assertIn('\n', self.cue.text)
        original = next(track for track in captions if track['language'] == 'pt')
        self.assertEqual(original['cues'][0]['text'], self.cue.text)

    def test_long_captions_keep_all_words_without_forced_wrapping(self):
        self.cue.text = 'Uma frase longa para importar como uma única linha no Premiere. ' * 8
        self.cue.save(update_fields=['text'])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Captions').mkdir()
            InternalTimelineBuilder()._captions(self.project, root)
            block = (root / 'Captions' / 'pt.srt').read_text().split('\n\n')[0].splitlines()
            self.assertEqual(len(block), 3)
            self.assertEqual(block[2], self.cue.text.strip())
