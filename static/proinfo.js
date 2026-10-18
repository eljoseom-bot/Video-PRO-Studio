/* «Ritmo del vídeo» + «Títulos, miniatura y descripción» — el bloque que antes SOLO salía en
   "Detalles" (/job/<jid>). el autor pidió (2026-09-23) verlo directamente en cada versión de
   /video/<base>, sin ese clic — compartido entre job.html y versiones.html para no repetir
   el mismo HTML en los dos sitios. Cada página decide QUÉ pasa al hacer clic en una fila del
   ritmo o en un título (pasando el código como texto: `alClicFila`/`alClicTitulo`), porque el
   vídeo al que hay que saltar vive en sitios distintos según la página. */

// job.html ya trae su propio esc() (se redeclara sin problema, son iguales); versiones.html no
// tenía ninguno — nunca había construido HTML por JS — así que este archivo trae el suyo.
/** Barra de progreso VISUAL para lo que no avisa de su avance (regla del autor, 14-oct-2026: nunca un «en marcha…» a secas). Se llena en curva con el tiempo hasta ~92 % y no retrocede
    aunque la página se repinte: el arranque se recuerda por `clave` (o se da con `desdeTs`, segundos Unix del panel). `estSeg` = lo que suele tardar. */
const _PROG_T0 = {};
function progBarHTML(clave, texto, estSeg, desdeTs){
  const ahora = Date.now() / 1000;
  const t0 = desdeTs ? Math.min(ahora, desdeTs) : (_PROG_T0[clave] = _PROG_T0[clave] || ahora);
  const seg = Math.max(0, ahora - t0);
  const pct = Math.round(Math.min(92, 4 + 88 * (1 - Math.exp(-seg / Math.max(5, estSeg / 2.2)))));
  const m = Math.floor(seg / 60), s = String(Math.floor(seg % 60)).padStart(2, '0');
  return `<div class="art-proc"><div class="prog-bar"><i style="width:${pct}%"></i></div><div class="prog-txt"><b>${pct} %</b> · ${esc(texto)} <span class="muted">· ${m}:${s} · suele tardar ${estSeg >= 60 ? Math.round(estSeg / 60) + ' min' : estSeg + ' s'}</span></div></div>`;
}
function progFin(clave){ delete _PROG_T0[clave]; }

