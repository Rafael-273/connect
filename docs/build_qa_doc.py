#!/usr/bin/env python3
"""Rebuild docs/qa_document.html from parts with print-friendly minimalist styling."""
from __future__ import annotations

import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "qa_parts"
OUT = ROOT / "qa_document.html"
LOGO = ROOT.parent / "static" / "assets" / "logo_white.png"

CSS = r"""
  :root {
    --primary: #C90905;
    --primary-dark: #8C0910;
    --dark: #260200;
    --accent: #FDDEB9;
    --text: #0e0e0e;
    --muted: #5c5c5c;
    --border: #e5e5e5;
    --surface: #fafafa;
    --surface-alt: #f5f5f5;
    --font: 'Montserrat', 'Helvetica Neue', Arial, sans-serif;
    --mono: 'Consolas', 'Liberation Mono', 'Courier New', monospace;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body {
    font-family: var(--font);
    font-size: 14px;
    font-weight: 400;
    line-height: 1.65;
    color: var(--text);
    background: #fff;
    -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 900px; margin: 0 auto; padding: 28px 36px 48px; }
  .print-hint {
    font-size: 11px;
    color: var(--muted);
    border: 1px dashed var(--border);
    padding: 10px 14px;
    margin-bottom: 24px;
    background: var(--surface);
  }
  .cover {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    padding: 72px 48px;
    background: var(--dark);
    color: #fff;
    text-align: center;
  }
  .cover-mark { width: 56px; height: 4px; background: var(--primary); margin-bottom: 28px; }
  .cover-logo {
    width: 240px;
    max-width: 72vw;
    height: auto;
    margin-bottom: 20px;
    object-fit: contain;
  }
  .cover h1 {
    font-family: var(--font);
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.01em;
    margin-bottom: 10px;
    color: #fff;
  }
  .cover .subtitle { font-size: 14px; font-weight: 400; color: rgba(255,255,255,0.75); margin-bottom: 36px; line-height: 1.55; }
  .cover .badge {
    display: inline-block;
    border: 1px solid var(--primary);
    color: var(--accent);
    padding: 4px 14px;
    border-radius: 2px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 20px;
  }
  .cover .meta {
    display: flex;
    justify-content: center;
    gap: 36px;
    flex-wrap: wrap;
    margin-top: 40px;
    padding-top: 32px;
    border-top: 1px solid rgba(255,255,255,0.15);
  }
  .cover .meta-item { text-align: center; }
  .cover .meta-item .label {
    font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
    color: rgba(255,255,255,0.5); margin-bottom: 4px;
  }
  .cover .meta-item .value { font-size: 14px; font-weight: 600; color: #fff; }
  .toc { background: var(--surface); border: 1px solid var(--border); padding: 24px 28px; margin: 0 0 36px; }
  .toc h2 { font-size: 13px; font-weight: 700; margin-bottom: 14px; color: var(--dark); letter-spacing: 0.01em; }
  .toc ol { padding-left: 18px; }
  .toc li { margin-bottom: 4px; font-size: 13px; font-weight: 500; }
  .toc a { color: var(--primary); text-decoration: none; }
  .toc .sub { list-style: none; padding-left: 14px; margin-top: 2px; }
  .toc .sub li { font-size: 11px; color: var(--muted); }
  .module-section { margin: 36px 0 28px; }
  .module-header {
    display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px;
    padding: 0 0 10px; margin-bottom: 18px; border-bottom: 2px solid var(--primary);
  }
  .module-header h2 { font-size: 18px; font-weight: 700; color: var(--dark); letter-spacing: -0.01em; }
  .module-header .access-badge {
    margin-left: auto; font-size: 11px; padding: 2px 8px;
    border: 1px solid var(--border); border-radius: 2px;
    font-weight: 500; color: var(--muted); background: var(--surface);
  }
  .mod-public, .mod-member, .mod-admin, .mod-ops, .mod-tech { background: none; }
  .feature { border: 1px solid var(--border); margin-bottom: 14px; background: #fff; }
  .feature-title {
    background: var(--surface); padding: 9px 14px; font-weight: 600;
    font-size: 14px; color: var(--dark); border-bottom: 1px solid var(--border);
  }
  .feature-body { padding: 14px 16px; font-size: 13px; }
  .flow-title {
    font-size: 11px; font-weight: 600;
    color: var(--muted); margin: 12px 0 6px;
  }
  ol.flow { padding-left: 18px; margin-bottom: 10px; }
  ol.flow li { margin-bottom: 4px; }
  ul.notes, ul.expects, ul.precond { padding-left: 0; list-style: none; margin-bottom: 10px; }
  ul.notes li, ul.expects li, ul.precond li {
    margin-bottom: 4px; font-size: 13px; padding-left: 10px; margin-left: 2px; line-height: 1.55;
  }
  ul.notes li { color: var(--muted); border-left: 2px solid #ccc; }
  ul.expects li { color: #333; border-left: 2px solid var(--primary); }
  ul.precond li { color: #333; border-left: 2px solid var(--dark); }
  .url-tag {
    display: inline-block; background: var(--surface-alt); color: var(--muted);
    font-family: var(--mono); font-size: 11px; font-weight: 400;
    padding: 1px 6px; border: 1px solid var(--border); border-radius: 2px; margin: 0 3px 3px 0;
  }
  .method-tag { display: inline-block; font-family: var(--font); font-size: 10px; font-weight: 600; padding: 1px 5px; border-radius: 2px; margin-right: 3px; }
  .get { background: var(--surface); color: var(--muted); border: 1px solid var(--border); }
  .post { background: var(--dark); color: #fff; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12px; }
  thead { display: table-header-group; }
  th {
    background: var(--dark); color: #fff; padding: 8px 10px; text-align: left;
    font-family: var(--font); font-weight: 600; font-size: 11px; letter-spacing: 0;
  }
  td { padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; font-size: 12px; }
  tr:nth-child(even) td { background: var(--surface); }
  td .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
  .dot-yes { background: var(--primary); }
  .dot-no { background: #ccc; }
  hr.section-divider { border: none; border-top: 1px solid var(--border); margin: 28px 0; }
  .info-box, .warn-box, .danger-box {
    padding: 10px 14px; margin: 12px 0; font-size: 12px;
    border-left: 3px solid var(--primary); background: var(--surface);
  }
  .warn-box { border-left-color: #999; }
  .danger-box { border-left-color: var(--primary-dark); background: #fff8f8; }
  .doc-footer {
    text-align: center; padding: 24px 0 8px; color: var(--muted);
    font-size: 10px; border-top: 1px solid var(--border); margin-top: 36px;
  }
  .persona {
    display: inline-block; font-size: 10px; padding: 2px 7px;
    border: 1px solid var(--border); border-radius: 2px; font-weight: 500; margin: 2px;
    color: var(--muted); background: var(--surface);
  }
  code {
    font-family: var(--mono); font-size: 12px; font-weight: 400;
    background: var(--surface-alt); padding: 1px 4px; border-radius: 2px;
  }
  th code { background: transparent; color: inherit; padding: 0; font-size: inherit; font-family: var(--mono); }
  h3 { font-size: 14px; font-weight: 700; color: var(--dark); }
  strong { font-weight: 600; }
  @media print {
    @page { margin: 18mm 16mm; size: A4; }
    html, body { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; font-size: 11px; }
    .no-print { display: none !important; }
    .cover {
      min-height: auto; height: 100vh; page-break-after: always; break-after: page;
      background: var(--dark) !important; -webkit-print-color-adjust: exact !important;
    }
    .cover h1, .cover .meta-item .value { color: #fff !important; }
    .cover .badge { border-color: var(--primary) !important; color: var(--accent) !important; }
    .cover-mark { background: var(--primary) !important; }
    .cover-logo { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    .container { padding: 0; max-width: none; }
    .module-section { break-inside: auto; page-break-inside: auto; margin: 24px 0 16px; }
    .module-header { break-after: avoid; page-break-after: avoid; }
    .feature { break-inside: auto; page-break-inside: auto; }
    .feature-title { break-after: avoid; page-break-after: avoid; }
    table { break-inside: auto; page-break-inside: auto; }
    tr { break-inside: avoid; page-break-inside: avoid; }
    th { background: var(--dark) !important; color: #fff !important; -webkit-print-color-adjust: exact !important; }
    th code { background: transparent !important; color: #fff !important; }
    .info-box, .warn-box, .danger-box { break-inside: avoid; page-break-inside: avoid; }
    a { color: inherit; text-decoration: none; }
  }
"""

