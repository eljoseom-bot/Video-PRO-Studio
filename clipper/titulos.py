"""«↻» junto a cada título sugerido (el autor, 2026-09-25): cambia SOLO ese título por otro nuevo, sin tocar los demás («muy
posiblemente uno de los títulos sí me guste… si quiero que cambie todo el set, le doy a los tres botones»). Sin volver a
analizar el vídeo ni tocarlo.

- UNA petición a Flash-Lite por clic (500 al día): no gasta de las 20 diarias de los Flash que analizan los vídeos (su
  regla: nada nuevo puede quitarle cuota al modelo más potente). Sale en «Uso» como «Títulos nuevos (Flash-Lite)».
- Ve lo mismo que el director para los títulos: lo que se dice en el vídeo YA MONTADO (sin lo recortado), sus capítulos,
  el gancho de la descripción, el contexto del canal y las MISMAS reglas (`director.REGLAS_TITULOS`: pide 3 candidatos y
  se queda con el mejor que no esté repetido), los títulos que se QUEDAN (para buscar un ángulo que no tengan) y TODOS
  los que ya salieron, para no repetir ninguno.
- No se pierde nada: el título sustituido pasa a `titulos_anteriores` (lista plana, el más reciente primero) en el
  manifest, y todo va también al plan de esa versión (`work/<base>--pro/director_<slug>.json`), porque «Reintentar»
  rehace el manifest desde el plan.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from . import util as _util
from .util import log, nombre_modelo

MAX_ANTERIORES = 30         # títulos sustituidos que se guardan (los más viejos se caen)
MAX_CHARS_TEXTO = 24000     # ≈ 6 mil tokens de transcripción: un vídeo de 20 min entra casi entero

_SYSTEM = """Eres el editor de títulos del canal de YouTube "{canal}": escribes títulos que consiguen clics SIN
engañar al que los lee. Respondes SOLO con JSON válido, sin markdown."""

_PROMPT = """Contexto del canal: {contexto}

Este vídeo YA está editado. Su título ahora mismo: «{titulo}».
Gancho de su descripción: {gancho}

Capítulos del vídeo:
{capitulos}

Lo que se dice en el vídeo (transcripción del vídeo YA montado, sin lo recortado):
{texto}

El autor tiene estos títulos sugeridos y se QUEDA con ellos (no los cambies, no los repitas y busca un ángulo que ellos
no tengan):
{quedan}
Quiere cambiar SOLO este: «{cambia}».

Todos los títulos que el autor ya vio (no repitas ninguno ni hagas variaciones de la misma frase):
{vistos}

Escribe 3 candidatos para sustituir a «{cambia}» (campo "titulos_sugeridos"), con estas reglas:
{reglas}
   - Español bien escrito: CON todas sus tildes (á, é, í, ó, ú, ñ) y con los signos de apertura ¿ ¡ — un título
     sin tildes («trafico», «segun», «Como ven») se ve descuidado en YouTube.