function esc(s){ return (s || '').toString().replace(/[&<>"]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m])); }

const RITMO_TIPOS = {
  camara: ['🎥', 'Cambio de escena'],
  suscribete: ['🔔', 'Suscríbete'],
  like: ['👍', 'Dale like'],
  tarjeta: ['🗂️', 'Tarjeta'],
  ilustracion: ['✨', 'Ilustración'],
  imagen: ['🖼️', 'Imagen'],
  palabra: ['🔠', 'Palabra clave'],
  escena: ['🎨', 'Escena a pantalla completa'],
};
// el autor, 2026-09-23: una imagen de Openverse no tenía indicativo PROPIO en "Ritmo del vídeo" — ni
// siquiera cabía en ninguno de los otros (caía por el "resto es tarjeta" de más abajo, así que ni
// se distinguía). "imagen"/"like" se comprueban ANTES de ese resto por la misma razón.
function _ritmoClave(ev){
  const t = ev.tipo || '';
  if(t === 'camara') return 'camara';
  if(t.startsWith('ilustracion:')) return 'ilustracion';
  if(t.startsWith('tarjeta:suscribete')) return 'suscribete';
  if(t === 'tarjeta:like') return 'like';
  if(t === 'imagen' || t.startsWith('imagen:')) return 'imagen';
  if(t === 'palabra') return 'palabra';          // las palabras clave en grande (clipper/ritmo.py, 2026-09-25)
  if(t.startsWith('escena:')) return 'escena';   // escena a pantalla completa (clipper/escena_completa.py, 2026-09-26)
  return 'tarjeta';
}

/** «Tramo más largo sin cambios: N s» (el metrónomo de clipper/ritmo.py, el autor 2026-09-25): lo que de verdad separaba sus
    vídeos del de Hormozi no era la media sino los tramos muertos. '' en un vídeo de antes de esto. */
function _ritmoResumen(r){
  if(!r || r.max_hueco_s == null) return '';
  const tope = r.max_sin_cambio_seg || 15;
  const ok = r.max_hueco_s <= tope;
  const antes = r.antes && r.antes.max_hueco_s != null && r.antes.max_hueco_s !== r.max_hueco_s
    ? ` (sin palabras clave: ${Math.round(r.antes.max_hueco_s)} s)` : '';
  return `<div class="pro-ritmo-resumen ${ok ? 'ok' : ''}">Tramo más largo sin ningún cambio: <b>${Math.round(r.max_hueco_s)} s</b>${antes}
    · ${r.palabras || 0} palabra(s) clave · objetivo: nunca más de ${Math.round(tope)} s</div>`;
}

/** La tablita "RITMO DEL VÍDEO — N evento(s)…" con su leyenda. `alClicFila(ev)` devuelve el
    código (como texto) para el onclick de esa fila. '' si no hay eventos. `resumen` (opcional): el `ritmo` del manifest. */
function ritmoTablaHTML(eventos, duracion, alClicFila, resumen){
  eventos = eventos || [];
  if(!eventos.length || !duracion) return '';
  const filas = eventos.map(ev => {
    const m = Math.floor(ev.t / 60), s = Math.round(ev.t % 60);
    const [icono] = RITMO_TIPOS[_ritmoClave(ev)];
    return `<div class="pro-ritmo-ev" data-t="${ev.t}" onclick="${alClicFila(ev)}" title="Clic para ir a este momento del vídeo">
      <span class="pro-ritmo-ico">${icono}</span>
      <span class="pro-ritmo-t">${m}:${String(s).padStart(2, '0')}</span>
      <span class="pro-ritmo-det">${esc(ev.detalle || ev.tipo)}</span>
    </div>`;
  }).join('');
  const leyenda = [...new Set(eventos.map(_ritmoClave))].map(k => {
    const [icono, nombre] = RITMO_TIPOS[k];
    return `<span class="pro-ritmo-ley-item">${icono} = ${nombre}</span>`;
  }).join('');
  return `<div class="pro-ab-label">RITMO DEL VÍDEO — ${eventos.length} evento(s) en ${(duracion / 60).toFixed(1)} min
      (${(eventos.length / (duracion / 60)).toFixed(1)}/min)</div>
    ${_ritmoResumen(resumen)}
    <div class="pro-ritmo-list">${filas}</div>
    <div class="pro-ritmo-leyenda">${leyenda}</div>`;
}

/** Los 3 prompts de miniatura = el A/B/C test (clipper/director.py, _FORMATOS_MINIATURA; el autor, 2026-09-26: «que las
    variaciones sean distintas»): cada uno lleva su letra y su formato, que se lee de la frase que el código le pone al
    final. Los de antes de ese día eran los tres «tu cara a la derecha». */
function formatoMiniatura(p){
  if(/Symmetrical composition:/.test(p)) return 'Tu cara en el centro';
  if(/Minimalist composition:/.test(p)) return 'Minimalista';
  return 'Tu cara a la derecha';
}
/** El titular de una miniatura: lo escribe el PROGRAMA con Rubik (la IA de imágenes escribe mal las letras: el autor,
    2026-10-03), nunca la imagen. '' = esa miniatura sale sin ningún texto. */
function titularMiniaturaHTML(t){
  return t ? `Titular (lo escribe el programa con Rubik): <b>«${esc(t)}»</b>`
           : `<span class="muted">Sin titular: esta miniatura sale sin ningún texto.</span>`;
}

// Los prompts de miniatura de cada versión que hay en la página (por jid): para volver a pintarlos tras un «↻».
const _PM = {};

function promptsFilasHTML(jid, d){
  return (d.error ? `<div class="pro-tit-err">${esc(d.error)}</div>` : '')
    + (d.nota ? `<div class="mia-nota">${esc(d.nota)}</div>` : '')
    + d.prompts.map((p, i) => {
      const pendiente = d.pendientes.has(i);
      return `
        <div class="pro-ab-prompt${pendiente ? ' pendiente' : ''}">
          <span class="pro-ab-letra">${'ABC'[i] || i + 1}</span>
          <span style="flex:1"><b class="pro-ab-formato">${formatoMiniatura(p)}</b>${esc(p)}
            <span class="mia-titular" id="mia-tit-${esc(jid || '')}-${'ABC'[i]}">${titularMiniaturaHTML((d.titulares || [])[i] || '')}</span></span>
          <button type="button" class="btn ghost sm" style="flex:0 0 auto;padding:2px 6px;font-size:10px"
                  data-txt="${esc(p)}" onclick="copyTxt(this)">copiar</button>${jid && i < 3 ? `
          <button type="button" class="btn ghost sm pro-tit-otro" ${pendiente ? 'disabled' : ''}
                  onclick="conceptoOtro('${esc(jid)}', ${i})" aria-label="Otro concepto para la miniatura ${'ABC'[i]}"
                  title="Otro concepto para la ${'ABC'[i]} (Flash-Lite: no gasta de las 20 peticiones diarias de los mejores modelos). El de ahora pasa a «anteriores». Luego pulsa «↻ Otra» en su imagen para dibujarlo en Cloudflare.">${pendiente ? '⏳' : '↻'}</button>` : ''}
        </div>${jid && i < 3 ? `<div class="mia-slot" id="mia-${esc(jid)}-${'ABC'[i]}"></div>` : ''}`;
    }).join('');
}

function promptsMiniaturaHTML(prompts, jid, titulares){
  if(!prompts || !prompts.length) return '';
  const previo = jid ? _PM[jid] : null;       // (si la página se vuelve a pintar a mitad de un «↻», no se pierde)
  const d = {prompts, titulares: titulares || [], pendientes: previo ? previo.pendientes : new Set(), error: '', nota: ''};
  if(jid) _PM[jid] = d;
  if(jid) setTimeout(() => miaCarga(jid), 0);          // 🎨 las miniaturas con IA de este vídeo (se pintan al llegar)
  return `
        <div class="pro-ab-label" style="margin-top:18px">PROMPTS DE MINIATURA · A/B/C (inglés)</div>
        ${jid ? `<div class="mia-cab" id="mia-cab-${esc(jid)}"></div>` : ''}
        <div class="mia-prompts"${jid ? ` data-jid="${esc(jid)}"` : ''}>${promptsFilasHTML(jid, d)}</div>`;
}

/** «↻» de UN concepto de miniatura (el autor, 2026-10-03): pide a la IA otra idea en su lugar (POST /job/<jid>/miniatura-nueva
    {indice}, ~10-30 s): el prompt, su titular y su cara. La imagen de Cloudflare que ya había NO cambia (queda marcada «de la
    idea anterior»): con «↻ Otra» en esa letra se dibuja la nueva. Se pueden pedir varias seguidas (el panel las atiende de
    una en una). */
async function conceptoOtro(jid, i){
  const d = _PM[jid];
  if(!d || d.pendientes.has(i)) return;
  d.pendientes.add(i); d.error = ''; d.nota = '';
  conceptoRepinta(jid);
  try{
    const r = await (await fetch(`/job/${encodeURIComponent(jid)}/miniatura-nueva`, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({indice: i})})).json();
    if(!r.ok) throw new Error(r.error || 'No se pudo pedir otro concepto.');
    d.prompts = r.prompts || d.prompts; d.titulares = r.titulares || d.titulares;
    d.nota = `✔ Concepto nuevo para la ${'ABC'[i]}${r.idea ? ': ' + r.idea : ''}. La imagen de abajo es la del concepto anterior: `
      + `pulsa «🎨 Dibujar el concepto nuevo» en ella para hacerla con el prompt nuevo.`;
    // las páginas que guardan los datos de la versión (job.html: MANIFEST; versiones.html: VERSIONES) los ponen al día
    if(typeof MANIFEST !== 'undefined' && Array.isArray(MANIFEST) && typeof JID !== 'undefined' && JID === jid){
      MANIFEST.forEach(m => { if(m.pro){ m.miniatura_prompts = d.prompts; m.miniatura_titulares = d.titulares; } });
    }
    if(typeof VERSIONES !== 'undefined' && Array.isArray(VERSIONES)){
      const v = VERSIONES.find(x => x.jid === jid);
      if(v){ v.miniatura_prompts = d.prompts; v.miniatura_titulares = d.titulares; }
    }
  }catch(e){
    d.error = (e && e.message && !/fetch|network/i.test(e.message)) ? e.message
      : 'No se pudo hablar con el panel (¿está abierto?). Prueba otra vez.';
  }finally{
    d.pendientes.delete(i);
    conceptoRepinta(jid);
    miaCarga(jid);                                      // (las imágenes se pintan en los huecos que acaban de rehacerse)
  }
}
function conceptoRepinta(jid){
  const d = _PM[jid], caja = document.querySelector(`.mia-prompts[data-jid="${CSS.escape(jid)}"]`);
  if(d && caja) caja.innerHTML = promptsFilasHTML(jid, d);
}

/* 🎨 MINIATURAS CON IA (el autor, 2026-09-28; clipper/miniaturas_ia.py): debajo de cada prompt A/B/C, su miniatura hecha
   con Cloudflare (fondo de FLUX + tu cara real + el titular con tu letra). «✅ Usar esta» la deja elegida al programar
   (sin descargar ni subir nada), «↻ Otra» saca otra versión de ESA, «⬇» para montar el A/B en YouTube Studio. La
   batería de Cloudflare va aquí, no en la cabecera: esa API es solo para las miniaturas. */
const _MIA = {};
async function miaCarga(jid){
  let d;
  try{ d = await (await fetch('/api/miniaturas-ia/' + encodeURIComponent(jid))).json(); }catch(e){ return; }
  miaPinta(jid, d);
  clearTimeout(_MIA[jid]);
  if(d.en_marcha) _MIA[jid] = setTimeout(() => miaCarga(jid), 4000);
}
function miaHora(ts){ return ts ? new Date(ts * 1000).toLocaleTimeString('es', {hour: '2-digit', minute: '2-digit'}) : ''; }
function miaBateriaHTML(b){
  if(!b) return '';
  const usado = Math.max(0, Math.min(100, 100 - (b.pct || 0)));
  const clase = b.quedan_miniaturas < 3 ? 'mal' : (b.pct < 30 ? 'aviso' : '');
  const tit = `Cloudflare hoy: ${b.usadas} de ${b.tope} neuronas (tope por código; te da ${b.gratis} gratis al día). `
    + `Cada miniatura ≈ ${b.por_miniatura}. Se recarga a las ${b.recarga}.`;
  return `<span class="usochip mia-bat ${clase}" title="${esc(tit)}"><span class="pila" style="width:${usado}%"></span>`
    + `<span class="pila-txt">🎨 Cloudflare · quedan ~${b.quedan_miniaturas} miniaturas hoy</span></span>`;
}
function miaPinta(jid, d){
  const cab = document.getElementById('mia-cab-' + jid);
  if(!cab) return;
  const items = d.items || {};
  (d.titulares || []).forEach((t, i) => {              // el titular que de verdad se va a escribir (el de un vídeo de antes sale del prompt)
    const el = document.getElementById(`mia-tit-${jid}-${'ABC'[i]}`);
    if(el) el.innerHTML = titularMiniaturaHTML(t);
  });
  const faltan = ['A', 'B', 'C'].filter(l => document.getElementById(`mia-${jid}-${l}`) && !(items[l] || {}).ok);
  let msg = '';
  if(!d.disponible) msg = `<span class="muted">${esc(d.motivo)}</span>`;
  else if(d.en_marcha) msg = `<span class="muted">${esc(d.mensaje || 'Generando…')}</span>`;
  else if(d.mensaje && d.estado === 'parado') msg = `<span class="muted">${esc(d.mensaje)}</span>`;
  const boton = d.disponible && !d.en_marcha && faltan.length
    ? `<button type="button" class="btn sm" onclick="miaGenera('${jid}', this)">🎨 ${Object.keys(items).length ? 'Generar las que faltan' : 'Generar las miniaturas'} (${faltan.length})</button>` : '';
  cab.innerHTML = `${miaBateriaHTML(d.bateria)} ${boton} ${msg}`;
  for(const letra of ['A', 'B', 'C']){
    const slot = document.getElementById(`mia-${jid}-${letra}`);
    if(slot) slot.innerHTML = miaItemHTML(jid, letra, items[letra] || {}, d);
  }
}
function miaItemHTML(jid, letra, it, d){
  const cara = String(it.cara || '').replace(/^cara\s+(de\s+)?/i, '').replace(/\s*mirando.*$/i, '');
  if(it.ok && it.url){
    const limpia = it.url.split('?')[0];
    const vieja = it.vigente === false;                 // el prompt cambió con el «↻» del concepto: esta imagen es de la idea de antes
    const neuronas = (d.bateria || {}).por_miniatura || 156;
    return `<div class="mia-item${vieja ? ' mia-vieja' : ''}">
      ${vieja ? `<div class="mia-vieja-aviso">⚠ Esta imagen es del concepto ANTERIOR. Pulsa «🎨 Dibujar el concepto nuevo» para hacerla con el prompt de arriba (≈ ${neuronas} neuronas); las otras miniaturas no cambian.</div>` : ''}
      <a href="${it.url}" target="_blank" rel="noopener" title="Verla en grande"><img src="${it.url}" alt="Miniatura ${letra}"></a>
      <div class="mia-acc">
        ${it.usada ? '<span class="mia-usada">✅ En uso: sale al programar</span>'
                   : `<button type="button" class="btn ${vieja ? 'ghost ' : ''}sm" onclick="miaUsar('${jid}', '${letra}', this)">✅ Usar esta</button>`}
        <button type="button" class="btn ${vieja ? '' : 'ghost '}sm" onclick="miaOtra('${jid}', '${letra}', this)" ${d.en_marcha ? 'disabled' : ''}
                title="${vieja ? 'Dibuja el concepto nuevo de la ' + letra : 'Otra versión de la ' + letra} (≈ ${neuronas} neuronas)">${vieja ? '🎨 Dibujar el concepto nuevo' : '↻ Otra'}</button>
        <a class="btn ghost sm" href="${limpia}" download>⬇</a>
        ${cara ? `<span class="muted mia-cara">cara: ${esc(cara)}</span>` : ''}
        ${it.con_texto ? `<span class="mia-aviso-texto" title="${esc(it.aviso_texto || '')}">⚠ la IA escribió algo de texto en el fondo y no pude evitarlo: prueba «↻ Otra»</span>` : ''}
      </div>
    </div>`;
  }
  if(it.estado === 'generando') return `<div class="mia-item mia-espera">${progBarHTML('mia-' + jid + letra, 'Dibujando la ' + letra + ' en Cloudflare…', 60)}</div>`;
  if(it.estado === 'esperando') return `<div class="mia-item mia-espera">⏸ Cloudflare no respondió: la reintento a las ${miaHora(it.hasta)}</div>`;
  if(it.error) return `<div class="mia-item mia-err">${esc(it.error)}
      ${d.disponible && !d.en_marcha ? `<button type="button" class="btn ghost sm" onclick="miaOtra('${jid}', '${letra}', this)">↻ Reintentar</button>` : ''}</div>`;
  return '';
}
async function miaPide(jid, cuerpo, btn){
  if(btn) btn.disabled = true;
  try{
    const r = await (await fetch('/api/miniaturas-ia/' + encodeURIComponent(jid) + '/generar',
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cuerpo)})).json();
    if(!r.ok && r.error) alert(r.error);
  }catch(e){ alert('El panel no respondió.'); }
  miaCarga(jid);
}
function miaGenera(jid, btn){ miaPide(jid, {}, btn); }
function miaOtra(jid, letra, btn){ miaPide(jid, {letra, otra: true}, btn); }
async function miaUsar(jid, letra, btn){
  btn.disabled = true;
  try{
    const r = await (await fetch(`/api/miniaturas-ia/${encodeURIComponent(jid)}/usar/${letra}`, {method: 'POST'})).json();
    if(!r.ok){ alert(r.error || 'No se pudo.'); btn.disabled = false; return; }
    // en la página del vídeo, si ya está abierta la fila de programar, aparece ahí ya elegida
    if(typeof agregarCandidatoMiniatura === 'function' && typeof JID !== 'undefined' && JID === jid){
      document.querySelectorAll('#yt-rows .thumb-pick-row').forEach(row => agregarCandidatoMiniatura(row, r.id,
        `/job/${encodeURIComponent(jid)}/thumb-candidato?archivo=${encodeURIComponent(row.dataset.arch)}&id=${r.id}`, true));
    }
  }catch(e){ alert('El panel no respondió.'); btn.disabled = false; return; }
  miaCarga(jid);
}

