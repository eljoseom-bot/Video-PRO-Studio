/** 📝 ARTÍCULO PARA TU WEB (el autor, 2026-09-26): debajo de los Shorts de cada Vídeo PRO. Un borrador SEO del vídeo ya editado
    — estructura con H2/H3, listas, tablas si hacen falta, capturas del propio vídeo, title y meta descripción — con UNA
    petición a Flash-Lite (no toca las 20 de Flash). Ver clipper/articulo.py. Usa esc() y copyTxt() de proinfo.js. */
const _ART_T = {};

function articuloSlot(jid, slot){
  if(!slot || !jid) return;
  slot.dataset.jid = jid;
  articuloPinta(jid, slot);
}

async function articuloPinta(jid, slot){
  let r;
  try{ r = await (await fetch('/api/articulo/' + encodeURIComponent(jid))).json(); }
  catch(e){ return; }
  slot.innerHTML = articuloHTML(jid, r);
  clearTimeout(_ART_T[jid]);
  if(r.estado === 'creando' || (r.hero && r.hero.en_marcha)) _ART_T[jid] = setTimeout(() => articuloPinta(jid, slot), 3000);
}

function _artFila(nombre, valor, maximo){
  const n = (valor || '').length;
  const largo = maximo && n > maximo;
  return `<div class="art-seo-fila">
      <span class="art-seo-k">${nombre}</span>
      <span class="art-seo-v">${esc(valor || '—')}${maximo ? ` <b class="art-cuenta${largo ? ' largo' : ''}">${n}/${maximo}</b>` : ''}</span>
      <button type="button" class="btn ghost sm art-copiar" data-txt="${esc(valor || '')}" onclick="copyTxt(this)">copiar</button>
    </div>`;
}

