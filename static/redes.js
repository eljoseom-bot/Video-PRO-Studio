/** 📣 REDES SOCIALES (el autor, 2026-09-27): debajo del artículo de cada Vídeo PRO. Una publicación para cada red, en tu voz y con
    el estilo de esa red, para copiar y pegar (paso 1: nada se publica solo). UNA petición a Flash-Lite para todas. El enlace
    va como {ENLACE} y aquí se cambia por el del vídeo publicado (si aún no está subido, lo dice). La primera es la de YouTube ·
    Publicaciones (29-sep-2026): la API de YouTube no deja crearla sola, se pega en YouTube Studio. Ver clipper/redes.py.
    Usa esc() y copyTxt() de proinfo.js. */
const _RED_T = {};
const RED_ICONOS = {
  youtube: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="1" y="4.5" width="22" height="15" rx="4.5" fill="#FF0000"/><path d="M10 8.9l5.6 3.1-5.6 3.1z" fill="#fff"/></svg>`,
  linkedin: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5" fill="#0A66C2"/><rect x="5" y="9.5" width="3" height="9" fill="#fff"/><circle cx="6.5" cy="6.6" r="1.8" fill="#fff"/><path d="M10.5 9.5h2.9v1.3c.5-.9 1.6-1.6 3.1-1.6 2.6 0 3 1.7 3 3.8v5.5h-3v-4.9c0-1.1-.1-2.1-1.4-2.1s-1.6 1-1.6 2.1v4.9h-3z" fill="#fff"/></svg>`,
  bluesky: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5" fill="#1185FE"/><path d="M12 11.4C11 9.4 8.3 5.8 5.9 4.4 4.3 3.5 4 4.3 4 5.3c0 .9.5 4.4.8 5 .9 2.2 3.2 2.5 5.3 2.2-3.3.5-5.3 1.9-2.4 5.2 3 3 4.1-.7 4.3-2.5.2 1.8 1.1 5.5 4.3 2.5 2.9-3.3.9-4.7-2.4-5.2 2.1.3 4.4 0 5.3-2.2.3-.6.8-4.1.8-5 0-1-.3-1.8-1.9-.9C15.7 5.8 13 9.4 12 11.4z" fill="#fff"/></svg>`,
  reddit: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#FF4500"/><ellipse cx="12" cy="14.2" rx="6.4" ry="4.4" fill="#fff"/><circle cx="17.6" cy="10.4" r="1.6" fill="#fff"/><circle cx="6.4" cy="10.4" r="1.6" fill="#fff"/><circle cx="9.7" cy="13.6" r="1.1" fill="#FF4500"/><circle cx="14.3" cy="13.6" r="1.1" fill="#FF4500"/><path d="M9.6 16.3c1.4.9 3.4.9 4.8 0" stroke="#FF4500" stroke-width=".9" fill="none" stroke-linecap="round"/><path d="M12 9.8l1-4.3 3.1.7" stroke="#fff" stroke-width="1" fill="none"/><circle cx="16.8" cy="6.2" r="1.2" fill="#fff"/></svg>`,
  discord: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5" fill="#5865F2"/><path d="M7.2 7.4c1.4-.7 2.9-1 4.8-1s3.4.3 4.8 1c1.1 1.8 1.9 4 1.9 7.2-1.2 1-2.6 1.7-4 2.1l-.8-1.3c.6-.2 1.2-.5 1.7-.9-2.3 1.1-5 1.1-7.3 0 .5.4 1.1.7 1.7.9l-.8 1.3c-1.4-.4-2.8-1.1-4-2.1 0-3.2.8-5.4 2-7.2z" fill="#fff"/><ellipse cx="9.6" cy="12.2" rx="1.3" ry="1.5" fill="#5865F2"/><ellipse cx="14.4" cy="12.2" rx="1.3" ry="1.5" fill="#5865F2"/></svg>`,
  instagram: `<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="igg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#FEDA75"/><stop offset=".35" stop-color="#FA7E1E"/><stop offset=".6" stop-color="#D62976"/><stop offset="1" stop-color="#4F5BD5"/></linearGradient></defs><rect width="24" height="24" rx="6" fill="url(#igg)"/><rect x="5" y="5" width="14" height="14" rx="4.2" stroke="#fff" stroke-width="1.8" fill="none"/><circle cx="12" cy="12" r="3.4" stroke="#fff" stroke-width="1.8" fill="none"/><circle cx="16.3" cy="7.7" r="1.1" fill="#fff"/></svg>`,
  tiktok: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5" fill="#111"/><path d="M13.6 5h2.3c.2 1.7 1.3 2.9 3 3.1v2.3c-1.1 0-2.1-.3-3-.9v4.8c0 2.6-2.1 4.4-4.5 4.4-2.4 0-4.4-2-4.4-4.4 0-2.6 2.3-4.6 5-4.3v2.4c-1.3-.3-2.6.6-2.6 1.9 0 1.1.9 2 2 2s2.1-.9 2.1-2.1z" fill="#25F4EE" transform="translate(-.6 -.4)"/><path d="M13.6 5h2.3c.2 1.7 1.3 2.9 3 3.1v2.3c-1.1 0-2.1-.3-3-.9v4.8c0 2.6-2.1 4.4-4.5 4.4-2.4 0-4.4-2-4.4-4.4 0-2.6 2.3-4.6 5-4.3v2.4c-1.3-.3-2.6.6-2.6 1.9 0 1.1.9 2 2 2s2.1-.9 2.1-2.1z" fill="#FE2C55" transform="translate(.6 .4)"/><path d="M13.6 5h2.3c.2 1.7 1.3 2.9 3 3.1v2.3c-1.1 0-2.1-.3-3-.9v4.8c0 2.6-2.1 4.4-4.5 4.4-2.4 0-4.4-2-4.4-4.4 0-2.6 2.3-4.6 5-4.3v2.4c-1.3-.3-2.6.6-2.6 1.9 0 1.1.9 2 2 2s2.1-.9 2.1-2.1z" fill="#fff"/></svg>`,
};
const RED_NOMBRE = {youtube: 'YouTube · Publicaciones', linkedin: 'LinkedIn', bluesky: 'Bluesky', reddit: 'Reddit', discord: 'Discord', instagram: 'Instagram', tiktok: 'TikTok'};
const RED_CONSEJO = {
  youtube: 'Pégala como publicación de tu canal en YouTube Studio y prográmala para la hora en que sale el vídeo (o unos minutos después: hasta entonces el enlace no se ve).',
  linkedin: 'El enlace va en el primer comentario: así LinkedIn no le quita alcance al post.',
  bluesky: 'Si son varios, publícalos como hilo (cada uno responde al anterior).',
  reddit: 'Mejor en tu r/tu-comunidad; en otros subreddits, solo si el texto aporta de verdad (Reddit castiga la autopromoción).',
  discord: 'En el canal de anuncios de tu servidor.',
  instagram: 'Sube el Short como Reel con este texto (el enlace va en tu perfil).',
  tiktok: 'Sube el Short con este texto.',
};

