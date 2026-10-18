"""🧭 Coherencia del carrusel: ¿la captura de cada paso enseña lo que el paso dice? (el autor, 10-oct-2026: «el reto es que la imagen concuerde con lo que se está mostrando en cuanto al texto»).

Las capturas se sacan por TIEMPO (el segundo en que dices ese paso); nadie miraba la imagen. Aquí Flash-Lite la MIRA:
1. UNA petición con todas las capturas del carrusel (reducidas) + lo que dice cada paso y lo que se decía en ese momento -> coincide sí/no por paso.
2. Solo si alguna no encaja: sacamos hasta 3 capturas alternativas cercanas de esa zona del vídeo y UNA segunda petición elige la que sí enseña lo del paso, o ninguna.
3. Si ninguna encaja, el paso sale SIN captura: mejor ninguna que una que no tiene que ver.
Si Gemini no responde, las capturas se quedan como estaban y se avisa. Nunca tumba el carrusel. Cuesta 1-2 peticiones de Flash-Lite (500 al día); 0 de las 20 de los Flash.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from .util import log

_PROMPT_COHERENCIA = """Eres el revisor de un carrusel de Instagram/TikTok hecho con capturas del vídeo de un presentador. Para cada PASO te doy lo que dice el paso, lo que el presentador decía en ese momento
y la CAPTURA de su pantalla. Decide si la captura enseña lo que el paso explica (la herramienta, el informe, el ajuste, la página o el resultado de los que habla).
- coincide=true: en la captura se VE eso (o algo claramente de lo mismo).
- coincide=false: enseña otra cosa distinta, una pantalla sin relación (otro programa, otra página, el escritorio, algo en blanco), o solo la cara del presentador.
Sé estricto: una imagen que no tiene que ver es peor que ninguna. Responde SOLO con JSON: {"pasos": [{"id": "p1", "coincide": true, "ve": "qué se ve, 8 palabras como mucho"}]} (todos los pasos, con su «id»)."""

_PROMPT_ALTERNATIVAS = """Estos pasos de un carrusel tienen una captura que NO encaja con lo que explican. Para cada paso te doy varias capturas ALTERNATIVAS del mismo vídeo (cada una con su número). Elige la que
de verdad enseña lo que el paso explica, o 0 si NINGUNA lo enseña (es mejor ninguna que una que no tiene que ver). Responde SOLO con JSON: {"pasos": [{"id": "p1", "elegida": 2}]} (todos los pasos, con su «id»)."""

DESFASES = (-4.0, 4.0, -8.0, 8.0, -12.0, 12.0, 16.0)     # dónde buscar alternativas respecto al segundo del paso
MAX_ALTERNATIVAS = 3


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    return json.loads(t)


def _jpeg_chico(ruta: Path, ancho: int = 512) -> bytes:
    with Image.open(ruta) as im:
        im = im.convert("RGB")
        if im.width > ancho:
            im = im.resize((ancho, max(1, round(im.height * ancho / im.width))), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=80)
        return b.getvalue()


def _frase_en(t: float, d: dict) -> str:
    """Lo que el presentador dice en el segundo `t` (la frase que empieza justo antes y la siguiente)."""
    fr = d.get("frases") or []
    if not fr:
        return ""
    k = max([i for i, f in enumerate(fr) if float(f["t"]) <= t] or [0])
    return " ".join(f["texto"] for f in fr[k:k + 2])[:300]


def lee_veredictos(texto: str, ids: set[str], clave: str) -> dict:
    """{id: valor de `clave`}. Estricto: tiene que opinar de TODOS los pasos (si falta alguno, la cadena de modelos prueba otro)."""
    x = _json_de(texto)
    filas = x.get("pasos") if isinstance(x, dict) else x
    if not isinstance(filas, list):
        raise ValueError("la respuesta no trae los pasos")
    out: dict = {}
    for f in filas:
        if isinstance(f, dict) and str(f.get("id")) in ids:
            out[str(f["id"])] = f.get(clave)
    if set(out) != ids:
        raise ValueError("faltan pasos en la respuesta")
    return out


def llama_imagenes(cfg, partes: list, valida, jid: str = ""):
    """La petición multimodal real a Flash-Lite (texto + JPEG en bytes)."""
    from . import modelos as _m
    from . import uso as _uso
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("no hay clave de Gemini")
    client, types = g[:2]
    conv = [x if isinstance(x, str) else types.Part.from_bytes(data=x, mime_type="image/jpeg") for x in partes]
    _uso.contexto(job=jid or None)
    try:
        res, modelo, _g = _util.call_gemini_cadena(client, types, _m.cadena_lite(cfg, client), conv, system=None, valida=valida, reintento_s=30.0, pasadas=1,
                                                   timeout_s=90.0, esquema=None, etiqueta="carrusel")
    finally:
        _uso.contexto(job=None)
    return res, modelo


def verifica(c: dict, d: dict, carpeta: Path, cfg, _verifica=None, progreso=None, subcarpeta: str = "capturas", tipo: str = "carrusel", ancho_max: int | None = None) -> list[str]:
    """Modifica `c["pasos"]` (campo `coherencia`: ok / cambiada / quitada / sin_comprobar; `captura`/`t_captura` si se cambia o quita) y devuelve los avisos para el panel.
    Sirve igual para las capturas de un artículo (`tipo="artículo"`, las imágenes viven en `carpeta / subcarpeta`; `""` = la propia carpeta): el autor (11-oct-2026) pidió «la misma lógica»."""
    from . import carrusel as K
    from .articulo import momento_captura
    base = carpeta / subcarpeta if subcarpeta else carpeta
    pasos = [s for s in c["pasos"] if s.get("captura")]
    if not pasos:
        return []
    _verifica = _verifica or (lambda partes, va: llama_imagenes(cfg, partes, va, c.get("jid", "")))
    partes: list = [_PROMPT_COHERENCIA if tipo == "carrusel" else _PROMPT_COHERENCIA.replace("un carrusel de Instagram/TikTok", "un artículo de blog").replace("PASO", "IMAGEN")]
    ids: set[str] = set()
    for s in pasos:
        try:
            jpg = _jpeg_chico(base / s["captura"])
        except OSError:
            continue
        ids.add(s["id"])
        partes += [f"\n=== PASO {s['id']}: {s['titulo']} ===\nEl paso dice: {s['texto']}\nEl presentador decía: «{_frase_en(s.get('t_captura') or s['t'], d)}»", jpg]
    pasos = [s for s in pasos if s["id"] in ids]
    if not pasos:
        return []
    try:
        if progreso:
            progreso("Comprobando que cada imagen encaje con su paso…")
        res, _m = _verifica(partes, lambda t: lee_veredictos(t, ids, "coincide"))
    except Exception as exc:                                    # noqa: BLE001
        log(f"AVISO: no pude comprobar la coherencia de las capturas del {tipo} ({str(exc)[:140]}).")
        for s in pasos:
            s["coherencia"] = "sin_comprobar"
        if tipo != "carrusel":
            return ["No pude comprobar que cada captura encaje con su apartado (Gemini no respondió): míralas antes de publicar."]
        return ["No pude comprobar que cada imagen encaje con su paso (Gemini no respondió): revisa las capturas con «✏ Editar» o prueba «↻ Otro carrusel»."]
    mal = [s for s in pasos if res.get(s["id"]) in (False, "false", "False", "no", 0)]
    for s in pasos:
        if s not in mal:
            s["coherencia"] = "ok"
    if not mal:
        log(f"{tipo.capitalize()}: las {len(pasos)} capturas encajan con su {'paso' if tipo == 'carrusel' else 'apartado'}.")
        return []
    # alternativas: otras pantallas cercanas al paso, a ≥ 4 s de lo que ya se usa
    ocupados = [s["t_captura"] for s in pasos if s.get("t_captura") is not None]
    cand: dict[str, list[tuple[float, Path]]] = {}
    for s in mal:
        vistos: set[float] = set()
        lista: list[tuple[float, Path]] = []
        for desfase in DESFASES:
            tc = momento_captura(min(max(0.5, s["t"] + desfase), max(0.5, d["duracion"] - 0.5)), d)
            if (tc in vistos or not K._en_pantalla(tc, d) or any(abs(tc - o) < 4.0 for o in ocupados + [x[0] for x in lista])
                    or abs(tc - (s.get("t_captura") if s.get("t_captura") is not None else -99.0)) < 2.0):
                continue
            vistos.add(tc)
            dest = base / f"alt-{s['id']}-{len(lista) + 1}.jpg"
            if K._captura_hd(d["video"], tc, dest) and not K._plana(dest):
                K.recorta_sin_camara(dest, tc, d)
                if ancho_max:
                    K._reduce(dest, ancho_max)
                lista.append((tc, dest))
            else:
                dest.unlink(missing_ok=True)
            if len(lista) >= MAX_ALTERNATIVAS:
                break
        cand[s["id"]] = lista
    con_alt = [s for s in mal if cand.get(s["id"])]
    elegidas: dict = {}
    if con_alt:
        partes2: list = [_PROMPT_ALTERNATIVAS]
        for s in con_alt:
            partes2.append(f"\n=== PASO {s['id']}: {s['titulo']} ===\nEl paso dice: {s['texto']}")
            for j, (tc, dest) in enumerate(cand[s["id"]], 1):
                partes2 += [f"Alternativa {j} (minuto {int(tc // 60)}:{int(tc % 60):02d}):", _jpeg_chico(dest)]
        try:
            ids2 = {s["id"] for s in con_alt}
            elegidas, _m = _verifica(partes2, lambda t: lee_veredictos(t, ids2, "elegida"))
        except Exception as exc:                                # noqa: BLE001
            log(f"AVISO: no pude elegir capturas alternativas del carrusel ({str(exc)[:140]}); quito las que no encajan.")
    for s in mal:
        viejo = base / s["captura"]
        try:
            k = int(elegidas.get(s["id"]) or 0)
        except (TypeError, ValueError):
            k = 0
        alts = cand.get(s["id"]) or []
        if 1 <= k <= len(alts):
            tc, dest = alts[k - 1]
            dest.replace(viejo)
            s.update(t_captura=round(tc, 2), coherencia="cambiada")
        else:
            viejo.unlink(missing_ok=True)
            s.update(captura="", t_captura=None, coherencia="quitada")
    for f in base.glob("alt-*.jpg"):
        f.unlink(missing_ok=True)
    cam = sum(1 for s in mal if s.get("coherencia") == "cambiada")
    qui = sum(1 for s in mal if s.get("coherencia") == "quitada")
    avisos = []
    if cam:
        avisos.append(f"{cam} captura(s) no encajaban con su paso y se cambiaron por otra del vídeo.")
    if qui and tipo == "carrusel":
        avisos.append(f"{qui} diapositiva(s) salen sin captura: ninguna imagen cercana enseñaba lo que dice el paso (mejor ninguna que una que no tiene que ver).")
    elif qui:
        avisos.append(f"{qui} captura(s) se quitaron del artículo: ninguna imagen cercana enseñaba lo que dice su apartado (mejor ninguna que una que no tiene que ver).")
    log(f"{tipo.capitalize()}: {len(mal)} captura(s) no encajaban: {cam} cambiada(s), {qui} quitada(s).")
    return avisos
