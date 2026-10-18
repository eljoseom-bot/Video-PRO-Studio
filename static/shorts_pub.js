/* «🎬 Shorts que salieron» · título y tags para YouTube y TikTok + subir el Short a YouTube (el autor, 2026-10-09).
   El HTML de cada Short lo pinta `shortPubHTML` (proinfo.js); aquí vive lo que se hace con él: copiar, y subir con confirmación
   (cuesta ≈1.600 de la cuota diaria de YouTube) con el progreso a la vista. Los Shorts NO se suben solos. */
(function(){
  const $ = (el, q) => el.querySelector(q);
  const tags2txt = v => (v || '').split(',').map(x => x.trim()).filter(Boolean);
  const PRIV = {private: 'privado', unlisted: 'no listado', public: 'público'};

  function pinta(root, e){
    const est = $(root, '.sp-estado'), btn = $(root, '.sp-subir'), sel = $(root, '.sp-priv');
    if(!est) return;
    if(e && e.estado === 'subido'){
      est.innerHTML = `✅ Subido (${PRIV[e.privacidad] || 'privado'}): <a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.url)}</a>`
        + (e.studio ? ` · <a href="${esc(e.studio)}" target="_blank" rel="noopener">abrir en Studio</a>` : '');
      btn.disabled = true; btn.textContent = '✅ Ya está en YouTube'; sel.disabled = true;
    }else if(e && e.estado === 'subiendo'){
      est.textContent = `⏳ Subiendo a YouTube… ${e.pct || 0} %`;
      btn.disabled = true; btn.textContent = '⏳ Subiendo…';
    }else if(e && e.estado === 'error'){
      est.innerHTML = `⚠ No se subió: ${esc(e.error || 'error')} <span class="muted">Puedes reintentar.</span>`;
      btn.disabled = false; btn.textContent = '⬆ Reintentar subida';
    }else{
      est.textContent = '';
      btn.disabled = false; btn.textContent = '⬆ Subir a YouTube';
    }
  }

  async function carga(root){
    const jid = root.dataset.jid, arch = root.dataset.archivo;
    try{
      const d = await (await fetch('/api/shorts/' + encodeURIComponent(jid))).json();
      root._info = d;
      const e = (d.subidos || {})[arch];
      pinta(root, e);
      const ap = $(root, '.sp-enlace');
      if(ap) ap.textContent = d.enlace ? `La descripción lleva el enlace a tu vídeo completo (${d.enlace}).`
        : 'Tu vídeo completo aún no está subido: si lo subes antes, la descripción del Short llevará su enlace.';
      if(e && e.estado === 'subiendo') setTimeout(() => carga(root), 2000);
    }catch(err){ /* sin red: se queda como está */ }
  }

  async function sube(root){
    const jid = root.dataset.jid, arch = root.dataset.archivo, info = root._info || {};
    if(info.conectado === false){ alert('YouTube no está conectado: usa «Conectar YouTube» arriba a la derecha.'); return; }
    const titulo = $(root, '.sp-yt-titulo').value.trim();
    if(titulo.length < 3){ alert('Escribe un título para el Short.'); return; }
    const priv = $(root, '.sp-priv').value;
    const nombre = {private: 'PRIVADO (lo revisas en Studio y lo publicas con un clic)', unlisted: 'NO LISTADO', public: 'PÚBLICO (lo ve todo el mundo ya)'}[priv];
    if(!confirm(`¿Subir este Short a YouTube?\n\n«${titulo}»\nQuedará ${nombre}.\n\nGasta unas ${info.coste || 1600} unidades de la cuota diaria de la API (de 10.000, igual que un vídeo largo).`)) return;
    const btn = $(root, '.sp-subir'); btn.disabled = true; btn.textContent = '⏳ Empezando…';
    try{
      const r = await (await fetch(`/api/shorts/${encodeURIComponent(jid)}/subir`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({archivo: arch, titulo, descripcion: $(root, '.sp-yt-desc').value, tags: tags2txt($(root, '.sp-yt-tags').value), privacidad: priv})})).json();
      if(!r.ok){ $(root, '.sp-estado').textContent = '⚠ ' + (r.error || 'No se pudo empezar.'); btn.disabled = false; btn.textContent = '⬆ Subir a YouTube'; return; }
      pinta(root, {estado: 'subiendo', pct: 0});
      setTimeout(() => carga(root), 1500);
    }catch(err){ $(root, '.sp-estado').textContent = '⚠ Sin conexión con el panel.'; btn.disabled = false; btn.textContent = '⬆ Subir a YouTube'; }
  }

  document.addEventListener('click', ev => {
    const b = ev.target.closest('.sp-subir');
    if(b){ const root = b.closest('.short-pub'); if(root) sube(root); return; }
    const c = ev.target.closest('.sp-copia');
    if(c){
      const root = c.closest('.short-pub'), q = c.dataset.de;
      const txt = q === 'tt' ? `${$(root, '.sp-tt-titulo').value}\n${$(root, '.sp-tt-hash').value}`.trim()
        : q === 'yt-tags' ? tags2txt($(root, '.sp-yt-tags').value).join(', ')
        : $(root, q === 'yt-titulo' ? '.sp-yt-titulo' : '.sp-yt-desc').value;
      c.dataset.txt = txt; copyTxt(c);
    }
  });

  function escanea(){ document.querySelectorAll('.short-pub:not([data-ok])').forEach(r => { r.dataset.ok = '1'; carga(r); }); }
  new MutationObserver(escanea).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('DOMContentLoaded', escanea);
})();
