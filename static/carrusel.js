/** 🖼️ CARRUSEL PARA INSTAGRAM Y TIKTOK (el autor, 2026-10-05): debajo de las publicaciones para redes de cada Vídeo PRO. Un vídeo terminado se vuelve un carrusel de
    diapositivas —portada con tu cara, un paso por captura del propio vídeo y un cierre— en Instagram (4:5), TikTok (9:16) y PDF. UNA petición a Flash-Lite (no toca las 20 de
    Flash); las capturas salen del vídeo en tu PC, sin IA. Aquí se ve (eliges la diapositiva y el formato), se descarga, se copia el texto de la publicación y se edita
    («✏ Editar» vuelve a dibujar con tus cambios SIN IA). Ver clipper/carrusel.py. Usa esc() y copyTxt() de proinfo.js. */
const _CAR_T = {};                      // temporizador de «creando…» por vídeo
const _CAR = {};                        // por vídeo: la última respuesta del panel + qué formato/diapositiva se está viendo + si se está editando

function carruselSlot(jid, slot){
  if(!slot || !jid) return;
  slot.dataset.jid = jid;
  _CAR[jid] = _CAR[jid] || {fmt: 'ig', idx: 0, editando: false, r: null, slot};
  _CAR[jid].slot = slot;
  carruselPinta(jid);
}

async function carruselPinta(jid){
  const st = _CAR[jid]; if(!st) return;
  let r;
  try{ r = await (await fetch('/api/carrusel/' + encodeURIComponent(jid))).json(); }
  catch(e){ return; }
  st.r = r;
  carruselRepinta(jid);
  clearTimeout(_CAR_T[jid]);
  if(r.estado === 'creando') _CAR_T[jid] = setTimeout(() => carruselPinta(jid), 2500);
}

function carruselRepinta(jid){
  const st = _CAR[jid]; if(!st || !st.r) return;
  const enfoque = st.slot.contains(document.activeElement) && document.activeElement.id === 'car-visor-' + jid;
  st.slot.innerHTML = carruselHTML(jid, st.r, st);
  if(enfoque){ const v = document.getElementById('car-visor-' + jid); if(v) v.focus(); }
}

