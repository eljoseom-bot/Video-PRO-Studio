"""El RITMO del vídeo: el «metrónomo» y las PALABRAS CLAVE (el autor, 2026-09-25).

el autor pasó un vídeo de Alex Hormozi y pidió tener «fijada» su frecuencia de eventos. Medido fotograma a fotograma con el
MISMO contador en los dos (un cambio de plano, y cada vez que un texto o un gráfico entra o sale): Hormozi, 11,2 cambios
por minuto y solo el 21 % del tiempo en tramos de más de 15 s sin que cambie nada (el más largo, 35 s); las 4 versiones
del autor del 24-sep, 4,9-10,6 por minuto y el 39-73 % del tiempo en esos tramos muertos (hasta 58 s con el mismo plano).
La diferencia no era tanto la media como los tramos muertos. Dos piezas, aprobadas por el autor («haz el A y B de una»):

A) El METRÓNOMO (`elige`): ningún tramo de más de `videos.ritmo.max_sin_cambio_seg` (15 s) sin un cambio visual y, si el
   vídeo va por debajo, subir hacia `objetivo_por_min` (10, contado como en el de Hormozi). Rellena con palabras clave
   (B): son una CAPA de texto (un .ass quemado en el mismo pase de ffmpeg), así que no añaden tramos al render —la RAM
   del render crece con cada tramo— ni tocan el audio. Qué cuenta como cambio: un cambio de cámara, una tarjeta /
   imagen / ilustración / palabra clave que entra o sale, y la pantalla que se MUEVE de verdad (`movimiento.py`): en una
   demostración con el ratón la acción ya está en la pantalla, así que ahí no hace falta rellenar, y ninguna palabra
   clave sale encima de una demostración.
B) Las PALABRAS CLAVE: 1-3 palabras en grande, abajo en el centro, que salen justo cuando las dices (el segundo de esa
   palabra en la transcripción, afinado con el audio si viene tras una pausa: Whisper las apunta tarde al salir de un
   silencio), con la letra y los colores de la marca y un «pop» al entrar. Las propone el director en su llamada de
   siempre (`palabras_clave`, con importancia 1-3: 0 peticiones más). Un plan de antes no las trae y el programa saca
   las suyas de la transcripción: las cifras con su unidad («90%», «100 MIL PESOS») y las etiquetas SEO del vídeo
   cuando las dices. Nunca encima de una tarjeta (usan la misma franja de abajo) ni de una escena «explicativa»; sin
   sonido; la misma palabra no se repite en 90 s.

Funciones puras + `prueba()` (en «Probar el sistema»); `aplica` es lo que llama el pipeline.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from pathlib import Path

from .util import ass_time, normalize

VERSION = 1

_DEF = {
    "activo": True,
    "max_sin_cambio_seg": 15.0,     # ningún tramo muerto más largo que esto (si hay algo que decir ahí)
    "objetivo_por_min": 10.0,       # cambios visuales por minuto (fuera de las demostraciones) a los que se sube
    "palabras_activo": True,
    "palabras_por_codigo": True,    # cifras y etiquetas sacadas de la transcripción (planes sin palabras de la IA)
    "max_por_min": 6.0,             # palabras clave como mucho en cualquier minuto
    "separacion_seg": 4.0,          # entre el inicio de una y el de la siguiente
    "duracion_min_seg": 1.5,
    "duracion_max_seg": 2.5,
    "margen_tarjetas_seg": 0.4,     # aire con una tarjeta (misma franja de abajo) antes y después
    "inicio_min_seg": 0.6,          # el vídeo entra con un fundido de 0,5 s
    "final_libre_seg": 1.5,
    "no_repetir_seg": 90.0,
    "tam_1080": 104,                # tamaño de la letra con un vídeo de 1080 de alto
    "margen_abajo_1080": 150,       # distancia al borde de abajo
}
MAX_CARACTERES = 36                  # una frase corta ENTERA cabe en dos líneas (el autor, 2026-09-26: con 24 la IA cortaba
MAX_PALABRAS = 6                     # «¿Cómo estás apareciendo en las IAs?» y salía el trozo «¿EN LAS IAS?»)
_LINEA = 18                          # caracteres por línea al tamaño normal (1920 de ancho); más largo = dos líneas
_MODOS_PANTALLA = ("pantalla", "pantalla_zoom", "ambos")

# palabras «de unión»: no sirven para reconocer dónde dices algo
_VACIAS = {"de", "del", "la", "las", "el", "los", "en", "para", "por", "y", "o", "u", "un", "una", "unos", "unas", "con",
           "a", "al", "que", "se", "lo", "su", "sus", "es", "mi", "tu", "le", "les", "me", "te", "muy", "mas"}
# unidades que acompañan a una cifra («100 mil pesos»); la clave va sin tildes (como la deja `_clave`)
_UNIDADES = {
    "mil": "MIL", "millon": "MILLÓN", "millones": "MILLONES", "billones": "BILLONES", "dolares": "DÓLARES",
    "dolar": "DÓLAR", "pesos": "PESOS", "euros": "EUROS", "soles": "SOLES", "minutos": "MINUTOS", "segundos": "SEGUNDOS",
    "horas": "HORAS", "dias": "DÍAS", "semanas": "SEMANAS", "meses": "MESES", "anos": "AÑOS", "veces": "VECES",
    "visitas": "VISITAS", "clics": "CLICS", "personas": "PERSONAS", "usuarios": "USUARIOS", "videos": "VÍDEOS",
    "suscriptores": "SUSCRIPTORES", "seguidores": "SEGUIDORES", "palabras": "PALABRAS", "paginas": "PÁGINAS",
    "articulos": "ARTÍCULOS", "impresiones": "IMPRESIONES", "empresas": "EMPRESAS", "clientes": "CLIENTES",
    "ventas": "VENTAS", "posiciones": "POSICIONES", "enlaces": "ENLACES", "backlinks": "BACKLINKS",
}
_PUNT = " .,;:¿?¡!\"'()[]«»…-–—"
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def parametros(cfg) -> dict:
    """`videos.ritmo` de config.yaml encima de los valores por defecto (un valor raro no rompe nada: se ignora)."""
    p = dict(_DEF)
    try:
        propios = cfg.get_path("videos.ritmo") or {}
    except Exception:  # noqa: BLE001
        propios = {}
    for k, v in (propios.items() if isinstance(propios, dict) else []):
        if k not in p or v is None:
            continue
        try:
            p[k] = bool(v) if isinstance(p[k], bool) else type(p[k])(v)
        except (TypeError, ValueError):
            pass
    return p


# ------------------------------------------------------------------------------------------ palabras y dónde se dicen
def _limpia(s) -> str:
    return str(s or "").strip().strip(_PUNT)


def _clave(s) -> str:
    """Una palabra para COMPARAR: sin tildes, mayúsculas ni signos; una cifra, con punto decimal y sin «%»."""
    t = _limpia(s).replace("%", "").strip()
    if _NUM.fullmatch(t):
        return t.replace(",", ".")
    return "".join(normalize(t).split())


def _parecidas(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a[0].isdigit() or b[0].isdigit():
        return a == b                                   # una cifra, exacta
    return a == b or (min(len(a), len(b)) >= 2 and SequenceMatcher(None, a, b).ratio() >= 0.75)


def _tokens(texto: str) -> list[str]:
    """Las palabras con contenido de un texto (sin las de unión, salvo que no haya otras)."""
    toks = [c for c in (_clave(x) for x in str(texto or "").split()) if c]
    return [c for c in toks if c not in _VACIAS] or toks


def _busca(tokens: list[str], claves: list[str], i0: int, holgura: int = 3) -> tuple[int, int, int] | None:
    """¿Se dicen `tokens` en orden empezando en la palabra `i0` (entre medias pueden ir hasta `holgura` palabras
    más)? -> (primera, última, cuántos casaron) o None si la primera no casa."""
    if not tokens or i0 >= len(claves) or not _parecidas(tokens[0], claves[i0]):
        return None
    j, casados, ultima = i0 + 1, 1, i0
    for tk in tokens[1:]:
        k = next((k for k in range(j, min(len(claves), j + 1 + holgura)) if _parecidas(tk, claves[k])), None)
        if k is None:
            continue
        casados, ultima, j = casados + 1, k, k + 1
    return i0, ultima, casados


def ancla(texto: str, t: float, words: list[dict], claves: list[str] | None = None,
          ventana: float = 4.0) -> tuple[int, int, bool] | None:
    """Dónde DICES `texto` cerca del segundo `t` (del original): (palabra inicial, palabra final, exacta). Si no se dice
    tal cual (el director a veces resume), la palabra que empieza justo en `t` con exacta=False; None si no hay."""
    claves = claves if claves is not None else [_clave(w.get("word")) for w in words]
    cont = _tokens(texto)
    if not cont:
        return None
    mejor = None
    for i, w in enumerate(words):
        s = float(w.get("start", 0))
        if s < t - ventana:
            continue
        if s > t + ventana:
            break
        m = _busca(cont, claves, i)
        if m is None:
            continue
        a, b, n = m
        if n / len(cont) < (1.0 if len(cont) == 1 else 0.5):
            continue
        puntos = (n / len(cont), -abs(s - t))
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, a, b)
    if mejor:
        return mejor[1], mejor[2], True
    for i, w in enumerate(words):
        if t - 0.3 <= float(w.get("start", 0)) <= t + 1.5:
            return i, i, False
    return None


# Palabras de duda justo antes de una cifra: es un ESTIMADO tuyo, no un dato (el autor, 2026-09-26: «ese 90% fue un estimado
# mío… que no se vea como que yo estoy inventando esos números»). Sin tildes, como las deja `normalize`.
_DUDA = ("posiblemente", "probablemente", "casi", "mas o menos", "aproximadamente", "aprox", "alrededor de", "cerca de",
         "unos", "unas", "yo diria", "diria", "calculo", "creo", "supongo", "me imagino", "imagino", "a ojo", "por ahi",
         "digamos", "tal vez", "quizas", "quiza", "estimo", "estimado", "puede que", "podria", "seguramente",
         "a lo mejor", "algo asi", "por decir", "yo pienso", "pienso que", "me parece", "a grosso modo", "grosso modo")


def es_estimado(texto, t: float, words: list[dict], ventana_s: float = 4.0) -> bool:
    """¿La cifra de `texto`, dicha cerca del segundo `t` del ORIGINAL, es un estimado u opinión tuya y no un dato? Lo
    dicen las palabras de duda de esa misma frase, justo antes de la cifra o pegadas detrás («muy posiblemente casi que
    el 90 %», «unos 100 mil», «el 70 %, más o menos»)."""
    m = re.search(r"\d+(?:[.,]\d+)?", str(texto or ""))
    if not m or not words:
        return False
    cifra = m.group(0).replace(",", ".")
    cerca = [(abs(float(w.get("start", 0)) - t), i) for i, w in enumerate(words)
             if abs(float(w.get("start", 0)) - t) <= ventana_s and _clave(w.get("word")) == cifra]
    if not cerca:
        return False
    i = min(cerca)[1]
    ini = i
    while ini > 0 and i - ini < 10 and not str(words[ini - 1].get("word", "")).strip().endswith((".", "?", "!")):
        ini -= 1                                      # solo la frase de la cifra (hasta 10 palabras antes)
    fin = i + 1                                       # y hasta 4 palabras después, sin pasar a la frase siguiente
    while fin < len(words) and fin - i <= 4 and not str(words[fin - 1].get("word", "")).strip().endswith((".", "?", "!")):
        fin += 1
    ctx = " " + " ".join(" ".join(normalize(str(w.get("word", ""))).split()) for w in words[ini:fin]) + " "
    return any(f" {h} " in ctx for h in _DUDA)


def _texto_limpio(texto) -> str:
    """Lo que se puede escribir en la pantalla: sin llaves ni barras (son órdenes en un .ass), ≤ 36 caracteres y ≤ 6
    palabras (dos líneas como mucho). '' si no cabe."""
    t = re.sub(r"\s+", " ", str(texto or "").replace("{", "").replace("}", "").replace("\\", "")).strip()
    return t if t and len(t) <= MAX_CARACTERES and len(t.split()) <= MAX_PALABRAS else ""


_ARTICULOS = {"la", "las", "el", "los", "lo", "un", "una", "unos", "unas", "mi", "mis", "tu", "tus", "su", "sus"}
_PREPOSICIONES = {"en", "de", "del", "a", "al", "con", "por", "para", "sobre", "desde", "hasta", "sin", "entre"}
_NO_ACABA = _ARTICULOS | _PREPOSICIONES | {"y", "o", "e", "u", "que", "pero", "porque", "como", "cuando"}


def es_fragmento(texto) -> bool:
    """¿Es un TROZO cortado de una frase y no algo que se entiende solo? «¿EN LAS IAS?», «DE LA WEB», «LAS VENTAS DE»,
    «Y MEJOR»: empieza por conjunción o por preposición + artículo, o acaba colgando. «EN SEGUNDOS», «SIN PAGAR NADA»,
    «LOS 3 ERRORES», «¿CÓMO APARECEMOS EN LAS IAS?» no lo son."""
    toks = [x for x in (" ".join(normalize(str(texto or "")).split())).split() if x]
    if not toks:
        return True
    if toks[0] in {"y", "o", "e", "u", "que", "pero", "porque"}:
        return True
    if len(toks) >= 2 and toks[0] in _PREPOSICIONES and toks[1] in _ARTICULOS:
        return True
    return len(toks) >= 2 and toks[-1] in _NO_ACABA


def filtra_calidad(texto: str, importancia: int, t_dicho: float, words: list[dict]) -> str | None:
    """Por qué NO vale una palabra clave (None = vale). el autor (2026-09-26): «tienen que ser importantes… el mensaje
    principal de esa frase»; «si es solo una palabra, que tenga una relevancia única y especial»; y las cifras solo si
    son un dato, no un estimado suyo."""
    if es_fragmento(texto):
        return "trozo cortado"
    tiene_cifra = any(ch.isdigit() for ch in texto)
    if len(texto.split()) == 1 and not tiene_cifra and importancia < 3:
        return "una sola palabra sin peso de clave"
    if tiene_cifra and es_estimado(texto, t_dicho, words):
        return "cifra que es un estimado"
    return None


def candidatas_director(items, words: list[dict], claves: list[str] | None = None,
                        descartes: list | None = None) -> list[dict]:
    """Las `palabras_clave` del plan, ancladas a la palabra donde las dices. Las que no se dicen tal cual bajan un punto
    de importancia (salen en `t`, pero no se oyen igual). Fuera las que no pasan `filtra_calidad` (se apuntan en
    `descartes`, para el registro)."""
    claves = claves if claves is not None else [_clave(w.get("word")) for w in words]
    out = []
    for it in items or []:
        texto = _texto_limpio(it.get("texto") if isinstance(it, dict) else "")
        if not texto:
            continue
        try:
            t = float(it["t"])
            imp = max(1, min(3, int(it.get("importancia") or 2)))
        except (KeyError, TypeError, ValueError):
            continue
        m = ancla(texto, t, words, claves)
        if m is None:
            continue
        a, b, exacta = m
        motivo = filtra_calidad(texto, imp if exacta else max(1, imp - 1), float(words[a].get("start", t)), words)
        if motivo:
            if descartes is not None:
                descartes.append((texto, motivo))
            continue
        out.append({"ia": a, "ib": b, "texto": texto, "importancia": imp if exacta else max(1, imp - 1),
                    "fuente": "ia", "exacta": exacta})
    return out


def _cifra(base: str) -> str:
    """«3.6» -> «3,6» (decimal en español); «100.000» se queda (miles)."""
    return base.replace(".", ",") if re.fullmatch(r"\d+\.\d{1,2}", base) else base


def candidatas_codigo(words: list[dict], tags=None, claves: list[str] | None = None) -> list[dict]:
    """Sin IA: las CIFRAS que dices con su unidad («90%», «100 MIL PESOS»; una cifra suelta de una sola cifra no) y las
    ETIQUETAS SEO del vídeo cada vez que las dices."""
    claves = claves if claves is not None else [_clave(w.get("word")) for w in words]
    crudos = [_limpia(w.get("word")) for w in words]
    out: list[dict] = []
    for i in range(len(words)):
        base = crudos[i].replace("%", "").strip()
        if not _NUM.fullmatch(base):
            continue
        pct, j = crudos[i].endswith("%"), i
        if not pct and i + 1 < len(words) and crudos[i + 1] == "%":
            pct, j = True, i + 1
        if not pct and i + 2 < len(words) and claves[i + 1] == "por" and claves[i + 2] == "ciento":
            pct, j = True, i + 2
        unidades: list[str] = []
        if not pct:
            k = i + 1
            while k < len(words) and len(unidades) < 2 and claves[k] in _UNIDADES:
                unidades.append(_UNIDADES[claves[k]])
                j, k = k, k + 1
        num = _cifra(base)
        decimal = "," in num and not re.fullmatch(r"\d{1,3}(,\d{3})+", num)
        if pct:
            texto, imp = f"{num}%", 2
        elif unidades:
            texto, imp = f"{num} {' '.join(unidades)}", 2
        elif decimal:
            texto, imp = num, 2
        elif len(base.replace(".", "").replace(",", "")) >= 2:
            texto, imp = num, 1
        else:
            continue                                     # «1», «3»…: casi siempre «una de…», «paso 2»
        prev = out[-1] if out else None
        if prev and prev["texto"] == texto and float(words[i]["start"]) - float(words[prev["ia"]]["start"]) < 2.0:
            continue                                     # «50 50», «100… 100»: la misma cifra repetida al momento
        if es_estimado(texto, float(words[i]["start"]), words):
            continue                                     # «casi que el 90 %»: un estimado tuyo no se subraya como dato
        if len(texto) <= MAX_CARACTERES:
            out.append({"ia": i, "ib": j, "texto": texto, "importancia": imp, "fuente": "cifra", "exacta": True})
    for tag in tags or []:
        texto = _texto_limpio(tag)
        cont = _tokens(texto)
        if not texto or not cont:
            continue
        i = 0
        while i < len(words):
            m = _busca(cont, claves, i)
            if m and m[2] == len(cont):
                out.append({"ia": m[0], "ib": m[1], "texto": texto, "importancia": 2, "fuente": "etiqueta",
                            "exacta": True})
                i = m[1] + 1
            else:
                i += 1
    return sorted(out, key=lambda c: c["ia"])


def _afina(ini: float, fin: float, prev_fin: float | None, audio, nivel) -> float:
    """Tras una pausa, Whisper apunta la palabra tarde (hasta ~0,9 s): el inicio de su sonido de verdad (`voz`)."""
    if audio is None or nivel is None or prev_fin is None or ini - prev_fin < 0.25:
        return ini
    try:
        from . import voz
        isl = voz.islas(audio, max(prev_fin, ini - 1.5), ini + 1.0, nivel)
        k = voz._isla_de({"start": ini, "end": fin}, isl)
    except Exception:  # noqa: BLE001
        return ini
    if k is None:
        return ini
    s = float(isl[k].ini)
    return s if max(prev_fin, ini - 0.9) <= s <= ini + 0.05 else ini


def a_final(cands: list[dict], words: list[dict], mapa, p: dict, audio=None, nivel=None) -> list[dict]:
    """Cada candidata en el tiempo del vídeo YA MONTADO (`mapa`: segundo del original -> segundo final o None si se
    cortó): `t` = 40 ms antes de que empiece a sonar la palabra (que el texto no llegue tarde) y `d` = lo que tardas en
    decirla + 0,9 s, entre `duracion_min_seg` y un máximo que crece con lo largo del texto (una frase de 30 letras
    necesita más tiempo para leerse que «GRATIS»: hasta ~3,8 s)."""
    out = []
    for c in cands:
        wa, wb = words[c["ia"]], words[c["ib"]]
        ini, fin = float(wa["start"]), float(wb["end"])
        prev_fin = float(words[c["ia"] - 1]["end"]) if c["ia"] > 0 else None
        ini = _afina(ini, float(wa.get("end", ini)), prev_fin, audio, nivel)
        t0 = mapa(ini)
        if t0 is None:
            continue
        t1 = mapa(fin)
        habla = (t1 - t0) if (t1 is not None and t1 >= t0) else (fin - ini)
        d_max = float(p["duracion_max_seg"]) + 0.06 * max(0, len(c["texto"]) - 14)
        d = min(d_max, max(p["duracion_min_seg"], habla + 0.9))
        t_ini = max(0.0, t0 - 0.04)
        if t_ini < p["inicio_min_seg"]:
            # la frase del gancho empieza con el vídeo: sale en cuanto acaba el fundido de entrada (en vez de perderla)
            d -= p["inicio_min_seg"] - t_ini
            t_ini = float(p["inicio_min_seg"])
            if d < 1.2:
                continue
        out.append({**c, "t": round(t_ini, 3), "d": round(d, 2)})
    return out


def _sin_repetir_tarjetas(cands: list[dict], tarjetas: list[dict] | None, ventana: float = 8.0) -> list[dict]:
    """Fuera la que dice lo mismo que una tarjeta cercana («90%» con la tarjeta de dato «90%»): sería doble. Y «SUSCRÍBETE»
    o «DALE LIKE»: esas ya tienen su tarjeta propia en todos los vídeos."""
    tj = [(float(t["t"]), " ".join(_tokens(f"{t.get('texto', '')} {t.get('extra', '')}")))
          for t in tarjetas or [] if t.get("tipo") not in ("suscribete", "like")]
    out = []
    for c in cands:
        k = " ".join(_tokens(c["texto"]))
        if k and any(abs(c["t"] - tt) <= ventana and k in txt for tt, txt in tj):
            continue
        if "suscrib" in k or k in ("like", "dale like"):
            continue
        out.append(c)
    return out


# ------------------------------------------------------------------------------------------ el metrónomo
def _une(tramos: list[tuple[float, float]], junta: float = 0.05) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(tramos):
        if out and a <= out[-1][1] + junta:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def eventos_base(segs: list[tuple[float, float]], modos: list[str] | None, overlays: list[dict] | None) -> list[float]:
    """Los cambios que ya trae el vídeo (tiempo final): cada cambio de cámara y cada tarjeta, imagen o ilustración que
    entra o sale."""
    evs, acum = [], 0.0
    for i in range(len(segs) - 1):
        acum += max(0.10, segs[i][1] - segs[i][0])
        if modos and i + 1 < len(modos) and modos[i] != modos[i + 1]:
            evs.append(acum)
    for ov in overlays or []:
        evs += [float(ov["inicio"]), float(ov["fin"])]
    return sorted(evs)


def vivos(segs: list[tuple[float, float]], modos: list[str] | None, actividad: list[dict] | None) -> list[tuple[float, float]]:
    """Dónde la PANTALLA se mueve de verdad mientras se ve (tiempo final): ahí no hay tramo muerto que rellenar y
    ninguna palabra clave tapa la demostración."""
    out, acum = [], 0.0
    modos = modos or ["pantalla"] * len(segs)
    for (a, b), m in zip(segs, modos):
        if m in _MODOS_PANTALLA:
            for v in actividad or []:
                x0, x1 = max(a, float(v["inicio"])), min(b, float(v["fin"]))
                if x1 - x0 > 0.05:
                    out.append((acum + x0 - a, acum + x1 - a))
        acum += b - a
    return _une(out)


def huecos(eventos: list[float], vivos_: list[tuple[float, float]], dur: float,
           mas_de: float = 0.0) -> list[tuple[float, float]]:
    """Los tramos MUERTOS (sin ningún cambio y sin la pantalla moviéndose) de más de `mas_de` s."""
    puntos = sorted({0.0, dur, *(e for e in eventos if 0.0 < e < dur)})
    out = []
    for p, q in zip(puntos, puntos[1:]):
        trozos = [(p, q)]
        for a, b in vivos_:
            nuevos = []
            for x, y in trozos:
                if b <= x or a >= y:
                    nuevos.append((x, y))
                    continue
                if a > x:
                    nuevos.append((x, a))
                if b < y:
                    nuevos.append((b, y))
            trozos = nuevos
        out += [(x, y) for x, y in trozos if y - x > mas_de]
    return out


def por_minuto(eventos: list[float], vivos_: list[tuple[float, float]], dur: float) -> float:
    """Cambios por minuto FUERA de las demostraciones (dentro, la pantalla ya se mueve)."""
    muerto = dur - sum(b - a for a, b in vivos_)
    if muerto < 20.0:
        return math.inf
    n = sum(1 for e in eventos if 0.0 < e < dur and not any(a <= e <= b for a, b in vivos_))
    return n / (muerto / 60.0)


def _con(base: list[float], palabras: list[dict]) -> list[float]:
    return sorted(base + [x for c in palabras for x in (c["t"], c["t"] + c["d"])])


def elige(cands: list[dict], base: list[float], vivos_: list[tuple[float, float]], ocupados: list[tuple[float, float]],
          dur: float, p: dict) -> list[dict]:
    """El METRÓNOMO: qué palabras clave salen. (1) las que la IA marca como clave (importancia 3); (2) en cada tramo
    muerto de más de `max_sin_cambio_seg`, la mejor que lo parte (importancia y cercanía al punto que lo divide en
    partes iguales), hasta que no quede ninguno o no haya nada que decir ahí; (3) si el vídeo sigue por debajo de
    `objetivo_por_min`, las de importancia ≥ 2 que caen más lejos de cualquier otro cambio. Siempre: nada encima de
    `ocupados` (tarjetas, escenas «explicativa») ni de una demostración, `separacion_seg` entre inicios, ≤ `max_por_min`
    en cualquier minuto, sin repetir la misma en `no_repetir_seg`. Determinista."""
    max_gap = float(p["max_sin_cambio_seg"])
    cands = [dict(c, _id=k) for k, c in enumerate(sorted(cands, key=lambda c: (c["t"], -c["importancia"], c["texto"])))]
    elegidas: list[dict] = []
    usados: set[int] = set()

    def cabe(c: dict) -> bool:
        s, e = c["t"], c["t"] + c["d"]
        if s < p["inicio_min_seg"] or e > dur - p["final_libre_seg"]:
            return False
        if any(s < b and e > a for a, b in ocupados) or any(s < b and e > a for a, b in vivos_):
            return False
        norm = " ".join(_tokens(c["texto"]))
        for x in elegidas:
            if abs(x["t"] - s) < p["separacion_seg"] or (s < x["t"] + x["d"] and e > x["t"]):
                return False
            if " ".join(_tokens(x["texto"])) == norm and abs(x["t"] - s) < p["no_repetir_seg"]:
                return False
        # ≤ `max_por_min` en CUALQUIER ventana de 60 s (no solo alrededor de la nueva: las de antes también cuentan)
        ts = sorted([x["t"] for x in elegidas] + [s])
        j = 0
        for i, ti in enumerate(ts):
            while ti - ts[j] >= 60.0:
                j += 1
            if i - j + 1 > p["max_por_min"]:
                return False
        return True

    def pon(c: dict) -> None:
        elegidas.append(c)
        usados.add(c["_id"])

    for c in cands:                                                   # (1) las clave
        if c.get("fuente") == "ia" and c["importancia"] >= 3 and cabe(c):
            pon(c)
    for _ in range(len(cands) + 1):                                  # (2) los tramos muertos
        puesto = False
        for a, b in sorted(huecos(_con(base, elegidas), vivos_, dur, max_gap), key=lambda g: (g[0] - g[1], g[0])):
            dentro = [c for c in cands if c["_id"] not in usados and a < c["t"] and c["t"] + c["d"] < b and cabe(c)]
            if not dentro:
                continue
            partes = max(2, math.ceil((b - a) / max_gap))
            objetivo = a + (b - a) / partes
            pon(max(dentro, key=lambda c: (c["importancia"] * 4 - abs(c["t"] - objetivo) / 2, -c["t"])))
            puesto = True
            break
        if not puesto:
            break
    while por_minuto(_con(base, elegidas), vivos_, dur) < p["objetivo_por_min"]:   # (3) hacia el objetivo
        evs = _con(base, elegidas)
        resto = [c for c in cands if c["_id"] not in usados and c["importancia"] >= 2 and cabe(c)]
        if not resto:
            break
        pon(max(resto, key=lambda c: (min(min((abs(e - c["t"]) for e in evs), default=dur), 20.0) + 2 * c["importancia"],
                                      -c["t"])))
    return sorted(({k: v for k, v in c.items() if k != "_id"} for c in elegidas), key=lambda c: c["t"])


def resumen(eventos: list[float], vivos_: list[tuple[float, float]], dur: float, p: dict) -> dict:
    """Cómo queda el ritmo (para el registro, el manifest y el panel)."""
    muertos = huecos(eventos, vivos_, dur)
    largos = [(a, b) for a, b in muertos if b - a > float(p["max_sin_cambio_seg"])]
    return {"cambios_min": round(sum(1 for e in eventos if 0.0 < e < dur) / (dur / 60.0), 1) if dur > 0 else 0.0,
            "huecos_largos": len(largos),
            "max_hueco_s": round(max((b - a for a, b in muertos), default=0.0), 1),
            "pct_en_huecos_largos": round(100.0 * sum(b - a for a, b in largos) / dur) if dur > 0 else 0,
            "max_sin_cambio_seg": float(p["max_sin_cambio_seg"])}


# ------------------------------------------------------------------------------------------ el dibujo (.ass)
def _ass_color(hexa: str) -> str:
    """#RRGGBB -> &H00BBGGRR (ASS guarda los colores al revés)."""
    h = (hexa or "#FFFFFF").lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def familia_fuente(ruta) -> tuple[str, Path | None]:
    """(nombre de la letra para el .ass, carpeta donde está) de `videos.marca.fuente_titulo` — «Rubik ExtraBold»,
    fonts/ — leído del propio archivo, así sirve la letra de cualquier canal."""
    from .config import ROOT
    p = Path(str(ruta or "fonts/Rubik-ExtraBold.ttf"))
    p = p if p.is_absolute() else ROOT / p
    try:
        from PIL import ImageFont
        fam, estilo = ImageFont.truetype(str(p), 20).getname()
        nombre = fam if not estilo or estilo.lower() in ("regular", "normal") else f"{fam} {estilo}"
        return nombre, p.parent
    except Exception:  # noqa: BLE001
        return "Rubik ExtraBold", (ROOT / "fonts")


