const overview = document.getElementById('overview');
const documentView = document.getElementById('document');
const content = document.getElementById('doc-content');
const loading = document.getElementById('doc-loading');
const sidebar = document.getElementById('sidebar');
const allowed = new Set([...document.querySelectorAll('[data-doc]')].map(a => a.dataset.doc));

function setActive(route) {
  document.querySelectorAll('.sidebar a').forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === route);
  });
  sidebar.classList.remove('open');
}

function rewriteDocLinks() {
  content.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href');
    const file = href && href.split('#')[0];
    if (allowed.has(file)) a.href = `#doc=${file}`;
    else if (href && !href.startsWith('#') && !/^(https?:|mailto:)/.test(href)) a.removeAttribute('href');
  });
}

async function render() {
  const hash = location.hash || '#overview';
  if (hash === '#overview') {
    overview.hidden = false;
    documentView.hidden = true;
    document.title = 'Investment Companion · 文档中心';
    setActive('#overview');
    window.scrollTo(0, 0);
    return;
  }
  const match = hash.match(/^#doc=([A-Z0-9-]+\.md)$/i);
  const file = match && match[1];
  if (!file || !allowed.has(file)) { location.hash = '#overview'; return; }
  overview.hidden = true;
  documentView.hidden = false;
  loading.hidden = false;
  content.innerHTML = '';
  setActive(`#doc=${file}`);
  try {
    const response = await fetch(file, {cache: 'no-cache'});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const source = await response.text();
    content.innerHTML = marked.parse(source, {gfm: true});
    rewriteDocLinks();
    document.title = `${content.querySelector('h1')?.textContent || file} · Investment Companion`;
  } catch (error) {
    content.innerHTML = `<h1>文档载入失败</h1><p>${String(error)}</p>`;
  } finally {
    loading.hidden = true;
    documentView.focus();
    window.scrollTo(0, 0);
  }
}

document.getElementById('nav-toggle').addEventListener('click', () => sidebar.classList.toggle('open'));
window.addEventListener('hashchange', render);
render();
