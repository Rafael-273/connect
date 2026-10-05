(() => {
  const copy = document.querySelector('[data-copy-pix]');
  if (copy) copy.addEventListener('click', async () => {
    const key = document.querySelector('#pix-key')?.textContent.trim();
    const feedback = document.querySelector('.copy-feedback');
    try { await navigator.clipboard.writeText(key); feedback.textContent = 'Chave Pix copiada'; }
    catch (_) { feedback.textContent = 'Não foi possível copiar a chave'; }
    setTimeout(() => { feedback.textContent = ''; }, 2800);
  });
  document.querySelectorAll('.value-option').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('.value-option').forEach((item) => item.setAttribute('aria-pressed', 'false'));
    button.setAttribute('aria-pressed', 'true');
  }));
  const dialog = document.querySelector('.gallery-lightbox');
  const images = dialog ? [...dialog.querySelectorAll('img')] : [];
  let active = 0;
  const show = (index) => { active = (index + images.length) % images.length; images.forEach((image, i) => image.hidden = i !== active); };
  document.querySelector('[data-open-gallery]')?.addEventListener('click', () => dialog.showModal());
  dialog?.querySelector('[data-close-gallery]').addEventListener('click', () => dialog.close());
  dialog?.querySelector('.previous').addEventListener('click', () => show(active - 1));
  dialog?.querySelector('.next').addEventListener('click', () => show(active + 1));
  dialog?.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  document.addEventListener('keydown', (event) => { if (!dialog?.open) return; if (event.key === 'ArrowLeft') show(active - 1); if (event.key === 'ArrowRight') show(active + 1); });
  let touchStart = 0;
  dialog?.addEventListener('touchstart', (event) => { touchStart = event.changedTouches[0].screenX; }, { passive: true });
  dialog?.addEventListener('touchend', (event) => { const distance = event.changedTouches[0].screenX - touchStart; if (Math.abs(distance) > 45) show(active + (distance < 0 ? 1 : -1)); }, { passive: true });
  const sticky = document.querySelector('.mobile-cta'); const contribution = document.querySelector('#contribuir'); const hero = document.querySelector('.hero');
  if (sticky && contribution && hero && 'IntersectionObserver' in window) { const observer = new IntersectionObserver(([entry]) => sticky.classList.toggle('is-hidden', entry.isIntersecting), { threshold: .15 }); observer.observe(contribution); const heroObserver = new IntersectionObserver(([entry]) => sticky.classList.toggle('is-visible', !entry.isIntersecting), { threshold: .1 }); heroObserver.observe(hero); }
})();