def _en_lineas(partes: list[str]) -> int:
    """Dónde partir en DOS líneas una frase de más de `_LINEA` caracteres (el corte entre palabras que deja las dos
    líneas más parecidas); 0 = una sola línea."""
    if len(" ".join(partes)) <= _LINEA or len(partes) < 2:
        return 0
    return min(range(1, len(partes)), key=lambda k: max(len(" ".join(partes[:k])), len(" ".join(partes[k:]))))


def _texto_ass(texto: str, crema: str, naranja: str) -> tuple[str, int]:
    """EN MAYÚSCULAS; la cifra (o la última palabra, o la única) en el naranja de la marca, el resto en crema; una frase
    larga en dos líneas. -> (texto del .ass, caracteres de la línea más larga)."""
    partes = texto.upper().split()
    con_cifra = [i for i, x in enumerate(partes) if any(ch.isdigit() for ch in x)]
    realce = set(con_cifra) if con_cifra else {len(partes) - 1}
    corte = _en_lineas(partes)
    trozos = [f"{{\\1c{naranja if i in realce else crema}&}}{x}" for i, x in enumerate(partes)]
    if corte:
        texto_ass = " ".join(trozos[:corte]) + "\\N" + " ".join(trozos[corte:])
        return texto_ass, max(len(" ".join(partes[:corte])), len(" ".join(partes[corte:])))
    return " ".join(trozos), len(" ".join(partes))


