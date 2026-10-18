"""🎨 MINIATURAS SUELTAS (el autor, 2026-10-08: «generar miniaturas sin tener un vídeo… por ejemplo, un directo… siempre un campo para añadirle al prompt, escribir o hablar…
que le dé generar, descargar, y un botón de refresh»; «unas 15 requests por día»): «Tools → Miniaturas» del menú.

Es el MISMO motor que las miniaturas de cada vídeo (`miniaturas_ia`: FLUX.2 [klein] de Cloudflare, gratis; tu cara de `mis_caras/` con su contorno blanco; tus colores y el
estilo cómic de la marca; el titular lo escribe el código con Rubik; Flash-Lite revisa que la IA no escribiera texto), pero SIN vídeo: lo que dice el director de cada vídeo
(la escena) lo escribe aquí el propio el autor con sus palabras (o dictado por voz en la página).

- UNA petición a Flash-Lite por concepto NUEVO («director del prompt»: convierte lo que pides en la escena en inglés, con las reglas fijas del código —zona de la cara, paleta,
  sin texto—) y 1 a Flash-Lite por cada imagen para mirar que no traiga texto. Nada toca las 20 peticiones diarias de los Flash que analizan los vídeos.
- Tres composiciones (las del A/B/C de los vídeos): «derecha» (cara a la derecha, completa y cargada), «centro» (cara en el medio, fondo de color) y «minimalista».
- «↻» = la MISMA idea con otro dibujo (otra semilla, sin gastar Gemini) si lo que hay en el formulario no cambió; si cambiaste el texto, el formato, la cara o el titular, es un
  concepto nuevo. «Generar» siempre pide un concepto nuevo.
- TOPE PROPIO (regla del autor): `miniaturas_ia.sueltas_max_dia` (15) LLAMADAS a Cloudflare al día (cada intento cuenta: un reintento por texto o por servidor caído también), aparte del
  tope general de neuronas que comparte con las miniaturas de los vídeos (`tope_dia`): esta herramienta nunca puede dejar sin cupo a los vídeos más allá de esas 15 llamadas.
- Se guardan las últimas `sueltas_historial` (12) en `work/miniaturas_sueltas/` (estado.json + <id>.jpg) para volver a verlas y descargarlas.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time
from pathlib import Path

from . import conceptos_miniatura as _cm
from . import miniaturas_ia as _mia
from . import util as _util
from .util import log, nombre_modelo

FORMATOS = _mia.FORMATOS                       # ("derecha", "centro", "minimalista"): el mismo orden que A/B/C de los vídeos
NOMBRES = {"derecha": "Cara a la derecha", "centro": "Cara en el centro", "minimalista": "Minimalista"}
AYUDA = {"derecha": "Completa y cargada: lo importante a la izquierda y tu cara a la derecha (la de tus miniaturas de siempre).",
         "centro": "Tu cara en el medio, elementos a los dos lados y un fondo de COLOR (naranja o dorado).",
         "minimalista": "Un solo elemento grande y mucho aire; tu cara a la derecha."}
MIN_TEXTO, MAX_TEXTO = 6, 1200
_LOCK = threading.RLock()

_PROMPT = """Contexto del canal: {contexto}

El autor quiere una miniatura de YouTube SUELTA: todavía no hay vídeo (por ejemplo, para un directo). Esto es lo que pide, con sus palabras:
«{texto}»

{titular_linea}