function _carFecha(ts){
  return ts ? new Date(ts * 1000).toLocaleString('es', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
}

function carruselHTML(jid, r, st){
  const cab = `<div class="pro-ab-label">🖼️ CARRUSEL PARA INSTAGRAM Y TIKTOK</div>`;
  const J = esc(jid);
  if(r.estado === 'creando'){
    return `<section class="pro-carrusel">${cab}${progBarHTML('car-' + jid, r.paso || 'Creando el carrusel…', 100)}</section>`;
  }
  if(r.estado !== 'listo'){
    return `<section class="pro-carrusel">${cab}
      <p class="muted art-intro">Convierte este vídeo en un <b>carrusel para Instagram y TikTok</b>: una portada con tu cara, <b>un paso por diapositiva con una
      captura del propio vídeo</b> y un cierre. Lo ves aquí, eliges y cambias lo que quieras, y descargas las imágenes (Instagram 4:5, TikTok 9:16 y un PDF).
      Gasta 1 petición a Flash-Lite (no toca tus 20 de Flash); las capturas salen del vídeo en tu PC, sin IA.</p>
      ${r.error ? `<div class="callout bad" style="margin:0 0 10px">✖ ${esc(r.error)}</div>` : ''}
      <button type="button" class="btn" onclick="carruselCrea('${J}', this, false)">🖼️ Crear carrusel</button></section>`;
  }
  const c = r.carrusel || {};
  const v = (c.ts_edicion || c.ts || 0);
  const archivos = c.archivos || {};
  const fmt = (st && st.fmt === 'tiktok') ? 'tiktok' : 'ig';
  const lista = archivos[fmt] || [];
  const idx = Math.min(Math.max(0, (st && st.idx) || 0), Math.max(0, lista.length - 1));
  const url = f => `/carrusel/${encodeURIComponent(jid)}/${fmt}/${encodeURIComponent(f)}?v=${Math.floor(v)}`;
  const pestana = (k, nombre, dim) => `<button type="button" class="${fmt === k ? 'on' : ''}" onclick="carruselFmt('${J}', '${k}')">${nombre} <span class="muted">${dim}</span></button>`;
  const rotulo = idx === 0 ? 'Portada' : (idx === lista.length - 1 ? 'Cierre' : `Paso ${idx}`);
  const titulo = idx === 0 ? ((c.portada || {}).titulo || '') : (idx === lista.length - 1 ? ((c.cierre || {}).titulo || '') : (((c.pasos || [])[idx - 1] || {}).titulo || ''));
  const visor = lista.length ? `
    <div class="car-visor-caja">
      <button type="button" class="btn ghost sm car-nav" onclick="carruselIr('${J}', -1)" ${idx === 0 ? 'disabled' : ''} title="Diapositiva anterior (←)">◀</button>
      <div class="car-visor car-${fmt}" id="car-visor-${J}" tabindex="0" onkeydown="carruselTecla(event, '${J}')" title="Usa ← y → para pasar de diapositiva">
        <img src="${url(lista[idx])}" alt="Diapositiva ${idx + 1}">
      </div>
      <button type="button" class="btn ghost sm car-nav" onclick="carruselIr('${J}', 1)" ${idx >= lista.length - 1 ? 'disabled' : ''} title="Diapositiva siguiente (→)">▶</button>
    </div>
    <div class="car-pie"><b>${idx + 1} / ${lista.length}</b> · ${esc(rotulo)}${titulo ? ': ' + esc(titulo) : ''}</div>
    <div class="car-tira" role="listbox" aria-label="Diapositivas">${lista.map((f, i) => `
      <button type="button" class="car-mini car-${fmt}${i === idx ? ' on' : ''}" onclick="carruselVer('${J}', ${i})" title="${i === 0 ? 'Portada' : (i === lista.length - 1 ? 'Cierre' : 'Paso ' + i)}">
        <img loading="lazy" src="${url(f)}" alt=""><span>${i + 1}</span></button>`).join('')}</div>` : '<div class="callout warn">No encuentro las diapositivas de este carrusel en el PC: «↻ Otro carrusel» las vuelve a hacer.</div>';
  const base = `/api/carrusel/${encodeURIComponent(jid)}`;
  const editor = st && st.editando ? carruselEditorHTML(jid, c, r) : '';
  return `<section class="pro-carrusel">${cab}
    <div class="fuente-tabs car-tabs">${pestana('ig', 'Instagram', '4:5')}${pestana('tiktok', 'TikTok', '9:16')}</div>
    ${r.error ? `<div class="callout bad" style="margin:0 0 8px">✖ ${esc(r.error)} <span class="muted">(el carrusel de antes sigue como estaba)</span></div>` : ''}
    ${(c.avisos || []).map(x => `<div class="callout warn" style="margin:0 0 8px">⚠ ${esc(x)}</div>`).join('')}
    ${visor}
    <div class="art-acciones">
      <a class="btn descargar sm" href="${base}/zip/${fmt}" title="Las diapositivas de ${fmt === 'ig' ? 'Instagram' : 'TikTok'} y el texto de la publicación">⬇ Descargar ${fmt === 'ig' ? 'Instagram' : 'TikTok'} (.zip)</a>
      <a class="btn ghost sm" href="${base}/zip/todo" title="Instagram, TikTok, el PDF y los textos">⬇ Todo (.zip)</a>
      ${c.hay_pdf ? `<a class="btn ghost sm" href="${base}/pdf" title="Para subirlo como documento a LinkedIn">⬇ PDF</a>` : ''}
      <button type="button" class="btn ghost sm" onclick="carruselEditar('${J}')">${st && st.editando ? '✕ Cerrar el editor' : '✏ Editar'}</button>
      <button type="button" class="btn ghost sm" onclick="carruselCrea('${J}', this, true)"
              title="Otro carrusel (1 petición a Flash-Lite): el de ahora se sustituye, también lo que hayas editado">↻ Otro carrusel</button>
    </div>
    <div class="muted art-meta">${c.diapositivas || lista.length} diapositivas · ${esc(c.nombre_modelo || '')}${_carFecha(c.ts) ? ' · ' + _carFecha(c.ts) : ''}${c.ts_edicion ? ' · editado ' + _carFecha(c.ts_edicion) : ''}</div>
    ${editor}
    <div class="red-grid car-textos">
      <div class="red-bloque"><div class="red-cab"><b>Texto para Instagram</b><span class="muted red-consejo">con los hashtags</span></div>
        <div class="red-caja"><div class="red-texto">${esc(c.texto_instagram || '')}</div>
          <div class="red-pie"><span class="red-cuenta">${[...(c.texto_instagram || '')].length}/2200</span>
            <button type="button" class="btn ghost sm red-copiar" data-txt="${esc(c.texto_instagram || '')}" onclick="copyTxt(this)">copiar</button></div></div></div>
      <div class="red-bloque"><div class="red-cab"><b>Texto para TikTok</b><span class="muted red-consejo">título y descripción</span></div>
        <div class="red-caja"><div class="red-texto">${esc(c.texto_tiktok || '')}</div>
          <div class="red-pie"><span class="red-cuenta">${[...(c.texto_tiktok || '')].length}/2200</span>
            <button type="button" class="btn ghost sm red-copiar" data-txt="${esc(c.texto_tiktok || '')}" onclick="copyTxt(this)">copiar</button></div></div></div>
    </div>
  </section>`;
}

// ------------------------------------------------------------------ ver: formato y diapositiva
function carruselFmt(jid, fmt){
  const st = _CAR[jid]; if(!st) return;
  st.fmt = fmt; st.idx = 0;
  carruselRepinta(jid);
}
function carruselVer(jid, i){
  const st = _CAR[jid]; if(!st) return;
  st.idx = i;
  carruselRepinta(jid);
  const v = document.getElementById('car-visor-' + jid); if(v) v.focus({preventScroll: true});
}
function carruselIr(jid, d){
  const st = _CAR[jid]; if(!st || !st.r || !st.r.carrusel) return;
  const n = ((st.r.carrusel.archivos || {})[st.fmt === 'tiktok' ? 'tiktok' : 'ig'] || []).length;
  st.idx = Math.min(Math.max(0, (st.idx || 0) + d), Math.max(0, n - 1));
  carruselRepinta(jid);
  const v = document.getElementById('car-visor-' + jid); if(v) v.focus({preventScroll: true});
}
function carruselTecla(ev, jid){
  if(ev.key === 'ArrowRight'){ ev.preventDefault(); carruselIr(jid, 1); }
  else if(ev.key === 'ArrowLeft'){ ev.preventDefault(); carruselIr(jid, -1); }
}

// ------------------------------------------------------------------ crear / otro
async function carruselCrea(jid, btn, rehacer){
  if(rehacer && !confirm('¿Hacer otro carrusel? El de ahora se sustituye, y también lo que hayas editado a mano (1 petición a Flash-Lite).')) return;
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/carrusel/' + encodeURIComponent(jid) + '/crear', {method: 'POST'})).json();
    if(!r.ok){ alert(r.error || 'No se pudo crear el carrusel.'); btn.disabled = false; return; }
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); btn.disabled = false; return; }
  const st = _CAR[jid]; if(st){ st.editando = false; st.idx = 0; }
  carruselPinta(jid);
}