def escribe_ass(palabras: list[dict], marca: dict, W: int, H: int, destino: Path, p: dict) -> tuple[Path | None, Path | None]:
    """El .ass de las palabras clave (el render lo quema en el mismo pase). -> (ruta, carpeta de la letra) o (None, None)."""
    if not palabras:
        return None, None
    esc = H / 1080.0
    tam = round(float(p["tam_1080"]) * esc)
    crema, tinta, naranja = (_ass_color(marca.get(k, d)) for k, d in (("crema", "#FFFBF2"), ("tinta", "#14100F"),
                                                                     ("naranja", "#FE330A")))
    fuente, carpeta = familia_fuente(marca.get("fuente_titulo"))
    lineas = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        # contorno de tinta y SOMBRA DURA de tinta (el lenguaje de las tarjetas), abajo en el centro
        f"Style: Clave,{fuente},{tam},{crema},{crema},{tinta},{tinta},0,0,0,0,100,100,1,0,1,{max(2, round(7 * esc))},"
        f"{max(2, round(6 * esc))},2,{round(80 * esc)},{round(80 * esc)},{round(float(p['margen_abajo_1080']) * esc)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for c in palabras:
        texto_ass, largo = _texto_ass(c["texto"], crema, naranja)
        fs = tam if largo <= _LINEA else round(tam * _LINEA / largo)   # cada línea cabe (WrapStyle 2 no parte solo)
        pop = (f"{{\\fs{fs}\\fad(50,140)\\fscx62\\fscy62\\t(0,120,\\fscx110\\fscy110)"
               f"\\t(120,210,\\fscx100\\fscy100)}}")                    # entra con un «pop» y un pequeño rebote
        lineas.append(f"Dialogue: 0,{ass_time(c['t'])},{ass_time(c['t'] + c['d'])},Clave,,0,0,0,,{pop}{texto_ass}")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return destino, carpeta


