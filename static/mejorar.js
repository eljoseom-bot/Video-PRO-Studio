/* «¿Quieres mejorarle algo?» — se usa en la página de versiones (/video/<base>) y en la de un trabajo (/job/<jid>). */

/* Dictado por voz (el autor, 2026-09-21): el reconocimiento de voz del propio navegador (Web Speech API) — gratis, sin
   clave ni servidor, no gasta cuota de Gemini. Solo Chrome/Edge lo traen; en los que no, el botón ni se dibuja. */
const SOPORTA_DICTADO = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
let _dictado = null;               // {jid, rec, manual} — el único que puede estar escuchando a la vez
function _dictadoTermina(jid, mensaje){
  const btn = document.getElementById('dictado-btn-' + jid), est = document.getElementById('dictado-estado-' + jid), a = document.getElementById('mej-txt-' + jid);
  if(btn){ btn.classList.remove('oyendo'); btn.title = 'Dictar por voz'; }
  if(est) est.textContent = mensaje || '';
  if(_dictado && _dictado.jid === jid) _dictado = null;
  if(a && !mensaje){ a.focus(); a.setSelectionRange(a.value.length, a.value.length); }    // dejó de escuchar sin error: el cursor al final, listo para seguir escribiendo
}
function dictadoToggle(jid){
  if(_dictado && _dictado.jid === jid){ _dictado.manual = true; _dictado.rec.stop(); return; }    // ya escuchaba ESTE cuadro: para
  if(_dictado){ _dictado.manual = true; _dictado.rec.stop(); }                                    // escuchaba OTRO cuadro: para ese primero (solo uno a la vez)
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const a = document.getElementById('mej-txt-' + jid), btn = document.getElementById('dictado-btn-' + jid), est = document.getElementById('dictado-estado-' + jid);
  if(!a || !Rec) return;
  const base = a.value.trim() ? a.value.replace(/\s+$/, '') + ' ' : '';        // lo que ya hubiera escrito se conserva; se dicta A CONTINUACIÓN
  const rec = new Rec();
  rec.lang = 'es-CO'; rec.continuous = true; rec.interimResults = true;
  const st = {jid, rec, manual: false};
  rec.onstart = () => { if(btn){ btn.classList.add('oyendo'); btn.title = 'Escuchando… pulsa para parar'; } if(est) est.textContent = '🎙️ Escuchando…'; };
  rec.onresult = ev => {
    let firme = '', enCurso = '';
    for(let i = 0; i < ev.results.length; i++){ const r = ev.results[i]; if(r.isFinal) firme += r[0].transcript; else enCurso += r[0].transcript; }
    a.value = base + firme + enCurso;
    a.scrollTop = a.scrollHeight;
  };
  rec.onerror = ev => {
    // 'no-speech' (un silencio) y 'aborted' (lo paramos nosotros) no son un fallo real: los demás sí y ahí se avisa y se deja de escuchar.
    const FATAL = {'not-allowed': 'No diste permiso al micrófono (el candado de la barra de direcciones).', 'service-not-allowed': 'El navegador bloqueó el dictado.',
                   'audio-capture': 'No encuentro un micrófono.', 'network': 'Falló la conexión con el dictado de Google.'};
    if(FATAL[ev.error]){ st.manual = true; _dictadoTermina(jid, '⚠️ ' + FATAL[ev.error]); }
  };
  rec.onend = () => {
    if(!st.manual && _dictado === st){ try{ rec.start(); return; }catch(e){} }    // Chrome corta solo tras un silencio: se retoma sin que el autor haga nada
    _dictadoTermina(jid, '');
  };
  _dictado = st;
  try{ rec.start(); }catch(e){ _dictadoTermina(jid, '⚠️ No se pudo empezar a escuchar.'); }
}
function mejAnade(jid, texto){
  const a = document.getElementById('mej-txt-' + jid); if(!a) return;
  a.value = (a.value.trim() ? a.value.replace(/\s+$/, '') + '\n' : '') + texto;
  a.focus(); a.setSelectionRange(a.value.length, a.value.length);
}
// Enter envía (como pidió el autor: «cuando le dé enter, empieza otro renderizado»); Shift+Enter baja de línea
function mejKey(ev, jid){
  if(ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing){ ev.preventDefault(); mejorarVideo(jid); }
}
async function mejorarVideo(jid){
  const a = document.getElementById('mej-txt-' + jid), m = document.getElementById('mej-msg-' + jid), b = document.getElementById('mej-btn-' + jid);
  const err = t => { m.innerHTML = '<span style="color:var(--bad);font-weight:700"></span>'; m.firstChild.textContent = t; };
  const texto = a.value.trim();
  if(texto.length < 6){ err('Cuéntale con unas palabras qué quieres cambiar.'); a.focus(); return; }
  // 📌 «Recordar esto para todos mis próximos vídeos» (el autor, 2026-10-03): antes de enviar, un aviso que pide confirmar
  const rec = document.getElementById('mej-rec-' + jid);
  const recordar = !!(rec && rec.checked);
  let regla = '';
  if(recordar){
    regla = await mejConfirmaRegla(texto);                  // null = canceló: no se envía nada
    if(regla === null){ m.textContent = ''; return; }
  }
  b.disabled = true; m.textContent = 'Mandándoselo a la IA…';
  try{
    const cuerpo = recordar ? {texto, recordar: true, regla} : {texto};
    const r = await fetch('/api/job/' + jid + '/mejorar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cuerpo)});
    const j = await r.json();
    if(!j.ok){ err(j.error || 'No se pudo empezar la mejora.'); b.disabled = false; return; }
    if(j.regla && j.regla.ok === false){                   // (una regla repetida no frena la mejora: se avisa un momento)
      m.textContent = 'ℹ️ ' + (j.regla.error || 'La regla no se guardó.') + ' La mejora sigue.';
      await new Promise(res => setTimeout(res, 2600));
    }
    if(location.pathname.startsWith('/video/')){ location.href = j.url; location.reload(); } else { location.href = j.url; }
  }catch(e){ err('No se pudo hablar con el panel: ' + e.message); b.disabled = false; }
}

/* 📌 REGLAS PERMANENTES DEL DIRECTOR (clipper/reglas.py; el autor, 2026-10-03): feedback general que el director lee en TODOS los
   análisis y mejoras siguientes del canal. */
const MOMENTO_RE = /\b\d{1,2}:\d{2}\b|\bminutos?\s+\d|\bsegundos?\s+\d|\ben el \d+\b|\bal minuto\b/i;
function _escR(s){ return (s || '').toString().replace(/[&<>"]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m])); }

/** El aviso de doble check. Devuelve (Promise) el texto de la regla que se guardará —lo que el autor deje en el cuadro— o null si
    cancela. Su texto de partida es el comentario; si habla de un momento concreto del vídeo, avisa (no lo impide). */
function mejConfirmaRegla(texto){
  return new Promise(resolve => {
    const viejo = document.getElementById('dlg-regla'); if(viejo) viejo.remove();
    const dlg = document.createElement('dialog');
    dlg.id = 'dlg-regla'; dlg.className = 'dlg-regla';
    dlg.innerHTML = `
      <h3>📌 Antes de enviar: esto se recordará para siempre</h3>
      <p class="dlg-aviso"><b>Recuerda que este feedback va a impactar al director, es decir, los siguientes videos van a tener este feedback.</b></p>
      <label for="dlg-regla-txt">La regla que se guardará — escríbela en general, que valga para TODOS tus vídeos:</label>
      <textarea id="dlg-regla-txt" rows="3" maxlength="400"></textarea>
      <div class="dlg-nota" id="dlg-regla-nota" hidden></div>
      <div class="dlg-botones">
        <button type="button" class="btn ghost" id="dlg-regla-no">Cancelar (no se envía nada)</button>
        <button type="button" class="btn" id="dlg-regla-si">Sí, recordarla y mejorar el vídeo</button>
      </div>`;
    document.body.appendChild(dlg);
    const ta = dlg.querySelector('#dlg-regla-txt'), nota = dlg.querySelector('#dlg-regla-nota');
    ta.value = texto.length <= 400 ? texto : '';
    const revisa = () => {
      const t = ta.value.trim();
      let msg = '';
      if(!t) msg = 'Tu comentario es largo: resume aquí, en una o dos frases (máximo 400 letras), lo que quieres que valga SIEMPRE.';
      else if(MOMENTO_RE.test(t)) msg = '⚠ Esto parece hablar de un momento concreto de ESTE vídeo («' + (t.match(MOMENTO_RE) || [''])[0] + '»). Una regla permanente vale para todos los vídeos: reescríbela en general, o cancela y desmarca la casilla.';
      nota.hidden = !msg; nota.textContent = msg;
    };
    ta.addEventListener('input', revisa); revisa();
    const cierra = valor => { try{ dlg.close(); }catch(e){} dlg.remove(); resolve(valor); };
    dlg.querySelector('#dlg-regla-no').onclick = () => cierra(null);
    dlg.querySelector('#dlg-regla-si').onclick = () => {
      const t = ta.value.trim();
      if(t.length < 8){ revisa(); nota.hidden = false; if(!nota.textContent) nota.textContent = 'La regla es muy corta: cuéntale al director qué quieres siempre.'; ta.focus(); return; }
      cierra(t);
    };
    dlg.addEventListener('cancel', ev => { ev.preventDefault(); cierra(null); });          // Esc = cancelar
    if(typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    ta.focus();
  });
}

const _REGLAS = {};                 // jid -> {reglas, max, editando, error}
async function reglasCarga(jid){
  try{
    const d = await (await fetch('/api/job/' + encodeURIComponent(jid) + '/reglas')).json();
    const prev = _REGLAS[jid] || {};
    _REGLAS[jid] = {reglas: d.reglas || [], max: d.max || 12, editando: prev.editando || null, error: ''};
  }catch(e){ return; }
  reglasPinta(jid);
}
function reglasRecargaTodas(){
  document.querySelectorAll('[id^="reglas-cuerpo-"]').forEach(el => reglasCarga(el.id.slice('reglas-cuerpo-'.length)));
}
function reglasPinta(jid){
  const cuerpo = document.getElementById('reglas-cuerpo-' + jid), cuenta = document.getElementById('reglas-cuenta-' + jid);
  const d = _REGLAS[jid];
  if(!cuerpo || !d) return;
  const activas = d.reglas.filter(r => r.activa !== false).length;
  if(cuenta) cuenta.textContent = d.reglas.length ? `(${activas} activa${activas === 1 ? '' : 's'}${activas !== d.reglas.length ? ' · ' + (d.reglas.length - activas) + ' en pausa' : ''})` : '(ninguna todavía)';
  const filas = d.reglas.map(r => {
    const edita = d.editando === r.id;
    return `<div class="regla${r.activa === false ? ' pausada' : ''}">
      <span class="regla-estado" title="${r.activa === false ? 'En pausa: el director NO la lee' : 'Activa: el director la lee en cada vídeo'}">${r.activa === false ? '⏸' : '✅'}</span>
      ${edita ? `<textarea class="regla-edit" id="regla-edit-${jid}" rows="2" maxlength="400">${_escR(r.texto)}</textarea>
        <span class="regla-acc"><button type="button" class="btn sm" onclick="reglaGuardaEdicion('${jid}', '${r.id}')">✔ Guardar</button>
        <button type="button" class="btn ghost sm" onclick="reglaEdita('${jid}', null)">✖</button></span>`
      : `<span class="regla-texto">${_escR(r.texto)}</span>
        <span class="regla-acc">
          <button type="button" class="btn ghost sm" title="Editar el texto" onclick="reglaEdita('${jid}', '${r.id}')">✎</button>
          <button type="button" class="btn ghost sm" title="${r.activa === false ? 'Reanudar: el director vuelve a leerla' : 'Pausar: el director deja de leerla (sin borrarla)'}" onclick="reglaPausa('${jid}', '${r.id}', ${r.activa === false ? 'true' : 'false'})">${r.activa === false ? '▶' : '⏸'}</button>
          <button type="button" class="btn ghost sm" title="Borrar la regla" onclick="reglaBorra('${jid}', '${r.id}')">🗑</button>
        </span>`}
    </div>`;
  }).join('');
  cuerpo.innerHTML = `<p class="hint" style="margin:0 0 8px">Feedback <b>general</b> que el director lee en <b>todos</b> los vídeos siguientes de este canal (análisis y mejoras). Lo de un momento concreto de un vídeo va en el cuadro de arriba, sin la casilla. Máximo ${d.max}.</p>
    ${d.error ? `<div class="pro-tit-err">${_escR(d.error)}</div>` : ''}
    ${filas || '<div class="muted" style="font-size:13px;margin:6px 0">Todavía no tienes ninguna regla.</div>'}
    <div class="regla-nueva">
      <textarea id="regla-nueva-${jid}" rows="2" maxlength="400" placeholder="Añadir una regla sin crear versión. p. ej.: No cortes mis risas, las dejo a propósito"></textarea>
      <button type="button" class="btn sm" onclick="reglaAgrega('${jid}', this)">📌 Guardar regla</button>
    </div>`;
}
async function reglaLlama(jid, url, cuerpo, ok){
  const d = _REGLAS[jid] || (_REGLAS[jid] = {reglas: [], max: 12, editando: null, error: ''});
  d.error = '';
  try{
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cuerpo || {})});
    const j = await r.json();
    if(!r.ok || j.ok === false){ d.error = j.error || 'No se pudo guardar el cambio.'; reglasPinta(jid); return false; }
    if(ok) ok(d);
  }catch(e){ d.error = 'No se pudo hablar con el panel (¿está abierto?). Prueba otra vez.'; reglasPinta(jid); return false; }
  reglasRecargaTodas();
  return true;
}
async function reglaAgrega(jid, btn){
  const ta = document.getElementById('regla-nueva-' + jid); if(!ta) return;
  const texto = ta.value.trim();
  if(btn) btn.disabled = true;
  const ok = await reglaLlama(jid, `/api/job/${encodeURIComponent(jid)}/reglas`, {texto});
  if(!ok && btn) btn.disabled = false;
  if(ok){ const t = document.getElementById('regla-nueva-' + jid); if(t) t.value = ''; }
}
function reglaEdita(jid, id){ const d = _REGLAS[jid]; if(d){ d.editando = id; d.error = ''; reglasPinta(jid); const t = document.getElementById('regla-edit-' + jid); if(t) t.focus(); } }
async function reglaGuardaEdicion(jid, id){
  const t = document.getElementById('regla-edit-' + jid); if(!t) return;
  await reglaLlama(jid, `/api/job/${encodeURIComponent(jid)}/reglas/${id}`, {texto: t.value.trim()}, d => { d.editando = null; });
}
function reglaPausa(jid, id, activa){ reglaLlama(jid, `/api/job/${encodeURIComponent(jid)}/reglas/${id}`, {activa}); }
function reglaBorra(jid, id){ reglaLlama(jid, `/api/job/${encodeURIComponent(jid)}/reglas/${id}/borrar`, {}); }
/* la tarjeta grande, la misma en las dos páginas (`info` = {base, versiones, usadas, maximo, restantes, en_marcha}) */
function mejoraCardHTML(jid, info, abierta){
  const ej = ['Quita la parte donde hablo de …', 'Corta el silencio del minuto …', 'Me gustó lo que cortaste en el minuto …, devuélvelo', 'Edítalo más fuerte: quita más relleno',
              'Menos tarjetas', 'Menos ilustraciones', 'Más zoom a la pantalla cuando …'];
  const chips = ej.map(t => `<button type="button" class="chip-ej" onclick="mejAnade('${jid}', ${JSON.stringify(t).replace(/"/g, '&quot;')})">${t.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</button>`).join('');
  let cuerpo;
  if(info.puede === false){
    cuerpo = `<div class="callout warn">${info.motivo.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</div>`;
  } else if(info.restantes <= 0){
    cuerpo = `<div class="callout warn">Ya usaste las ${info.maximo} mejoras de este vídeo. Para retoques pequeños, usa <b>✂️ Editar / cortar el vídeo</b> (no gasta IA).</div>`;
  } else if(info.en_marcha){
    cuerpo = `<div class="callout info">Hay una versión de este vídeo procesándose. Cuando termine podrás pedir otra mejora.</div>`;
  } else {
    cuerpo = `<p class="hint" style="margin:4px 0 8px">Cuéntale a la IA qué no te cuadra, qué te gustó o qué quitarías. Puedes usar los tiempos del reproductor («en el 3:20…»).
      Hará una versión <b>nueva</b> partiendo de ESTA; ninguna versión anterior se toca. <b>Enter</b> envía · <b>Shift+Enter</b> baja de línea.${SOPORTA_DICTADO ? ' <b>🎤</b> también puedes dictarlo.' : ''}<br>
      <b>💡 Junta todos los cambios en UN solo comentario:</b> una mejora gasta una petición a Gemini y un render, pidas lo que pidas (un corte, 10 tarjetas o «más animaciones» cuestan casi lo mismo).</p>
      <div class="mej-campo">
        <textarea id="mej-txt-${jid}" rows="3" maxlength="2000" onkeydown="mejKey(event, '${jid}')" placeholder="p. ej.: Corta el silencio del 3:20. La parte donde hablo de WordPress no aporta, quítala."></textarea>
        ${SOPORTA_DICTADO ? `<button type="button" class="dictado-btn" id="dictado-btn-${jid}" title="Dictar por voz" onclick="dictadoToggle('${jid}')">🎤</button>` : ''}
      </div>
      ${SOPORTA_DICTADO ? `<div class="dictado-estado" id="dictado-estado-${jid}"></div>` : ''}
      <div class="mej-chips">${chips}</div>
      <label class="mej-recordar" for="mej-rec-${jid}">
        <input type="checkbox" id="mej-rec-${jid}">
        <span>📌 <b>Recordar esto para todos mis próximos vídeos</b>
        <span class="muted">— solo feedback GENERAL («no cortes mis risas», «tarjetas más cortas»), no algo de un momento de este vídeo. Antes de enviar te pido confirmar.</span></span>
      </label>
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:10px">
        <button type="button" class="btn lg" id="mej-btn-${jid}" onclick="mejorarVideo('${jid}')">✨ Mejorar (crear la versión ${info.siguiente || info.versiones + 1})</button>
        <span id="mej-msg-${jid}" style="font-size:14px"></span>
      </div>`;
  }
  setTimeout(() => reglasCarga(jid), 0);                     // 📌 las reglas permanentes del canal (se pintan al llegar)
  const reglas = `<details class="reglas-det">
      <summary>📌 Tus reglas para el director <span class="reglas-cuenta" id="reglas-cuenta-${jid}"></span></summary>
      <div class="reglas-cuerpo" id="reglas-cuerpo-${jid}"><span class="muted">Cargando…</span></div>
    </details>`;
  return `<section class="mejora"><div class="mejora-cab"><b>¿Quieres mejorarle algo?</b>
      <span class="mejora-cuenta">${info.restantes > 0 ? `Te quedan <b>${info.restantes}</b> de ${info.maximo} mejoras con IA` : 'Sin mejoras con IA disponibles'}</span></div>${cuerpo}${reglas}</section>`;
}
