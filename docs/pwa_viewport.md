# PWA: tela inteira e áreas seguras

O Connect reaproveita a estratégia de viewport da Alba: `100vh` como fallback, `100dvh` no navegador e `100vh` no modo instalado. A altura da superfície não é alterada por `visualViewport`; esse objeto informa somente a oclusão do teclado em campos editáveis. A heurística ignora zoom e diferenças menores que 120 px.

`parciais/head.html` carrega `pwa.css` e `pwa.js`, permite `viewport-fit=cover` e mantém `black-translucent`. Fundos, login, recuperação de senha, navegação inferior e menus administrativos respeitam as áreas seguras. O editor de vídeo conserva seu gerenciamento próprio do viewport. No PWA instalado em telas até 768 px, a navegação inferior ocupa toda a largura e permanece visível durante a rolagem. No navegador, a barra flutuante recebe afastamento da área segura.

A combinação de `viewport-fit=cover` e insets segue a [documentação do WebKit](https://webkit.org/blog/7929/designing-websites-for-iphone-x/).

O Service Worker passa a ser servido por `/sw.js`, com escopo `/`, e é registrado no cabeçalho compartilhado. O registro antigo em `/static/` é removido após sucesso do novo registro. O cache v4 elimina somente caches antigos do Connect e armazena uma lista explícita de CSS/JS. HTML autenticado, APIs e mídia não são armazenados. Navegação sem internet recebe uma mensagem com botão para tentar novamente. A instalação continua exigindo HTTPS em produção.

O manifest mantém `display: standalone`, o endereço inicial e a identidade anterior (o `id` explícito corresponde ao antigo `start_url`). As cores de abertura e da interface do navegador usam o fundo base `#f7f1ef`.

## Validação

```sh
# Na raiz do Connect. DB_NAME apenas evita o nome vazio na configuração local;
# estes testes SimpleTestCase não acessam nem criam banco de dados.
DB_NAME=connect_pwa_test .venv/bin/python manage.py test website.test_pwa
node --test static/tests/pwa.test.cjs
# Requer playwright-core e um Chromium instalado; aceita as mesmas variáveis dos demais testes browser.
node static/tests/pwa_worker.browser.cjs
```

Passaram quatro testes Django e três testes Node. O teste Chromium do worker verificou instalação, controle das páginas, cache somente de arquivos estáticos e navegação offline. `manage.py check` e `git diff --check` passaram.

Também foram renderizados seis templates reais de autenticação/recuperação e o template da navegação de membros numa fixture local, com utilitários Tailwind compilados para o teste. Chromium verificou 390×844, 844×390 e 1440×900, sem overflow horizontal; a superfície de login preencheu a altura, o botão permaneceu acessível por rolagem com oclusão simulada de 300 px, e a barra instalada móvel preencheu a largura e respeitou inset inferior simulado de 34 px. Essa fixture não exercitou fluxos autenticados com dados reais.

## Conferência no aparelho

Após publicar e carregar a versão atualizada, abrir pela tela inicial no iPhone e Android. Conferir abertura e retorno do segundo plano, login e recuperação, teclado, última ação de páginas longas, navegação inferior, menu administrativo, modais e editor de vídeo. Conferir também Safari/Chrome sem instalação e orientação paisagem onde o sistema permitir. A emulação Chromium não certifica Safari nem o PWA instalado no iOS; essa validação permanece pendente.
