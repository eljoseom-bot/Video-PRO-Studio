/** 📣 POSTS (el autor, 2026-10-13): 1 o 2 imágenes sobre UNA idea (no un carrusel). Desde el 14-oct, UN tamaño por red: Instagram 4:5, LinkedIn 1,91:1, YouTube 1:1, Bluesky 16:9, Reddit 4:3, Discord 16:9
    (los posts de antes solo traen «ig» y «wide»). Se usa en dos sitios:
    la herramienta Tools → Posts (`postsPagina`, todos los posts) y el botón «Post» de «📣 Redes sociales» de cada vídeo (`postsMini`, los de ese vídeo). Ver clipper/posts.py.
    Usa esc() y copyTxt() de proinfo.js. */
const _PU = {fmt: {}, editando: {}, datos: {}, cont: [], timer: null};

// las redes, en el orden de las pestañas (clave = carpeta en disco); `c` = la clase del tamaño en pantalla (v = vertical, s = cuadrada, w = apaisada)
const REDES_POST = [
  {k: 'instagram', n: 'Instagram', d: '4:5', px: '1080×1350', c: 'v', t: 'texto Instagram'},
  {k: 'linkedin', n: 'LinkedIn', d: '1,91:1', px: '1200×627', c: 'w', t: 'texto LinkedIn'},
  {k: 'youtube', n: 'YouTube', d: '1:1', px: '1080×1080', c: 's', t: 'publicación de la comunidad de YouTube'},
  {k: 'bluesky', n: 'Bluesky', d: '16:9', px: '1200×675', c: 'w', t: 'post de Bluesky'},
  {k: 'reddit', n: 'Reddit', d: '4:3', px: '1440×1080', c: 'w', t: 'post de Reddit'},
  {k: 'discord', n: 'Discord', d: '16:9', px: '1600×900', c: 'w', t: 'aviso de Discord'},
  {k: 'ig', n: 'Instagram', d: '4:5', px: '1080×1350', c: 'v', t: 'texto Instagram'},
  {k: 'wide', n: 'LinkedIn · Bluesky · Discord · Reddit · YouTube', d: '16:9', px: '1600×900', c: 'w', t: 'texto para las demás redes'},
];

const ESTILO_NOMBRE = {clasico: 'Portada + idea', comic: 'Cómic B/N', antes_despues: 'Antes / Después', checklist: 'Checklist', dato: 'Dato grande', captura: 'Captura anotada',
  conversacion: 'Conversación', cartel: 'Cartel tipográfico', flujo: 'Diagrama de pasos', cita: 'Frase con tu cara', top3: 'Top 3'};
const PORTADA_NOMBRE = {cara: 'tu cara', logo: 'el logo', comic: 'tu retrato en cómic'};