/** Títulos A/B/C (clic para usar) + prompts de miniatura + descripción de YouTube (con sus
    créditos de imagen/música si los llevó, tal cual salió el vídeo). `datos` = {titulos_sugeridos,
    titulos_anteriores, miniatura_prompts, miniatura_titulares, descripcion, archivo, jid}; `alClicTitulo(texto)` da el código del
    onclick de cada título — cada página decide cómo "usarlo" (job.html ya tenía su forma; versiones.html
    tiene la suya). Con `jid`, cada título lleva su «↻» (tituloOtro: cambia SOLO ese). */
function titulosDescripcionHTML(datos, alClicTitulo){
  const prompts = datos.miniatura_prompts || [];
  const clave = datos.jid || ('sin-jid-' + Object.keys(_TIT).length);
  const previo = _TIT[clave];               // (si la página se vuelve a pintar a mitad de un «↻», no se pierde)
  _TIT[clave] = {jid: datos.jid || '', titulos: datos.titulos_sugeridos || [], anteriores: datos.titulos_anteriores || [],
                 arch: datos.archivo || '', clic: alClicTitulo, pendientes: previo ? previo.pendientes : new Set(),
                 error: '', antAbierto: previo ? previo.antAbierto : false};
  return `<div class="pro-ab-grid">
      <div>
        <div class="pro-titulos" data-clave="${esc(clave)}">${titulosBloqueHTML(clave)}</div>
        ${promptsMiniaturaHTML(prompts, datos.jid, datos.miniatura_titulares)}
      </div>
      ${datos.descripcion ? `
      <div>
        <div class="pro-ab-desc-head">
          <span class="pro-ab-label" style="margin:0">DESCRIPCIÓN DE YOUTUBE</span>
          <button type="button" class="btn ghost sm" style="flex:0 0 auto;padding:2px 6px;font-size:10px"
                  data-txt="${esc(datos.descripcion)}" onclick="copyTxt(this)">copiar todo</button>
        </div>
        <pre class="pro-ab-desc">${esc(datos.descripcion)}</pre>
      </div>` : ''}
    </div>`;
}