# ------------------------------------------------------------------------------------------ planes de ANTES
_PROMPT_PALABRAS = """Contexto del canal: {contexto}

Vídeo de YouTube ya editado del canal «{canal}». Esto es lo que se dice, con el segundo en que empieza cada línea:
{texto}

Elige PALABRAS CLAVE: texto que saldrá EN GRANDE abajo, en el centro de la pantalla, justo cuando el presentador lo
dice, para subrayar el MENSAJE PRINCIPAL de ese momento (como en los vídeos de Alex Hormozi). Cada una tiene que
entenderse SOLA y tener sentido completo: o la FRASE corta entera que lleva ese mensaje, tal como la dice (una pregunta o
una afirmación completa: "¿CÓMO APARECEMOS EN LAS IAS?", "NO VENDIÓ NADA"), o UNA palabra o cifra con un peso ÚNICO en
el vídeo (el concepto central, y entonces con importancia 3). NUNCA un trozo cortado de una frase ("¿EN LAS IAS?",
"DE LA WEB"). Máximo 6 palabras y 36 caracteres, en español y con sus tildes. Tiene que ser lo que DICE en ese instante
(tal cual o casi: una cifra dicha con letras va en número), nunca un resumen inventado; una cifra, solo si es un DATO
(un estimado u opinión suya, «casi que el 90 %», no se subraya). Propón MUCHAS, repartidas por TODO el vídeo (más o
menos una cada 8-12 s de habla): el programa elige cuáles salen para que el vídeo nunca pase más de {max_sin_cambio} s
sin que cambie algo en pantalla. Nada de nombres de herramientas sueltos (ya tienen su tarjeta) ni "SUSCRÍBETE". "t" = el
segundo en que EMPIEZA a decir la primera de esas palabras (el de su línea, afinado si puedes); "importancia": 3 = el
mensaje principal (tiene que salir), 2 = buena, 1 = de relleno.
Responde SOLO con este JSON: {{"palabras_clave": [{{"t": 62.4, "texto": "NO VENDIÓ NADA", "importancia": 3}}]}}"""


