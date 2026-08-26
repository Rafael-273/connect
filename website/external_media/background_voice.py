"""Conservative removal of quiet off-camera voices from selected blocks.

This deliberately does not attempt source separation. In the intended interview
setup the interviewer is away from the microphone, so their speech is consistently
quieter than the featured speaker. We only cut isolated, clearly quieter utterances
and leave anything ambiguous untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .speech_edit import AudioActivity, SpeechCut, SpeechEditPlan, SpeechEditService


@dataclass(frozen=True)
class QuietUtterance:
    start_ms: int
    end_ms: int
    level_db: float

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class BackgroundVoicePlan:
    cuts: tuple[SpeechCut, ...]
    duration_ms: int
    reference_db: float | None = None

    def as_dict(self):
        return {
            'duration_ms': self.duration_ms,
            'reference_db': self.reference_db,
            'cuts': [cut.as_dict() for cut in self.cuts],
        }


class BackgroundVoiceRemovalService:
    """Find low-volume utterances in opted-in interview/testimony blocks."""

    minimum_utterance_ms = 260
    maximum_word_gap_ms = 420
    quiet_difference_db = 10.0
    cut_lead_ms = 35
    cut_tail_ms = 65

    def __init__(self, runner):
        self.editor = SpeechEditService(runner)

    def analyze(self, video_path: Path, words, selected_ranges) -> BackgroundVoicePlan:
        duration_ms = self.editor.duration_ms(video_path)
        ranges = self._ranges(selected_ranges, duration_ms)
        # The word-level timeline is the safety mechanism here. Without it we
        # cannot distinguish an off-camera sentence from a quiet pause.
        if not ranges or not words or not all(word.granularity == 'word' for word in words):
            return BackgroundVoicePlan((), duration_ms)
        wav_path = video_path.with_suffix('.quiet-voice.wav')
        self.editor.extract_analysis_audio(video_path, wav_path)
        try:
            activity = AudioActivity(wav_path)
            utterances = self._utterances(words, ranges, activity)
        finally:
            wav_path.unlink(missing_ok=True)
        if len(utterances) < 2:
            return BackgroundVoicePlan((), duration_ms)
        reference_db = self._reference_level(utterances)
        cuts = self._quiet_cuts(utterances, ranges, reference_db, duration_ms)
        return BackgroundVoicePlan(tuple(cuts), duration_ms, reference_db)

    def apply(self, video_path: Path, output_path: Path, plan: BackgroundVoicePlan):
        self.editor.apply(
            video_path,
            output_path,
            SpeechEditPlan(plan.cuts, plan.duration_ms, crossfade_ms=40),
        )

    @staticmethod
    def _ranges(selected_ranges, duration_ms):
        return [
            (max(0, int(item.get('start_ms') or 0)), min(duration_ms, int(item.get('end_ms') or 0)))
            for item in (selected_ranges or [])
            if int(item.get('end_ms') or 0) > int(item.get('start_ms') or 0)
        ]

    def _utterances(self, words, ranges, activity):
        selected_words = [
            word for word in sorted(words, key=lambda item: item.start_ms)
            if any(word.start_ms >= start and word.end_ms <= end for start, end in ranges)
        ]
        utterances = []
        group = []
        for word in selected_words:
            if group and word.start_ms - group[-1].end_ms > self.maximum_word_gap_ms:
                utterances.append(self._make_utterance(group, activity))
                group = []
            group.append(word)
        if group:
            utterances.append(self._make_utterance(group, activity))
        return [item for item in utterances if item.duration_ms >= self.minimum_utterance_ms]

    @staticmethod
    def _make_utterance(words, activity):
        start_ms, end_ms = words[0].start_ms, words[-1].end_ms
        return QuietUtterance(start_ms, end_ms, activity.average_db(start_ms, end_ms))

    @staticmethod
    def _reference_level(utterances):
        # The upper half represents speech nearest to the microphone and avoids
        # allowing several quiet questions to pull the reference level down.
        levels = sorted(item.level_db for item in utterances)
        return float(median(levels[len(levels) // 2:]))

    def _quiet_cuts(self, utterances, ranges, reference_db, duration_ms):
        cuts = []
        for item in utterances:
            if item.level_db > reference_db - self.quiet_difference_db:
                continue
            range_start, range_end = next(
                ((start, end) for start, end in ranges if item.start_ms >= start and item.end_ms <= end),
                (item.start_ms, item.end_ms),
            )
            start_ms = max(range_start, item.start_ms - self.cut_lead_ms)
            end_ms = min(range_end, item.end_ms + self.cut_tail_ms, duration_ms)
            if end_ms - start_ms >= self.minimum_utterance_ms:
                cuts.append(SpeechCut(start_ms, end_ms, 'background_voice'))
        return list(SpeechEditPlan(tuple(cuts), duration_ms).cuts)