// Los títulos de cada versión que hay en la página (por jid): para volver a pintarlos tras un «↻».
const _TIT = {};

function _tituloFilaHTML(t, medalla, d, i){
  // `i` = su posición en el set de ahora (lleva «↻»); undefined = un título anterior (solo usar / copiar)
  const pendiente = i !== undefined && d.pendientes.has(i);
  const refresh = (i !== undefined && d.jid) ? `
          <button type="button" class="btn ghost sm pro-tit-otro" ${pendiente ? 'disabled' : ''}
                  onclick="tituloOtro(this, ${i})" aria-label="Otro título en lugar de este"
                  title="Otro título en lugar de este (Flash-Lite: no gasta de las 20 peticiones diarias de los mejores modelos). El de ahora pasa a «Títulos anteriores».">${pendiente ? '⏳' : '↻'}</button>` : '';
  return `<div class="pro-ab-titulo${pendiente ? ' pendiente' : ''}">
          <span style="flex:0 0 16px">${medalla}</span>
          <span style="flex:1;cursor:pointer;text-decoration:underline dotted"
                title="clic para usar este título" data-txt="${esc(t)}" data-arch="${esc(d.arch)}"
                onclick="${d.clic(t)}">${esc(t)}</span>
          <button type="button" class="btn ghost sm" style="flex:0 0 auto;padding:2px 6px;font-size:10px"
                  data-txt="${esc(t)}" onclick="copyTxt(this)">copiar</button>${refresh}
        </div>`;
}