function _postFecha(ts){
  return ts ? new Date(ts * 1000).toLocaleString('es', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
}

function _postCaja(titulo, texto, consejo){
  return `<div class="red-bloque"><div class="red-cab"><b>${titulo}</b><span class="muted red-consejo">${esc(consejo)}</span></div>
    <div class="red-caja"><div class="red-texto">${esc(texto)}</div>
      <div class="red-pie"><span class="red-cuenta">${[...texto].length}</span>
        <button type="button" class="btn ghost sm red-copiar" data-txt="${esc(texto)}" onclick="copyTxt(this)">copiar</button></div></div></div>`;
}

function postHTML(p){
  const id = p.jid + '/' + p.id, I = esc(p.jid), K = esc(p.id);
  const redes = REDES_POST.filter(r => (p.archivos || {})[r.k]);
  const red0 = redes.find(r => r.k === _PU.fmt[id]) || redes[0] || REDES_POST[0];
  const dm = (p.dims || {})[red0.k];                                    // (los estilos nuevos son cuadrados: 1080×1080 en todas las redes)
  const red = dm ? {...red0, px: dm[0] + '×' + dm[1], d: dm[0] === dm[1] ? '1:1' : red0.d, c: dm[0] === dm[1] ? 's' : red0.c} : red0;
  const fmt = red.k;
  const archivos = (p.archivos || {})[fmt] || [];
  const tab = r => `<button type="button" class="${fmt === r.k ? 'on' : ''}" onclick="postFmt('${I}', '${K}', '${r.k}')">${r.n} <span class="muted">${((p.dims || {})[r.k] || [0, 1])[0] === ((p.dims || {})[r.k] || [0, 0])[1] ? '1:1' : r.d}</span></button>`;
  const imgs = archivos.map(f => `<a href="${p.base}/${fmt}/${f}?descargar=1" title="Descargar esta imagen"><img class="post-img post-${red.c}" loading="lazy" src="${p.base}/${fmt}/${f}?v=${p.v}" alt="Imagen del post"></a>`).join('');
  const tx = p.textos || {};
  const texto = (tx.redes || {})[fmt] || (fmt === 'ig' ? tx.instagram : tx.corto) || '';
  const portada = PORTADA_NOMBRE[p.portada] || p.portada;
  const base = `/api/posts/${encodeURIComponent(p.jid)}/${encodeURIComponent(p.id)}`;
  const ed = _PU.editando[id] ? postEditorHTML(p) : '';
  return `<section class="card post-card" id="post-${esc(p.id)}" style="margin-bottom:16px">
    <div style="display:flex;gap:8px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap">
      <div><b>${esc(p.gancho)}</b><div class="muted" style="font-size:12px">${esc(p.fuente_titulo || '')} · estilo «${esc(ESTILO_NOMBRE[p.estilo || 'clasico'] || p.estilo)}» · ${p.imagenes} imagen${p.imagenes > 1 ? 'es' : ''}${(p.estilo || 'clasico') === 'clasico' ? ' · portada con ' + esc(portada) : ''}${_postFecha(p.ts) ? ' · ' + _postFecha(p.ts) : ''}</div></div>
      <button type="button" class="btn ghost sm" onclick="postBorra('${I}', '${K}')" title="Borra este post del PC (no toca tus redes)">🗑</button>
    </div>
    ${(p.avisos || []).map(x => `<div class="callout warn" style="margin:8px 0 0">⚠ ${esc(x)}</div>`).join('')}
    <div class="fuente-tabs car-tabs" style="margin-top:10px">${redes.map(tab).join('')}</div>
    <div class="muted" style="font-size:12px;margin-top:6px">${esc(red.n)} · ${red.px} px (${red.d})</div>
    <div class="post-imgs">${imgs}</div>
    <div class="art-acciones">
      <a class="btn descargar sm" href="${base}/zip/${fmt}" title="Las imágenes de esta red, en su tamaño, y su texto">⬇ Descargar ${esc(red.n.split(' · ')[0])} (.zip)</a>
      <a class="btn ghost sm" href="${base}/zip/todo" title="Un .zip con UNA CARPETA por red (Instagram, LinkedIn, YouTube…), cada una con sus imágenes y su texto.txt">⬇ Todo, una carpeta por red (.zip)</a>
      <button type="button" class="btn ghost sm" onclick="postCarpeta('${I}', '${K}', this)" title="Lo mismo, pero sin comprimir: crea la carpeta en Descargas/Posts Video PRO y la abre">📂 Guardar en una carpeta</button>
      <button type="button" class="btn ghost sm" onclick="postEditar('${I}', '${K}')">${_PU.editando[id] ? '✕ Cerrar el editor' : '✏ Editar'}</button>
      <select class="btn ghost sm" title="Cambia el estilo gráfico de este post (sin gastar IA)" onchange="if(this.value) postRehaz('${I}', '${K}', {estilo: this.value}, this)">
        <option value="">🎨 Estilo: ${esc(ESTILO_NOMBRE[p.estilo || 'clasico'] || '')}</option>${(p.estilos || []).filter(e => e.id !== (p.estilo || 'clasico')).map(e => `<option value="${esc(e.id)}">${esc(e.nombre)}</option>`).join('')}
      </select>
      <button type="button" class="btn ghost sm" onclick="postRehaz('${I}', '${K}', {estilo: 'azar'}, this)" title="Otro estilo gráfico al azar entre los que sirven para esta idea: no gasta IA">🎲 Otro estilo</button>
      ${(p.estilo || 'clasico') === 'clasico' ? `<button type="button" class="btn ghost sm" onclick="postRehaz('${I}', '${K}', {portada: 'azar'}, this)" title="Otra portada al azar (tu cara, el logo o tu retrato en cómic), con la misma idea: no gasta IA">↻ Otra portada</button>` : ''}
      <button type="button" class="btn ghost sm" onclick="postOtro('${I}', this)" title="Otra idea de la misma fuente (1 petición a Flash-Lite)">↻ Otro post</button>
    </div>
    ${ed}
    <div class="red-grid car-textos">
      ${_postCaja('Texto para ' + red.t.replace(/^texto /, '').replace(/^(post|aviso|publicación) de /, ''), texto, fmt === 'bluesky' ? 'cabe en 300 caracteres, con el enlace' : (fmt === 'reddit' ? 'con su título' : (fmt === 'instagram' || fmt === 'ig' ? 'con los hashtags' : 'con el enlace si ya lo hay')))}
    </div>
  </section>`;
}

function postEditorHTML(p){
  const I = esc(p.jid), K = esc(p.id), idp = esc(p.id);
  const c = (id, et, v, max, area) => `<label class="car-campo"><span>${et}</span>${area ? `<textarea id="${id}-${idp}" rows="2" maxlength="${max}">${esc(v || '')}</textarea>`
    : `<input type="text" id="${id}-${idp}" maxlength="${max}" value="${esc(v || '')}">`}</label>`;
  const pu = [0, 1, 2].map(i => c('pe-p' + i, 'Punto ' + (i + 1) + ' <span class="muted">(déjalo vacío para quitarlo; sin puntos sale 1 sola imagen)</span>', (p.puntos || [])[i], 70, false)).join('');
  return `<div class="car-editor"><div class="pro-ab-label">✏ EDITAR EL POST <span class="muted">· se vuelve a dibujar con lo que cambies, sin gastar IA</span></div>
    ${c('pe-g', 'Gancho (el título de la portada)', p.gancho, 70, false)}${c('pe-s', 'Subtítulo', p.subtitulo, 90, false)}${c('pe-i', 'La idea (segunda imagen)', p.idea, 140, true)}${pu}
    <label class="car-campo"><span>Portada</span><select id="pe-pt-${idp}"><option value="">Dejarla como está (${esc(PORTADA_NOMBRE[p.portada] || p.portada)})</option><option value="cara">Tu cara</option>
      <option value="logo">Tu logo</option><option value="comic">Tu retrato en cómic</option></select></label>
    ${c('pe-ig', 'Texto de Instagram (los hashtags se añaden solos)', p.pie_instagram, 2000, true)}${c('pe-tc', 'Texto para las demás redes', p.texto_corto, 400, true)}
    <div class="art-acciones"><button type="button" class="btn" onclick="postAplica('${I}', '${K}', this)">✔ Volver a dibujar con mis cambios</button>
      <button type="button" class="btn ghost" onclick="postEditar('${I}', '${K}')">Cancelar</button></div><div id="pe-msg-${idp}" class="muted" style="font-size:13px;min-height:20px"></div></div>`;
}

function _postRepinta(){
  _PU.cont = _PU.cont.filter(c => c.el && document.body.contains(c.el));      // (las zonas que ya no están en la página se olvidan)
  _PU.cont.forEach(c => c.pinta());
}

function postFmt(jid, pid, fmt){ _PU.fmt[jid + '/' + pid] = fmt; _postRepinta(); }
function postEditar(jid, pid){ const k = jid + '/' + pid; _PU.editando[k] = !_PU.editando[k]; _postRepinta(); }

async function postAplica(jid, pid, btn){
  const v = id => ((document.getElementById(id + '-' + pid) || {}).value || '').trim();
  const msg = document.getElementById('pe-msg-' + pid);
  const puntos = [0, 1, 2].map(i => v('pe-p' + i)).filter(Boolean);
  const cambios = {gancho: v('pe-g'), subtitulo: v('pe-s'), idea: v('pe-i'), puntos, pie_instagram: v('pe-ig'), texto_corto: v('pe-tc')};
  if(v('pe-pt')) cambios.portada = v('pe-pt');
  await postRehaz(jid, pid, cambios, btn, msg);
}

async function postRehaz(jid, pid, cambios, btn, msg){
  if(btn) btn.disabled = true;
  if(msg) msg.textContent = 'Dibujando…';
  try{
    const r = await (await fetch(`/api/posts/${encodeURIComponent(jid)}/${encodeURIComponent(pid)}/rehacer`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cambios)})).json();
    if(!r.ok){ if(msg) msg.innerHTML = `<span style="color:var(--bad);font-weight:700">✖ ${esc(r.error || 'No se pudo.')}</span>`; else alert(r.error || 'No se pudo.'); if(btn) btn.disabled = false; return; }
    _PU.editando[jid + '/' + pid] = false;
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); if(btn) btn.disabled = false; return; }
  _postCarga();
}

