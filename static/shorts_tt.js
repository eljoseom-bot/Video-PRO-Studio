/** 🎬 SHORTS · TIKTOK (Tools): subir un vídeo o «enviar» un Short de un vídeo horizontal → un Short vertical muy editado + los textos de YouTube, TikTok e Instagram. Ver clipper/shorts_tt.py.
    Usa esc(), copyTxt() y progBarHTML() de proinfo.js. */
const _S = {datos: null, timer: null, recibido: 0, tick: null, tab: 'subir', subiendo: false, yt: {}};
const $s = id => document.getElementById(id);

function sttTab(k){
  _S.tab = k;
  ['subir', 'video'].forEach(x => { $s('stt-p-' + x).style.display = x === k ? '' : 'none'; $s('stt-t-' + x).classList.toggle('on', x === k); });
}

function sttInicio(){ sttCarga(); }

async function sttCarga(){
  clearTimeout(_S.timer);
  let r = null;
  try{ r = await (await fetch('/api/shorts-tt')).json(); }catch(e){ return; }
  _S.datos = r; _S.recibido = Date.now();
  const sel = $s('stt-canal');
  if(sel && !sel.options.length) (r.canales || []).forEach(c => sel.insertAdjacentHTML('beforeend', `<option value="${esc(c.id)}"${c.id === r.canal_defecto ? ' selected' : ''}>${esc(c.nombre)}${c.camara ? '' : ' (sin cámara medida)'}</option>`));
  sttPinta();
  const sube = (r.items || []).some(i => i.subiendo !== null && i.subiendo !== undefined);
  if(r.estado === 'creando' || sube) _S.timer = setTimeout(sttCarga, 2000);
  else if(typeof cuotasRefresca === 'function') cuotasRefresca();
}

function _sttBarra(pct, texto, desde){
  const seg = Math.max(0, Math.round((Date.now() / 1000) - (desde || Date.now() / 1000)));
  return `<div class="art-proc"><div class="prog-bar"><i style="width:${Math.max(2, Math.min(100, pct))}%"></i></div><div class="prog-txt"><b>${Math.round(pct)} %</b> · ${esc(texto)}`
    + `${desde ? ` <span class="muted">· ${Math.floor(seg / 60)}:${String(seg % 60).padStart(2, '0')}</span>` : ''}</div></div>`;
}

function sttPinta(){
  const r = _S.datos || {items: []};
  // el avance del Short en marcha
  const est = $s('stt-estado');
  if(r.estado === 'creando') est.innerHTML = `<section class="card" style="margin-bottom:16px"><b>🎬 Haciendo el Short…</b>${_sttBarra(r.pct || 0, r.paso || 'Trabajando…', r.inicio)}`
    + `<div class="muted" style="font-size:12.5px">Puedes dejar esta página abierta o irte a otra pestaña: sigue en tu PC. Un Short de 1-2 min suele tardar unos minutos (transcribir + montar).</div></section>`;
  else est.innerHTML = '';
  // las fuentes: Shorts de tus vídeos horizontales
  const fu = $s('stt-fuentes');
  if(fu){
    const f = r.fuentes || [];
    fu.innerHTML = f.length ? f.map(x => `<div class="stt-fila"><div><b>${esc(x.titulo || x.archivo)}</b><div class="muted" style="font-size:12px">${Math.round(x.duracion || 0)} s · de «${esc(x.video || '')}»${x.original ? '' : ' · ⚠ su grabación original ya no está en el PC'}</div></div>`
      + `<button type="button" class="btn sm" ${x.original && r.estado !== 'creando' ? '' : 'disabled'} onclick="sttEnviar('${esc(x.jid)}', '${esc(x.archivo)}', this)">🎬 Hacerlo aquí</button></div>`).join('')
      : '<p class="muted">Aún no hay Shorts en tus vídeos. Sube un vídeo en la otra pestaña.</p>';
  }
  const lista = $s('stt-lista');
  lista.innerHTML = (r.items || []).length ? r.items.filter(i => !(i.estado === 'creando' && r.estado === 'creando' && i.id === r.sid)).map(sttCard).join('') : (r.estado === 'creando' ? '' : '<p class="muted">Aún no has hecho ningún Short.</p>');
}

function _sttCaja(titulo, texto, consejo){
  return `<div class="red-bloque"><div class="red-cab"><b>${titulo}</b><span class="muted red-consejo">${esc(consejo)}</span></div><div class="red-caja"><div class="red-texto">${esc(texto)}</div>`
    + `<div class="red-pie"><span class="red-cuenta">${[...texto].length}</span><button type="button" class="btn ghost sm red-copiar" data-txt="${esc(texto)}" onclick="copyTxt(this)">copiar</button></div></div></div>`;
}