/** El bloque de títulos: el set de ahora (🥇🥈🥉), cada uno con su «↻» (el autor, 2026-09-25: cambiar SOLO ese, porque
    muchas veces alguno ya le gusta) y, plegados, los que se sustituyeron (también se pueden usar: no se pierde ninguno). */
function titulosBloqueHTML(clave){
  const d = _TIT[clave];
  const med = ['🥇', '🥈', '🥉'];
  const ant = _plana(d.anteriores);
  return `<div class="pro-ab-label">TÍTULOS A/B/C — clic para usar</div>
      <div class="pro-tit-err" ${d.error ? '' : 'hidden'}>${esc(d.error || '')}</div>
      ${d.titulos.length ? d.titulos.map((t, i) => _tituloFilaHTML(t, med[i] || '•', d, i)).join('')
        : `<div class="muted" style="font-size:11px">(el director no sugirió títulos)</div>`}
      ${ant.length ? `<details class="pro-tit-ant"${d.antAbierto ? ' open' : ''} ontoggle="_TIT['${esc(clave)}'].antAbierto = this.open">
        <summary>Títulos anteriores (${ant.length})</summary>
        ${ant.map(t => _tituloFilaHTML(t, '•', d)).join('')}
      </details>` : ''}`;
}

// los anteriores como lista plana (el formato de la primera versión eran sets: [[...], [...]])
function _plana(anteriores){
  const out = [];
  (anteriores || []).forEach(x => (Array.isArray(x) ? x : [x]).forEach(t => { if(t && !out.includes(t)) out.push(t); }));
  return out;
}