def _transcripcion_con_tiempos(words: list[dict], segs: list, max_palabras: int = 12, max_caracteres: int = 40000) -> str:
    """Lo que se OYE en el vídeo montado (solo las palabras dentro de `segs`), en líneas cortas con el segundo del
    ORIGINAL en que empieza cada una (las palabras clave van en segundos del original, como el resto del plan)."""
    lineas, actual, t0, ult = [], [], None, None
    for w in words:
        s = float(w.get("start", 0))
        if not any(float(a) - 0.05 <= s <= float(b) for a, b in segs):
            continue
        if actual and (len(actual) >= max_palabras or (ult is not None and s - ult > 1.2)):
            lineas.append(f"[{t0:.1f}] " + " ".join(actual))
            actual = []
        if not actual:
            t0 = s
        actual.append(str(w.get("word", "")).strip())
        ult = float(w.get("end", s))
    if actual:
        lineas.append(f"[{t0:.1f}] " + " ".join(actual))
    texto = "\n".join(lineas)
    return texto[:max_caracteres]


def limpia_palabras(items, dur: float) -> list[dict]:
    """Lo que se puede guardar en el plan: {t, texto, importancia} válidos, dentro del vídeo, en orden."""
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            t = float(it["t"])
            imp = max(1, min(3, int(it.get("importancia") or 2)))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        texto = _texto_limpio(it.get("texto"))
        if texto and 0.0 <= t <= dur:
            out.append({"t": round(t, 2), "texto": texto, "importancia": imp})
    return sorted(out, key=lambda x: x["t"])