async function postCarpeta(jid, pid, btn){
  btn.disabled = true;
  const t0 = btn.textContent;
  btn.textContent = '⏳ Guardando…';
  try{
    const r = await (await fetch(`/api/posts/${encodeURIComponent(jid)}/${encodeURIComponent(pid)}/carpeta`, {method: 'POST'})).json();
    btn.textContent = r.ok ? '✔ Guardada y abierta' : '⚠ ' + (r.error || 'No se pudo');
    if(r.ok) btn.title = r.ruta;
  }catch(e){ btn.textContent = '⚠ Sin conexión con el panel'; }
  setTimeout(() => { btn.textContent = t0; btn.disabled = false; }, 4000);
}

async function postBorra(jid, pid){
  if(!confirm('¿Borrar este post del PC? (No toca tus redes.)')) return;
  const r = await (await fetch(`/api/posts/${encodeURIComponent(jid)}/${encodeURIComponent(pid)}/borrar`, {method: 'POST'})).json();
  if(r.ok) _postCarga(); else alert('No se pudo borrar.');
}

async function postOtro(jid, btn){
  if(!confirm('¿Hacer otro post de esta fuente con una idea distinta? (1 petición a Flash-Lite)')) return;
  await postCrea({jid, otro: true}, btn);
}

async function postCrea(cuerpo, btn, msgEl){
  if(btn) btn.disabled = true;
  if(msgEl) msgEl.textContent = 'Empezando…';
  try{
    const r = await (await fetch('/api/posts/crear', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cuerpo)})).json();
    if(!r.ok){ if(msgEl) msgEl.textContent = '⚠ ' + (r.error || 'No se pudo empezar.'); else alert(r.error || 'No se pudo hacer el post.'); if(btn) btn.disabled = false; return false; }
    if(msgEl) msgEl.textContent = '';
  }catch(e){ if(msgEl) msgEl.textContent = '⚠ Sin conexión con el panel.'; if(btn) btn.disabled = false; return false; }
  if(btn) btn.disabled = false;
  _postCarga();
  return true;
}

