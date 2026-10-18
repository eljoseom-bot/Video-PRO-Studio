/* «🗣️ Tus muletillas» (clipper/muletillas.py, GET /api/muletillas/<base>): el mismo recuadro en la página del vídeo
   (/video/<base>) y en las tarjetas del Historial (vídeos ya borrados del PC: su transcripción sigue guardada). */
function _mulEsc(s){ return (s == null ? '' : String(s)).replace(/[&<>"]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m])); }
function _mulNum(x, d = 1){ return Number(x).toLocaleString('es', {maximumFractionDigits: d}); }

async function muletillasCargaEn(base, contenedorId){
  const el = document.getElementById(contenedorId);
  if(!el) return;
  el.innerHTML = '<span class="muted">Contando tus muletillas…</span>';
  try{
    const j = await (await fetch(`/api/muletillas/${encodeURIComponent(base)}`)).json();
    if(!j.ok){ el.innerHTML = `<span class="muted">${_mulEsc(j.error || 'No hay datos todavía.')}</span>`; return false; }
    const chips = (j.top || []).slice(0, 8).map(m =>
      `<span class="mul-chip" title="${_mulEsc(m.nombre)} · ${_mulNum(m.por_min, 2)} por minuto">${_mulEsc(m.nombre.split(' (')[0])} <b>×${m.n}</b></span>`).join('');
    let comp = '';
    if(j.tendencia){
      const t = j.tendencia, mejor = j.por_min < t.media_por_min;
      comp = `<div class="mul-comp">${mejor ? '📉' : '📈'} Tus otros ${t.videos} vídeo(s): <b>${_mulNum(t.media_por_min)}</b> por minuto de media
        → este, <b>${_mulNum(j.por_min)}</b> (${mejor ? 'mejor' : 'más'} que tu media).</div>`;
    }
    const extra = [`Ritmo: ${j.palabras_min} palabras por minuto`];
    if(j.pausas_duda) extra.push(`pausas de duda a media frase: ${j.pausas_duda.n} (${_mulNum(j.pausas_duda.seg)} s)`);
    extra.push('los «eh» no salen: Whisper no los escribe');
    el.innerHTML = j.total
      ? `<div class="mul-tit">🗣️ <b>${j.total}</b> muletillas en ${_mulNum(j.minutos)} min: una cada <b>${j.cada_s} s</b> (${_mulNum(j.por_min)} por minuto)</div>
         ${comp}<div class="mul-lista">${chips}</div>
         ${(j.consejos || []).map(c => `<div class="mul-tip">💡 ${_mulEsc(c)}</div>`).join('')}
         <div class="hint">${extra.join(' · ')}.</div>`
      : `<div class="mul-tit">🗣️ Ni una muletilla de las que conozco en este vídeo. 👏</div><div class="hint">${extra.join(' · ')}.</div>`;
    return true;
  }catch(e){ el.innerHTML = `<span class="muted">No se pudo cargar: ${_mulEsc(e.message)}</span>`; return false; }
}
