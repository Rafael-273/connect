"""Render manual text using a small formatting vocabulary and allowlisted HTML."""
import bleach
import markdown


def sanitize_manual_content(content):
    html = markdown.markdown(content or '')
    return bleach.clean(
        html,
        tags={'p', 'br', 'h1', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'ul', 'ol', 'li', 'a', 'hr', 'blockquote', 'code', 'pre'},
        attributes={'a': ['href', 'title']},
        protocols={'http', 'https', 'mailto'},
        strip=True,
    )