// ------------------------------------------------------------------ carga y sondeo (una sola vez para todas las zonas de la página)
async function _postCarga(){
  clearTimeout(_PU.timer);
  let r = null;
  try{ r = await (await fetch('/api/posts' + (_PU.jid ? '?jid=' + encodeURIComponent(_PU.jid) : ''))).json(); }catch(e){ return; }
  _PU.datos = r;
  _PU.recibido = Date.now();
  _postRepinta();
  if(r.estado === 'creando') _PU.timer = setTimeout(_postCarga, 2500);
}

// La barra de progreso del post: por etapa (preparar la fuente → la IA elige la idea → dibujar cada red) y, dentro de cada una, se llena en curva con el tiempo sin llegar al final antes de acabar.
function _postPct(r, seg){
  const paso = r.paso || '', ps = Math.max(0, (r.ahora || 0) - (r.paso_ts || r.ahora || 0));
  const k = REDES_POST.slice(0, 6).findIndex(x => paso.indexOf(x.n) >= 0 && paso.startsWith('Dibujando'));
  if(k >= 0) return Math.min(97, 52 + 45 * (k + 1 - Math.exp(-ps / 3)) / 6);
  if(paso.startsWith('Eligiendo')) return Math.min(50, 14 + 36 * (1 - Math.exp(-ps / 22)));
  return Math.min(13, 2 + 11 * (1 - Math.exp(-seg / 90)));                  // preparar la fuente (descargar y transcribir un vídeo, leer un artículo)
}
function _postBarra(){
  const r = _PU.datos;
  if(!r || r.estado !== 'creando'){ clearInterval(_PU.tick); _PU.tick = null; return; }
  const adv = (Date.now() - (_PU.recibido || Date.now())) / 1000;
  const rr = {...r, ahora: (r.ahora || 0) + adv};
  const seg = Math.max(0, Math.round(rr.ahora - (r.inicio || rr.ahora)));
  const pct = Math.round(_postPct(rr, seg));
  document.querySelectorAll('.post-pbar>i').forEach(b => { b.style.width = pct + '%'; });
  document.querySelectorAll('.post-ptxt').forEach(t => {
    t.innerHTML = `<b>${pct} %</b> · ${esc(r.paso || 'Haciendo el post…')} <span class="muted">· ${Math.floor(seg / 60)}:${String(seg % 60).padStart(2, '0')} · suele tardar 1-2 min</span>`;
  });
}