Responde SOLO con este JSON: {{"titulos_sugeridos": ["...", "...", "..."]}}"""


def _norma(t: str) -> str:
    """Para comparar títulos: sin tildes, sin mayúsculas, sin signos ni espacios de más."""
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def texto_montado(words: list[dict], segments: list | None) -> str:
    """Las palabras que se OYEN en el vídeo montado: las que caen dentro de `segments` (los tramos del original que
    sobreviven, del manifest). Recortado a `MAX_CHARS_TEXTO` si el vídeo es muy largo (el principio: ahí va el gancho;
    los capítulos cuentan el resto)."""
    tramos = [(float(a), float(b)) for a, b in (segments or [])]
    out = []
    for w in words or []:
        try:
            t = (float(w.get("start", 0)) + float(w.get("end", 0))) / 2
        except (TypeError, ValueError):
            continue
        if not tramos or any(a <= t <= b for a, b in tramos):
            out.append(str(w.get("word", "")).strip())
    texto = re.sub(r"\s+", " ", " ".join(out)).strip()
    if len(texto) > MAX_CHARS_TEXTO:
        texto = texto[:MAX_CHARS_TEXTO].rsplit(" ", 1)[0] + " […]"
    return texto


def _plana(anteriores) -> list[str]:
    """`titulos_anteriores` como lista plana de títulos (tolera el formato de antes: una lista de sets)."""
    out: list[str] = []
    for x in anteriores or []:
        for t in (x if isinstance(x, list) else [x]):
            t = str(t or "").strip()
            if t and t not in out:
                out.append(t)
    return out


def vistos_de(entrada: dict) -> list[str]:
    """Todos los títulos que ya salieron para esta versión (el de ahora, los sugeridos y los anteriores), sin repetir."""
    todos = [entrada.get("titulo") or ""] + list(entrada.get("titulos_sugeridos") or []) + _plana(
        entrada.get("titulos_anteriores"))
    out, ya = [], set()
    for t in todos:
        t = str(t or "").strip()
        if t and _norma(t) not in ya:
            ya.add(_norma(t))
            out.append(t)
    return out


def arma_prompt(entrada: dict, words: list[dict], contexto: str, vistos: list[str], cambia: str,
                quedan: list[str]) -> str:
    from .director import REGLAS_TITULOS
    return _PROMPT.format(
        contexto=contexto or "canal de YouTube", titulo=entrada.get("titulo") or "(sin título)",
        gancho=entrada.get("gancho") or "(sin gancho)",
        capitulos=str(entrada.get("capitulos") or "").strip() or "(sin capítulos)",
        texto=texto_montado(words, entrada.get("segments")) or "(sin transcripción)",
        quedan="\n".join(f"- {t}" for t in quedan) or "- (ninguno)", cambia=cambia or "(vacío)",
        vistos="\n".join(f"- {t}" for t in vistos) or "- (ninguno)", reglas=REGLAS_TITULOS)


def parsea(texto: str, vistos: list[str]) -> list[str]:
    """El JSON de la IA → hasta 3 títulos NUEVOS (ni repetidos entre sí ni ya vistos, comparados sin tildes ni signos).
    Lanza si no queda ninguno: así la cadena prueba con el siguiente Flash-Lite."""
    from .director import _lista_textos
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    d = json.loads(t)
    lista = d.get("titulos_sugeridos") if isinstance(d, dict) else d
    ya = {_norma(v) for v in vistos}
    out = []
    for s in _lista_textos(lista, 100, tope_n=10):
        s = re.sub(r"\s+", " ", s.strip().strip('"«»“”').strip())[:80].strip()
        if s and _norma(s) and _norma(s) not in ya:
            ya.add(_norma(s))
            out.append(s)
    if not out:
        raise ValueError("la IA solo devolvió títulos que ya habías visto")
    return out[:3]


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Titulos(BaseModel):
                titulos_sugeridos: list[str]

            _ESQUEMA = Titulos
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def _lee_json(p: Path, defecto=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _escribe_json(p: Path, d) -> None:
    """Atómico: nunca deja un manifest a medias (si se corta la luz, queda el de antes)."""
    tmp = Path(p).with_suffix(Path(p).suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _error_claro(exc: Exception) -> str:
    from .director import _motivo_corto, es_saturacion
    msg = str(exc).lower()
    if es_saturacion(exc):
        return ("Gemini (Flash-Lite) está saturado ahora mismo. Prueba otra vez en un minuto: los intentos saturados no "
                "gastan tu cuota.")
    if "exceeded your current quota" in msg or "quota exceeded" in msg:
        return ("Se acabaron por hoy las peticiones gratis de Flash-Lite (500 al día). Vuelve a intentarlo mañana: la "
                "cuenta se reinicia a las 2:00 a. m.")
    if "ya habías visto" in msg:
        return "La IA solo devolvió títulos que ya habías visto. Dale otra vez al ↻."
    return f"No se pudieron pedir títulos nuevos ({_motivo_corto(str(exc))}). Prueba otra vez en un momento."


def renueva(out_dir: Path, work: Path, slug: str, cfg, indice: int, jid: str = "", _gemini=None,
            _modelos=None) -> dict:
    """Cambia SOLO el título sugerido número `indice` (0, 1 o 2) de la versión de `out_dir` (videos/<jid>/) por uno
    nuevo de Flash-Lite, y lo guarda (manifest, videos.json y el plan `slug` de `work`). El sustituido pasa a
    `titulos_anteriores`. Devuelve {"ok": True, "titulos": [...], "anteriores": [...], "indice", "nuevo", "modelo",
    "nombre"} o {"ok": False, "error": qué pasó y qué hacer}. Nunca lanza. `_gemini` = (client, types) y `_modelos` = la
    cadena, para las pruebas."""
    out_dir, work = Path(out_dir), Path(work)
    man = _lee_json(out_dir / "manifest.json")
    if not isinstance(man, list) or not man or not isinstance(man[0], dict):
        return {"ok": False, "error": "No encuentro el manifest.json de este vídeo (¿lo borraste del PC?)."}
    entrada = man[0]
    actuales = [str(t or "").strip() for t in (entrada.get("titulos_sugeridos") or [])] or [""]
    if isinstance(indice, bool) or not isinstance(indice, int) or not 0 <= indice < len(actuales):
        return {"ok": False, "error": "Ese título ya no está en la lista: recarga la página."}
    cambia = actuales[indice]
    quedan = [t for j, t in enumerate(actuales) if j != indice and t]
    canal_id = str(entrada.get("tema") or "")
    info = ((cfg.get("videos") or {}).get("canales") or {}).get(canal_id) or {}
    nombre_canal = str(info.get("nombre") or entrada.get("tematica") or canal_id or "de YouTube")
    words = _lee_json(work / "words.json", []) or []
    vistos = vistos_de(entrada)
    prompt = arma_prompt(entrada, words if isinstance(words, list) else [], str(info.get("contexto") or ""), vistos,
                         cambia, quedan)
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return {"ok": False, "error": "No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso»."}
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        from . import uso as _uso
        _uso.contexto(job=jid or out_dir.name)
        try:
            candidatos, modelo, _gasto = _util.call_gemini_cadena(
                client, types, _modelos, prompt, system=_SYSTEM.format(canal=nombre_canal),
                valida=lambda t: parsea(t, vistos), reintento_s=20.0, pasadas=1, timeout_s=60.0, esquema=_esquema(),
                etiqueta="titulos")
        finally:
            _uso.contexto(job=None)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de Gemini: se dice qué pasó, el vídeo no se toca
        log(f"«Otro título» ({out_dir.name}): {exc}")
        return {"ok": False, "error": _error_claro(exc)}

    nuevo = candidatos[0]                                  # (la IA los ordena del mejor al tercero)
    actuales[indice] = nuevo
    actuales = [t for t in actuales if t]
    anteriores = ([cambia] if cambia else []) + [t for t in _plana(entrada.get("titulos_anteriores")) if t != cambia]
    anteriores = anteriores[:MAX_ANTERIORES]
    try:
        entrada["titulos_sugeridos"], entrada["titulos_anteriores"] = actuales, anteriores
        _escribe_json(out_dir / "manifest.json", man)
        rep = _lee_json(out_dir / "videos.json")
        if isinstance(rep, dict):
            rep["titulos_sugeridos"] = actuales
            _escribe_json(out_dir / "videos.json", rep)
        from . import director
        plan = director.cargar(work, slug)
        if plan is not None:              # «Reintentar» rehace el manifest desde el plan: que conserve los nuevos
            plan["titulos_sugeridos"], plan["titulos_anteriores"] = actuales, anteriores
            director.guardar(work, slug, plan)
    except OSError as exc:
        return {"ok": False, "error": f"El título salió pero no se pudo guardar ({exc})."}
    log(f"«Otro título» ({out_dir.name}, {nombre_modelo(modelo)}): «{cambia}» -> «{nuevo}»")
    return {"ok": True, "titulos": actuales, "anteriores": anteriores, "indice": indice, "nuevo": nuevo,
            "modelo": modelo, "nombre": nombre_modelo(modelo)}


_PROMPT_TAGS = """Contexto del canal: {contexto}