def palabras_para(words: list[dict], segs: list, dur: float, contexto: str, canal: str, cfg,
                  _gemini=None, _modelos=None, _llama=None) -> list[dict] | None:
    """Las palabras clave de un vídeo cuyo plan es de ANTES de pedirlas en el análisis: UNA petición a Flash-Lite
    (500 al día; nunca a los Flash de 20 al día), una sola vez — quien llama las guarda en el plan, así «Reintentar» ya
    no vuelve a pedirlas. None si no se pudo (se intentará en el siguiente render); [] si no propuso ninguna válida."""
    import json as _json
    from . import util as _util
    texto = _transcripcion_con_tiempos(words, segs)
    if not texto:
        return []
    p = parametros(cfg)
    prompt = _PROMPT_PALABRAS.format(contexto=contexto or "canal de YouTube", canal=canal or "de YouTube", texto=texto,
                                     max_sin_cambio=int(p["max_sin_cambio_seg"]))
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return None
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from . import modelos as _m
            _modelos = _m.cadena_lite(cfg, client)
        llama = _llama or _util.call_gemini_cadena
        crudo, _modelo, _u = llama(
            client, types, _modelos, prompt, system="Eres el editor de vídeo de un canal de YouTube en español.",
            valida=lambda t: _json.loads(t.strip().strip("`").removeprefix("json").strip()), reintento_s=20.0,
            pasadas=1, timeout_s=90.0, etiqueta="palabras")
        return limpia_palabras(crudo.get("palabras_clave") if isinstance(crudo, dict) else crudo, dur)
    except Exception as exc:  # noqa: BLE001 — sin palabras de la IA, el vídeo sale con las de la transcripción
        from .util import log
        log(f"Palabras clave: no se pudieron pedir ({str(exc)[:120]}); sigo con las de la transcripción.")
        return None


# ------------------------------------------------------------------------------------------ todo junto
def aplica(plan: dict, words: list[dict], segs: list[tuple[float, float]], modos: list[str] | None,
           overlays: list[dict] | None, tarjetas: list[dict] | None, actividad: list[dict] | None, mapa, dur: float,
           marca: dict, W: int, H: int, destino: Path, cfg, explicativas: list[tuple[float, float]] = (),
           audio=None, nivel=None) -> dict:
    """Lo que llama el pipeline tras colocar tarjetas, imágenes e ilustraciones (todo en tiempo FINAL; `mapa` pasa un
    segundo del original al final). -> {"palabras", "ass", "fontsdir", "antes", "despues", "notas"}."""
    p = parametros(cfg)
    base = eventos_base(segs, modos, overlays)
    vivos_ = vivos(segs, modos, actividad)
    antes = resumen(base, vivos_, dur, p)
    res = {"palabras": [], "ass": None, "fontsdir": None, "antes": antes, "despues": antes, "notas": []}
    if not p["activo"] or not p["palabras_activo"] or not words or dur <= 5.0:
        return res
    claves = [_clave(w.get("word")) for w in words]
    descartes: list[tuple[str, str]] = []
    cands = candidatas_director(plan.get("palabras_clave"), words, claves, descartes)
    n_ia = len(cands)
    # las del código (cifras y etiquetas) solo si la IA no dejó ninguna: son más flojas que las suyas (el autor, 2026-09-26:
    # «tienen que ser importantes»; en su vídeo salieron dos «100%» de frases hechas como «digo acá 100 % general»)
    if p["palabras_por_codigo"] and not n_ia:
        cands += candidatas_codigo(words, plan.get("tags"), claves)
    vistas: set[tuple[str, int]] = set()
    unicas = []
    for c in cands:                                   # la IA primero: si el código saca la misma, sobra
        k = (" ".join(_tokens(c["texto"])), c["ia"])
        if k not in vistas:
            vistas.add(k)
            unicas.append(c)
    cands = _sin_repetir_tarjetas(a_final(unicas, words, mapa, p, audio, nivel), tarjetas)
    ocup = _une([(float(o["inicio"]) - p["margen_tarjetas_seg"], float(o["fin"]) + p["margen_tarjetas_seg"])
                 for o in overlays or [] if o.get("ancla") != "top-right"]
                + [(a - p["margen_tarjetas_seg"], b + p["margen_tarjetas_seg"]) for a, b in explicativas])
    elegidas = elige(cands, base, vivos_, ocup, dur, p)
    despues = resumen(_con(base, elegidas), vivos_, dur, p)
    ass, carpeta = escribe_ass(elegidas, marca, W, H, destino, p)
    de_ia = sum(1 for c in elegidas if c["fuente"] == "ia")
    res.update(palabras=[{"t": c["t"], "d": c["d"], "texto": c["texto"], "fuente": c["fuente"],
                          "importancia": c["importancia"]} for c in elegidas],
               ass=ass, fontsdir=carpeta, despues=despues)
    origen = (f"{de_ia} de la IA" + (f", {len(elegidas) - de_ia} de la transcripción" if len(elegidas) > de_ia else "")
              if elegidas else ("la IA propuso " + str(n_ia) if n_ia else "sin candidatas"))
    res["notas"].append(
        f"ritmo: {antes['cambios_min']} -> {despues['cambios_min']} cambios por minuto; tiempo en tramos muertos de más de "
        f"{p['max_sin_cambio_seg']:.0f} s: {antes['pct_en_huecos_largos']} % -> {despues['pct_en_huecos_largos']} % (el más "
        f"largo: {antes['max_hueco_s']:.0f} -> {despues['max_hueco_s']:.0f} s); {len(elegidas)} palabra(s) clave ({origen}).")
    if descartes:
        res["notas"].append(f"palabras de la IA que no pasan el filtro de calidad ({len(descartes)}): " + "; ".join(
            f"«{t}» ({m})" for t, m in descartes[:8]))
    quedan = [(a, b) for a, b in huecos(_con(base, elegidas), vivos_, dur, p["max_sin_cambio_seg"])]
    if quedan:
        res["notas"].append("sin nada que destacar en: " + ", ".join(
            f"{int(a) // 60}:{int(a) % 60:02d} ({b - a:.0f} s)" for a, b in sorted(quedan, key=lambda g: g[0] - g[1])[:5]))
    return res