function sttCard(it){
  const I = esc(it.id);
  const cab = `<div style="display:flex;gap:8px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;margin-bottom:8px"><div><b>${esc((it.meta || {}).titulo_yt || it.origen.titulo_fuente || it.origen.archivo || 'Short')}</b>`
    + `<div class="muted" style="font-size:12px">${esc(it.origen.tipo === 'video' ? 'De un vídeo tuyo' : 'Subido')}: ${esc(it.origen.archivo || '')}${it.dur ? ' · ' + Math.round(it.dur) + ' s' : ''}</div></div>`
    + `<button type="button" class="btn ghost sm" onclick="sttBorra('${I}')" title="Borra este Short del PC (no toca tu vídeo de origen)">🗑</button></div>`;
  if(it.estado === 'error') return `<section class="card" style="margin-bottom:16px">${cab}<div class="callout bad" style="margin:0">✖ ${esc(it.error || 'No se pudo hacer.')}</div>`
    + `<div class="art-acciones" style="margin-top:8px"><button type="button" class="btn sm" onclick="sttRehaz('${I}', this)">↻ Intentar otra vez</button></div></section>`;
  if(it.estado !== 'listo') return `<section class="card" style="margin-bottom:16px">${cab}${_sttBarra(it.pct || 0, it.paso || 'En cola…')}</section>`;
  const t = it.textos || {}, yt = t.youtube || {}, tt = t.tiktok || {}, ig = t.instagram || {}, e = it.eventos || {};
  const por = e.por_tipo || {}, nombres = {palabra: 'palabras clave', tarjeta: 'tarjetas', ilus: 'ilustraciones', escena: 'escenas a pantalla completa', zoom_pantalla: 'acercamientos a la pantalla', zoom_cara: 'acercamientos a la cara', flash: 'destellos'};
  const chips = Object.keys(por).map(k => `<span class="stt-chip">${por[k]} ${esc(nombres[k] || k)}</span>`).join('');
  const ytEst = it.yt || {};
  const subir = it.subiendo !== null && it.subiendo !== undefined
    ? _sttBarra(it.subiendo || 0, 'Subiendo a YouTube…')
    : (ytEst.estado === 'subido' ? `<a class="btn ghost sm" href="${esc(ytEst.url)}" target="_blank" rel="noopener">▶ En YouTube (${esc(ytEst.privacidad === 'public' ? 'público' : ytEst.privacidad === 'unlisted' ? 'no listado' : 'privado')})</a>`
      : `<span class="yt-sub"><select id="stt-priv-${I}" class="btn ghost sm" title="Cómo queda en YouTube"><option value="private">Privado (lo miras en Studio)</option><option value="unlisted">No listado</option><option value="public">Público</option></select> `
        + `<button type="button" class="btn sm" onclick="sttSubirYT('${I}', this)" title="Cuesta ~${(_S.datos || {}).coste_yt || 1600} unidades de la cuota de YouTube de hoy (de 10.000). Solo si tú lo pulsas.">⬆ Subir a YouTube</button></span>${ytEst.estado === 'error' ? `<div class="callout bad" style="margin:6px 0 0">✖ ${esc(ytEst.error)}</div>` : ''}`);
  return `<section class="card" style="margin-bottom:16px"><div class="stt-card"><div><video controls preload="metadata" playsinline poster="${esc(it.poster || '')}?v=${it.v}" src="${esc(it.url)}?v=${it.v}"></video></div><div>${cab}`
    + `<div class="muted" style="font-size:13px">Quité <b>${Math.round(it.quitado_s || 0)} s</b> de silencio (${it.n_cortes || 0} cortes) · ${e.n || 0} cosas que pasan en pantalla (el hueco más largo sin nada: ${(e.hueco_max || 0).toFixed(1)} s) · ${esc(it.modo === 'camara' ? 'tu cara arriba y la pantalla sin la cámara' : it.modo === 'vertical' ? 'vídeo ya vertical' : 'sin cámara medida')}${it.modelo ? ' · IA: ' + esc(it.modelo) : ''}</div>`
    + `<div class="stt-chips">${chips}</div>${(it.avisos || []).map(x => `<div class="callout warn" style="margin:6px 0">⚠ ${esc(x)}</div>`).join('')}`
    + `<div class="art-acciones" style="margin:8px 0"><a class="btn descargar sm" href="${esc(it.url)}?descargar=1">⬇ Descargar el Short</a> <button type="button" class="btn ghost sm" onclick="sttRehaz('${I}', this)" title="Otra edición del mismo vídeo (otra petición a la IA y otro montaje; reusa la transcripción)">↻ Otra edición</button> ${subir}</div>`
    + `<div class="red-grid car-textos">${_sttCaja('▶ YouTube · título', yt.titulo || '', '≤ 100 caracteres')}${_sttCaja('▶ YouTube · descripción', yt.descripcion || '', 'con #Shorts')}${_sttCaja('▶ YouTube · tags', yt.tags || '', 'separados por comas')}`
    + `${_sttCaja('♪ TikTok', tt.texto || '', 'texto + hashtags')}${_sttCaja('◎ Instagram (Reels)', ig.texto || '', 'texto + hashtags')}</div></div></div></section>`;
}