// ------------------------------------------------------------------ editar (vuelve a dibujar SIN IA)
function carruselEditar(jid){
  const st = _CAR[jid]; if(!st) return;
  st.editando = !st.editando;
  carruselRepinta(jid);
  if(st.editando){ const e = document.getElementById('car-ed-' + jid); if(e) e.scrollIntoView({behavior: 'smooth', block: 'nearest'}); }
}

function carruselEditorHTML(jid, c, r){
  const J = esc(jid);
  r = r || {};
  const por = c.portada || {}, cie = c.cierre || {};
  const campo = (id, etiqueta, valor, max, area) => `<label class="car-campo"><span>${etiqueta}</span>${area
    ? `<textarea id="${id}" rows="2" maxlength="${max}">${esc(valor || '')}</textarea>`
    : `<input type="text" id="${id}" maxlength="${max}" value="${esc(valor || '')}">`}</label>`;
  const pasos = (c.pasos || []).map((s, i) => `
    <div class="car-ed-paso" data-id="${esc(s.id)}">
      <div class="car-ed-cab"><label><input type="checkbox" class="ed-quedar" checked> <b>Paso ${i + 1}</b> (desmarca para quitarlo)</label></div>
      <div class="car-ed-cuerpo">
        <div class="car-ed-textos">
          ${campo(`ed-t-${i}`, 'Título', s.titulo, 70, false)}
          ${campo(`ed-x-${i}`, 'Frase', s.texto, 200, true)}
        </div>
        ${c.texto_mode ? carruselImagenTextoHTML(jid, c, r, s, i) : `<div class="car-ed-cap">
          ${s.captura ? `<img id="ed-img-${i}" src="/carrusel/${encodeURIComponent(jid)}/capturas/${encodeURIComponent(s.captura)}?v=${Math.floor(c.ts_edicion || c.ts || 0)}" alt="captura">` : `<div class="car-sincap" id="ed-img-${i}">sin captura</div>`}
          <label class="car-campo"><span>Segundo de la captura <span class="muted">(vídeo de ${Math.round(c.duracion || 0)} s)</span></span>
            <input type="number" step="0.1" min="0.5" id="ed-s-${i}" value="${s.t_captura != null ? s.t_captura : ''}" placeholder="auto"></label>
          <div class="car-ed-bot">
            <button type="button" class="btn ghost sm" onclick="carruselPrevia('${J}', ${i})">👁 Ver ese segundo</button>
            <label><input type="checkbox" class="ed-sincap" ${s.captura ? '' : 'checked'}> sin captura</label>
          </div>
        </div>`}
      </div>
    </div>`).join('');
  return `<div class="car-editor" id="car-ed-${J}">
    <div class="pro-ab-label">✏ EDITAR EL CARRUSEL <span class="muted">· se vuelve a dibujar con lo que cambies, sin gastar IA</span></div>
    <div class="car-ed-grupo"><b>Portada</b>${campo('ed-p-t', 'Título', por.titulo, 90, false)}${campo('ed-p-s', 'Subtítulo', por.subtitulo, 110, false)}</div>
    ${pasos}
    <div class="car-ed-grupo"><b>Cierre</b>${campo('ed-c-t', 'Título', cie.titulo, 50, false)}${campo('ed-c-x', 'Texto', cie.texto, 130, true)}</div>
    <div class="car-ed-grupo"><b>Publicaciones</b>
      ${campo('ed-ig', 'Texto de Instagram (los hashtags se añaden solos)', c.pie_instagram, 2000, true)}
      ${campo('ed-tt-t', 'Título de TikTok', c.tiktok_titulo, 90, false)}
      ${campo('ed-tt-x', 'Descripción de TikTok', c.tiktok_texto, 300, true)}</div>
    <div class="art-acciones">
      <button type="button" class="btn" onclick="carruselAplica('${J}', this)">✔ Volver a dibujar con mis cambios</button>
      <button type="button" class="btn ghost" onclick="carruselEditar('${J}')">Cancelar</button>
    </div>
    <div id="car-ed-msg-${J}" class="muted" style="font-size:13px;min-height:20px"></div>
  </div>`;
}