TOC = """
  <div class="toc no-print">
    <h2>Sumário</h2>
    <ol>
      <li><a href="#permission-groups">Grupos de Permissão</a></li>
      <li><a href="#public">Site Público</a>
        <ul class="sub"><li>Página inicial, eventos, testemunhos, contato</li><li>Formulários públicos</li></ul>
      </li>
      <li><a href="#auth">Autenticação e Redefinição de Senha</a></li>
      <li><a href="#member">Portal do Membro</a></li>
      <li><a href="#admin-overview">Painel Admin — Visão Geral</a></li>
      <li><a href="#admin-people">Admin — Pessoas e Cadastros</a></li>
      <li><a href="#admin-consolidation">Admin — Consolidação</a>
        <ul class="sub"><li>Follow-ups, templates, relatórios</li></ul>
      </li>
      <li><a href="#admin-schedules">Admin — Escalas</a>
        <ul class="sub"><li>Equipes, dias, divisões, impressão</li></ul>
      </li>
      <li><a href="#admin-ministration">Admin — Ministração</a></li>
      <li><a href="#admin-other">Admin — Outros Módulos</a>
        <ul class="sub"><li>Eventos, testemunhos, roteiro, ministérios, música, casa da paz, cursos</li></ul>
      </li>
      <li><a href="#translator">Tradução ao Vivo</a></li>
      <li><a href="#out-of-scope">Fora do Escopo</a></li>
      <li><a href="#checklist">Checklist Rápido</a></li>
    </ol>
  </div>
"""