async function sttSubir(){
  const f = $s('stt-archivo').files[0], msg = $s('stt-msg');
  if(!f){ msg.textContent = '⚠ Elige primero el vídeo.'; return; }
  if(_S.subiendo) return;
  _S.subiendo = true;
  const btn = $s('stt-crear'); btn.disabled = true;
  const fd = new FormData();
  fd.append('video', f); fd.append('canal', $s('stt-canal').value);
  await new Promise(res => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/shorts-tt/subir');
    x.upload.onprogress = ev => { if(ev.lengthComputable) msg.innerHTML = _sttBarra(Math.min(99, ev.loaded * 100 / ev.total), `Subiendo el vídeo a tu PC… ${(ev.loaded / 1048576).toFixed(0)} de ${(ev.total / 1048576).toFixed(0)} MB`); };
    x.onload = () => {
      let r = {};
      try{ r = JSON.parse(x.responseText); }catch(e){}
      msg.innerHTML = r.ok ? '' : `<span style="color:var(--bad);font-weight:700">✖ ${esc(r.error || 'No se pudo.')}</span>`;
      res();
    };
    x.onerror = () => { msg.textContent = '⚠ Sin conexión con el panel.'; res(); };
    x.send(fd);
  });
  _S.subiendo = false; btn.disabled = false;
  sttCarga();
}

async function sttEnviar(jid, archivo, btn){
  btn.disabled = true;
  const msg = $s('stt-msg');
  try{
    const r = await (await fetch('/api/shorts-tt/desde-video', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({jid, archivo, canal: $s('stt-canal').value})})).json();
    msg.innerHTML = r.ok ? '' : `<span style="color:var(--bad);font-weight:700">✖ ${esc(r.error || 'No se pudo.')}</span>`;
  }catch(e){ msg.textContent = '⚠ Sin conexión con el panel.'; }
  btn.disabled = false;
  sttCarga();
}

async function sttRehaz(id, btn){
  if(!confirm('¿Otra edición de este vídeo? (1 petición a Gemini; reusa la transcripción)')) return;
  btn.disabled = true;
  try{
    const r = await (await fetch(`/api/shorts-tt/${id}/rehacer`, {method: 'POST'})).json();
    if(!r.ok) alert(r.error || 'No se pudo.');
  }catch(e){ alert('No se pudo hablar con el panel.'); }
  sttCarga();
}

async function sttBorra(id){
  if(!confirm('¿Borrar este Short del PC? (No toca tu vídeo de origen ni lo que ya subiste.)')) return;
  const r = await (await fetch(`/api/shorts-tt/${id}/borrar`, {method: 'POST'})).json();
  if(!r.ok) alert(r.error || 'No se pudo borrar.');
  sttCarga();
}

async function sttSubirYT(id, btn){
  const priv = ($s('stt-priv-' + id) || {}).value || 'private';
  const coste = (_S.datos || {}).coste_yt || 1600;
  if(!confirm(`¿Subir este Short a YouTube (${priv === 'public' ? 'PÚBLICO' : priv === 'unlisted' ? 'no listado' : 'privado'})?\n\nGasta unas ${coste} unidades de la cuota de hoy (de 10.000). Nunca se sube solo.`)) return;
  btn.disabled = true;
  try{
    const r = await (await fetch(`/api/shorts-tt/${id}/subir-yt`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({privacidad: priv})})).json();
    if(!r.ok) alert(r.error || 'No se pudo.');
  }catch(e){ alert('No se pudo hablar con el panel.'); }
  setTimeout(sttCarga, 600);
}