// De un artículo o un texto: cada paso lleva una imagen del propio artículo, un símbolo de tu biblioteca o nada (no hay vídeo: no hay «segundos»)
function carruselImagenTextoHTML(jid, c, r, s, i){
  const tipo = {articulo: 'una imagen del artículo', simbolo: 'un símbolo'}[s.imagen_tipo] || 'solo texto';
  const arts = (r.imagenes_fuente || []).map(m => `<option value="art:${esc(m.archivo)}">${esc(m.archivo)}${m.alt ? ' · ' + esc(m.alt).slice(0, 40) : ''}</option>`).join('');
  const sims = (r.simbolos || []).map(m => `<option value="sim:${esc(m.clave)}">${esc(m.nombre)}</option>`).join('');
  const actual = s.captura ? `<img id="ed-img-${i}" src="/carrusel/${encodeURIComponent(jid)}/capturas/${encodeURIComponent(s.captura)}?v=${Math.floor(c.ts_edicion || c.ts || 0)}" alt="imagen">`
                           : `<div class="car-sincap" id="ed-img-${i}">sin imagen</div>`;
  const urls = JSON.stringify(Object.fromEntries((r.imagenes_fuente || []).map(m => ['art:' + m.archivo, m.url]))).replace(/"/g, '&quot;');
  return `<div class="car-ed-cap">${actual}
    <label class="car-campo"><span>Imagen de este paso <span class="muted">(ahora: ${tipo})</span></span>
      <select id="ed-im-${i}" data-urls="${urls}" onchange="carruselImagenElige(${i}, this)">
        <option value="conservar">Dejarla como está</option><option value="ninguna">Sin imagen (solo texto)</option>
        ${arts ? `<optgroup label="Imágenes del artículo">${arts}</optgroup>` : ''}${sims ? `<optgroup label="Símbolos (biblioteca de ilustraciones)">${sims}</optgroup>` : ''}
      </select></label></div>`;
}

function carruselImagenElige(i, sel){
  const img = document.getElementById('ed-img-' + i);
  let urls = {}; try{ urls = JSON.parse(sel.dataset.urls || '{}'); }catch(e){}
  if(img && img.tagName === 'IMG' && urls[sel.value]) img.src = urls[sel.value];
}

async function carruselPrevia(jid, i){
  const t = parseFloat((document.getElementById('ed-s-' + i) || {}).value);
  const img = document.getElementById('ed-img-' + i);
  if(!img || !(t >= 0)){ alert('Escribe primero el segundo del vídeo que quieres ver.'); return; }
  try{
    const r = await (await fetch(`/api/carrusel/${encodeURIComponent(jid)}/previa?t=${encodeURIComponent(t)}`)).json();
    if(!r.ok){ alert(r.error || 'No pude sacar ese fotograma.'); return; }
    if(img.tagName === 'IMG') img.src = r.url + '?r=' + Date.now();
    else { const n = document.createElement('img'); n.id = img.id; n.src = r.url + '?r=' + Date.now(); n.alt = 'captura'; img.replaceWith(n); }
    const sin = img.closest('.car-ed-cap') && img.closest('.car-ed-cap').querySelector('.ed-sincap');
    if(sin) sin.checked = false;
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); }
}