function articuloHTML(jid, r){
  const cab = `<div class="pro-ab-label">📝 ARTÍCULO PARA TU WEB</div>`;
  if(r.estado === 'creando'){
    return `<section class="pro-articulo">${cab}${progBarHTML('art-' + jid, r.paso || 'Creando el artículo…', 90)}</section>`;
  }
  if(r.estado !== 'listo'){
    return `<section class="pro-articulo">${cab}
      <p class="muted art-intro">Convierte este vídeo en un <b>borrador de artículo SEO</b> para tu web: estructura con H2 y H3,
      listas, tablas si hacen falta, <b>capturas del propio vídeo</b> donde enseñas algo en pantalla, title y meta descripción.
      Gasta 1 petición a Flash-Lite (no toca tus 20 de Flash); las capturas salen del vídeo en tu PC, sin IA.</p>
      ${r.error ? `<div class="callout bad" style="margin:0 0 10px">✖ ${esc(r.error)}</div>` : ''}
      <button type="button" class="btn" onclick="articuloCrea('${esc(jid)}', this, false)">📝 Crear artículo</button></section>`;
  }
  const a = r.articulo || {};
  const base = `/salida/${encodeURIComponent(jid)}/articulo/`;
  const htmlPanel = (a.html || '').replace(/src="(img-\d+\.jpg)"/g, (m, f) => `src="${base}${f}"`);
  const fecha = a.ts ? new Date(a.ts * 1000).toLocaleString('es', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
  return `<section class="pro-articulo">${cab}
    <div class="art-seo">
      ${_artFila('Title', a.title_seo, 60)}
      ${_artFila('Meta descripción', a.meta_descripcion, 155)}
      ${_artFila('Slug', a.slug ? '/' + a.slug + '/' : '')}
      ${_artFila('Palabra clave', a.keyword)}
    </div>
    ${(a.avisos || []).map(x => `<div class="callout warn" style="margin:8px 0 0">⚠ ${esc(x)}</div>`).join('')}
    <div class="art-acciones">
      <button type="button" class="btn sm" data-txt="${esc(a.markdown || '')}" onclick="copyTxt(this)">Copiar Markdown</button>
      <button type="button" class="btn ghost sm" data-txt="${esc(a.html || '')}" onclick="copyTxt(this)">Copiar HTML</button>
      <a class="btn ghost sm" href="${base}articulo.html" target="_blank" rel="noopener">👁 Vista previa</a>
      <a class="btn ghost sm" href="/api/articulo/${encodeURIComponent(jid)}/zip">⬇ Descargar (.zip con capturas)</a>
      <button type="button" class="btn ghost sm" onclick="articuloCrea('${esc(jid)}', this, true)"
              title="Otro borrador (1 petición a Flash-Lite): el de ahora se sustituye">↻ Rehacer</button>
    </div>
    ${articuloHeroHTML(jid, r)}
    ${articuloSitioHTML(jid, r)}
    <div class="muted art-meta">${a.palabras || 0} palabras · ${a.imagenes || 0} captura(s) · ${esc(a.nombre_modelo || '')}${fecha ? ' · ' + fecha : ''}</div>
    ${(r.capturas || []).length ? `<div class="art-capturas">${r.capturas.map(f => `
      <a href="${base}${encodeURIComponent(f)}" target="_blank" rel="noopener" title="${esc(f)}"><img loading="lazy" src="${base}${encodeURIComponent(f)}" alt=""></a>`).join('')}</div>` : ''}
    <details class="art-ver"><summary>Leer el artículo aquí</summary><article class="art-cuerpo">${htmlPanel}</article></details>
  </section>`;
}

/** 🎨 Imagen destacada con IA (el autor, 2026-10-11): una caricatura en blanco y negro del artículo, dibujada con Cloudflare (clipper/imagen_articulo.py). */
function articuloHeroHTML(jid, r){
  const h = r.hero || {};
  const base = `/salida/${encodeURIComponent(jid)}/articulo/`;
  const cab = `<b>🎨 Imagen destacada con IA</b> <span class="muted">· caricatura en blanco y negro, al estilo de tu web, sacada del artículo (1 petición a Flash-Lite + ≈156 neuronas de Cloudflare, gratis)</span>`;
  if(!h.disponible) return `<div class="art-hero" style="margin:12px 0;padding:10px 12px;border:1.5px solid var(--ink,#14100f);border-radius:10px">${cab}<div class="muted" style="margin-top:6px">${esc(h.motivo || 'Cloudflare no está configurado.')}</div></div>`;
  const imgs = h.imagenes || [];
  const fila = imgs.map(i => {
    const el = h.elegida === i.archivo;
    return `<div style="text-align:center;width:230px"><a href="${base}${encodeURIComponent(i.archivo)}" target="_blank" rel="noopener"><img loading="lazy" src="${base}${encodeURIComponent(i.archivo)}"
        alt="" style="width:230px;border:${el ? 3 : 1.5}px solid ${el ? '#fe330a' : 'var(--ink,#14100f)'};border-radius:8px"></a>
      <div style="display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-top:4px">
        <button type="button" class="btn ${el ? '' : 'ghost'} sm" onclick="articuloHeroElige('${esc(jid)}', ${el ? 'null' : `'${esc(i.archivo)}'`}, this)">${el ? '⭐ Es la destacada (quitar)' : '⭐ Usar como destacada'}</button>
        <a class="btn ghost sm" href="${base}${encodeURIComponent(i.archivo)}" download="${esc(articuloHeroNombre(r, i.archivo))}" title="Guardar esta imagen en tu PC (JPG, 1280×720)">⬇ Descargar</a>
      </div>
      ${i.con_texto ? '<div class="muted">⚠ puede traer texto</div>' : ''}</div>`;
  }).join('');
  const marcha = h.en_marcha ? `<div style="margin-top:8px">${progBarHTML('hero-' + jid, h.paso || 'Dibujando la imagen…', 45)}</div>` : '';
  const botones = h.en_marcha ? '' : `<button type="button" class="btn sm" onclick="articuloHeroGenera('${esc(jid)}', false, this)">${imgs.length ? '↻ Otra (misma escena)' : '🎨 Dibujar la imagen'}</button>
      ${imgs.length ? `<button type="button" class="btn ghost sm" onclick="articuloHeroGenera('${esc(jid)}', true, this)" title="Una escena nueva (1 petición a Flash-Lite)">💡 Otra idea</button>` : ''}
      <span class="muted">hoy ${h.cupo ? h.cupo.hoy : 0}/${h.cupo ? h.cupo.max : 10} dibujos</span>`;
  return `<div class="art-hero" style="margin:12px 0;padding:10px 12px;border:1.5px solid var(--ink,#14100f);border-radius:10px">${cab}
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px">${botones}</div>
    ${h.error ? `<div class="callout bad" style="margin:8px 0 0">✖ ${esc(h.error)}</div>` : ''}${h.aviso ? `<div class="callout warn" style="margin:8px 0 0">⚠ ${esc(h.aviso)}</div>` : ''}${marcha}
    ${h.concepto ? `<div class="muted" style="margin-top:6px">Escena: ${esc(h.concepto.escena || '')} · <i>alt: ${esc(h.concepto.alt || '')}</i></div>` : ''}
    ${fila ? `<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px">${fila}</div>` : ''}
    <div class="muted" style="margin-top:6px">${h.elegida ? 'Al pulsar «Mandar a tu web», la destacada elegida será el <code>heroImage</code> del artículo.' : 'Elige una con «⭐ Usar como destacada»; si no, la destacada será la primera captura.'}</div></div>`;
}

/** El nombre con el que se guarda en tu PC: «<slug-del-artículo>-destacada-2.jpg» (así sabes de qué artículo es). */
function articuloHeroNombre(r, archivo){
  const slug = String((r && r.articulo && (r.articulo.slug || r.articulo.keyword)) || 'articulo').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'articulo';
  const n = (String(archivo).match(/(\d+)/) || [])[1] || '1';
  return `${slug}-destacada-${n}.jpg`;
}

async function articuloHeroGenera(jid, nueva, btn){
  btn.disabled = true;
  try{
    await fetch('/api/articulo/' + encodeURIComponent(jid) + '/hero/generar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nueva_idea: nueva})});
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); btn.disabled = false; return; }
  const slot = btn.closest('.pro-articulo') ? btn.closest('.pro-articulo').parentElement : null;
  if(slot) articuloPinta(jid, slot);
}

async function articuloHeroElige(jid, archivo, btn){
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/articulo/' + encodeURIComponent(jid) + '/hero/elegir', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({archivo: archivo})})).json();
    if(!r.ok) alert(r.error || 'No se pudo.');
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); }
  const slot = btn.closest('.pro-articulo') ? btn.closest('.pro-articulo').parentElement : null;
  if(slot) articuloPinta(jid, slot);
}

/** 📤 A tu web (el autor, 2026-10-11): escribe el artículo con su cabecera, las capturas y el vídeo DIRECTO en tu proyecto de Astro (clipper/sitio_astro.py). */
function articuloSitioHTML(jid, r){
  const s = r.sitio || {};
  if(!s.configurado) return '';
  const h = s.hecho;
  const ya = h ? `<div class="callout ok" style="margin:8px 0 0">✅ Ya está en tu proyecto: <code>${esc(h.relativo || '')}</code>
      (${h.borrador ? 'borrador' : 'PUBLICADO'}, ${(h.imagenes || []).length} imagen(es)).
      <button type="button" class="btn ghost sm" onclick="articuloSitioAbrir('${esc(jid)}', this)">Abrir en Visual Studio Code</button></div>` : '';
  return `<div class="art-sitio" style="margin:12px 0;padding:10px 12px;border:1.5px solid var(--ink,#14100f);border-radius:10px">
    <b>📤 Mandar a tu web</b> <span class="muted">· escribe el artículo, las capturas y el vídeo en <code>${esc(s.carpeta || '')}</code>; no hace Git ni sube nada</span>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px">
      <label>Categoría <select id="art-cat-${esc(jid)}">${(s.categorias || []).map(c => `<option value="${esc(c)}"${c === s.categoria_defecto ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
      <label title="Con borrador, tu web NO lo publica hasta que quites draft: true"><input type="checkbox" id="art-borr-${esc(jid)}"${s.borrador ? ' checked' : ''}> Borrador (draft: true)</label>
      <button type="button" class="btn sm" onclick="articuloSitio('${esc(jid)}', this)">📤 Crear en mi proyecto</button>
    </div>
    <div id="art-sitio-res-${esc(jid)}">${ya}</div>
  </div>`;
}

async function articuloSitio(jid, btn){
  const cat = (document.getElementById('art-cat-' + jid) || {}).value;
  const borr = !!(document.getElementById('art-borr-' + jid) || {}).checked;
  const res = document.getElementById('art-sitio-res-' + jid);
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/articulo/' + encodeURIComponent(jid) + '/sitio', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({categoria: cat, borrador: borr})})).json();
    if(!r.ok){ res.innerHTML = `<div class="callout bad" style="margin:8px 0 0">✖ ${esc(r.error || 'No se pudo.')}</div>`; btn.disabled = false; return; }
    res.innerHTML = `<div class="callout ok" style="margin:8px 0 0">✅ Creado: <code>${esc(r.relativo)}</code> (${r.borrador ? 'borrador' : 'PUBLICADO'}, ${(r.imagenes || []).length} imagen(es)${r.video ? ', con el vídeo' : ', SIN vídeo: aún no está publicado en YouTube'}).
      Revísalo en Visual Studio Code y haz commit/push como siempre.
      <button type="button" class="btn ghost sm" onclick="articuloSitioAbrir('${esc(jid)}', this)">Abrir en Visual Studio Code</button></div>`;
  }catch(e){ res.innerHTML = `<div class="callout bad" style="margin:8px 0 0">✖ No se pudo hablar con el panel: ${esc(e.message)}</div>`; }
  btn.disabled = false;
}

async function articuloSitioAbrir(jid, btn){
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/articulo/' + encodeURIComponent(jid) + '/sitio/abrir', {method: 'POST'})).json();
    if(!r.ok) alert(r.error || 'No se pudo abrir.');
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); }
  btn.disabled = false;
}

async function articuloCrea(jid, btn, rehacer){
  if(rehacer && !confirm('¿Hacer otro borrador? El de ahora se sustituye (1 petición a Flash-Lite).')) return;
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/articulo/' + encodeURIComponent(jid) + '/crear', {method: 'POST'})).json();
    if(!r.ok){ alert(r.error || 'No se pudo crear el artículo.'); btn.disabled = false; return; }
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); btn.disabled = false; return; }
  const slot = btn.closest('.pro-articulo') ? btn.closest('.pro-articulo').parentElement : null;
  if(slot) articuloPinta(jid, slot);
}