/** «↻» de UN título: pide a la IA otro en su lugar (POST /job/<jid>/titulos-nuevos {indice}, ~5-20 s). Se pueden
    pulsar varios seguidos (el panel los atiende de uno en uno). En job.html también actualiza MANIFEST y las filas de
    programar abiertas. */
async function tituloOtro(btn, i){
  const caja = btn.closest('.pro-titulos');
  const clave = caja.dataset.clave;
  const d = _TIT[clave];
  d.pendientes.add(i); d.error = '';
  caja.innerHTML = titulosBloqueHTML(clave);
  try{
    const r = await (await fetch(`/job/${encodeURIComponent(d.jid)}/titulos-nuevos`, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({indice: i})})).json();
    if(!r.ok) throw new Error(r.error || 'No se pudo pedir otro título.');
    d.titulos = r.titulos || d.titulos; d.anteriores = r.anteriores || d.anteriores;
    if(typeof MANIFEST !== 'undefined' && Array.isArray(MANIFEST)){
      const m = MANIFEST.find(x => x.archivo === d.arch);
      if(m){ m.titulos_sugeridos = d.titulos; m.titulos_anteriores = d.anteriores; }
      if(typeof titulosChipsHTML === 'function')
        document.querySelectorAll(`#yt-rows tr[data-arch="${CSS.escape(d.arch)}"] .title-pick-row`)
          .forEach(el => { el.outerHTML = titulosChipsHTML(d.titulos); });
    }
  }catch(e){
    d.error = (e && e.message && !/fetch|network/i.test(e.message)) ? e.message
      : 'No se pudo hablar con el panel (¿está abierto?). Prueba otra vez.';
  }finally{
    d.pendientes.delete(i);
    const c = document.querySelector(`.pro-titulos[data-clave="${CSS.escape(clave)}"]`);
    if(c) c.innerHTML = titulosBloqueHTML(clave);
  }
}

