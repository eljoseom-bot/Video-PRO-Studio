"""Las ilustraciones animadas de «Shorts · TikTok»: COPIA PROPIA de lo necesario de `ilustraciones.py` (el autor, 16-oct-2026: «lo de Video Pro no lo toques,
duplícalo»). Usa `st_ilus_motor` y `st_ilus_escenas*` (copias de `ilus_*`): si cambias las ilustraciones de Vídeos PRO, los Shorts no cambian."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import config
from . import st_ilus_escenas2  # noqa: F401  (registra la segunda tanda de escenas)
from .st_ilus_escenas import CATALOGO
from .st_ilus_motor import (SS, Lienzo, Pal, cinta, clamp, e_back, e_in, e_out, lerp, onda, pegatina, prog, transforma)

T_IN, T_OUT = 0.50, 0.36
BORDE = 0.0175


def paleta(marca: dict) -> Pal:
    """`videos.marca` con las rutas de fuente resueltas contra la carpeta del proyecto."""
    m = dict(marca)
    for k in ("fuente_texto", "fuente_titulo", "fuente_numero"):
        v = str(m.get(k, ""))
        if v and not Path(v).is_absolute() and not Path(v).exists():
            m[k] = str(config.ROOT / v)
    return Pal(m)


def catalogo_para_prompt() -> str:
    """El catálogo como lista para el prompt del director: «clave» — qué significa."""
    return "\n".join(f'   - "{c.clave}": {c.significa}' for c in CATALOGO.values())


# ------------------------------------------------------------------ movimiento global (entrada / vaivén / salida)
def _reposo(t: float) -> tuple[float, float, float]:
    """Vaivén suave mientras está en pantalla: (escala, rotación°, desplazamiento vertical en fracción)."""
    u = t - T_IN
    return 1.0 + 0.010 * onda(u, 2.3), 1.1 * onda(u, 3.3), 0.006 * onda(u, 2.7)


def movimiento(t: float, dur: float) -> dict:
    if t < T_IN:
        k = t / T_IN
        return {"esc": lerp(0.08, 1.0, e_back(k, 1.75)), "rot": lerp(-11.0, 0.0, e_out(k)),
                "dy": lerp(0.055, 0.0, e_out(k)), "alpha": clamp(t / 0.11)}
    t_sal = dur - T_OUT
    if t > t_sal:
        e0, r0, d0 = _reposo(t_sal)
        k = clamp((t - t_sal) / (T_OUT - 0.03))         # llega a 0 antes del último fotograma
        return {"esc": lerp(e0, 0.84, e_in(k)), "rot": lerp(r0, 7.0, e_in(k)),
                "dy": lerp(d0, -0.035, e_in(k)), "alpha": clamp(1.0 - k ** 1.6)}
    e, r, d = _reposo(t)
    return {"esc": e, "rot": r, "dy": d, "alpha": 1.0}


# ------------------------------------------------------------------ un fotograma
def fotograma(tipo: str, t: float, dur: float, F: int, pal: Pal, etiqueta: str = "") -> Image.Image:
    """El fotograma RGBA (F×F) de la ilustración `tipo` en el segundo `t` de una de duración `dur`."""
    esc_def = CATALOGO[tipo]
    n = F * SS
    l = Lienzo(n, pal, BORDE)
    esc_def.fn(l, t, dur)
    icono = l.im.reduce(SS)
    icono = pegatina(icono, pal, crema_px=0.030 * F, tinta_px=0.014 * F, sombra_px=0.028 * F)
    g = movimiento(t, dur)
    sube = 0.05 * F if etiqueta else 0.0            # con etiqueta el símbolo sube un poco
    out = transforma(icono, g["esc"], g["rot"], 0.0, g["dy"] * F - sube, g["alpha"])
    if etiqueta:
        alto = int(0.135 * F)
        cin = cinta(etiqueta, pal, alto, int(0.98 * F))
        kk = prog(t, 0.28, 0.62)
        k_sal = clamp((t - (dur - T_OUT)) / T_OUT)
        esc_c = (e_back(kk, 2.2) if kk < 1 else 1.0) * (1.0 - 0.16 * e_in(k_sal))
        alfa_c = clamp(kk * 4.0) * clamp(1.0 - k_sal ** 1.6)
        res = transforma(cin, esc_c, -2.2 + 3.0 * (1 - kk), 0, 0, alfa_c, bbox=True,
                         destino=(F, F), centro_dest=(F / 2.0, F * 0.885))
        if res is not None:
            out.alpha_composite(res[0], res[1])
    return out