Escribe el concepto de la miniatura con estas reglas:
   - Formato: {formato}
   - "miniatura_prompt": 4-6 frases EN INGLÉS que describen SOLO la escena. No escribas «YouTube thumbnail…», ni colores ni códigos hex, ni la frase de la zona de la cara: el
     sistema los añade. En la B tampoco menciones un fondo oscuro, negro o de tinta: su fondo es de color y lo pone el sistema.
   - Fiel a LO QUE PIDE el autor: sus elementos, su idea, su contraste. Si pide algo muy concreto, dibújalo; si deja libertad, elige elementos visuales concretos del tema.
   - Ilustración tipo CÓMIC/caricatura (no foto realista): trazo cartoon con contornos gruesos de tinta, trama de puntos Ben-Day, alto contraste y ráfagas detrás del protagonista.
     El protagonista en naranja de la marca contra un resto sobrio (menciónalo).
   - NINGÚN TEXTO dentro de la imagen: ni rótulo, banner o titular, ni palabras, cifras o siglas, ni el nombre de una herramienta junto a su logo; las pantallas, páginas,
     libros y carteles muestran SOLO formas, iconos y gráficas, sin escritura. Las herramientas se representan con un pictograma sencillo (lupa, robot, engranaje, gráfica…),
     nunca con su logo. Deja libre arriba el hueco del titular.
   - "miniatura_titular": el TITULAR corto que el programa escribe encima (el prompt NO lo lleva): 2-4 palabras, TODO EN MAYÚSCULAS, en español (salvo términos técnicos que se
     dicen en inglés), sin comillas, con gancho.
   - "miniatura_cara": el gesto de la cara del autor que va en esa miniatura, SOLO de esta lista: {caras}. Si no lo tienes claro, "feliz".
   - "idea": una frase corta en español para el autor que diga de qué va el concepto.
