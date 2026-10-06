(() => {
  const translations = {
    pt: {},
    en: {
      skip: 'Skip to content', navLabel: 'Sítio Filadélfia navigation', navProject: 'The project', navStructure: 'The venue', navVision: 'The vision', navJoin: 'Be part of it',
      siteName: 'Sítio Filadélfia', heroTitle: 'A dream.<br>A purpose.', heroLead: 'An achievement we will make together. God gave us this dream, and together we will turn it into a miracle.', heroJoin: 'I want to be part of it', heroProject: 'Discover the project',
      videoTitle: 'See the dream God has placed before us.', dreamEyebrow: 'The dream', dreamTitle: 'A place with purpose', dreamOne: 'Today, we have the opportunity to acquire Sítio Filadélfia: a space of approximately 6,000 m², ready to welcome people, families, and generations.', dreamTwo: 'More than acquiring a space, we want to build a place where the Church can experience fellowship, celebrate, serve, and fulfill God’s purpose.', dreamThree: 'A setting for families, children, youth, and teenagers. A place for gatherings, celebrations, training, and moments that will mark lives.',
      existing: 'What is already here', structureTitle: 'A space that already holds so many possibilities', allPhotos: 'View all photos', structureTitle1: 'Pool area', structureDescription1: 'Leisure, fellowship, and landscaping.', structureTitle2: 'Main house', structureDescription2: 'Facilities for lodging, meetings, and support.', structureTitle3: 'Games room', structureDescription3: 'A space for fellowship and entertainment.', structureTitle4: 'Playground and sports area', structureDescription4: 'Settings designed for children, youth, and families.', structureTitle5: 'Support houses', structureDescription5: 'More facilities for lodging and activities.', structureTitle6: 'Sports court and fellowship area', structureDescription6: 'Complementary spaces for different needs.', moreThanStructure: 'More than a venue', purposeStatement: 'We are not talking only about walls, rooms, and buildings.<br><em>We are talking about lives.</em>', purposeLead: 'We want this place to be an environment where families are strengthened, people find fellowship, and new generations can grow in God’s presence.',
      missionCenter: 'Mission center', missionTitle: 'A place to welcome, equip, and send', missionLead: 'We dream that Sítio Filadélfia will also become a Mission Center: a place where people can be welcomed, trained, equipped, and sent to share the Gospel around the world.', receive: 'Welcome', equip: 'Equip', send: 'Send',
      futureEyebrow: 'The vision for the future', futureTitle: 'What is a possibility today can become reality.', futureLead: 'We dream of a venue ready to welcome even more people and expand everything God can do in this place.', futureCaption: 'Vision for the future',
      joinEyebrow: 'Be part of it', offeringTitle: 'My <span class="campaign-word">Sacrificial</span> Offering for the Kingdom', offeringLead: 'We are united in “My Sacrificial Offering for the Kingdom.” Pray, seek the Lord’s guidance, and participate according to what God places on your heart.', deadline: 'Until the second Sunday of October.', valuesTitle: 'Be part of this achievement', valuesLead: 'The amounts below are only references. You can contribute according to what God places on your heart.', valuesLabel: 'Suggested contribution amounts', contributionValue1: 'R$ 500 – 1,000', contributionValue2: 'R$ 1,000 – 2,500', contributionValue3: 'R$ 2,500 – 5,000', contributionValue4: 'R$ 5,000 – 10,000', contributionValue5: 'Over R$ 10,000', otherValue: 'Other amount',
      contributeEyebrow: 'How to contribute', pixTitle: 'Be part of this miracle', pixLead: 'Contribute according to what God places on your heart.', pixContribution: 'Contribution via Pix', recipient: 'Recipient', pixKey: 'Pix key', copyPix: 'Copy Pix key', pixPending: 'The Pix key will be shared by the church.', cardContribution: 'Contribute by card'
    }
  };
  const languageButton = document.querySelector('[data-language-toggle]');
  const originalText = new Map([...document.querySelectorAll('[data-i18n]')].map((element) => [element, element.innerHTML]));
  const originalAriaLabels = new Map([...document.querySelectorAll('[data-i18n-aria-label]')].map((element) => [element, element.getAttribute('aria-label')]));
  const applyLanguage = (language) => {
    const dictionary = translations[language] || translations.pt;
    document.documentElement.lang = language === 'en' ? 'en' : 'pt-BR';
    document.querySelectorAll('[data-i18n]').forEach((element) => {
      const text = dictionary[element.dataset.i18n];
      element.innerHTML = text || originalText.get(element);
    });
    document.querySelectorAll('[data-i18n-aria-label]').forEach((element) => {
      const text = dictionary[element.dataset.i18nAriaLabel];
      element.setAttribute('aria-label', text || originalAriaLabels.get(element));
    });
    const isEnglish = language === 'en';
    const label = languageButton?.querySelector('.language-toggle-label');
    if (label) label.textContent = isEnglish ? 'PT' : 'EN';
    languageButton?.setAttribute('aria-label', isEnglish ? 'Switch language to Portuguese' : 'Switch language to English');
    languageButton?.setAttribute('title', isEnglish ? 'Português' : 'English');
    localStorage.setItem('philadelphia-language', language);
  };
  applyLanguage(localStorage.getItem('philadelphia-language') === 'en' ? 'en' : 'pt');
  languageButton?.addEventListener('click', () => applyLanguage(document.documentElement.lang === 'en' ? 'pt' : 'en'));

  const copy = document.querySelector('[data-copy-pix]');
  if (copy) copy.addEventListener('click', async () => {
    const key = document.querySelector('#pix-key')?.textContent.trim();
    const feedback = document.querySelector('.copy-feedback');
    try { await navigator.clipboard.writeText(key); feedback.textContent = document.documentElement.lang === 'en' ? 'Pix key copied' : 'Chave Pix copiada'; }
    catch (_) { feedback.textContent = document.documentElement.lang === 'en' ? 'The key could not be copied' : 'Não foi possível copiar a chave'; }
    setTimeout(() => { feedback.textContent = ''; }, 2800);
  });
  document.querySelectorAll('.value-option').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('.value-option').forEach((item) => item.setAttribute('aria-pressed', 'false'));
    button.setAttribute('aria-pressed', 'true');
    document.querySelector('#pix-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