/** «🎬 Shorts que salieron» (el autor, 2026-09-25): los Shorts verticales que el render sacó del vídeo ya editado, abajo del
    todo (este programa es de vídeos largos: los Shorts son un extra). `datos` = la entrada del manifest (con `shorts` y
    `shorts_motivo`); una versión de antes de esto no tiene `shorts` y no enseña nada. */
const _TIPO_SHORT = {dato: 'dato', error_comun: 'error común', truco: 'truco', opinion: 'opinión', comparacion: 'comparación',
                     antes_despues: 'antes y después', otro: 'otro'};
/** «Enviar» un Short de este vídeo a la herramienta Tools → Shorts · TikTok (toma ese trozo de la grabación ORIGINAL). */
async function sttEnviarDesde(jid, archivo, btn){
  if(!confirm('¿Rehacer este Short con toda la edición de «Shorts · TikTok»? Tarda unos minutos y gasta 1 petición a Gemini.')) return;
  btn.disabled = true;
  try{
    const r = await (await fetch('/api/shorts-tt/desde-video', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({jid, archivo})})).json();
    if(!r.ok){ alert(r.error || 'No se pudo.'); btn.disabled = false; return; }
    location.href = '/shorts-tiktok';
  }catch(e){ alert('No se pudo hablar con el panel.'); btn.disabled = false; }
}

