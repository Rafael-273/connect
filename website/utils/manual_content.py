"""Render manual text using a small formatting vocabulary and allowlisted HTML."""
import bleach
import markdown


def sanitize_manual_content(content):
    html = markdown.markdown(content or '', extensions=['tables'])
    return bleach.clean(
        html,
        tags={'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'ul', 'ol', 'li', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'blockquote', 'code', 'pre'},
        attributes={'a': ['href', 'title'], 'img': ['src', 'alt', 'title'], 'th': ['colspan', 'rowspan'], 'td': ['colspan', 'rowspan']},
        protocols={'http', 'https', 'mailto'},
        strip=True,
    )
