"""Speaker-aware interviewer removal from selected testimony blocks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .speaker_diarization import CommunitySpeakerDiarizer, DiarizationUnavailable, SpeakerTurn
from .speech_edit import AudioActivity, SpeechCut, SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService

logger = logging.getLogger(__name__)


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
    method: str = 'amplitude'

    def as_dict(self):
        return {
            'duration_ms': self.duration_ms,
            'reference_db': self.reference_db,
            'method': self.method,
            'cuts': [cut.as_dict() for cut in self.cuts],
        }


class BackgroundVoiceRemovalService:
    """Find low-volume utterances in opted-in interview/testimony blocks."""

    minimum_utterance_ms = 260
    maximum_word_gap_ms = 420
    quiet_difference_db = 10.0
    speaker_change_db = 8.0
    cut_lead_ms = 35
    cut_tail_ms = 65
    neighboring_speech_guard_ms = 80

    def __init__(self, runner, diarizer=None):
        self.editor = SpeechEditService(runner)
        self.diarizer = diarizer or CommunitySpeakerDiarizer()

    def analyze(self, video_path: Path, words, selected_ranges) -> BackgroundVoicePlan:
        duration_ms = self.editor.duration_ms(video_path)
        ranges = self._ranges(selected_ranges, duration_ms)
        if not ranges:
            return BackgroundVoicePlan((), duration_ms)
        wav_path = video_path.with_suffix('.quiet-voice.wav')
        self.editor.extract_analysis_audio(video_path, wav_path)
        try:
            activity = AudioActivity(wav_path)
            try:
                turns = self.diarizer.diarize(wav_path)
            except DiarizationUnavailable as exc:
                logger.warning('Community-1 indisponível; usando análise de amplitude: %s', exc)
                turns = None
            if turns is not None:
                cuts, reference_db = self._speaker_cuts(turns, ranges, activity, duration_ms)
                return BackgroundVoicePlan(tuple(cuts), duration_ms, reference_db, 'community-1')
            # The fallback needs word timestamps to avoid confusing a quiet pause
            # with an off-camera sentence.
            if not words or not all(word.granularity == 'word' for word in words):
                return BackgroundVoicePlan((), duration_ms)
            utterances = self._utterances(words, ranges, activity)
        finally:
            wav_path.unlink(missing_ok=True)
        if len(utterances) < 2:
            return BackgroundVoicePlan((), duration_ms)
        reference_db = self._reference_level(utterances)
        cuts = self._quiet_cuts(utterances, ranges, reference_db, duration_ms)
        return BackgroundVoicePlan(tuple(cuts), duration_ms, reference_db)

    def _speaker_cuts(self, turns, ranges, activity, duration_ms):
        cuts = []
        references = []
        for range_start, range_end in ranges:
            selected = []
            for turn in turns:
                start_ms = max(turn.start_ms, range_start)
                end_ms = min(turn.end_ms, range_end)
                if end_ms - start_ms >= self.minimum_utterance_ms:
                    selected.append(SpeakerTurn(start_ms, end_ms, turn.speaker))
            speakers = {turn.speaker for turn in selected}
            if len(speakers) < 2:
                continue
            levels = {
                speaker: [
                    activity.average_db(turn.start_ms, turn.end_ms)
                    for turn in selected if turn.speaker == speaker
                ]
                for speaker in speakers
            }
            durations = {
                speaker: sum(turn.duration_ms for turn in selected if turn.speaker == speaker)
                for speaker in speakers
            }
            # Labels are anonymous and may represent different people in another
            # testimony, so the featured speaker is selected independently per block.
            featured = max(
                speakers,
                key=lambda speaker: (
                    float(median(levels[speaker])) + min(6.0, durations[speaker] / 30000)
                ),
            )
            references.append(float(median(levels[featured])))
            for index, turn in enumerate(selected):
                if turn.speaker == featured:
                    continue
                previous_featured = next(
                    (item for item in reversed(selected[:index]) if item.speaker == featured), None,
                )
                next_featured = next(
                    (item for item in selected[index + 1:] if item.speaker == featured), None,
                )
                start_ms = max(range_start, turn.start_ms - self.cut_lead_ms)
                end_ms = min(range_end, turn.end_ms + self.cut_tail_ms, duration_ms)
                if previous_featured:
                    start_ms = max(start_ms, previous_featured.end_ms + self.neighboring_speech_guard_ms)
                if next_featured:
                    end_ms = min(end_ms, next_featured.start_ms - self.neighboring_speech_guard_ms)
                if end_ms - start_ms >= self.minimum_utterance_ms:
                    cuts.append(SpeechCut(start_ms, end_ms, 'background_voice'))
        reference_db = float(median(references)) if references else None
        return SpeechEditAnalyzer._merge_safe_cuts(cuts, duration_ms), reference_db

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
        measured_words = [
            (word, activity.average_db(word.start_ms, word.end_ms))
            for word in selected_words
        ]
        levels = sorted(level for _word, level in measured_words)
        featured_reference_db = (
            float(median(levels[len(levels) // 2:])) if levels else -96.0
        )
        utterances = []
        group = []
        group_levels = []
        for word, word_level in measured_words:
            group_level = float(median(group_levels)) if group_levels else word_level
            group_is_quiet = group_level <= featured_reference_db - self.quiet_difference_db
            word_is_quiet = word_level <= featured_reference_db - self.quiet_difference_db
            # Speaker turns may have almost no pause. Split only when the words cross
            # the global quiet/featured boundary with a meaningful level change.
            has_new_speaker_level = (
                group
                and group_is_quiet != word_is_quiet
                and abs(word_level - group_level) >= self.speaker_change_db
            )
            if group and (
                word.start_ms - group[-1].end_ms > self.maximum_word_gap_ms
                or has_new_speaker_level
            ):
                utterances.append(self._make_utterance(group, activity))
                group = []
                group_levels = []
            group.append(word)
            group_levels.append(word_level)
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
        quiet_flags = [
            item.level_db <= reference_db - self.quiet_difference_db
            for item in utterances
        ]
        for index, item in enumerate(utterances):
            if item.level_db > reference_db - self.quiet_difference_db:
                continue
            range_start, range_end = next(
                ((start, end) for start, end in ranges if item.start_ms >= start and item.end_ms <= end),
                (item.start_ms, item.end_ms),
            )
            is_last_utterance_in_range = not any(
                candidate.start_ms >= item.end_ms and candidate.start_ms < range_end
                for candidate in utterances[index + 1:]
            )
            # A testimony can end with a quieter final answer after a long interviewer
            # turn. Amplitude alone cannot reliably distinguish that answer, so the last
            # utterance in the selected block is always kept for editorial safety.
            if is_last_utterance_in_range:
                continue
            start_ms = max(range_start, item.start_ms - self.cut_lead_ms)
            end_ms = min(range_end, item.end_ms + self.cut_tail_ms, duration_ms)
            if index and not quiet_flags[index - 1]:
                start_ms = max(
                    start_ms,
                    utterances[index - 1].end_ms + self.neighboring_speech_guard_ms,
                )
            if index + 1 < len(utterances) and not quiet_flags[index + 1]:
                # Remove the dead space between interviewer and answer while keeping
                # a small lead-in before the featured person's first syllable.
                end_ms = min(range_end, utterances[index + 1].start_ms - self.neighboring_speech_guard_ms)
            if end_ms - start_ms >= self.minimum_utterance_ms:
                cuts.append(SpeechCut(start_ms, end_ms, 'background_voice'))
        return SpeechEditAnalyzer._merge_safe_cuts(cuts, duration_ms)