Responde SOLO con este JSON: {{"miniatura_prompt": "...", "miniatura_titular": "...", "miniatura_cara": "feliz", "idea": "..."}}"""


# ============================================================================================ archivos
def _dir(dir_work=None) -> Path:
    return Path(dir_work or _mia._rutas()["work"]) / "miniaturas_sueltas"


def _lee_json(p: Path, defecto):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _escribe_json(p: Path, d) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    _util.reemplaza(tmp, p)


def items(dir_work=None) -> list[dict]:
    """Las miniaturas sueltas guardadas, de la más nueva a la más vieja (solo las que tienen su imagen en disco)."""
    d = _dir(dir_work)
    lista = _lee_json(d / "estado.json", {}).get("items") or []
    return [x for x in lista if isinstance(x, dict) and x.get("id") and (d / f"{x['id']}.jpg").is_file()]


def ruta_imagen(item_id: str, dir_work=None) -> Path | None:
    if not re.fullmatch(r"[\w\-]+", str(item_id or "")):
        return None
    p = _dir(dir_work) / f"{item_id}.jpg"
    return p if p.is_file() else None


def item(item_id: str, dir_work=None) -> dict | None:
    return next((x for x in items(dir_work) if x["id"] == item_id), None)


def _guarda_item(nuevo: dict, maximo: int, dir_work=None) -> None:
    """Pone `nuevo` el primero y borra (de verdad: son archivos de esta herramienta) lo que pase del máximo."""
    with _LOCK:
        d = _dir(dir_work)
        lista = [x for x in items(dir_work) if x["id"] != nuevo["id"]]
        lista.insert(0, nuevo)
        for viejo in lista[max(1, int(maximo)):]:
            (d / f"{viejo['id']}.jpg").unlink(missing_ok=True)
        _escribe_json(d / "estado.json", {"items": lista[:max(1, int(maximo))]})


def quita(item_id: str, dir_work=None) -> bool:
    """Quita una miniatura suelta del historial (su .jpg y su fila). No toca nada fuera de `work/miniaturas_sueltas/`."""
    p = ruta_imagen(item_id, dir_work)
    if p is None:
        return False
    with _LOCK:
        d = _dir(dir_work)
        _escribe_json(d / "estado.json", {"items": [x for x in items(dir_work) if x["id"] != item_id]})
        p.unlink(missing_ok=True)
    return True


# ============================================================================================ el tope de ESTA herramienta (15 llamadas a Cloudflare al día)
def _uso_ruta(dir_work=None) -> Path:
    return Path(dir_work or _mia._rutas()["work"]) / "miniaturas_sueltas_uso.json"


def uso_hoy(dir_work=None, ahora: float | None = None) -> dict:
    d = _lee_json(_uso_ruta(dir_work), {})
    if d.get("dia") != _mia._dia_utc(ahora):
        return {"dia": _mia._dia_utc(ahora), "intentos": 0, "conceptos": 0}
    return {"dia": d["dia"], "intentos": int(d.get("intentos") or 0), "conceptos": int(d.get("conceptos") or 0)}


def _apunta(dir_work=None, ahora: float | None = None, intento: int = 0, concepto: int = 0) -> None:
    with _LOCK:
        d = uso_hoy(dir_work, ahora)
        d["intentos"] += intento
        d["conceptos"] += concepto
        _escribe_json(_uso_ruta(dir_work), d)


def cupo(cfg=None, dir_work=None, ahora: float | None = None) -> dict:
    """Lo que enseña la página: cuántas llamadas a Cloudflare se gastaron hoy en esta herramienta y cuántas quedan, y la batería general (las neuronas se comparten con los vídeos)."""
    p = _mia.parametros(cfg)
    u = uso_hoy(dir_work, ahora)
    tope = int(p["sueltas_max_dia"])
    return {"usadas": u["intentos"], "max": tope, "quedan": max(0, tope - u["intentos"]), "recarga": _mia.recarga_local(ahora),
            "bateria": _mia.bateria(cfg, dir_work, ahora)}


# ============================================================================================ el concepto (el «director del prompt»)
def firma(texto: str, formato: str, gesto: str, titular: str, sin_titular: bool) -> str:
    """Lo que define el CONCEPTO: mientras no cambie nada de esto, «↻» dibuja la misma idea con otra semilla (sin gastar Gemini)."""
    base = json.dumps([" ".join(str(texto or "").split()).lower(), formato, str(gesto or "").strip().lower(), " ".join(str(titular or "").split()).lower(), bool(sin_titular)],
                      ensure_ascii=False)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _canal_y_marca(cfg) -> tuple[str, str, dict]:
    """(nombre del canal, su contexto para la IA, su marca) del canal de las ideas (`ideas.canal`, por defecto «ejemplo»)."""
    try:
        from .config import canal_defecto
        canal = canal_defecto(cfg) if cfg is not None else ""
        info = (cfg.get_path(f"videos.canales.{canal}") or {}) if cfg is not None else {}
        marca = {**dict(cfg.get_path("videos.marca") or {}), **{k: v for k, v in (info.get("marca") or {}).items() if v}} if cfg is not None else {}
        return str(info.get("nombre") or canal or "de YouTube"), str(info.get("contexto") or "canal de YouTube"), marca
    except Exception:  # noqa: BLE001
        return "de YouTube", "canal de YouTube", {}


def arma_prompt(texto: str, formato: str, contexto: str, caras: str, titular: str) -> str:
    i = FORMATOS.index(formato)
    linea = (f"El titular YA lo eligió el autor: «{titular}». Devuélvelo tal cual en miniatura_titular." if titular
             else "El autor no pidió un titular: invéntalo tú según lo que pide.")
    return _PROMPT.format(contexto=contexto or "canal de YouTube", texto=texto, titular_linea=linea, formato=_cm._FORMATOS_ES[i], caras=caras or '"feliz"')


def _error_claro(exc: Exception) -> str:
    return _cm._error_claro(exc).replace("un concepto nuevo", "el concepto de la miniatura")


def _concepto(texto, formato, gesto, titular, cfg, marca, canal, contexto, caras_txt, caras_ok, _gemini=None, _modelos=None) -> dict:
    """UNA petición a Flash-Lite: lo que pide el autor -> {"prompt", "titular", "cara", "idea", "modelo"} limpio (sin texto, con las reglas fijas del código). Lanza si no se pudo."""
    i = FORMATOS.index(formato)
    if _gemini is None:
        from .director import _gemini as _abre
        g = _abre(cfg)
        if not g:
            raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso» (o en «Primeros pasos»).")
        _gemini = g[:2]
    client, types = _gemini
    if _modelos is None:
        from . import modelos as _m
        _modelos = _m.cadena_lite(cfg, client)
    from . import uso as _uso
    _uso.contexto(job="miniatura-suelta")
    try:
        nuevo, modelo, _gasto = _util.call_gemini_cadena(
            client, types, _modelos, arma_prompt(texto, formato, contexto, caras_txt, titular), system=_cm._SYSTEM.format(canal=canal),
            valida=lambda t: _cm.parsea(t, i, marca, [], caras_ok), reintento_s=20.0, pasadas=1, timeout_s=75.0, esquema=_cm._esquema(), etiqueta="miniatura_concepto")
    finally:
        _uso.contexto(job=None)
    return {**nuevo, "modelo": modelo}


# ============================================================================================ generar
def genera(texto, formato: str = "derecha", gesto: str = "", titular: str = "", sin_titular: bool = False, cfg=None, de: str | None = None, dir_work=None,
           progreso=None, *, _gemini=None, _modelos=None, _llama=None, _texto=None, _caras=None, _dormir=time.sleep, _ahora=time.time) -> dict:
    """Una miniatura suelta. `de` = el id de la miniatura desde la que se pulsó «↻»: si lo del formulario no cambió, se dibuja la MISMA idea con otra semilla (sin Gemini); si cambió
    (o no hay `de`), concepto nuevo. Nunca lanza. -> {"ok": True, "item": {...}} o {"ok": False, "error": qué pasó y qué hacer}. `_llama`, `_texto`, `_gemini`, `_modelos`, `_caras`,
    `_dormir`, `_ahora`: para las pruebas (sin red)."""
    p = _mia.parametros(cfg)
    avisa = progreso or (lambda t: None)
    texto = " ".join(str(texto or "").split())
    if len(texto) < MIN_TEXTO:
        return {"ok": False, "error": "Cuéntale con unas palabras qué quieres en la miniatura (de qué va, qué elementos, qué contraste)."}
    texto = texto[:MAX_TEXTO]
    formato = formato if formato in FORMATOS else "derecha"
    gesto = " ".join(str(gesto or "").split())[:40]
    try:
        from . import director as _d
        titular_pedido = _d.limpia_titular(titular).upper() if str(titular or "").strip() else ""
    except Exception:  # noqa: BLE001
        titular_pedido = " ".join(str(titular or "").split()[:5]).upper()[:40]
    sin_titular = bool(sin_titular) or not p["titular"]
    if _llama is None:
        ok, motivo = _mia.disponible(cfg)
        if not ok:
            return {"ok": False, "error": motivo + " (Mira «Primeros pasos» para ponerlas.)"}
    sello = firma(texto, formato, gesto, titular_pedido, sin_titular)
    previo = item(de, dir_work) if de else None
    reutiliza = bool(previo and previo.get("firma") == sello and previo.get("prompt"))
    ahora = _ahora()
    nuevo_id = time.strftime("m-%Y%m%d-%H%M%S", time.localtime(ahora)) + "-" + hashlib.sha1(f"{ahora}{texto}".encode("utf-8")).hexdigest()[:3]
    coste = _mia.neuronas(p["ancho"], p["alto"])
    tope_llamadas = int(p["sueltas_max_dia"])

    def hay_cupo() -> str:
        """'' si se puede hacer UNA llamada más a Cloudflare; si no, por qué no."""
        if uso_hoy(dir_work, _ahora())["intentos"] >= tope_llamadas:
            return (f"Llegaste al tope de hoy: {tope_llamadas} llamadas a Cloudflare para miniaturas sueltas (cada intento cuenta, también los reintentos). "
                    f"Se reinicia a las {_mia.recarga_local(_ahora())}.")
        if _mia.uso_hoy(dir_work, _ahora())["neuronas"] + coste > min(p["tope_dia"], p["neuronas_dia"]):
            return f"Se acabó el cupo gratis de Cloudflare de hoy (lo comparten las miniaturas de tus vídeos). Se recarga a las {_mia.recarga_local(_ahora())}."
        return ""

    if (m := hay_cupo()):
        return {"ok": False, "error": m}

    # 1) el concepto: el de antes (↻ sin cambios) o uno nuevo con Flash-Lite
    canal, contexto, marca = _canal_y_marca(cfg)
    lista = _caras if _caras is not None else _mia.caras(cfg=cfg)
    if reutiliza:
        c = {"prompt": previo["prompt"], "titular": previo.get("titular_ia", ""), "cara": previo.get("cara_ia", ""), "idea": previo.get("idea", ""),
             "modelo": previo.get("modelo", "")}
        n = int(previo.get("n", 0)) + 1
    else:
        avisa("Pensando la idea de la miniatura (Flash-Lite)…")
        try:
            from . import director as _d
            caras_txt = _d._caras_para_prompt(cfg)
        except Exception:  # noqa: BLE001
            caras_txt = '"feliz"'
        caras_ok = _mia.gestos(lista, p["cara_defecto"]) if lista else []
        try:
            c = _concepto(texto, formato, gesto, titular_pedido, cfg, marca, canal, contexto, caras_txt, caras_ok, _gemini, _modelos)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de Gemini: se dice qué pasó
            log(f"Miniatura suelta: el concepto falló ({exc}).")
            return {"ok": False, "error": _error_claro(exc)}
        _apunta(dir_work, _ahora(), concepto=1)
        n = 0
    titular_final = "" if sin_titular else (titular_pedido or " ".join(str(c.get("titular") or "").split()[:5])[:40]).upper()
    cara, espejo = _mia.elige_cara(lista, gesto or c.get("cara") or "", formato, p["cara_defecto"])
    avisos = [] if cara else ["No encuentro fotos tuyas en mis_caras/: la miniatura sale sin tu cara."]

    # 2) el dibujo (el mismo ciclo de `miniaturas_ia.crea`: sin texto, reintento por servidor, filtro de Cloudflare)
    prompt_base = _mia.para_ia(c["prompt"], formato, completa=(formato == "derecha"))
    prompt_refuerzo = f"{_mia._REFUERZO_TEXTO} " + _mia.para_ia(c["prompt"], formato, completa=(formato == "derecha"), reserva=len(_mia._REFUERZO_TEXTO) + 1)
    llama = _llama or (lambda pr, sd: _mia.llama_cloudflare(pr, sd, cfg))
    revisa = bool(p["revisa_texto"]) and (_texto is not None or cfg is not None)
    mira = _texto or (lambda b: _mia.hay_texto(b, cfg, "miniatura-suelta"))

    def semilla(k: int) -> int:
        return int(hashlib.sha1(nuevo_id.encode("utf-8")).hexdigest()[:7], 16) + 7919 * k

    resembrado, retextos, esperado, t0 = False, 0, False, _ahora()
    while True:
        if (m := hay_cupo()):
            return {"ok": False, "error": m}
        avisa("Dibujando con Cloudflare (suele tardar 20-60 s)…")
        try:
            bruto = llama(prompt_refuerzo if retextos else prompt_base, semilla(n))
            _mia.apunta(coste, True, dir_work, _ahora())
            _apunta(dir_work, _ahora(), intento=1)
            from PIL import Image
            fondo = Image.open(io.BytesIO(bruto))
            fondo.load()
            veo, donde = None, ""
            if revisa:
                avisa("Revisando que la IA no escribiera texto en la imagen…")
                veo, donde = mira(_mia._chico(fondo))
                if veo is True and retextos < int(p["reintentos_texto"]):
                    retextos += 1
                    n += 1
                    log(f"Miniatura suelta: la IA escribió texto ({donde}); otra semilla ({retextos} de {int(p['reintentos_texto'])}).")
                    continue
            avisa("Poniendo tu cara y el titular…")
            final = _mia.compone(fondo, cara, espejo, formato, titular_final, marca, int(p["contorno_px"]), W=int(p["ancho"]), H=int(p["alto"]))
            _dir(dir_work).mkdir(parents=True, exist_ok=True)
            (_dir(dir_work) / f"{nuevo_id}.jpg").write_bytes(_mia.a_jpg(final))
            it = {"id": nuevo_id, "ts": ahora, "texto": texto, "formato": formato, "gesto": gesto, "titular_pedido": titular_pedido, "sin_titular": sin_titular, "firma": sello,
                  "prompt": c["prompt"], "titular_ia": str(c.get("titular") or ""), "cara_ia": str(c.get("cara") or ""), "titular": titular_final,
                  "idea": c.get("idea", ""), "cara": cara["nombre"] if cara else "", "n": n, "segundos": round(_ahora() - t0, 1), "con_texto": veo is True,
                  "modelo": c.get("modelo", ""), "nombre_modelo": nombre_modelo(c["modelo"]) if c.get("modelo") else "", "misma_idea": reutiliza,
                  "avisos": avisos + ([f"Puede que la IA haya escrito algo de texto en la imagen ({donde}): si lo ves, pulsa «↻»."] if veo is True else [])}
            _guarda_item(it, int(p["sueltas_historial"]), dir_work)
            log(f"Miniatura suelta {nuevo_id}: lista en {it['segundos']:.0f} s ({'misma idea' if reutiliza else 'idea nueva'}, cara «{it['cara']}»).")
            return {"ok": True, "item": it}
        except _mia.Marcada:
            _mia.apunta(coste, False, dir_work, _ahora())
            _apunta(dir_work, _ahora(), intento=1)
            if not resembrado:
                resembrado, n = True, n + 1
                continue
            return {"ok": False, "error": "El filtro de Cloudflare marcó dos dibujos seguidos: cambia un poco lo que pides (evita guerras, violencia, personas) o pulsa «↻»."}
        except _mia.ErrorCuenta as exc:
            return {"ok": False, "error": str(exc)}
        except _mia.ErrorServidor as exc:
            _mia.apunta(coste, False, dir_work, _ahora())
            _apunta(dir_work, _ahora(), intento=1)
            if esperado:
                return {"ok": False, "error": f"Cloudflare no respondió ({exc}) ni {p['reintento_min']:g} min después. Prueba otra vez en un rato."}
            esperado = True
            avisa(f"Cloudflare no respondió ({exc}): espero {p['reintento_min']:g} min y reintento una vez…")
            log(f"Miniatura suelta: {exc}; espero {p['reintento_min']:g} min y reintento una vez.")
            _dormir(float(p["reintento_min"]) * 60)
        except Exception as exc:  # noqa: BLE001 — la imagen no se pudo leer o componer
            return {"ok": False, "error": f"No se pudo armar la miniatura ({str(exc)[:160]})."}


# ============================================================================================ pruebas (sin red: Cloudflare y Gemini de mentira; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    import tempfile
    from PIL import Image
    from . import config
    filas: list[dict] = []

    def caso(n, ok, det=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(det)})

    def jpeg(color=(200, 60, 20)) -> bytes:
        b = io.BytesIO()
        Image.new("RGB", (1280, 720), color).save(b, "JPEG")
        return b.getvalue()

    cfg = config.load()
    CONCEPTO = {"miniatura_prompt": "A giant magnifying glass over a glowing website with rising charts, arrows and simple round badges, comic cartoon style with thick ink outlines.",
                "miniatura_titular": "SEO EN VIVO", "miniatura_cara": "feliz", "idea": "Una lupa gigante sobre una web"}
    pedidos: list = []                                                      # las peticiones a Gemini
    llamadas: list = []                                                     # las llamadas a Cloudflare

    def gemini(client, types, modelos, prompt, system=None, valida=None, **k):
        pedidos.append(prompt)
        return valida(json.dumps(CONCEPTO)), "gemini-3.5-flash-lite", {}

    real_cadena = _util.call_gemini_cadena
    _util.call_gemini_cadena = gemini
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tw = Path(tmp)
            kw = dict(cfg=cfg, dir_work=tw, _gemini=(object(), object()), _modelos=["m"], _llama=lambda pr, sd: (llamadas.append((pr, sd)), jpeg())[1],
                      _texto=lambda b: (False, ""), _caras=[])

            r = genera("hola", **kw)
            caso("un texto demasiado corto no gasta nada y dice qué falta", not r["ok"] and not pedidos and not llamadas, r)
            r1 = genera("Una miniatura para mi directo de SEO: una lupa gigante sobre una web con gráficas", "derecha", **kw)
            it1 = r1.get("item") or {}
            caso("generar: UNA petición a Gemini (el concepto), UNA llamada a Cloudflare, y la miniatura sale a 1280×720 y se guarda",
                 r1["ok"] and len(pedidos) == 1 and len(llamadas) == 1 and Image.open(ruta_imagen(it1["id"], tw)).size == (1280, 720), r1.get("error") or it1)
            caso("el prompt de Gemini lleva lo que pidió el autor y las reglas de la marca; el de Cloudflare no lleva texto ni caras y trae la zona de la cara",
                 "lupa gigante" in pedidos[0] and "CARA A LA DERECHA" in pedidos[0] and "NINGÚN TEXTO" in pedidos[0]
                 and "No text of any kind" in llamadas[0][0] and "no faces" in llamadas[0][0], (pedidos[0][:200], llamadas[0][0][-300:]))
            caso("el titular de la IA va en la miniatura (lo escribe el código con Rubik) y el cupo cuenta 1 llamada", it1.get("titular") == "SEO EN VIVO" and cupo(cfg, tw)["usadas"] == 1, (it1.get("titular"), cupo(cfg, tw)))

            # ↻ con el formulario igual: la MISMA idea, otra semilla, sin Gemini
            r2 = genera("Una miniatura para mi directo de SEO: una lupa gigante sobre una web con gráficas", "derecha", de=it1["id"], **kw)
            caso("↻ con lo mismo: la MISMA idea con otra semilla, SIN gastar Gemini (una llamada más a Cloudflare, mismo prompt)",
                 r2["ok"] and r2["item"]["misma_idea"] and len(pedidos) == 1 and len(llamadas) == 2 and llamadas[1][0] == llamadas[0][0] and llamadas[1][1] != llamadas[0][1]
                 and r2["item"]["id"] != it1["id"], r2.get("error") or (len(pedidos), len(llamadas)))
            # cambia el texto: concepto nuevo
            r3 = genera("Ahora quiero un robot enorme con una lupa, estilo más limpio", "derecha", de=r2["item"]["id"], **kw)
            caso("↻ con otro texto: concepto NUEVO (otra petición a Gemini)", r3["ok"] and not r3["item"]["misma_idea"] and len(pedidos) == 2 and "robot enorme" in pedidos[1], len(pedidos))
            # titular propio y sin titular
            r4 = genera("Una lupa gigante sobre una web", "centro", titular="mi titular propio", **kw)
            caso("un titular que escribe el autor manda sobre el de la IA (en mayúsculas y corto); «sin titular» lo quita; el formato centro pide la cara en el centro",
                 r4["item"]["titular"] == "MI TITULAR PROPIO" and "titular YA lo eligió" in pedidos[-1] and "CARA EN EL CENTRO" in pedidos[-1], r4.get("item", {}).get("titular"))
            r5 = genera("Una lupa gigante sobre una web", "minimalista", sin_titular=True, **kw)
            caso("sin titular: la miniatura sale sin titular", r5["ok"] and r5["item"]["titular"] == "" and r5["item"]["formato"] == "minimalista", r5.get("item", {}).get("titular"))

            # la IA escribió texto: repite con el refuerzo y otra semilla (cada intento cuenta en el tope)
            antes = len(llamadas)
            veredictos = iter([(True, "un cartel con letras"), (False, "")])
            kw2 = {**kw, "_texto": lambda b: next(veredictos)}
            r6 = genera("Una balanza con dos pantallas", "derecha", **kw2)
            caso("si la IA escribió texto, se repite UNA vez con el refuerzo «ZERO text» y otra semilla; las dos llamadas cuentan en el tope",
                 r6["ok"] and len(llamadas) == antes + 2 and "ZERO text" in llamadas[-1][0] and "ZERO text" not in llamadas[-2][0] and not r6["item"]["con_texto"], r6.get("error"))

            # sin claves de Cloudflare: lo dice (y no gasta Gemini)
            real_cred = _mia.credenciales
            _mia.credenciales = lambda: ("", "")
            try:
                n_ped0 = len(pedidos)
                rs = genera("Una miniatura para mi directo de SEO", cfg=cfg, dir_work=tw, _gemini=(object(), object()), _modelos=["m"], _caras=[])
            finally:
                _mia.credenciales = real_cred
            caso("sin las claves de Cloudflare avisa qué falta y dónde ponerlas, sin gastar Gemini", not rs["ok"] and "CLOUDFLARE" in rs["error"] and "Primeros pasos" in rs["error"] and len(pedidos) == n_ped0, rs)

            # errores del dibujo
            def servidor_caido(pr, sd):
                raise _mia.ErrorServidor("HTTP 503")
            dormidas: list = []
            r7 = genera("Un cohete sobre una gráfica", **{**kw, "_llama": servidor_caido, "_dormir": lambda s: dormidas.append(s)})
            caso("servidor caído: espera 5 min y reintenta UNA vez; si sigue, para y lo dice (las dos llamadas cuentan)",
                 not r7["ok"] and dormidas == [300.0] and "no respondió" in r7["error"], (r7, dormidas))

            def marcada(pr, sd):
                raise _mia.Marcada("flagged")
            r8 = genera("Un cohete sobre una gráfica", **{**kw, "_llama": marcada})
            caso("el filtro de Cloudflare marca dos dibujos seguidos: para y pide cambiar lo que se pide", not r8["ok"] and "marcó" in r8["error"], r8)

            def sin_cuenta(pr, sd):
                raise _mia.ErrorCuenta("Cloudflare no acepta el token")
            r9 = genera("Un cohete sobre una gráfica", **{**kw, "_llama": sin_cuenta})
            caso("token malo o cupo de Cloudflare: lo dice y no reintenta", not r9["ok"] and "token" in r9["error"], r9)

            # el tope propio (15 llamadas a Cloudflare al día)
            tope = int(_mia.parametros(cfg)["sueltas_max_dia"])
            caso("el tope propio por defecto es de 15 llamadas al día", tope == 15, tope)
            _apunta(tw, intento=tope - cupo(cfg, tw)["usadas"])                      # se llena el cupo
            n_ped, n_ll = len(pedidos), len(llamadas)
            r10 = genera("Otra miniatura más", **kw)
            caso("con las 15 llamadas del día gastadas NO se llama a Gemini ni a Cloudflare, y dice a qué hora se reinicia",
                 not r10["ok"] and "tope de hoy" in r10["error"] and len(pedidos) == n_ped and len(llamadas) == n_ll and cupo(cfg, tw)["quedan"] == 0, r10)
            # el cupo general de neuronas (compartido con los vídeos) también frena
            tw2 = tw / "otro"
            tw2.mkdir()
            _mia.apunta(9000.0, True, tw2)
            r11 = genera("Una miniatura", **{**kw, "dir_work": tw2})
            caso("si el cupo de neuronas de hoy (el de los vídeos y esta herramienta) ya está al tope, tampoco llama a nada", not r11["ok"] and "cupo gratis" in r11["error"], r11)

            # historial: solo las últimas 12 (las viejas se borran del disco), de la más nueva a la más vieja; quitar una
            tw3 = tw / "historial"
            tw3.mkdir()
            for k in range(14):
                _guarda_item({"id": f"m-t{k:02d}", "ts": float(k), "texto": "x"}, 12, tw3)
                (_dir(tw3) / f"m-t{k:02d}.jpg").write_bytes(jpeg())
            ids = [x["id"] for x in items(tw3)]
            caso("el historial guarda solo las últimas 12, de la más nueva a la más vieja, y borra del disco las que se caen",
                 len(ids) == 12 and ids[0] == "m-t13" and ids[-1] == "m-t02" and not (_dir(tw3) / "m-t00.jpg").exists() and not (_dir(tw3) / "m-t01.jpg").exists(), ids)
            caso("quitar una miniatura borra su .jpg y su fila; un id raro o que no existe no toca nada",
                 quita("m-t13", tw3) and ruta_imagen("m-t13", tw3) is None and len(items(tw3)) == 11 and not quita("../x", tw3) and not quita("m-no-existe", tw3)
                 and ruta_imagen("../estado", tw3) is None, [x["id"] for x in items(tw3)][:3])
    finally:
        _util.call_gemini_cadena = real_cadena
    return filas