async function carruselAplica(jid, btn){
  const val = id => ((document.getElementById(id) || {}).value || '').trim();
  const msg = document.getElementById('car-ed-msg-' + jid);
  const pasos = [];
  document.querySelectorAll(`#car-ed-${CSS.escape(jid)} .car-ed-paso`).forEach((el, i) => {
    if(!el.querySelector('.ed-quedar').checked) return;
    const s = parseFloat(val('ed-s-' + i));
    const sc = el.querySelector('.ed-sincap');
    pasos.push({id: el.dataset.id, titulo: val('ed-t-' + i), texto: val('ed-x-' + i), t_captura: isNaN(s) ? null : s,
                sin_captura: sc ? sc.checked : false, imagen: val('ed-im-' + i) || undefined});
  });
  if(pasos.length < 2){ msg.innerHTML = '<span style="color:var(--bad);font-weight:700">Deja al menos 2 pasos.</span>'; return; }
  const cambios = {portada: {titulo: val('ed-p-t'), subtitulo: val('ed-p-s')}, pasos, cierre: {titulo: val('ed-c-t'), texto: val('ed-c-x')},
                   pie_instagram: val('ed-ig'), tiktok_titulo: val('ed-tt-t'), tiktok_texto: val('ed-tt-x')};
  btn.disabled = true; msg.textContent = 'Enviando…';
  try{
    const r = await (await fetch(`/api/carrusel/${encodeURIComponent(jid)}/rehacer`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cambios)})).json();
    if(!r.ok){ msg.innerHTML = `<span style="color:var(--bad);font-weight:700">✖ ${esc(r.error || 'No se pudo.')}</span>`; btn.disabled = false; return; }
  }catch(e){ msg.innerHTML = `<span style="color:var(--bad);font-weight:700">✖ ${esc(e.message)}</span>`; btn.disabled = false; return; }
  const st = _CAR[jid]; if(st){ st.editando = false; st.idx = 0; }
  carruselPinta(jid);
}
