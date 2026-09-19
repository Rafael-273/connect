document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-manual-editor]').forEach(function (editor) {
        const source = editor.querySelector('[data-manual-source]');
        const result = editor.querySelector('[data-manual-result]');
        const status = editor.querySelector('[data-manual-status]');
        const formats = {
            heading: ['\n## ', '\n', 'Título'],
            subheading: ['\n### ', '\n', 'Subtítulo'],
            bold: ['**', '**', 'texto'],
            italic: ['*', '*', 'texto'],
            list: ['\n- ', '\n', 'Item'],
            numbered: ['\n1. ', '\n', 'Item'],
            link: ['[', '](https://exemplo.com)', 'Texto do link'],
            quote: ['\n> ', '\n', 'Citação'],
            rule: ['\n\n---\n', '', ''],
        };
        editor.querySelectorAll('[data-format]').forEach(function (button) {
            button.addEventListener('click', function () {
                const [before, after, placeholder] = formats[button.dataset.format];
                const start = source.selectionStart;
                const selected = source.value.slice(start, source.selectionEnd) || placeholder;
                source.setRangeText(before + selected + after, start, source.selectionEnd, 'end');
                source.focus();
                source.setSelectionRange(start + before.length, start + before.length + selected.length);
                source.dispatchEvent(new Event('input', {bubbles: true}));
                result.hidden = true;
                editor.querySelector('[data-manual-preview]').setAttribute('aria-expanded', 'false');
            });
        });
        editor.querySelector('[data-manual-preview]').addEventListener('click', async function () {
            const button = this;
            status.textContent = 'Preparando pré-visualização…';
            button.disabled = true;
            try {
                const body = new FormData();
                body.append('content', source.value);
                body.append('csrfmiddlewaretoken', source.form.querySelector('[name="csrfmiddlewaretoken"]').value);
                const response = await fetch(editor.dataset.previewUrl, {method: 'POST', body, credentials: 'same-origin'});
                if (!response.ok) throw new Error('preview');
                const data = await response.json();
                // Only server-rendered, allowlisted HTML is inserted here.
                result.innerHTML = data.html;
                result.hidden = false;
                button.setAttribute('aria-expanded', 'true');
                status.textContent = 'Pré-visualização atualizada. Salve para publicar as alterações.';
            } catch (error) {
                status.textContent = 'Não foi possível gerar a pré-visualização. Seu texto foi preservado.';
            } finally {
                button.disabled = false;
            }
        });
    });
});