PERMISSION_GROUPS = """
  <div class="module-section" id="permission-groups">
    <div class="module-header mod-ops">
      <h2>Grupos de Permissão</h2>
    </div>
    <p style="margin-bottom:12px">Cada usuário pertence a um <strong>grupo de permissão</strong> (campo <code>user_type</code> no cadastro). Esse grupo define o que a pessoa pode acessar no sistema após o login:</p>
    <table>
      <thead><tr><th>Grupo (<code>user_type</code>)</th><th>Nome no sistema</th><th>Portal Membro</th><th>Painel Admin</th><th>Módulos liberados</th><th>Gestão de usuários</th></tr></thead>
      <tbody>
        <tr><td><strong>—</strong></td><td>Anônimo (sem login)</td><td><span class="dot dot-no"></span>Não</td><td><span class="dot dot-no"></span>Não</td><td>Site público</td><td><span class="dot dot-no"></span>Não</td></tr>
        <tr><td><strong>member</strong></td><td>Membro</td><td><span class="dot dot-yes"></span>Sim</td><td><span class="dot dot-no"></span>Não</td><td>Portal do membro</td><td><span class="dot dot-no"></span>Não</td></tr>
        <tr><td><strong>visitors</strong></td><td>Visitantes</td><td><span class="dot dot-yes"></span>Sim</td><td><span class="dot dot-yes"></span>Parcial</td><td>Visitantes</td><td><span class="dot dot-no"></span>Não</td></tr>
        <tr><td><strong>consolidation</strong></td><td>Consolidação</td><td><span class="dot dot-yes"></span>Sim</td><td><span class="dot dot-yes"></span>Parcial</td><td>Consolidação / follow-ups</td><td><span class="dot dot-no"></span>Não</td></tr>
        <tr><td><strong>events</strong></td><td>Eventos</td><td><span class="dot dot-yes"></span>Sim</td><td><span class="dot dot-yes"></span>Parcial</td><td>Eventos</td><td><span class="dot dot-no"></span>Não</td></tr>
        <tr><td><strong>admin</strong></td><td>Administrador</td><td><span class="dot dot-yes"></span>Sim</td><td><span class="dot dot-yes"></span>Total</td><td>Todos</td><td><span class="dot dot-yes"></span>Sim</td></tr>
        <tr><td><strong>superuser</strong></td><td>Superusuário (Django)</td><td>Redireciona admin</td><td><span class="dot dot-yes"></span>Total</td><td>Todos</td><td><span class="dot dot-yes"></span>Sim</td></tr>
      </tbody>
    </table>
    <div class="info-box" style="margin-top:16px">
      <strong>Como testar:</strong> crie (ou peça ao dev) uma conta para cada grupo acima e valide se consegue acessar apenas os módulos permitidos.
    </div>
    <p style="margin:16px 0 10px"><strong>Permissões extras no cadastro de membro</strong> (além do grupo):</p>
    <table>
      <thead><tr><th>Flag / Condição</th><th>Habilita</th></tr></thead>
      <tbody>
        <tr><td><code>is_approver</code></td><td>Aprovar/rejeitar Palavras de Conhecimento</td></tr>
        <tr><td><code>is_available_to_consolidate</code></td><td>Receber acompanhamentos de consolidação</td></tr>
        <tr><td><code>is_consolidated</code></td><td>Indica que já passou pelo processo de consolidação</td></tr>
        <tr><td>Ministério "Ministração"</td><td>Aba de palavras/curas no dashboard</td></tr>
        <tr><td>Ministério "Boas Vindas"</td><td>Cadastro de visitantes no dashboard</td></tr>
        <tr><td>Qualquer ministério ativo</td><td>Card de escala no dashboard</td></tr>
      </tbody>
    </table>
    <div class="warn-box"><strong>Senhas padrão:</strong> Ao criar usuário pelo admin ou resetar senha, a senha padrão é <code>123</code>.</div>
    <h3 style="margin:20px 0 10px">Contas sugeridas para o QA</h3>
    <p style="margin-bottom:10px;font-size:12px;color:var(--muted)">Usuários de exemplo — um login por combinação que o QA precisa validar:</p>
    <p>
      <span class="persona">Sem login</span>
      <span class="persona">member — membro comum</span>
      <span class="persona">member + ministério Boas Vindas</span>
      <span class="persona">member + ministério Ministração</span>
      <span class="persona">member + is_approver</span>
      <span class="persona">member + consolidador</span>
      <span class="persona">visitors</span>
      <span class="persona">consolidation</span>
      <span class="persona">events</span>
      <span class="persona">admin</span>
    </p>
  </div>
  <hr class="section-divider" />
"""

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "\U0000200D"
    "\uFE0F"
    "]+",
    flags=re.UNICODE,
)


