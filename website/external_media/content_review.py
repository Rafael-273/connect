"""AI-assisted detection of speech that likely does not belong in the final cut.

Unlike ``SpeechEditAnalyzer`` (silence/filler, driven by audio activity), this
module reasons about the *content* of the transcript: abandoned sentences that
the speaker restarted, and backstage/meta remarks that were never meant to be
seen by the audience (asking to cut a take, talking to the crew, etc).

The analysis never mutates audio or timestamps. It only produces a list of
suggested ``SpeechCut``s that stay disabled until a member explicitly approves
them in the interactive review screen.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from django.conf import settings

from website.ai import get_ai_service
from website.ai.exceptions import AIServiceError

from .speech_edit import SpeechCut, SpeechEditPlan

logger = logging.getLogger(__name__)

OFF_CONTEXT_KIND = 'off_context'

# A new utterance starts after a pause at least this long. Shorter gaps are
# normal breathing room inside the same sentence/thought.
UTTERANCE_GAP_MS = 550
# Keeps prompts and model output small and unambiguous even for a very long
# take spoken without pauses.
MAX_WORDS_PER_UTTERANCE = 28

CATEGORY_LABELS = {
    'speech_error': 'Possível erro de fala',
    'meta_comment': 'Comentário de bastidor',
}


@dataclass(frozen=True)
class TranscriptUtterance:
    id: int
    start_ms: int
    end_ms: int
    text: str


def build_utterances(words) -> list[TranscriptUtterance]:
    """Groups Whisper word timestamps into short, sentence-like utterances."""
    ordered = sorted(words, key=lambda word: word.start_ms)
    utterances: list[TranscriptUtterance] = []
    current: list = []

    def flush():
        if not current:
            return
        utterances.append(TranscriptUtterance(
            id=len(utterances) + 1,
            start_ms=current[0].start_ms,
            end_ms=current[-1].end_ms,
            text=' '.join(word.text.strip() for word in current if word.text.strip()),
        ))

    previous = None
    for word in ordered:
        if current and (
            word.start_ms - previous.end_ms >= UTTERANCE_GAP_MS
            or len(current) >= MAX_WORDS_PER_UTTERANCE
        ):
            flush()
            current = []
        current.append(word)
        previous = word
    flush()
    return [item for item in utterances if item.text]


class OffContextAnalyzer:
    """Finds transcript spans worth flagging as "probably should be cut"."""

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or get_ai_service()

    def analyze(self, words, duration_ms: int, *, configuration=None) -> SpeechEditPlan:
        configuration = configuration or {}
        utterances = build_utterances(words)
        if len(utterances) < 2:
            return SpeechEditPlan.normalized((), max(1, int(duration_ms)))
        model = configuration.get('model') or settings.EXTERNAL_MEDIA_OFF_CONTEXT_MODEL
        batch_size = int(configuration.get('batch_size') or settings.EXTERNAL_MEDIA_OFF_CONTEXT_BATCH_SIZE)
        cuts: list[SpeechCut] = []
        for start in range(0, len(utterances), batch_size):
            batch = utterances[start:start + batch_size]
            try:
                cuts.extend(self._analyze_batch(batch, model))
            except AIServiceError as exc:
                # A flaky AI call should never fail the whole pipeline: the member
                # still gets their video, just without this optional suggestion.
                logger.warning('Detecção de trechos fora de contexto falhou para um lote: %s', exc)
        return SpeechEditPlan.normalized(tuple(cuts), max(1, int(duration_ms)))

    def _analyze_batch(self, utterances: list[TranscriptUtterance], model: str) -> list[SpeechCut]:
        by_id = {item.id: item for item in utterances}
        payload = [{'id': item.id, 'text': item.text} for item in utterances]
        prompt = json.dumps({'utterances': payload}, ensure_ascii=False)
        raw = self.ai_service.generate_text(
            prompt,
            instructions=self._instructions(),
            model=model,
            max_output_tokens=max(800, len(utterances) * 40),
        )
        cuts = []
        for item in self._parse_cuts(raw, by_id):
            start_utterance = by_id.get(item['start_id'])
            end_utterance = by_id.get(item['end_id'])
            if not start_utterance or not end_utterance or end_utterance.id < start_utterance.id:
                continue
            category = item.get('category') if item.get('category') in CATEGORY_LABELS else 'speech_error'
            reason = (item.get('reason') or '').strip() or CATEGORY_LABELS[category]
            cuts.append(SpeechCut(
                start_utterance.start_ms,
                end_utterance.end_ms,
                OFF_CONTEXT_KIND,
                f'{CATEGORY_LABELS[category]}: {reason}' if item.get('reason') else CATEGORY_LABELS[category],
            ))
        return cuts

    @staticmethod
    def _strip_code_fence(value):
        value = (value or '').strip()
        match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', value, flags=re.DOTALL)
        return match.group(1) if match else value

    @classmethod
    def _parse_cuts(cls, raw, by_id):
        cleaned = cls._strip_code_fence(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning('Resposta de detecção de contexto não é um JSON válido; ignorando lote.')
            return []
        items = data.get('cuts') if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        parsed = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            try:
                start_id = int(entry.get('start_id'))
                end_id = int(entry.get('end_id'))
            except (TypeError, ValueError):
                continue
            if start_id not in by_id or end_id not in by_id:
                continue
            parsed.append({
                'start_id': start_id,
                'end_id': end_id,
                'category': entry.get('category'),
                'reason': entry.get('reason'),
            })
        return parsed

    @staticmethod
    def _instructions():
        return """Você analisa a transcrição de vídeos institucionais de uma igreja evangélica
(sermões, cultos, entrevistas, podcasts e anúncios) para ajudar na edição.

Você recebe uma lista ordenada de "utterances" (pequenos trechos de fala contínua),
cada um com um "id" numérico sequencial e o texto falado.

TAREFA

Aponte apenas trechos que muito provavelmente NÃO deveriam ir para o vídeo final,
em duas categorias:

1. "speech_error": o orador começou uma frase ou ideia, tropeçou, se corrigiu ou
   pediu para recomeçar, e repetiu a mesma frase/ideia logo em seguida de forma
   mais limpa. Neste caso, aponte para corte APENAS a tentativa abandonada/errada
   (a que veio antes), nunca a versão final e correta.
2. "meta_comment": comentário claramente de bastidor, não destinado ao público,
   como pedir para cortar a gravação, falar com a equipe técnica, perguntar se
   já está gravando, testar áudio, ou comentários fora do contexto do
   conteúdo (piadas internas sobre a gravação, "corta isso", "vamos de novo").

REGRAS IMPORTANTES

- Seja conservador. Na dúvida, NÃO aponte o trecho. É muito pior cortar um trecho
  válido do sermão do que deixar de sugerir um corte.
- Nunca aponte um trecho só porque tem hesitação, repetição de palavra isolada ou
  vício de fala leve — isso já é tratado por outra ferramenta.
- Nunca aponte conteúdo teológico, bíblico ou testemunhal genuíno, mesmo que seja
  emocional, longo ou pareça repetitivo por ênfase retórica.
- Use "start_id" e "end_id" cobrindo o menor intervalo contíguo de utterances que
  contém o trecho problemático.
- "reason" deve ser uma frase curta em português explicando o motivo, citando
  trechos do texto quando útil, para o revisor humano decidir rapidamente.

FORMATO DE SAÍDA

Responda SOMENTE com um JSON válido, sem markdown e sem comentários, no formato:

{"cuts":[{"start_id":3,"end_id":4,"category":"speech_error","reason":"..."}]}

Se nenhum trecho se qualificar, responda {"cuts":[]}."""
