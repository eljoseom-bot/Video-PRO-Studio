"""📊 Cuánto te queda hoy de cada cosa gratis, en un solo sitio (el autor, 12-oct-2026: «siempre ponme algo visual para saber cuántas peticiones me quedan… en todo, todo el tiempo»).

Cuatro medidores que salen en la cabecera de TODAS las páginas (`templates/base.html`), con la barra llena = lo que QUEDA (como una batería: verde, ámbar por debajo del 30 %, rojo por debajo del 10 %):
  ⚡ Gemini Flash   (los mejores modelos: editan los vídeos; 20 al día por modelo)
  🪶 Gemini Flash-Lite (los de respaldo: nombres, miniaturas, artículos, carrusel…; 500 al día por modelo)
  🎨 Cloudflare     (imágenes: miniaturas, imágenes destacadas; neuronas al día)
  ▶ YouTube        (cuota de la API que gasta ESTE programa: 10.000 unidades, una subida = 1.600)
Cada uno sale de lo que el programa ya apunta (`uso`, `miniaturas_ia`, `cuota_yt`): esto solo lo junta y lo ordena para pintarlo. Un medidor que falle no tumba a los demás.
"""
from __future__ import annotations

import time

AVISO_PCT = 30.0
MAL_PCT = 10.0


def estado_de(quedan: float, total: float) -> str:
    """ok / aviso / mal según lo que QUEDA (no según lo gastado)."""
    if total <= 0:
        return "ok"
    if quedan <= 0:
        return "mal"
    pct = 100.0 * quedan / total
    return "mal" if pct < MAL_PCT else ("aviso" if pct < AVISO_PCT else "ok")


def medidor(id_: str, icono: str, nombre: str, quedan: float, total: float, unidad: str, reinicio: str, equivale: str = "", nota: str = "") -> dict:
    quedan = max(0, round(quedan))
    total = max(0, round(total))
    pct = round(100.0 * quedan / total) if total else 0
    est = estado_de(quedan, total)
    detalle = f"{nombre}: te quedan {quedan:,} de {total:,} {unidad} hoy".replace(",", ".")
    if equivale:
        detalle += f" ({equivale})"
    detalle += f". Se reinicia a las {reinicio}" + ("" if reinicio.endswith(".") else ".") + (f" {nota}" if nota else "")
    return {"id": id_, "icono": icono, "nombre": nombre, "quedan": quedan, "total": total, "pct": pct, "estado": est, "unidad": unidad, "reinicio": reinicio,
            "equivale": equivale, "nota": nota, "detalle": detalle}


def _hora(ts: float) -> str:
    t = time.localtime(ts)
    h12 = t.tm_hour % 12 or 12
    return f"{h12}:{t.tm_min:02d} {'a. m.' if t.tm_hour < 12 else 'p. m.'}"