function _postEstadoHTML(r, jid){
  if(r.estado === 'creando' && (!jid || r.jid_en_marcha === jid)){
    if(!_PU.tick) _PU.tick = setInterval(_postBarra, 1000);
    setTimeout(_postBarra, 0);
    return `<div class="art-proc"><div class="prog-bar post-pbar"><i></i></div><div class="prog-txt post-ptxt">⏳ ${esc(r.paso || 'Haciendo el post…')}</div></div>`;
  }
  if(r.error && (!jid || r.jid_en_marcha === jid || !r.jid_en_marcha)) return `<div class="callout bad" style="margin:0 0 10px">✖ ${esc(r.error)}</div>`;
  return '';
}

// «📣 Post» dentro de «📣 Redes sociales» de cada vídeo (manual, desde su transcripción)
function postsMini(jid, el){
  if(!el || el.dataset.postsOk === '1') return;
  el.dataset.postsOk = '1';
  const zona = {el, pinta(){
    const r = _PU.datos || {items: []};
    const mios = (r.items || []).filter(p => p.jid === jid);
    el.innerHTML = `<div class="red-bloque" style="margin-top:14px"><div class="red-cab"><span class="red-ico">📣</span><b>Post: una sola idea, 1 o 2 imágenes</b>
        <span class="muted red-consejo">11 estilos (la IA elige el que mejor encaja, con algo de azar) · una carpeta por red</span></div>
      <p class="muted" style="margin:6px 0 10px;font-size:13px">Saco la idea más valiosa de este vídeo (de su transcripción) y la dibujo con tu estilo; la portada va con tu cara, el logo o tu retrato en cómic. 1 petición a Flash-Lite; solo cuando tú lo pides.</p>
      ${_postEstadoHTML(r, jid)}
      <button type="button" class="btn" onclick="postCrea({jid: '${esc(jid)}'}, this)">📣 ${mios.length ? 'Otro post de este vídeo' : 'Crear un post de este vídeo'}</button>
      <div style="margin-top:12px">${mios.map(postHTML).join('')}</div></div>`;
  }};
  _PU.cont.push(zona);
  _postCarga();
}

// la herramienta Tools → Posts
function postsPagina(){
  _PU.jid = null;
  const lista = document.getElementById('posts-lista'), est = document.getElementById('posts-estado');
  _PU.cont.push({el: lista, pinta(){
    const r = _PU.datos || {items: []};
    est.innerHTML = _postEstadoHTML(r, null);
    lista.innerHTML = (r.items || []).length ? r.items.map(postHTML).join('') : '<p class="muted">Aún no has hecho ningún post.</p>';
    const sel = document.getElementById('po-video');
    if(sel && sel.options.length <= 1) (r.fuentes || []).forEach(f => sel.insertAdjacentHTML('beforeend', `<option value="${esc(f.jid)}">${esc(f.titulo)}</option>`));
  }});
  _postCarga();
}