def read_part(name: str) -> str:
    return (PARTS / name).read_text(encoding="utf-8")


def clean_html(html: str) -> str:
    html = re.sub(r"\s*<span class=\"icon\">[^<]*</span>\s*", "\n      ", html)
    html = html.replace("⚠ ", "").replace("style=\"background:#f7fafc;border-left:4px solid #a0aec0;\"", "")
    lines = []
    for line in html.splitlines():
        if "<" in line:
            parts = re.split(r"(<[^>]+>)", line)
            line = "".join(p if p.startswith("<") else EMOJI_RE.sub("", p) for p in parts)
            line = re.sub(r'(<div class="feature-title">)\s*', r"\1", line)
            line = re.sub(r'(<div class="feature-title">)[\s\uFE0F]+', r"\1", line)
        else:
            line = EMOJI_RE.sub("", line)
        lines.append(line)
    return "\n".join(lines)


def logo_data_uri() -> str:
    data = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def build_member_section() -> str:
    member = read_part("04_member.html")
    cut = member.index('    <div class="feature">\n      <div class="feature-title">')
    # find 3.3 feature start
    idx = member.index("3.3 — Roteiro")
    cut = member.rindex('    <div class="feature">', 0, idx)
    head = member[:cut]
    tail = Path("/tmp/member_update.html").read_text(encoding="utf-8")
    return head + tail + "\n  </div>\n  <hr class=\"section-divider\" />\n"


def build_rest() -> str:
    rest = read_part("08_rest.html")
    repl = [
        ("<h2>8. Tradução ao Vivo", "<h2>10. Tradução ao Vivo"),
        (">8.1 — Gravador", ">10.1 — Gravador"),
        (">8.2 — Exibição", ">10.2 — Exibição"),
        ("<h2>9. APIs Internas", "<h2>11. APIs Internas"),
        ("<h2>10. Fora do Escopo", "<h2>12. Fora do Escopo"),
        ("<h2>11. Checklist Rápido", "<h2>13. Checklist Rápido"),
        (
            "Casa da Paz — 4º membro tenta aceitar</td><td>Bloqueado (limite 3)",
            "Casa da Paz — aceitar casa que já tem 3 membros</td><td>Sistema bloqueia o 4º aceite",
        ),
    ]
    for old, new in repl:
        rest = rest.replace(old, new)
    return rest


def main() -> None:
    admin = "".join(read_part(n) for n in (
        "admin_detailed.html",
        "admin_detailed_2.html",
        "admin_detailed_3.html",
    ))

    body = "\n".join([
        TOC,
        PERMISSION_GROUPS,
        read_part("02_public.html"),
        read_part("03_auth.html"),
        build_member_section(),
        admin,
        build_rest(),
    ])

    body = clean_html(body)
    logo_src = logo_data_uri()

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Filadélfia Connect — Documento de QA</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet" />
<style>{CSS}
</style>
</head>
<body>

<div class="cover">
  <img class="cover-logo" src="{logo_src}" alt="Igreja Filadélfia" />
  <div class="cover-mark"></div>
  <div class="badge">Branch: production</div>
  <h1>Filadélfia Connect</h1>
  <p class="subtitle">Documento de Funcionalidades para QA<br>Plataforma de Gestão da Igreja</p>
  <div class="meta">
    <div class="meta-item"><div class="label">Versão</div><div class="value">Production</div></div>
    <div class="meta-item"><div class="label">Data</div><div class="value">Junho 2026</div></div>
    <div class="meta-item"><div class="label">Seções</div><div class="value">13 módulos</div></div>
    <div class="meta-item"><div class="label">Funcionalidades</div><div class="value">80+ fluxos</div></div>
  </div>
</div>

<div class="container">
  <div class="print-hint no-print">
    Para exportar em PDF: Ctrl+P → marque <strong>Gráficos de segundo plano</strong> (Background graphics) para imprimir as cores da capa e dos cabeçalhos.
  </div>
{body}
</div>
</body>
</html>
"""

    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({len(html.splitlines())} lines, {len(html)} bytes)")


if __name__ == "__main__":
    main()