# ------------------------------------------------------------------------------------------ prueba
def prueba() -> list[dict]:
    """Casos del ritmo y las palabras clave, sin ffmpeg ni Gemini (un .ass en una carpeta temporal)."""
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    def W(w, a, b):
        return {"word": w, "start": a, "end": b}

    p = dict(_DEF)
    ws = [W("Y", 10.0, 10.2), W("no", 10.3, 10.5), W("vendió", 10.55, 10.9), W("nada,", 10.95, 11.3),
          W("pero", 11.5, 11.7), W("el", 11.8, 11.9), W("3,6", 12.0, 12.5), W("%", 12.5, 12.7), W("de", 12.8, 12.9),
          W("las", 12.9, 13.0), W("impresiones", 13.0, 13.6), W("en", 13.6, 13.7), W("las", 13.7, 13.8),
          W("IAs", 13.8, 14.2)]
    a = ancla("NO VENDIÓ NADA", 10.0, ws)
    caso("una palabra clave de la IA se ancla a donde la DICES (sin tildes ni signos)",
         a is not None and a[0] == 1 and a[1] == 3 and a[2], a)
    a2 = ancla("3,6 %", 11.0, ws)
    caso("una cifra se ancla a la cifra dicha (con coma o con punto)", a2 is not None and a2[0] == 6 and a2[2], a2)
    a3 = ancla("GRATIS", 11.45, ws)
    caso("si no se dice tal cual, sale en la palabra que empieza en su segundo (y no cuenta como exacta)",
         a3 is not None and a3[0] == 4 and not a3[2], a3)
    caso("fuera de su ventana no se busca (una palabra de hace 30 s no vale)", ancla("NO VENDIÓ NADA", 40.0, ws) is None)

    cod = candidatas_codigo(ws, ["impresiones en IA", "SEO para Google"])
    textos = [c["texto"] for c in cod]
    caso("por código: la cifra con su «%» y la etiqueta SEO cuando la dices («IAs» ~ «IA»)",
         "3,6%" in textos and "impresiones en IA" in textos and "SEO para Google" not in textos, textos)
    ws2 = [W("Cuesta", 0.5, 0.9), W("100", 1.3, 1.6), W("mil", 1.6, 1.8), W("pesos.", 1.8, 2.2), W("Y", 3.0, 3.1),
           W("una", 3.2, 3.3), W("de", 3.3, 3.4), W("3.", 3.5, 3.6), W("En", 5.0, 5.1), W("50", 5.2, 5.4),
           W("50.", 5.45, 5.6), W("Del", 7.0, 7.1), W("2026.", 7.2, 7.8), W("El", 9.0, 9.1), W("90", 9.2, 9.5),
           W("por", 9.5, 9.6), W("ciento.", 9.6, 9.9), W("Serán", 11.0, 11.3), W("unos", 11.4, 11.6),
           W("20", 11.7, 11.9), W("mil", 11.9, 12.1), W("pesos.", 12.1, 12.5)]
    cod2 = [(c["texto"], c["importancia"]) for c in candidatas_codigo(ws2)]
    caso("cifras: «100 MIL PESOS» y «90 por ciento» -> «90%» (importantes); «3» suelto no; «50 50» una vez; «2026» de relleno;"
         " «unos 20 mil pesos» (un estimado) no", cod2 == [("100 MIL PESOS", 2), ("50", 1), ("2026", 1), ("90%", 2)], cod2)

    # ESTIMADOS (el autor, 2026-09-26: «ese 90 % fue un estimado mío»): las palabras de duda de ESA frase
    frase = [W("y", 29.0, 29.1), W("muy", 30.0, 30.2), W("posiblemente", 30.2, 30.8), W("casi", 30.9, 31.1),
             W("que", 31.1, 31.2), W("el", 31.2, 31.3), W("90", 31.4, 31.7), W("%", 31.7, 31.8), W("vaya", 31.9, 32.1)]
    dato = [W("Eso", 40.0, 40.2), W("creo.", 40.2, 40.5), W("El", 41.0, 41.1), W("73", 41.2, 41.5), W("%", 41.5, 41.6),
            W("de", 41.6, 41.7), W("los", 41.7, 41.8), W("clics", 41.8, 42.2)]
    luego = [W("el", 50.0, 50.1), W("70", 50.2, 50.5), W("%,", 50.5, 50.6), W("más", 50.7, 50.9), W("o", 50.9, 51.0),
             W("menos", 51.0, 51.3)]
    todo = frase + dato + luego
    caso("«muy posiblemente casi que el 90 %» y «el 70 %, más o menos» son estimados; «Eso creo. El 73 % de los clics», no"
         " (la duda era de la frase de antes)",
         es_estimado("90%", 31.4, todo) and es_estimado("70%", 50.2, todo) and not es_estimado("73%", 41.2, todo),
         (es_estimado("90%", 31.4, todo), es_estimado("73%", 41.2, todo), es_estimado("70%", 50.2, todo)))
    caso("trozos cortados fuera: «¿EN LAS IAS?», «DE LA WEB», «LAS VENTAS DE», «Y MEJOR»; frases enteras dentro",
         all(es_fragmento(x) for x in ("¿EN LAS IAS?", "DE LA WEB", "LAS VENTAS DE", "Y MEJOR"))
         and not any(es_fragmento(x) for x in ("¿CÓMO APARECEMOS EN LAS IAS?", "EN SEGUNDOS", "SIN PAGAR NADA",
                                                "LOS 3 ERRORES", "GEO Y AEO", "NO VENDIÓ NADA")))
    caso("calidad: una palabra suelta solo si es clave (3); una cifra que es un estimado no; una frase entera sí",
         filtra_calidad("ESTRATEGIA", 2, 0.0, []) and not filtra_calidad("GEO", 3, 0.0, [])
         and filtra_calidad("90% MÁS FUERZA", 3, 31.4, todo) == "cifra que es un estimado"
         and filtra_calidad("¿CÓMO APARECEMOS EN LAS IAS?", 3, 0.0, []) is None)

    ident = (lambda t: t)                                              # sin cortes: el tiempo final es el original
    fin = a_final([{"ia": 1, "ib": 3, "texto": "NO VENDIÓ NADA", "importancia": 3, "fuente": "ia", "exacta": True}],
                  ws, ident, p)
    caso("sale 40 ms antes de que empiece a sonar y dura lo que tardas en decirla + 0,9 s (1,5-2,5 s)",
         fin and abs(fin[0]["t"] - 10.26) < 1e-6 and abs(fin[0]["d"] - 1.9) < 1e-6, fin)
    caso("si la palabra cayó en un corte, no sale", a_final(fin[:0] + [dict(fin[0], ia=1, ib=3)], ws,
                                                            lambda t: None, p) == [])
    caso("fuera la que repite una tarjeta cercana (la tarjeta de dato «3,6%» ya lo dice)",
         _sin_repetir_tarjetas([{"t": 12.0, "texto": "3,6%"}, {"t": 30.0, "texto": "3,6%"}],
                               [{"t": 14.0, "tipo": "dato", "texto": "3,6%", "extra": "de las impresiones"}])
         == [{"t": 30.0, "texto": "3,6%"}])

    segs = [(0.0, 30.0), (40.0, 100.0), (100.0, 160.0)]            # final: 0-30, 30-90, 90-150
    modos = ["cara", "pantalla", "cara"]
    act = [{"inicio": 50.0, "fin": 70.0}, {"inicio": 120.0, "fin": 130.0}]
    vv = vivos(segs, modos, act)
    caso("la pantalla que se mueve cuenta como viva (en «cara», no: tu cara tapa la pantalla)", vv == [(40.0, 60.0)], vv)
    base = eventos_base(segs, modos, [{"inicio": 100.0, "fin": 104.0}])
    caso("cambios que ya trae: cada cambio de cámara y cada tarjeta que entra y sale", base == [30.0, 90.0, 100.0, 104.0],
         base)
    hh = huecos(base, vv, 150.0, 15.0)
    caso("tramos muertos de más de 15 s (la demostración parte el de 30-90)",
         hh == [(0.0, 30.0), (60.0, 90.0), (104.0, 150.0)], hh)

    def C(t, texto, imp=2, fuente="ia", d=2.0):
        return {"t": t, "d": d, "texto": texto, "importancia": imp, "fuente": fuente}
    cands = [C(5.0, "UNO"), C(9.0, "DOS"), C(14.0, "TRES", 1), C(21.0, "CUATRO"), C(45.0, "DEMO", 3),
             C(70.0, "CINCO"), C(75.0, "SEIS"), C(101.0, "TAPA LA TARJETA", 3), C(115.0, "SIETE"), C(127.0, "OCHO"),
             C(138.0, "NUEVE")]
    ocup = [(99.6, 104.4)]
    el = elige(cands, base, vv, ocup, 150.0, p)
    tt = [c["t"] for c in el]
    quedan = huecos(_con(base, el), vv, 150.0, 15.0)
    caso("el metrónomo deja todos los tramos muertos en ≤ 15 s cuando hay algo que decir", quedan == [], (tt, quedan))
    caso("nunca encima de una demostración ni de una tarjeta, aunque la IA la marque como clave",
         45.0 not in tt and 101.0 not in tt, tt)
    caso("entre dos palabras clave, ≥ 4 s y nunca solapadas", all(b - a >= 4.0 for a, b in zip(tt, tt[1:])), tt)
    caso("determinista: mismo plan, mismas palabras", elige(cands, base, vv, ocup, 150.0, p) == el)
    p_poco = dict(p, objetivo_por_min=0.0)
    el_poco = elige([C(13.0, "MITAD"), C(20.0, "OTRA")], [], [], [], 30.0, p_poco)
    caso("un tramo de 30 s se parte cerca de su mitad y, si con eso basta, no se añade más",
         [c["texto"] for c in el_poco] == ["MITAD"], el_poco)
    el_rep = elige([C(10.0, "GRATIS"), C(40.0, "GRATIS"), C(120.0, "GRATIS")], [], [], [], 150.0, p)
    caso("la misma palabra no se repite en 90 s", [c["t"] for c in el_rep] == [10.0, 120.0], el_rep)
    muchos = [C(float(t), f"P{t}") for t in range(1, 180)]
    el_max = elige(muchos, [], [], [], 180.0, dict(p, objetivo_por_min=99.0))
    ts_max = [c["t"] for c in el_max]
    peor = max(sum(1 for x in ts_max if a <= x < a + 60.0) for a in ts_max)
    caso("como mucho 6 en CUALQUIER minuto aunque se pida más", peor <= 6 and len(el_max) >= 12, (peor, len(el_max)))
    caso("sin candidatas no se inventa nada (el tramo muerto se queda y se avisa)",
         elige([], base, vv, ocup, 150.0, p) == [])
    rs = resumen([30.0, 90.0], [], 120.0, p)
    caso("resumen: cambios por minuto, tramos largos y el más largo",
         rs["cambios_min"] == 1.0 and rs["huecos_largos"] == 3 and rs["max_hueco_s"] == 60.0
         and rs["pct_en_huecos_largos"] == 100, rs)

    with tempfile.TemporaryDirectory() as tmp:
        ruta, carpeta = escribe_ass([C(2.0, "3,6% de {clics}"), C(8.0, "NO VENDIÓ NADA")],
                                    {"crema": "#FFFBF2", "tinta": "#14100F", "naranja": "#FE330A",
                                     "fuente_titulo": "fonts/Rubik-ExtraBold.ttf"}, 1920, 1080, Path(tmp) / "x.ass", p)
        txt = ruta.read_text(encoding="utf-8") if ruta else ""
        caso("el .ass: letra de la marca leída del archivo, tamaño a 1080, abajo en el centro",
             "Style: Clave,Rubik ExtraBold,104," in txt and ",2,80,80,150,1" in txt and carpeta is not None, txt[:400])
        caso("cada palabra entra a su segundo, en mayúsculas, la cifra en naranja y sin llaves sueltas",
             "Dialogue: 0,0:00:02.00,0:00:04.00," in txt and "{\\1c&H000A33FE&}3,6%" in txt
             and "VENDIÓ" in txt and "{clics}" not in txt and "{\\1c&H00F2FBFF&}NO" in txt, txt[-300:])
        caso("sin palabras no se escribe ningún .ass", escribe_ass([], {}, 1920, 1080, Path(tmp) / "y.ass", p)
             == (None, None) and not (Path(tmp) / "y.ass").exists())
        ruta2, _c2 = escribe_ass([C(3.0, "¿CÓMO APARECEMOS EN LAS IAS?", 3)], {"crema": "#FFFBF2", "tinta": "#14100F",
                                                                             "naranja": "#FE330A"}, 1920, 1080,
                                 Path(tmp) / "z.ass", p)
        t2 = ruta2.read_text(encoding="utf-8") if ruta2 else ""
        caso("una frase entera sale en DOS líneas equilibradas y a tamaño normal (no achicada en una sola)",
             "¿CÓMO {\\1c&H00F2FBFF&}APARECEMOS\\N" in t2 and "\\fs104\\" in t2 and "IAS?" in t2, t2[-260:])

    temprana = a_final([{"ia": 0, "ib": 1, "texto": "¿CÓMO ESTÁS?", "importancia": 3, "fuente": "ia"}],
                       [W("¿Cómo", 0.2, 0.5), W("estás?", 0.5, 1.1)], ident, p)
    caso("la frase del gancho que empieza con el vídeo sale al acabar el fundido de entrada (0,6 s), no se pierde",
         temprana and temprana[0]["t"] == 0.6 and abs(temprana[0]["d"] - (1.8 - 0.44)) < 1e-6, temprana)
    caso("«SUSCRÍBETE» o «DALE LIKE» no son palabra clave: ya tienen su tarjeta en todos los vídeos",
         _sin_repetir_tarjetas([{"t": 5.0, "texto": "¡SUSCRÍBETE!"}, {"t": 9.0, "texto": "DALE LIKE"},
                                {"t": 20.0, "texto": "GRATIS"}], []) == [{"t": 20.0, "texto": "GRATIS"}])

    # un plan de ANTES: UNA petición a Flash-Lite con lo que se OYE (segundos del original) y lo que vuelve, limpio
    visto: dict = {}

    def _falsa(client, types, modelos, prompt, **kw):
        visto.update(prompt=prompt, modelos=modelos, **kw)
        return ({"palabras_clave": [{"t": 10.3, "texto": "NO VENDIÓ NADA", "importancia": 5},
                                    {"t": 12.0, "texto": "{3,6%}", "importancia": 2},
                                    {"t": 999.0, "texto": "FUERA", "importancia": 2},
                                    {"t": 13.0, "texto": "X" * 40}]}, "lite-falso", {})
    pp_ws = ws + [W("zzquitado", 20.0, 20.5)]
    res_pl = palabras_para(pp_ws, [(0.0, 15.0)], 60.0, "SEO", "Mi canal", _CfgVacio(), _gemini=(None, None),
                           _modelos=["lite-falso"], _llama=_falsa)
    caso("plan de antes: la IA barata (Flash-Lite) ve lo que se OYE con sus segundos, y lo que devuelve se limpia",
         res_pl == [{"t": 10.3, "texto": "NO VENDIÓ NADA", "importancia": 3}, {"t": 12.0, "texto": "3,6%", "importancia": 2}]
         and "[10.0] Y no vendió nada," in visto.get("prompt", "") and "zzquitado" not in visto.get("prompt", "")
         and visto.get("etiqueta") == "palabras" and visto.get("modelos") == ["lite-falso"], (res_pl, visto.get("prompt", "")[:300]))

    def _rota(*a, **k):
        raise RuntimeError("503 UNAVAILABLE")
    caso("si Flash-Lite falla, None (se vuelve a intentar en el siguiente render; el vídeo sale con las de la transcripción)",
         palabras_para(ws, [(0.0, 15.0)], 60.0, "", "", _CfgVacio(), _gemini=(None, None), _modelos=["x"],
                       _llama=_rota) is None)

    class _Cfg:
        def get_path(self, k, d=None):
            return {"videos.ritmo": {"max_sin_cambio_seg": 12, "palabras_activo": "no-bool", "raro": 1}}.get(k, d)
    pp = parametros(_Cfg())
    caso("config: se leen los valores propios, se ignoran las claves que no existen",
         pp["max_sin_cambio_seg"] == 12.0 and "raro" not in pp and pp["palabras_activo"] is True, pp)
    with tempfile.TemporaryDirectory() as tmp:
        r = aplica({"palabras_clave": [{"t": 10.0, "texto": "NO VENDIÓ NADA", "importancia": 3}],
                    "tags": ["impresiones en IA"]}, ws, [(0.0, 60.0)], ["cara"], [], [], [], ident, 60.0,
                   {"crema": "#FFFBF2", "tinta": "#14100F", "naranja": "#FE330A"}, 1920, 1080, Path(tmp) / "p.ass",
                   _CfgVacio())
        caso("de punta a punta: la de la IA sale en su palabra, luego las de la transcripción, y el .ass existe",
             [c["texto"] for c in r["palabras"]][:1] == ["NO VENDIÓ NADA"] and r["ass"] is not None
             and r["despues"]["cambios_min"] > r["antes"]["cambios_min"], (r["palabras"], r["notas"]))
    return filas


class _CfgVacio:
    def get_path(self, k, d=None):
        return d


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    res = prueba()
    for f in res:
        print(("OK  " if f["ok"] else "FALLA ") + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
    print(f"{sum(f['ok'] for f in res)}/{len(res)}")
