# Exportação para pós-produção no Adobe Premiere Pro

## Arquitetura reutilizada

A exportação continua usando o fluxo assíncrono existente (`export_premiere_project`),
o armazenamento configurado pela aplicação, o modelo de status/progresso do projeto,
`InternalTimelineBuilder`, `PremiereExporter` e `PremierePackageService`. O formato de
intercâmbio permanece sendo Final Cut Pro 7 XML, importável pelo Premiere Pro.

O modo principal usa o intermediário visual aprovado que o pipeline já mantém antes da
aplicação das legendas. Esse arquivo já contém cortes, reenquadramento/tracking, cor e
elementos visuais. Na montagem do pacote o vídeo é copiado sem a faixa de áudio, evitando
nova composição visual e eliminando a dependência dos keyframes de Basic Motion que
causavam enquadramento incorreto no Premiere.

## Conteúdo do pacote

- `Project/timeline.xml`: sequência com resolução, proporção e FPS do preset.
- `Media/video_consolidado.mp4`: V1, sem áudio e sem legendas incorporadas.
- `Graphics/captions_*_styled.mov`: V2 ProRes 4444 com alpha real. O overlay em vídeo,
  em vez de PNG por cue, também preserva animações e o renderer ASS já usado pela plataforma.
- `Captions/*.srt`: alternativa editável com texto e timestamps.
- `Audio/voz.wav`: A1 alinhada no zero, PCM 48 kHz/24-bit, com os cortes e tratamentos
  individuais habilitados no template.
- `Audio/musica.wav`: A2 alinhada no zero, PCM 48 kHz/24-bit, com volume, ducking dinâmico
  e ducking espectral aprovados já aplicados quando disponíveis.
- `Audio/mix_referencia.wav`: mix estéreo aprovado para conferência. Não é inserido na
  sequência para impedir duplicação de áudio.
- `Metadata/timeline.json`: decisões, marcadores, metadados e matriz de compatibilidade.
- `Metadata/validation.json`: validação de paths, assets e layout de pós-produção.

A3 só deve ser criada quando houver uma fonte de ambiência realmente independente. O
pipeline atual não modela uma fonte separada desse tipo, portanto nenhuma separação
artificial é produzida.

## Conferência manual no Premiere Pro

1. Gere a exportação em um projeto aprovado e extraia o ZIP mantendo as pastas.
2. No Premiere Pro, use **Arquivo > Importar** e selecione `Project/timeline.xml`.
3. Confirme a sequência vertical (1080×1920) ou horizontal (1920×1080), além do FPS do preset.
4. Reproduza V1 isoladamente e compare cortes e enquadramento com o preview aprovado.
   A imagem não deve ficar preta ou deslocada e V1 não deve emitir áudio.
5. Ative V2 sobre V1 e confira fonte, cor, contorno, sombra, posição, quebras e sincronismo.
   O espaço fora dos glifos deve permanecer transparente.
6. Reproduza A1 e A2 juntas. Confirme sincronia labial, cortes e redução da música durante
   a fala. Depois silencie os stems e importe/reproduza `mix_referencia.wav` para comparação.
7. Não reproduza o mix de referência junto com A1/A2. V3 permanece livre para efeitos.
8. Repita com um projeto sem música, um sem legendas e um com múltiplos cortes.

## Limitações conhecidas

- FCP 7 XML não representa todos os controles modernos do Premiere. Por isso, o Auto
  Reframe aprovado fica consolidado em V1 e a aparência avançada das legendas fica em
  uma camada ProRes 4444.
- O SRT preserva texto e tempo, mas não toda a estilização visual.
- A masterização aplicada ao mix completo não é duplicada em cada stem, pois isso
  alteraria o equilíbrio ao somá-los. Ela permanece disponível no mix de referência e
  a masterização final pode ser refeita depois que efeitos adicionais forem incluídos.
- Se o intermediário aprovado não estiver disponível em um projeto legado, a exportação
  mantém o fallback anterior com takes editáveis e Basic Motion; a validação registra o aviso.