function redesSlot(jid, slot){
  if(!slot || !jid) return;
  slot.dataset.jid = jid;
  redesPinta(jid, slot);
}

async function redesPinta(jid, slot){
  let r;
  try{ r = await (await fetch('/api/redes/' + encodeURIComponent(jid))).json(); }
  catch(e){ return; }
  slot.innerHTML = redesHTML(jid, r);
  slot.querySelectorAll('.red-posts').forEach(el => { el.dataset.postsOk = ''; if(typeof postsMini === 'function') postsMini(el.dataset.jid, el); });   // «📣 Post» (posts.js)
  clearTimeout(_RED_T[jid]);
  if(r.estado === 'creando') _RED_T[jid] = setTimeout(() => redesPinta(jid, slot), 3000);
}

function _redCaja(texto, enlace, tope){
  const final = texto.split('{ENLACE}').join(enlace || '[enlace al vídeo]');
  const n = tope ? [...final].length : 0;
  return `<div class="red-caja">
      <div class="red-texto">${esc(final)}</div>
      <div class="red-pie">${tope ? `<span class="red-cuenta${n > tope ? ' largo' : ''}">${n}/${tope}</span>` : '<span></span>'}
        <button type="button" class="btn ghost sm red-copiar" data-txt="${esc(final)}" onclick="copyTxt(this)">copiar</button></div>
    </div>`;
}