Vídeo de YouTube ya editado. Título: «{titulo}».
Capítulos:
{capitulos}

Lo que se dice en el vídeo (transcripción del vídeo montado):
{texto}

Escribe EXACTAMENTE 5 etiquetas SEO de ESTE vídeo en concreto (no del canal en general), en español, de 1 a 4 palabras
cada una, con sus tildes: lo que alguien escribiría en YouTube o Google para encontrarlo — el tema principal, la
herramienta o función concreta, el problema que resuelve, el término técnico clave. La primera, la más importante. Sin
«#», sin repetirse y sin el nombre del canal.
Responde SOLO con este JSON: {{"tags": ["...", "...", "...", "...", "..."]}}"""


def tags_para(titulo: str, capitulos: str, words: list[dict], segs: list, contexto: str, canal: str, cfg, jid: str = "",
              _gemini=None, _modelos=None) -> list[str]:
    """Las 5 etiquetas SEO de un vídeo cuyo plan es de ANTES de pedirlas en el análisis (el autor, 2026-09-25: «todos repiten
    el mismo tag»): UNA petición a Flash-Lite, una sola vez (quien llama las guarda en el plan). [] si no se puede."""
    from .director import limpia_tags
    prompt = _PROMPT_TAGS.format(contexto=contexto or "canal de YouTube", titulo=titulo or "(sin título)",
                                 capitulos=capitulos or "(sin capítulos)",
                                 texto=texto_montado(words, segs) or "(sin transcripción)")
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return []
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        crudo, _modelo, _u = _util.call_gemini_cadena(
            client, types, _modelos, prompt, system=_SYSTEM.format(canal=canal or "de YouTube"),
            valida=lambda t: json.loads(t.strip().strip("`").removeprefix("json").strip()), reintento_s=20.0, pasadas=1,
            timeout_s=60.0, etiqueta="tags")
        return limpia_tags(crudo.get("tags") if isinstance(crudo, dict) else crudo)
    except Exception as exc:  # noqa: BLE001 — sin etiquetas nuevas, el vídeo sigue igual
        log(f"Etiquetas: no se pudieron pedir ({str(exc)[:120]}); sigo sin ellas.")
        return []


def prueba() -> list[dict]:
    """Sin red ni Gemini (uno falso): el prompt lleva lo que se oye en el vídeo montado, las reglas del director, los
    títulos que se quedan, el que se cambia y los ya vistos; lo que devuelve la IA se limpia; SOLO cambia ese título y el
    sustituido pasa a «anteriores» en el manifest, videos.json y el plan; un fallo no toca nada."""
    import shutil
    import tempfile
    from . import config, director
    from .director import REGLAS_TITULOS
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": detalle})

    words = [{"word": "Hola", "start": 0.5, "end": 0.8}, {"word": "silencioCORTADO", "start": 12.0, "end": 12.5},
             {"word": "Search", "start": 21.0, "end": 21.4}, {"word": "Console", "start": 21.4, "end": 21.9}]
    texto = texto_montado(words, [[0.0, 10.0], [20.0, 30.0]])
    r("la transcripción es la del vídeo MONTADO (sin lo recortado)", texto == "Hola Search Console", texto)

    vistos = ["Cómo medir la búsqueda multimodal", "Search Console ya mide Circle to Search"]
    limpio = parsea(json.dumps({"titulos_sugeridos": ["«como medir la busqueda MULTIMODAL!»", "  Nuevo   título  A ",
                                                      "Nuevo título A", "Otro ángulo B", "Tercero C", "Cuarto D"]}), vistos)
    r("de lo que devuelve la IA: fuera los ya vistos (aunque cambien tildes, mayúsculas o signos), sin repetir, máx. 3",
      limpio == ["Nuevo título A", "Otro ángulo B", "Tercero C"], limpio)
    try:
        parsea(json.dumps({"titulos_sugeridos": ["Cómo medir la búsqueda multimodal"]}), vistos)
        r("si solo repite títulos ya vistos, cuenta como fallo (prueba el siguiente modelo)", False)
    except ValueError:
        r("si solo repite títulos ya vistos, cuenta como fallo (prueba el siguiente modelo)", True)
    r("el historial de antes (sets) se lee como lista plana", _plana([["A", "B"], "C", ["A"]]) == ["A", "B", "C"])

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-titulos-"))
    real = _util.call_gemini_cadena
    try:
        out_dir, work = tmp / "videos" / "x--pro-v2", tmp / "work" / "x--pro"
        out_dir.mkdir(parents=True)
        work.mkdir(parents=True)
        entrada = {"archivo": "pro.mp4", "tema": "ejemplo", "titulo": "Título de ahora",
                   "titulos_sugeridos": ["Viejo 1", "Viejo 2", "Viejo 3"], "gancho": "Un gancho",
                   "capitulos": "0:00 Intro\n1:00 Lo nuevo", "segments": [[0.0, 10.0], [20.0, 30.0]]}
        (out_dir / "manifest.json").write_text(json.dumps([entrada], ensure_ascii=False), encoding="utf-8")
        (out_dir / "videos.json").write_text(json.dumps({"titulos_sugeridos": ["Viejo 1", "Viejo 2", "Viejo 3"]}),
                                             encoding="utf-8")
        (work / "words.json").write_text(json.dumps(words), encoding="utf-8")
        director.guardar(work, "pro-v2", {"titulos_sugeridos": ["Viejo 1", "Viejo 2", "Viejo 3"], "recortes": []})
        cfg = config.load()
        visto: dict = {}
        respuestas = [json.dumps({"titulos_sugeridos": ["Nuevo A", "Nuevo B", "Nuevo C"]}),
                      json.dumps({"titulos_sugeridos": ["Viejo 1", "Nuevo A", "Otro D", "Otro E"]})]

        def falso(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            visto.update(prompt=prompt, system=system, modelos=modelos, etiqueta=etiqueta)
            return valida(respuestas.pop(0)), modelos[0], {"entrada": 100, "salida": 20}

        _util.call_gemini_cadena = falso
        r1 = renueva(out_dir, work, "pro-v2", cfg, 1, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"])
        man = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
        plan = director.cargar(work, "pro-v2")
        rep = json.loads((out_dir / "videos.json").read_text(encoding="utf-8"))
        r("↻ en el 2.º: SOLO cambia ese (por el mejor candidato) y el sustituido pasa a «anteriores» — en el manifest, en "
          "videos.json y en el plan de ESA versión",
          r1.get("ok") and man["titulos_sugeridos"] == ["Viejo 1", "Nuevo A", "Viejo 3"]
          and man["titulos_anteriores"] == ["Viejo 2"] and plan["titulos_sugeridos"] == ["Viejo 1", "Nuevo A", "Viejo 3"]
          and plan["titulos_anteriores"] == ["Viejo 2"] and rep["titulos_sugeridos"] == ["Viejo 1", "Nuevo A", "Viejo 3"]
          and plan["recortes"] == [], (r1, man, plan))
        p = visto.get("prompt", "")
        r("la IA ve lo del vídeo montado, los capítulos, el gancho, las MISMAS reglas del director, los que se quedan, el "
          "que se cambia y los ya vistos",
          "Hola Search Console" in p and "silencioCORTADO" not in p and "1:00 Lo nuevo" in p and "Un gancho" in p
          and REGLAS_TITULOS.strip() in p and "cambiar SOLO este: «Viejo 2»" in p and "- Viejo 1\n- Viejo 3" in p
          and "- Título de ahora" in p, p[-1200:])
        r("va a Flash-Lite y se apunta en «Uso» como «titulos»",
          visto.get("modelos") == ["gemini-3.5-flash-lite"] and visto.get("etiqueta") == "titulos", visto.get("etiqueta"))
        r2 = renueva(out_dir, work, "pro-v2", cfg, 0, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"])
        man = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))[0]
        r("otro ↻ (en el 1.º): no repite nada ya visto (salta «Viejo 1» y «Nuevo A») y los sustituidos se acumulan, el "
          "más reciente primero",
          r2.get("ok") and man["titulos_sugeridos"] == ["Otro D", "Nuevo A", "Viejo 3"]
          and man["titulos_anteriores"] == ["Viejo 1", "Viejo 2"] and "- Viejo 2" in visto.get("prompt", ""), (r2, man))
        r("un número de título que no existe: lo dice y no toca nada",
          not renueva(out_dir, work, "pro-v2", cfg, 7, _gemini=(object(), object()), _modelos=["x"]).get("ok"))

        def saturado(*a, **kw):
            raise RuntimeError("503 UNAVAILABLE: high demand")

        _util.call_gemini_cadena = saturado
        antes = (out_dir / "manifest.json").read_text(encoding="utf-8")
        r3 = renueva(out_dir, work, "pro-v2", cfg, 2, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"])
        r("Gemini saturado: dice qué pasa y qué hacer, y no toca nada",
          not r3.get("ok") and "saturado" in r3.get("error", "")
          and (out_dir / "manifest.json").read_text(encoding="utf-8") == antes, r3)
        r("sin manifest: lo dice (no revienta)", not renueva(tmp / "no-existe", work, "pro", cfg, 0).get("ok"))

        # las 5 ETIQUETAS SEO (el autor, 2026-09-25: «todos están repitiendo el mismo tag»)
        from . import enlaces
        from .director import limpia_tags
        r("etiquetas limpias: sin «#», sin repetirse (aunque cambien mayúsculas), como mucho 5",
          limpia_tags(["#SEO para Google", "seo para google", " Search  Console ", "x", "Búsqueda multimodal", "IA", "b1",
                       "c2"]) == ["SEO para Google", "Search Console", "Búsqueda multimodal", "IA", "b1"])
        r("en la descripción, como hashtags que se leen («SEO para Google» -> «#SEOparaGoogle»)",
          enlaces.hashtags_de(["SEO para Google", "Search Console", "búsqueda multimodal", "IA y visibilidad"])
          == "#SEOparaGoogle #SearchConsole #BúsquedaMultimodal #IAyVisibilidad",
          enlaces.hashtags_de(["SEO para Google", "Search Console", "búsqueda multimodal", "IA y visibilidad"]))
        vistos_t: dict = {}

        def falso_tags(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            vistos_t.update(prompt=prompt, modelos=modelos, etiqueta=etiqueta)
            return valida(json.dumps({"tags": ["#Search Console", "SEO para Google", "search console", "Búsqueda por "
                                               "imagen", "Circle to Search", "IA", "otra más"]})), modelos[0], {}
        _util.call_gemini_cadena = falso_tags
        tg = tags_para("Nueva métrica en Search Console", "0:00 Intro", words, [[0.0, 10.0], [20.0, 30.0]], "",
                       "Mi canal", cfg, _gemini=(object(), object()), _modelos=["gemini-3.5-flash-lite"])
        r("vídeo ya analizado: sus 5 etiquetas salen de UNA petición a Flash-Lite (con lo que se dice en el vídeo)",
          tg == ["Search Console", "SEO para Google", "Búsqueda por imagen", "Circle to Search", "IA"]
          and vistos_t.get("modelos") == ["gemini-3.5-flash-lite"] and vistos_t.get("etiqueta") == "tags"
          and "Hola Search Console" in vistos_t.get("prompt", ""), (tg, vistos_t.get("etiqueta")))
    finally:
        _util.call_gemini_cadena = real
        shutil.rmtree(tmp, ignore_errors=True)
    return out