def resumen(cfg=None, ahora: float | None = None, dir_work=None, _uso=None) -> list[dict]:
    """Los medidores de hoy, en orden. `_uso`: el resultado de `uso.resumen` (para pruebas)."""
    ahora = ahora if ahora is not None else time.time()
    out: list[dict] = []
    try:
        from . import uso
        r = _uso if _uso is not None else uso.resumen(cfg)
        reinicio = _hora(uso.proximo_reinicio(ahora))
        h = r.get("hero") or {}
        if h.get("limite"):
            rest = int(h.get("restantes", 0) or 0)                  # cada vídeo gasta 2 peticiones Flash (análisis + elegir Shorts); cada mejora, otras 2
            videos = f"≈ {rest // 2} vídeos nuevos (2 peticiones cada uno), o {rest // 4} con 1 mejora" if rest else ""
            ahora_m = h.get("ahora") or {}
            out.append(medidor("flash", "⚡", "Gemini Flash (los mejores modelos)", h.get("restantes", 0), h["limite"], "peticiones", reinicio,
                               equivale=videos,
                               nota=(f"Ahora usa {ahora_m['nombre']}." if ahora_m.get("nombre") else "Los buenos están agotados: sigue con los de respaldo.")))
        p = (r.get("pools") or {}).get("respaldo") or {}
        if p.get("limite"):
            out.append(medidor("lite", "🪶", "Gemini Flash-Lite (respaldo)", p.get("restantes", 0), p["limite"], "peticiones", reinicio,
                               equivale="nombres, miniaturas, artículos, carrusel…"))
    except Exception:                                                # noqa: BLE001 — un medidor que falla no tumba a los demás
        pass
    try:
        from . import miniaturas_ia as mia
        b = mia.bateria(cfg, dir_work, ahora)
        total_img = int(b["tope"] // b["por_miniatura"]) if b.get("por_miniatura") else 0
        out.append(medidor("cf", "🎨", "Cloudflare (imágenes con IA)", b["quedan_miniaturas"], total_img, "imágenes", b["recarga"],
                           equivale="miniaturas de vídeo e imágenes destacadas", nota=f"Gastaste {b['usadas']} de {b['tope']} neuronas."))
    except Exception:                                                # noqa: BLE001
        pass
    try:
        from . import cuota_yt
        y = cuota_yt.hoy(dir_work, ahora)
        from . import uso as _u
        out.append(medidor("yt", "▶", "YouTube (API)", y["quedan"], y["tope"], "unidades", _hora(_u.proximo_reinicio(ahora)),
                           equivale=f"caben {y['subidas']} subidas más, de vídeo o de Short",
                           nota="Cuenta solo lo que gasta este programa (una subida = 1.600)."))
    except Exception:                                                # noqa: BLE001
        pass
    return out


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    caso("el color depende de lo que QUEDA: ≥ 30 % verde, 10-30 % ámbar, < 10 % o cero rojo",
         estado_de(93, 100) == "ok" and estado_de(30, 100) == "ok" and estado_de(29, 100) == "aviso" and estado_de(10, 100) == "aviso" and estado_de(9, 100) == "mal"
         and estado_de(0, 100) == "mal" and estado_de(5, 0) == "ok")
    m = medidor("flash", "⚡", "Gemini Flash", 93, 100, "peticiones", "2:00 a. m.", "unos 23 vídeos completos")
    caso("un medidor trae lo que queda, el total, el % que queda y un texto claro con cuándo se reinicia",
         m["quedan"] == 93 and m["total"] == 100 and m["pct"] == 93 and m["estado"] == "ok" and "93 de 100 peticiones hoy" in m["detalle"] and "2:00 a. m." in m["detalle"]
         and "unos 23 vídeos completos" in m["detalle"], m["detalle"])
    caso("los miles salen con punto (9.000) y nada queda en negativo", "8.299 de 10.000" in medidor("yt", "▶", "YouTube", 8299, 10000, "unidades", "x")["detalle"]
         and medidor("a", "a", "a", -5, 10, "u", "x")["quedan"] == 0)
    uso_falso = {"hero": {"restantes": 93, "limite": 100, "videos": 23, "ahora": {"nombre": "Gemini 3.8 Flash", "restantes": 16}},
                 "pools": {"respaldo": {"restantes": 940, "limite": 1000}}}
    with tempfile.TemporaryDirectory() as t:
        r = resumen(None, 1_800_000_000.0, t, uso_falso)
        ids = [x["id"] for x in r]
        caso("junta los 4 medidores en orden (Flash, Flash-Lite, Cloudflare, YouTube), cada uno con su % y su estado",
             ids == ["flash", "lite", "cf", "yt"] and r[0]["quedan"] == 93 and r[1]["quedan"] == 940 and r[3]["quedan"] == 10000 and "Gemini 3.8 Flash" in r[0]["detalle"] and "≈ 46 vídeos nuevos (2 peticiones cada uno), o 23 con 1 mejora" in r[0]["detalle"], ids)
        caso("YouTube cuenta lo gastado (aquí 0 y «caben 6 subidas más»)", "caben 6 subidas" in r[3]["detalle"], r[3]["detalle"])
        sin = resumen(None, 1_800_000_000.0, t, {"hero": {}, "pools": {}})
        caso("si Gemini no trae datos, los otros medidores salen igual (uno que falla no tumba a los demás)", [x["id"] for x in sin] == ["cf", "yt"], [x["id"] for x in sin])
        roto = resumen(None, 1_800_000_000.0, t, {"hero": 5})                      # los datos de Gemini vienen rotos: revienta dentro, y aun así salen los otros dos
        caso("aunque el cálculo de Gemini REVIENTE, Cloudflare y YouTube se siguen enseñando", [x["id"] for x in roto] == ["cf", "yt"], [x["id"] for x in roto])
    return filas