function shortsHTML(datos, jid){
  if(!datos || !('shorts' in datos) || !jid) return '';
  const lista = datos.shorts || [];
  const cab = `<div class="pro-ab-label">🎬 SHORTS QUE SALIERON${lista.length ? ` (${lista.length})` : ''}</div>`;
  if(!lista.length){
    return `<section class="pro-shorts">${cab}<div class="muted" style="font-size:13px">No salió ningún Short de este vídeo`
      + `${datos.shorts_motivo ? ': ' + esc(datos.shorts_motivo) : ''}. No pasa nada: solo salen los que se entienden solos y dicen algo que vale la pena.</div></section>`;
  }
  const base = `/salida/${encodeURIComponent(jid)}/`;
  return `<section class="pro-shorts">${cab}<div class="pro-shorts-grid">${lista.map(sh => `
      <div class="pro-short">
        <video controls preload="none" playsinline poster="${base}${encodeURIComponent(sh.miniatura || '')}" src="${base}${encodeURIComponent(sh.archivo)}"></video>
        <div class="pro-short-t">${esc(sh.titulo)}</div>
        <div class="pro-short-m">${Math.round(sh.duracion || 0)} s · ${esc(_TIPO_SHORT[sh.tipo] || sh.tipo || '')}${sh.nota ? ` · nota ${sh.nota}/5` : ''}</div>
        ${sh.idea ? `<div class="pro-short-idea">${esc(sh.idea)}</div>` : ''}
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <a class="btn ghost sm" download href="${base}${encodeURIComponent(sh.archivo)}">⬇ Descargar</a>
          <button type="button" class="btn ghost sm" data-txt="${esc(sh.titulo)}" onclick="copyTxt(this)">copiar título</button>
          <button type="button" class="btn ghost sm" onclick="sttEnviarDesde('${esc(jid)}', '${esc(sh.archivo)}', this)" title="Rehacer este trozo con TODA la edición de Shorts · TikTok (silencios fuera, animaciones, sonido…) desde la grabación original">🎬 Hacer Short · TikTok</button>
        </div>
        ${shortPubHTML(jid, sh)}
      </div>`).join('')}</div></section>`;
}

/** Título, descripción y tags de un Short para YouTube y para TikTok, editables, + «Subir a YouTube» (static/shorts_pub.js). */
function shortPubHTML(jid, sh){
  const m = sh.meta || {}, yt = m.yt || {}, tt = m.tiktok || {};
  const lbl = t => `<div class="sp-l">${t}</div>`;
  return `<div class="short-pub" data-jid="${esc(jid)}" data-archivo="${esc(sh.archivo)}">
    <details><summary>📝 Título y tags · YouTube y TikTok${m.origen === 'codigo' ? ' <span class="muted">(sin IA: salen de tu título y tus etiquetas)</span>' : ''}</summary>
      <div class="sp-bloque">
        ${lbl('▶ YouTube · título')}<div class="sp-fila"><input class="sp-yt-titulo" maxlength="100" value="${esc(yt.titulo || sh.titulo || '')}"><button type="button" class="btn ghost sm sp-copia" data-de="yt-titulo">copiar</button></div>
        ${lbl('Descripción <span class="muted">(el programa añade el enlace a tu vídeo y #Shorts)</span>')}<div class="sp-fila"><textarea class="sp-yt-desc" rows="3">${esc(yt.descripcion || '')}</textarea><button type="button" class="btn ghost sm sp-copia" data-de="yt-desc">copiar</button></div>
        ${lbl('Tags <span class="muted">(separados por comas)</span>')}<div class="sp-fila"><input class="sp-yt-tags" value="${esc((yt.tags || []).join(', '))}"><button type="button" class="btn ghost sm sp-copia" data-de="yt-tags">copiar</button></div>
      </div>
      <div class="sp-bloque">
        ${lbl('♪ TikTok · texto y hashtags')}<div class="sp-fila"><input class="sp-tt-titulo" value="${esc(tt.titulo || '')}"></div>
        <div class="sp-fila"><input class="sp-tt-hash" value="${esc((tt.hashtags || []).join(' '))}"><button type="button" class="btn ghost sm sp-copia" data-de="tt">copiar todo</button></div>
      </div>
    </details>
    <div class="sp-sube"><select class="sp-priv" title="Cómo queda en YouTube"><option value="private">Privado (recomendado)</option><option value="unlisted">No listado</option><option value="public">Público</option></select>
      <button type="button" class="btn sm sp-subir">⬆ Subir a YouTube</button></div>
    <div class="sp-estado muted" style="font-size:12px"></div><div class="sp-enlace muted" style="font-size:11px"></div>
  </div>`;
}

/* Copia al portapapeles (con red de seguridad si el navegador no da permiso). Genérico: lo usan
   los botones "copiar" de arriba, en cualquiera de las dos páginas. */
async function copyTxt(btn){
  const txt = btn.dataset.txt || '';
  let ok = true;
  try{ await navigator.clipboard.writeText(txt); }
  catch(e){
    try{
      const ta = document.createElement('textarea');
      ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }catch(e2){ ok = false; }
  }
  const orig = btn.textContent;
  btn.textContent = ok ? '✔ copiado' : '✖ no se pudo';
  setTimeout(() => { btn.textContent = orig; }, 1400);
}