function redesHTML(jid, r){
  const cab = `<div class="pro-ab-label">📣 REDES SOCIALES</div>`;
  if(r.estado === 'creando'){
    return `<section class="pro-redes">${cab}${progBarHTML('redes-' + jid, 'Escribiendo tus publicaciones…', 40)}</section>`;
  }
  if(r.estado !== 'listo'){
    return `<section class="pro-redes">${cab}
      <p class="muted art-intro">Una publicación para <b>cada una de tus redes</b>, en tu voz y con el estilo de cada una, para copiar y
      pegar (la primera es la de la pestaña «Publicaciones» de tu canal de YouTube); todas llevan a este vídeo. Gasta 1 petición
      a Flash-Lite (no toca tus 20 de Flash).</p>
      ${r.error ? `<div class="callout bad" style="margin:0 0 10px">✖ ${esc(r.error)}</div>` : ''}
      <button type="button" class="btn" onclick="redesCrea('${esc(jid)}', this, false)">📣 Crear publicaciones</button>
      <div class="red-posts" data-jid="${esc(jid)}"></div></section>`;
  }
  const d = r.datos || {}, enlace = r.enlace || '';
  let estado;
  if(!enlace) estado = `<div class="callout warn" style="margin:0 0 12px">⚠ Este vídeo aún no está en YouTube: al copiar sale «[enlace al vídeo]». Cuando lo subas, el enlace se pone solo (no hace falta rehacerlas).</div>`;
  else if(r.programado) estado = `<div class="callout info" style="margin:0 0 12px">📅 Está programado: sale el ${esc(new Date(r.programado * 1000).toLocaleString('es', {weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit'}))}. El enlace ya es el definitivo (no cambia cuando se publica), pero la gente solo lo verá funcionar cuando el vídeo salga: publica las publicaciones a esa hora o unos minutos después (en YouTube puedes programarlas). Enlace: <a href="${esc(enlace)}" target="_blank" rel="noopener">${esc(enlace)}</a></div>`;
  else estado = `<div class="muted" style="font-size:12.5px;margin:0 0 12px">Enlace al vídeo: <a href="${esc(enlace)}" target="_blank" rel="noopener">${esc(enlace)}</a></div>`;
  const bloques = (d.orden || Object.keys(d.redes || {})).filter(k => (d.redes || {})[k]).map(k => {
    const v = d.redes[k];
    let cuerpo = '';
    if(k === 'linkedin') cuerpo = _redCaja(v.texto || '', enlace, 3000) + `<div class="red-sub">Primer comentario (con el enlace)</div>` + _redCaja(v.comentario || '', enlace, 0);
    else if(k === 'bluesky') cuerpo = (v.posts || []).map((p, i, a) => (a.length > 1 ? `<div class="red-sub">Post ${i + 1} de ${a.length}</div>` : '') + _redCaja(p, enlace, 300)).join('');
    else if(k === 'reddit') cuerpo = `<div class="red-sub">Título</div>` + _redCaja(v.titulo || '', enlace, 300) + `<div class="red-sub">Texto</div>` + _redCaja(v.texto || '', enlace, 0);
    else if(k === 'youtube') cuerpo = _redCaja(v.texto || '', enlace, 0);
    else if(k === 'discord') cuerpo = _redCaja(v.texto || '', enlace, 2000);
    else cuerpo = (v.textos || []).map((t, i) => `<div class="red-sub">Short ${i + 1}${(d.shorts || [])[i] ? ': «' + esc(d.shorts[i]) + '»' : ''}</div>` + _redCaja(t, enlace, 2200)).join('');
    return `<div class="red-bloque">
        <div class="red-cab"><span class="red-ico">${RED_ICONOS[k] || ''}</span><b>${esc(RED_NOMBRE[k] || k)}</b>
          <span class="muted red-consejo">${esc(RED_CONSEJO[k] || '')}</span></div>
        ${cuerpo}
      </div>`;
  }).join('');
  const fecha = d.ts ? new Date(d.ts * 1000).toLocaleString('es', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
  return `<section class="pro-redes">${cab}${estado}
    ${(d.avisos || []).map(x => `<div class="callout warn" style="margin:0 0 8px">⚠ ${esc(x)}</div>`).join('')}
    <div class="red-grid">${bloques}</div>
    <div class="art-acciones">
      <button type="button" class="btn ghost sm" onclick="redesCrea('${esc(jid)}', this, true)"
              title="Otras publicaciones (1 petición a Flash-Lite): las de ahora se sustituyen">↻ Rehacer</button>
      <span class="muted art-meta">${esc(d.nombre_modelo || '')}${fecha ? ' · ' + fecha : ''}</span>
    </div>
    <div class="red-posts" data-jid="${esc(jid)}"></div>
  </section>`;
}

async function redesCrea(jid, btn, rehacer){
  if(rehacer && !confirm('¿Escribir otras publicaciones? Las de ahora se sustituyen (1 petición a Flash-Lite).')) return;
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/redes/' + encodeURIComponent(jid) + '/crear', {method: 'POST'})).json();
    if(!r.ok){ alert(r.error || 'No se pudieron crear las publicaciones.'); btn.disabled = false; return; }
  }catch(e){ alert('No se pudo hablar con el panel: ' + e.message); btn.disabled = false; return; }
  const slot = btn.closest('.pro-redes') ? btn.closest('.pro-redes').parentElement : null;
  if(slot) redesPinta(jid, slot);
}
