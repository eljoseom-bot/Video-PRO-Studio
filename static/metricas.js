/* «Cómo le fue en YouTube» — vistas, retención media y la CURVA de retención cruzada con el
   vídeo (el autor, 2026-09-22: «cruzar la retención con el video para ver dónde hubo menos pico»).
   Se usa en /video/<base> (la publicada) y en el Historial de Mis vídeos (aunque ya no esté
   en el PC: solo necesita el video_id que guardó clipper/historial.py al publicar). */
function _mEsc(s){ return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
function _mNum(n){ return n == null ? '–' : Number(n).toLocaleString('es'); }
function _mFmtSeg(s){ s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60); return m + ':' + String(s % 60).padStart(2, '0'); }

function _mCurvaSVG(curva, duracionS){
  if(!curva || curva.length < 2) return '<p class="hint" style="margin:0">Todavía no hay curva de retención (llega 1-2 días después de publicar).</p>';
  const W = 640, H = 150, L = 34, B = 22, T = 10, R = 10, iw = W - L - R, ih = H - T - B;
  const maxY = Math.max(100, ...curva.map(p => p.audiencia));
  const x = r => L + r * iw, y = a => T + ih - (a / maxY) * ih;
  const pts = curva.map(p => [x(p.ratio), y(p.audiencia)]);
  const linea = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const area = linea + ` L${pts[pts.length - 1][0].toFixed(1)},${(T + ih).toFixed(1)} L${pts[0][0].toFixed(1)},${(T + ih).toFixed(1)} Z`;
  const caidas = [];                                          // dónde se va la audiencia: caídas de 12 puntos o más entre dos muestras seguidas
  for(let i = 1; i < curva.length; i++){
    const d = curva[i - 1].audiencia - curva[i].audiencia;
    if(d >= 12) caidas.push({ratio: curva[i].ratio, desde: curva[i - 1].audiencia, hasta: curva[i].audiencia, d});
  }
  const top = caidas.sort((a, b) => b.d - a.d).slice(0, 3).sort((a, b) => a.ratio - b.ratio);
  const grid = [0, 25, 50, 75, 100].map(p => `<line x1="${L}" x2="${W - R}" y1="${y(p).toFixed(1)}" y2="${y(p).toFixed(1)}" stroke="var(--line-soft)" stroke-width="1"/>
    <text x="${L - 6}" y="${(y(p) + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--dim2)">${p}%</text>`).join('');
  const marcas = top.map(c => `<line x1="${x(c.ratio).toFixed(1)}" x2="${x(c.ratio).toFixed(1)}" y1="${T}" y2="${(T + ih).toFixed(1)}" stroke="var(--bad)" stroke-width="1.5" stroke-dasharray="3,3"/>
    <circle cx="${x(c.ratio).toFixed(1)}" cy="${y(c.hasta).toFixed(1)}" r="4" fill="var(--bad)" stroke="var(--ink)" stroke-width="1.5"/>`).join('');
  const fin = curva[curva.length - 1];
  const svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${grid}
      <path d="${area}" fill="var(--accent)" fill-opacity=".16"/>
      <path d="${linea}" fill="none" stroke="var(--ink)" stroke-width="2.2"/>${marcas}
      <circle cx="${x(fin.ratio).toFixed(1)}" cy="${y(fin.audiencia).toFixed(1)}" r="4" fill="var(--gold)" stroke="var(--ink)" stroke-width="1.5"/>
      <text x="${L}" y="${H - 5}" font-size="10" fill="var(--dim2)">Inicio</text>
      <text x="${W - R}" y="${H - 5}" text-anchor="end" font-size="10" fill="var(--dim2)">Final</text></svg>`;
  const nota = top.length ? `<p class="hint" style="margin:6px 0 0">📉 Mayor caída de audiencia: ` + top.map(c =>
      (duracionS ? `en el <b>${_mFmtSeg(c.ratio * duracionS)}</b>` : `al <b>${Math.round(c.ratio * 100)}%</b> del vídeo`) + ` (del ${Math.round(c.desde)}% al ${Math.round(c.hasta)}%)`
    ).join(' · ') + `</p>` : '';
  return svg + nota;
}

/** Pinta la tarjeta de métricas del vídeo `base` dentro del elemento con id `contenedorId`. */
async function metricasCargaEn(base, contenedorId, refrescar, soloCache){
  const el = document.getElementById(contenedorId);
  if(!el) return;
  el.innerHTML = `<p class="hint" style="margin:0">${soloCache ? 'Cargando…' : 'Consultando YouTube…'}</p>`;
  try{
    // `soloCache`: lo guardado, sin preguntar a YouTube (los cuadritos del Historial, el autor 2026-09-28)
    const q = refrescar ? '?refresh=1' : (soloCache ? '?cache=1' : '');
    const j = await (await fetch('/api/metricas/' + encodeURIComponent(base) + q)).json();
    if(!j.ok){ el.innerHTML = `<p class="hint" style="margin:0">${_mEsc(j.error || 'No se pudo leer.')}</p>`; return; }
    const edad = j.leido_en ? Math.round((Date.now() / 1000 - j.leido_en) / 60) : null;
    el.innerHTML = `
      <div class="uso-tiles" style="margin-bottom:10px">
        <div class="tile"><span class="tile-k">Vistas</span><b>${_mNum(j.vistas)}</b></div>
        <div class="tile"><span class="tile-k">Me gusta</span><b>${_mNum(j.likes)}</b></div>
        <div class="tile"><span class="tile-k">Comentarios</span><b>${_mNum(j.comentarios)}</b></div>
        <div class="tile"><span class="tile-k">Retención media</span><b>${j.ret_pct != null ? Math.round(j.ret_pct) + '%' : '–'}</b>${j.ret_seg != null ? `<small>${_mFmtSeg(j.ret_seg)} vistos de media</small>` : ''}</div>
        <div class="tile"><span class="tile-k">Suscriptores ganados</span><b>${_mNum(j.suscriptores)}</b></div>
        <div class="tile"><span class="tile-k">Tiempo visto</span><b>${j.minutos == null ? '–' : (j.minutos < 60 ? Math.round(j.minutos) + ' min' : (j.minutos / 60).toLocaleString('es', {maximumFractionDigits: 1}) + ' h')}</b></div>
      </div>
      ${_mCurvaSVG(j.curva, j.duracion_s)}
      <div style="display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap">
        <a class="btn ghost sm" href="${_mEsc(j.url)}" target="_blank" rel="noopener">▶ Ver en YouTube</a>
        <button type="button" class="btn ghost sm" onclick="metricasCargaEn('${base}', '${contenedorId}', true)">🔄 Actualizar</button>
        <span class="hint" style="margin:0">${edad != null ? (edad < 1 ? 'Actualizado justo ahora' : 'Actualizado hace ' + edad + ' min') : ''} · la retención de YouTube tarda 1-2 días en llegar</span>
      </div>`;
  }catch(e){ el.innerHTML = `<p class="hint" style="margin:0">No se pudo hablar con el panel: ${_mEsc(e.message)}</p>`; }
}
