"""💡 IDEAS DE VÍDEOS — la PRE-producción (el autor, 2026-09-26): «antes de yo grabar incluso el video, muchas veces me voy a
quedar sin ideas… que con todo el contexto, con todo lo que se ha subido, saque unas 10 ideas… con una pequeña
descripción… y que me diga más o menos qué tendría que hablar en ese video».

Desde el 2026-10-04 es UNA LISTA de 20 ideas, en una tabla: «literalmente solo la lista… muy llamativas… todas en una sola
pantalla sin tanto texto… y que todas se puedan refrescar».

- UNA petición al MEJOR Flash (el autor: «esto ya es estratégico… una petición de la IA más avanzada»), solo con su clic, y
  NUNCA si hoy quedan menos de `ideas.minimo_restantes` (8) peticiones de los mejores modelos: «lo más importante es que
  edite muy bien el video». Como mucho `ideas.max_por_dia` tandas al día (las del formato de 10 ideas, versión 1, no cuentan).
- El reparto de las 20 lo eligió él (4-oct-2026): 5 «negocio» (dueños de negocio que podrían contratar su SEO: su prioridad
  nº 1), 5 «aprender» (futuros CEOs y emprendedores que quieren aprender SEO: su curso) y 10 «general» (SEO en general, con
  base en lo que funciona en español Y en inglés). Hasta el 3-oct eran 10: 4 negocio, 4 crecimiento y 2 autoridad.
- La lista es CORTA a propósito: título, una frase, por qué ahora y los datos. El gancho y los puntos los da «📝 Guion».
- «↻» en CADA idea: la cambia por otra del MISMO tipo con UNA petición a Flash-Lite (500 al día: no toca las 20 de Flash),
  con un tope por día (`ideas.refrescos_max_dia`, 30) y nunca con menos de `ideas.minimo_lite` (150) peticiones de
  Flash-Lite hoy. Usa los datos guardados con la tanda: no gasta cuota de YouTube.
- Desde el 2026-10-04 las ideas NO saben de ningún tema en concreto: leen el PERFIL del canal (`clipper/ideas_perfil.py`: de qué trata,
  qué quiere lograr su autor, a qué públicos quiere llegar y cuántas ideas lleva cada uno, qué se busca en YouTube sobre su tema, a dónde
  llevar a la gente). El del autor sale de `ideas:` de config.yaml (sus ideas salen igual que antes); un canal nuevo lo configura la primera
  vez que abre «Ideas». Cada tanda lleva su `canal` y los tipos que usó; las de antes de los perfiles son del autor.
- Datos REALES y gratis. La búsqueda en Google de Gemini NO es gratis con los modelos actuales (comprobado en su página de
  precios el 2026-09-26), así que las tendencias salen de YouTube (API oficial, solo lectura):
    · sus vídeos (fecha, vistas, título): lo que ya tocó (para no repetir) y lo que rindió;
    · lo que la gente escribe en el buscador de YouTube para llegar a su canal (YouTube Analytics);
    · los vídeos de otros junto a los que YouTube recomienda los suyos -> esos canales -> sus vídeos que rinden muy por
      encima de lo normal EN SU canal («outliers»);
    · lo más visto del último mes para `ideas.busquedas` (español) y `ideas.busquedas_en` (inglés) (search.list: 100
      unidades de cuota cada una, de 10.000); cada vídeo lleva su idioma (el que dice YouTube o, si no, el del título);
    · las preguntas y peticiones de sus comentarios;
    · su web: los artículos del catálogo y sus servicios (cada idea dice a cuál llevar).
- Anti-invento (el mismo espíritu que los enlaces de la descripción): cada dato va con un CÓDIGO (B3, O2, C5…) y la IA
  cita códigos en «evidencia»; el panel enseña el dato REAL de cada código y tira los que no existen.
- «📝 Guion» de una idea: UNA petición a Flash-Lite (500 al día: no toca las 20 de Flash).
- «🎬 La grabo»: pasa a «Por grabar»; al subir la grabación se elige y el director la recibe como contexto
  (hasta el 12-oct-2026; desde entonces la idea ya no se le pasa al director: manda la grabación).
Todo se guarda en `work/ideas/` (una tanda por archivo + `datos_<tanda>.json` con los datos de esa tanda, que usan los «↻» +
el estado de cada idea + el contador de refrescos del día).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
from pathlib import Path

from . import ideas_perfil as perfiles
from .util import log, normalize, reemplaza

VERSION = 2          # 1 = las 10 ideas en tarjetas (hasta el 2026-10-03); 2 = la lista de 20 con «↻»
TIPOS = ("negocio", "aprender", "general")
TIPOS_ANTIGUOS = ("crecimiento", "autoridad")        # solo en las tandas de antes (4/4/2): se siguen enseñando
NOMBRE_TIPO = {"negocio": "Para clientes", "aprender": "Para aprender", "general": "Ideas generales",
               "crecimiento": "Crecimiento: temas con búsquedas", "autoridad": "Autoridad / curso"}
DESCRIPCION_TIPO = {"negocio": "personas y empresas que podrían contratar lo que ofreces",
                    "aprender": "quien quiere aprender lo que sabes (tu curso o producto)",
                    "general": "lo que se mueve en tu nicho, con tu estilo",
                    "crecimiento": "temas con búsquedas", "autoridad": "tu experiencia o tu curso"}
ESTADOS = ("grabar", "descartada", "grabada")
_DEF = {                              # los topes de TODOS los canales; lo de cada canal (públicos, objetivo, búsquedas) es de su perfil
    "activo": True,
    "minimo_restantes": 8,            # el autor: «si tengo menos de 8 peticiones, que ya no lo comience a correr»
    "max_por_dia": 2,
    "dias_tendencia": 30,
    "dias_trafico": 90,
    "canales_relacionados": 5,
    "canales_referencia": [],         # ids de canales que el autor quiera vigilar siempre (además de los que YouTube relaciona)
    "outlier_x": 2.0,                 # un vídeo «rinde por encima» si tiene ≥ 2× la mediana de vistas de su canal
    "outlier_dias": 180,
    "refrescos_max_dia": 30,          # «↻» de una idea: 1 petición a Flash-Lite cada uno (el tope por día lo puso el programa)
    "minimo_lite": 150,               # y nunca con menos de 150 peticiones de Flash-Lite hoy: lo primero es editar los vídeos
    "busquedas_por_idioma": 4,        # «lo más visto»: cuántas búsquedas por idioma mira CADA tanda (si hay más en el perfil, rotan: 100 unidades cada una)
}
CACHE_H = 12.0                        # tus vídeos y sus vistas: se releen como mucho cada 12 h
_SERVICIOS = {"cta_url": "Tu enlace principal", "web": "Tu página web"}   # los servicios del autor (su perfil): los de otro canal salen de su perfil y de sus enlaces
_PIDE = re.compile(r"\?|\b(como|podrias|podria|haz|hagas|hacer un video|video sobre|tutorial|explica|explicar|recomiendas|"
                   r"opinas|ayuda|ayudame|duda|pregunta|seria bueno|me gustaria)\b")
_VACIAS = set("de la el los las un una unos unas y o en con por para sin sobre tu tus mi mis su sus que como es son al del "
              "lo le se ya mas muy este esta estos estas".split())
_ES_PAL = frozenset("el la los las de del en y que para con por un una unos unas como cómo qué es se tu tus su mi mis más al lo "
                    "le pero este esta estos estas muy sin sobre cuando donde dónde cuánto cuanto".split())
_EN_PAL = frozenset("the and to of in is for you your how what with this that are on it my we best why from can will new about "
                    "not do does more".split())


def _entero(v, d: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return d
    return n if n >= 0 else d


def parametros(cfg, canal: str | None = None, dir_work=None) -> dict:
    """Los parámetros de las ideas de un canal: los topes de siempre (`ideas:` de config.yaml) + el PERFIL del canal (sus públicos y cuántas
    ideas lleva cada uno, su objetivo, sus búsquedas, a dónde llevar a la gente). Un canal sin perfil (la primera vez) trae
    `necesita_perfil` y un perfil provisional: no se le piden ideas hasta que lo configure."""
    c = dict(cfg.get_path("ideas") or {}) if cfg is not None else {}
    p = {**_DEF, **{k: v for k, v in c.items() if v is not None and k in _DEF}}
    canal = canal or _canal_por_defecto(cfg)
    perfil = perfiles.de_canal(canal, cfg, dir_work)
    p["canal"] = canal
    p["necesita_perfil"] = perfil is None
    if perfil is None:
        perfil = {"origen": "provisional", "tema": "", "objetivo": "", "vertientes": [], "busquedas": [], "busquedas_en": [],
                  "idiomas": "es", "destinos": [], "datos_propios": False,
                  "general": {"id": perfiles.ID_GENERAL, "nombre": "Generales", "resumen": "", "descripcion": "", "n": perfiles.TOTAL_DEF}}
    tipos = [{"id": v["id"], "nombre": v["nombre"], "resumen": v.get("resumen", ""), "descripcion": v["descripcion"], "n": int(v["n"])}
             for v in perfil["vertientes"]]
    g = perfil["general"]
    tipos.append({"id": perfiles.ID_GENERAL, "nombre": g["nombre"], "resumen": g.get("resumen", ""), "descripcion": g["descripcion"],
                  "n": int(g["n"])})
    p.update({"perfil": perfil, "tipos": tipos, "mezcla": {t["id"]: t["n"] for t in tipos}, "objetivo": perfil["objetivo"],
              "etiqueta_objetivo": perfil.get("etiqueta_objetivo") or perfiles.ETIQUETA_OBJETIVO,
              "etiqueta_destinos": perfil.get("etiqueta_destinos") or perfiles.ETIQUETA_DESTINOS,
              "busquedas": list(perfil.get("busquedas") or []), "idiomas": perfil.get("idiomas") or "es",
              "datos_propios": bool(perfil.get("datos_propios", True))})
    p["busquedas_en"] = list(perfil.get("busquedas_en") or []) if p["idiomas"] == "es+en" else []
    p["semillas"] = list(perfil.get("semillas") or [])             # SUS ideas favoritas (fijas arriba y modelo de estilo)
    p["herramientas"] = list(perfil.get("herramientas") or [])     # las herramientas que YA ha creado
    p["total"] = sum(p["mezcla"].values())
    return p


def idioma_de(titulo, declarado: str = "") -> str:
    """El idioma de un vídeo: «es», «en», «otro» (YouTube dice que es de otro idioma) o «» (no se sabe). Primero el que
    declara YouTube (audio o por defecto); si no dice, el del título (palabras de cada idioma y tildes)."""
    base = str(declarado or "").strip().lower().split("-")[0]
    if base in ("es", "en"):
        return base
    if base and base not in ("und", "zxx", "mis", "mul"):
        return "otro"
    t = str(titulo or "").lower()
    palabras = re.findall(r"[a-záéíóúñü]+", t)
    es = sum(1 for w in palabras if w in _ES_PAL) + (2 if re.search(r"[áéíóúñ¿¡]", t) else 0)
    en = sum(1 for w in palabras if w in _EN_PAL)
    return "es" if es > en else "en" if en > es else ""


def _etiqueta_idioma(i: str) -> str:
    return {"es": ", en español", "en": ", en inglés"}.get(i, "")


# ------------------------------------------------------------------ archivos
def _dir(dir_work=None) -> Path:
    if dir_work:
        return Path(dir_work) / "ideas"
    from . import config
    return config.paths()["work"] / "ideas"


def _lee(p: Path, defecto=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


_ESCRIBE = threading.RLock()          # el leer-cambiar-guardar de estado.json (marcar, guion, borrar una tanda): de uno en uno, para que dos clics seguidos no se pisen


def _guarda(p: Path, d) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}-{threading.get_ident()}.tmp")       # (un temporal por hilo: dos escrituras a la vez no comparten el suyo)
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    reemplaza(tmp, p)


def _canal_de(t: dict) -> str:
    """El canal de una tanda. Las de antes de los perfiles (4-oct-2026) no lo apuntaban: eran todas del autor."""
    return str(t.get("canal") or "ejemplo")


def tandas(dir_work=None, canal: str | None = None) -> list[dict]:
    """Las tandas guardadas (las de `canal`, si se pide), de la más nueva a la más vieja."""
    out = [t for t in (_lee(f) for f in _dir(dir_work).glob("tanda_*.json")) if isinstance(t, dict) and t.get("ideas")]
    if canal:
        out = [t for t in out if _canal_de(t) == canal]
    out.sort(key=lambda t: -float(t.get("ts") or 0))
    return out


def estado(dir_work=None) -> dict:
    d = _lee(_dir(dir_work) / "estado.json", {})
    return d if isinstance(d, dict) else {}


def _con_estado(idea: dict, est: dict) -> dict:
    e = est.get(idea.get("id")) or {}
    return {**idea, "estado": e.get("estado") or "", "guion": e.get("guion"), "jid": e.get("jid")}


def idea_por_id(idea_id: str, dir_work=None, cfg=None) -> dict | None:
    est = estado(dir_work)
    for t in tandas(dir_work):
        for i in t.get("ideas") or []:
            if i.get("id") == idea_id:
                return _con_estado(i, est)
    if str(idea_id).startswith("semilla-"):                    # una de TUS ideas favoritas (config.yaml): no está en ninguna tanda
        return next((s for s in _semillas_de_config(dir_work, cfg) if s["id"] == idea_id), None)
    copia = (est.get(idea_id) or {}).get("idea")               # una idea «Por grabar» cuya tanda ya se borró: vive en su propia copia
    if isinstance(copia, dict) and copia.get("id") == idea_id:
        return _con_estado(copia, est)
    return None


def _copia(idea: dict) -> dict:
    """La idea sin lo que cambia (estado, guion, vídeo): lo que se guarda aparte para que una idea «Por grabar» sobreviva a borrar su tanda."""
    return {k: v for k, v in idea.items() if k not in ("estado", "guion", "jid")}


def marca(idea_id: str, nuevo: str, dir_work=None, cfg=None, **extra) -> bool:
    """«grabar» (🎬 La grabo), «descartada» (✖), «grabada» (se subió su grabación) o «» (quitar la marca)."""
    if nuevo and nuevo not in ESTADOS:
        return False
    idea = idea_por_id(idea_id, dir_work, cfg)
    if idea is None:
        return False
    with _ESCRIBE:
        est = estado(dir_work)
        e = dict(est.get(idea_id) or {})
        e.update({"estado": nuevo, "ts": time.time(), **extra})
        if nuevo == "grabar" and not str(idea_id).startswith("semilla-"):
            e["idea"] = _copia(idea)                           # (si luego se borra su tanda, sigue en «Por grabar»)
        elif nuevo != "grabar":
            e.pop("idea", None)
            if not nuevo and not _vive(idea_id, dir_work, cfg):   # una copia huérfana que se quita de «Por grabar»: no queda ni el rastro (salvo su título, para no repetirla)
                _recuerda_eliminadas([idea], str(idea.get("canal") or "ejemplo"), dir_work)
                est.pop(idea_id, None)
                _guarda(_dir(dir_work) / "estado.json", est)
                return True
        est[idea_id] = e
        _guarda(_dir(dir_work) / "estado.json", est)
    return True


def _vive(idea_id: str, dir_work=None, cfg=None) -> bool:
    """¿Sigue la idea en alguna tanda o es una de tus favoritas de config.yaml? (si no, solo existe su copia de «Por grabar»)."""
    if str(idea_id).startswith("semilla-"):
        return True
    return any(i.get("id") == idea_id for t in tandas(dir_work) for i in t.get("ideas") or [])


def por_grabar(dir_work=None, cfg=None) -> list[dict]:
    """Tu CHECKLIST: las ideas marcadas «🎬 La grabo» (el autor, 12-oct-2026), la última que marcaste primero. Siguen aquí aunque se borre la tanda de la que salieron
    (cada una guarda su copia) y también salen tus favoritas de config.yaml si las marcaste."""
    est = estado(dir_work)
    en_tandas = {i.get("id"): i for t in tandas(dir_work) for i in t.get("ideas") or []}
    favoritas = {x["id"]: x for x in _semillas_de_config(dir_work, cfg)}
    out = []
    for iid, e in est.items():
        if not isinstance(e, dict) or e.get("estado") != "grabar":
            continue
        idea = en_tandas.get(iid) or favoritas.get(iid) or (e.get("idea") if isinstance(e.get("idea"), dict) else None)
        if idea:
            out.append((float(e.get("ts") or 0), _con_estado({**idea, "id": iid}, est)))
    out.sort(key=lambda x: -x[0])
    return [i for _t, i in out]


def _semillas_de_config(dir_work=None, cfg=None) -> list[dict]:
    """TUS IDEAS FAVORITAS del canal de config.yaml (las únicas que las tienen), sin que quien llama tenga que pasar la configuración. Nunca lanza."""
    try:
        if cfg is None:
            from . import config
            cfg = config.load()
        return semillas(cfg, None, dir_work)
    except Exception:                                          # noqa: BLE001
        return []


def semillas(cfg, canal: str | None = None, dir_work=None) -> list[dict]:
    """TUS IDEAS FAVORITAS (`ideas.semillas` de config.yaml; el autor, 2026-10-05: «ideas que sí o sí quiero que incluyas»): salen SIEMPRE arriba de «Ideas» hasta
    que las grabes («🎬 La grabo») o las descartes, sin gastar ninguna petición, y la IA las usa de MODELO del estilo de las ideas generales. Cada una lleva su
    id estable (sale del título), su potencial (1-3 🔥), su estado y, si se parece a un vídeo que ya subiste, `parecida_a` (con la lista de tus vídeos que ya
    se leyó: aquí no se pregunta a YouTube). Se tratan como las demás ideas (grabarla, descartarla, «📝 Guion», copiar), salvo el «↻»."""
    p = parametros(cfg, canal, dir_work)
    est = estado(dir_work)
    ocultas = set(_lee(_dir(dir_work) / "semillas_eliminadas.json", []) or [])        # las que eliminaste (siguen en config.yaml, pero ya no salen)
    cache = _lee(_dir(dir_work) / "cache_mis_videos.json", {}) or {}
    mios = [str(v.get("titulo")) for v in (cache.get("videos") or []) if isinstance(v, dict) and v.get("titulo")]
    return [_con_estado({"id": s["id"], "tipo": perfiles.ID_GENERAL, "titulo": s["titulo"], "potencial": int(s.get("potencial") or 2),
                         "semilla": True, "de_que_va": "", "por_que": "", "evidencia": [], "para_quien": "", "cta": None,
                         "formato": "", "dificultad": "", "idioma": "", "parecida_a": _parecida(s["titulo"], mios)}, est)
            for s in p.get("semillas") or [] if s["id"] not in ocultas]


def _rota(lista: list[str], n: int, vuelta: int) -> list[str]:
    """`n` búsquedas de `lista`, empezando donde se quedó la tanda anterior (la lista da la vuelta): cada tanda mira otras y en unas pocas se ven todas,
    sin gastar más cuota de YouTube (cada búsqueda son 100 unidades). Con `n` en 0 o una lista que no pasa de `n`, todas."""
    lista = [q for q in lista if q]
    if n <= 0 or len(lista) <= n:
        return lista
    ini = (int(vuelta) * n) % len(lista)
    return [lista[(ini + k) % len(lista)] for k in range(n)]


# ------------------------------------------------------------------ borrar una tanda (la ✖ junto a su fecha)
TANDA_ID = re.compile(r"\d{8}-\d{6}")


def _ruta_borradas(dir_work=None) -> Path:
    return _dir(dir_work) / "borradas.json"


def borradas(dir_work=None, canal: str | None = None) -> list[dict]:
    """El RECUERDO de las tandas que se borraron (fecha, versión, canal y los títulos con su marca; nada más). Sirve para que la IA no vuelva a proponer esas
    ideas y para que borrar una tanda no sea un truco para saltarse el tope de tandas al día."""
    d = _lee(_ruta_borradas(dir_work), []) or []
    out = [b for b in d if isinstance(b, dict)]
    return [b for b in out if _canal_de(b) == canal] if canal else out


# ------------------------------------------------------------------ lo ELIMINADO se recuerda (solo el título y el tipo) para que la IA no lo vuelva a proponer
MAX_ELIMINADAS = 400


def _ruta_eliminadas(dir_work=None) -> Path:
    return _dir(dir_work) / "eliminadas.json"


def eliminadas(dir_work=None, canal: str | None = None) -> list[dict]:
    """Las ideas que ELIMINASTE (o de las tandas que borraste), de la más reciente a la más vieja: solo {titulo, tipo, canal, ts}, nada más (el autor, 13-oct-2026: «alguna metadata que
    permita que cuando se genere otra vez no sea igual a una que yo eliminé»). Sirven para que la IA no las repita ni proponga algo muy parecido."""
    d = _lee(_ruta_eliminadas(dir_work), []) or []
    out = [e for e in d if isinstance(e, dict) and e.get("titulo")]
    out.sort(key=lambda e: -float(e.get("ts") or 0))
    return [e for e in out if (e.get("canal") or "ejemplo") == canal] if canal else out


def _recuerda_eliminadas(ideas: list[dict], canal: str, dir_work=None, ahora: float | None = None) -> None:
    """Apunta los títulos de lo que se elimina (sin repetir; las últimas MAX_ELIMINADAS). Nunca lanza: esto no puede impedir que algo se elimine."""
    try:
        ahora = time.time() if ahora is None else ahora
        nuevas = [(str(i.get("titulo") or "").strip(), str(i.get("tipo") or "")) for i in ideas if str(i.get("titulo") or "").strip()]
        if not nuevas:
            return
        with _ESCRIBE:
            cur = _lee(_ruta_eliminadas(dir_work), []) or []
            vistos = {(normalize(e.get("titulo", "")), e.get("canal") or "ejemplo") for e in cur if isinstance(e, dict)}
            for titulo, tipo in nuevas:
                k = (normalize(titulo), canal or "ejemplo")
                if k not in vistos:
                    vistos.add(k)
                    cur.append({"titulo": titulo, "tipo": tipo, "canal": canal or "ejemplo", "ts": ahora})
            _guarda(_ruta_eliminadas(dir_work), cur[-MAX_ELIMINADAS:])
    except Exception as exc:                                   # noqa: BLE001
        log(f"AVISO: no se pudo apuntar lo eliminado ({exc}).")


def titulos_eliminados(dir_work=None, canal: str | None = None) -> list[str]:
    return [e["titulo"] for e in eliminadas(dir_work, canal)]


def _quita_marcas(ids: set, dir_work=None, conservar: set | frozenset = frozenset()) -> None:
    with _ESCRIBE:                                             # (se vuelve a leer: entre medias pudo cambiar una marca)
        est = estado(dir_work)
        if any(k in est for k in ids if k not in conservar):
            _guarda(_dir(dir_work) / "estado.json", {k: v for k, v in est.items() if k in conservar or k not in ids})


def borra_tanda(tid: str, dir_work=None, ahora: float | None = None) -> dict:
    """La ✖ junto a la fecha de cada tanda (el autor, 2026-10-05; 12-oct-2026: «se tienen que eliminar todas menos las que salen por grabar»): borra la tanda con sus ideas, sus guiones
    y los datos de sus «↻», y SOLO se conservan las ideas que están en «Por grabar» (cada una guarda su propia copia, así que siguen en tu checklist). Del resto no queda NADA
    (solo la fecha de la tanda, para que borrar no sirva de truco contra el tope de tandas al día). Nunca lanza.
    -> {"ok": True, "ideas": n borradas, "conservadas": k} o {"ok": False, "codigo": no_esta|error, "error": …}"""
    ahora = time.time() if ahora is None else ahora
    tid = str(tid or "")
    ruta = _dir(dir_work) / f"tanda_{tid}.json"
    t = _lee(ruta) if TANDA_ID.fullmatch(tid) and ruta.is_file() else None
    if not isinstance(t, dict):
        return {"ok": False, "codigo": "no_esta", "error": "Esa tanda ya no está (¿ya la borraste?). Recarga la página."}
    todas = list(t.get("ideas") or []) + list(t.get("ideas_anteriores") or [])
    try:
        with _ESCRIBE:
            est = estado(dir_work)
            quedan = {i.get("id"): i for i in t.get("ideas") or [] if (est.get(i.get("id")) or {}).get("estado") == "grabar"}
            cambio = False
            for iid, i in quedan.items():                      # las «Por grabar» se llevan su copia: sobreviven a la tanda
                e = dict(est.get(iid) or {})
                if e.get("idea") != _copia(i):
                    e["idea"] = _copia(i)
                    est[iid] = e
                    cambio = True
            if cambio:
                _guarda(_dir(dir_work) / "estado.json", est)
        _recuerda_eliminadas([i for i in todas if i.get("id") not in quedan], _canal_de(t), dir_work, ahora)
        recuerdo = {"id": tid, "ts": float(t.get("ts") or 0), "borrada": ahora, "canal": _canal_de(t), "version": int(t.get("version") or 1), "ideas": []}
        _guarda(_ruta_borradas(dir_work), [b for b in borradas(dir_work) if b.get("id") != tid][-59:] + [recuerdo])
        ruta.unlink()
        _ruta_datos(tid, dir_work).unlink(missing_ok=True)
        _quita_marcas({i.get("id") for i in todas}, dir_work, conservar=set(quedan))
    except OSError as exc:
        return {"ok": False, "codigo": "error", "error": f"No se pudo borrar la tanda ({exc}). Prueba otra vez en un momento."}
    borradas_n = len(t.get("ideas") or []) - len(quedan)
    log(f"Ideas de vídeos: tanda {tid} borrada ({borradas_n} ideas; {len(quedan)} se quedan en «Por grabar»).")
    return {"ok": True, "ideas": borradas_n, "conservadas": len(quedan)}


def borra_anteriores(canal: str | None = None, dir_work=None, ahora: float | None = None) -> dict:
    """«🗑 Borrar todas las anteriores» (el autor, 12-oct-2026: «quiero eliminar todos esos antiguos y quedarme solo con los nuevos»): borra todas las tandas del canal menos la más
    reciente, conservando solo lo que está en «Por grabar». -> {"ok": True, "tandas": n, "ideas": n, "conservadas": k}"""
    viejas = tandas(dir_work, canal=canal)[1:]
    out = {"ok": True, "tandas": 0, "ideas": 0, "conservadas": 0}
    for t in viejas:
        r = borra_tanda(str(t.get("id") or ""), dir_work, ahora)
        if r.get("ok"):
            out["tandas"] += 1
            out["ideas"] += r["ideas"]
            out["conservadas"] += r["conservadas"]
    return out


def elimina_idea(idea_id: str, dir_work=None, cfg=None) -> dict:
    """«🗑 Eliminar» (el autor, 12-oct-2026: «que ese registro se elimine al 100 %… de mi PC y de todo»): la idea desaparece de su tanda (con su guion y su marca de «Por grabar»);
    si era la última de la tanda, la tanda también. No queda ningún rastro: la IA podría volver a proponer algo parecido en una tanda futura. Una de tus favoritas de config.yaml
    no se borra del archivo (es tuyo): se apunta que ya no salga. -> {"ok": bool, "codigo": "no_esta"}"""
    idea_id = str(idea_id or "")
    encontrada = False
    recordar: list[tuple[dict, str]] = []
    with _ESCRIBE:
        for f in sorted(_dir(dir_work).glob("tanda_*.json")):
            t = _lee(f)
            if not isinstance(t, dict):
                continue
            ideas, antes = list(t.get("ideas") or []), list(t.get("ideas_anteriores") or [])
            nuevas = [i for i in ideas if i.get("id") != idea_id]
            n_ant = [i for i in antes if i.get("id") != idea_id]
            if len(nuevas) == len(ideas) and len(n_ant) == len(antes):
                continue
            encontrada = True
            recordar += [(i, _canal_de(t)) for i in ideas + antes if i.get("id") == idea_id]
            if nuevas:
                _guarda(f, {**t, "ideas": nuevas, "ideas_anteriores": n_ant})
            else:                                              # era la última: se va la tanda entera y sus datos
                f.unlink(missing_ok=True)
                _ruta_datos(str(t.get("id") or f.stem.replace("tanda_", "")), dir_work).unlink(missing_ok=True)
        est = estado(dir_work)
        if idea_id in est:
            copia = est[idea_id].get("idea") if isinstance(est.get(idea_id), dict) else None
            if not recordar and isinstance(copia, dict):       # (ya no hay tanda: queda su copia de «Por grabar»)
                recordar.append((copia, "ejemplo"))
            est.pop(idea_id)
            _guarda(_dir(dir_work) / "estado.json", est)
            encontrada = True
        for i_, canal_ in recordar:
            _recuerda_eliminadas([i_], canal_, dir_work)
        if idea_id.startswith("semilla-") and any(x["id"] == idea_id for x in _semillas_de_config(dir_work, cfg)):
            ocultas = list(_lee(_dir(dir_work) / "semillas_eliminadas.json", []) or [])
            if idea_id not in ocultas:
                _guarda(_dir(dir_work) / "semillas_eliminadas.json", ocultas + [idea_id])
            encontrada = True
    if not encontrada:
        return {"ok": False, "codigo": "no_esta", "error": "Esa idea ya no está (¿ya la eliminaste?). Recarga la página."}
    log(f"Ideas de vídeos: idea {idea_id} eliminada.")
    return {"ok": True}


def limpia_descartadas(dir_work=None, cfg=None) -> int:
    """Las ideas que se marcaron «✖ No me interesa» antes del 12-oct-2026 se ELIMINAN del todo (ahora «Eliminar» sustituye a esa marca). -> cuántas."""
    ids = [k for k, e in estado(dir_work).items() if isinstance(e, dict) and e.get("estado") == "descartada"]
    return sum(1 for k in ids if elimina_idea(k, dir_work, cfg).get("ok"))


# ------------------------------------------------------------------ el freno (el autor: «lo más importante es el vídeo»)
def puede_generar(cfg, dir_work=None, ahora: float | None = None, _resumen=None, canal: str | None = None) -> dict:
    """¿Se puede pedir una tanda ahora? Mira cuántas peticiones de los MEJORES modelos quedan hoy (lo mismo que enseña la
    pestaña «Uso») y cuántas tandas se pidieron hoy (de todos los canales; las del formato de 10 ideas, versión 1, no cuentan). Un canal
    que aún no tiene perfil tiene que configurarlo primero. -> {"ok", "motivo", "restantes", "minimo", "hoy", "max", "total"}"""
    import os
    from . import uso
    p = parametros(cfg, canal, dir_work)
    ahora = time.time() if ahora is None else ahora
    hoy = uso.dia_cuota(ahora)
    n_hoy = sum(1 for t in tandas(dir_work) + borradas(dir_work)       # (las borradas siguen contando: borrar no sirve para saltarse el tope)
                if int(t.get("version") or 1) >= 2 and uso.dia_cuota(float(t.get("ts") or 0)) == hoy)
    try:
        res = _resumen(cfg) if _resumen is not None else uso.resumen(cfg, ahora)
        pool = ((res or {}).get("pools") or {}).get("mejores") or {}
        restantes = pool.get("restantes")
    except Exception:                                          # noqa: BLE001 — sin saberlo, mejor no gastar
        restantes = None
    info = {"restantes": restantes, "minimo": int(p["minimo_restantes"]), "hoy": n_hoy, "max": int(p["max_por_dia"]),
            "total": p["total"], "necesita_perfil": p["necesita_perfil"]}
    if not p["activo"]:
        return {"ok": False, "motivo": "Las ideas están apagadas (ideas.activo en config.yaml).", **info}
    if p["necesita_perfil"]:
        return {"ok": False, "motivo": "Antes de pedir ideas, cuéntame de tu canal: es solo la primera vez (así las ideas salen de TU "
                                       "tema y no de otro).", **info}
    if _resumen is None and not os.environ.get("GEMINI_API_KEY"):
        return {"ok": False, "motivo": "No hay clave de Gemini: pégala en la pestaña «Uso».", **info}
    if _resumen is None:
        from . import youtube as _yt
        if not _yt.is_connected():       # sin YouTube, las ideas saldrían casi sin datos: no se gasta la petición
            return {"ok": False, "motivo": "Conecta YouTube primero (arriba, «YouTube»): las ideas salen de tus datos del "
                                           "canal y de lo que se busca en YouTube.", **info}
    if n_hoy >= int(p["max_por_dia"]):
        return {"ok": False, "motivo": f"Hoy ya pediste {n_hoy} tanda(s) de ideas (el tope es {p['max_por_dia']} al día). "
                                       "Mañana, más.", **info}
    if restantes is None:
        return {"ok": False, "motivo": "No sé cuántas peticiones de Gemini te quedan hoy: mira la pestaña «Uso».", **info}
    if restantes < int(p["minimo_restantes"]):
        return {"ok": False, "motivo": f"Hoy te quedan {restantes} peticiones de los mejores modelos y las ideas solo se "
                                       f"piden con {p['minimo_restantes']} o más: lo primero es editar bien tus vídeos. "
                                       "Mañana se reinicia.", **info}
    return {"ok": True, "motivo": "", **info}


def _ruta_refrescos(dir_work=None) -> Path:
    return _dir(dir_work) / "refrescos.json"


def refrescos_hoy(dir_work=None, ahora: float | None = None) -> int:
    """Cuántas ideas se han refrescado hoy (el día de cuota de Google). Solo cuentan las que SALIERON bien."""
    from . import uso
    ahora = time.time() if ahora is None else ahora
    d = _lee(_ruta_refrescos(dir_work), {}) or {}
    return _entero(d.get("n"), 0) if d.get("dia") == uso.dia_cuota(ahora) else 0


def _cuenta_refresco(dir_work, ahora: float) -> int:
    from . import uso
    n = refrescos_hoy(dir_work, ahora) + 1
    _guarda(_ruta_refrescos(dir_work), {"dia": uso.dia_cuota(ahora), "n": n})
    return n


def puede_refrescar(cfg, dir_work=None, ahora: float | None = None, _resumen=None) -> dict:
    """¿Se puede refrescar una idea ahora (1 petición a Flash-Lite)? Tope por día (`ideas.refrescos_max_dia`) y nunca con
    menos de `ideas.minimo_lite` peticiones de Flash-Lite hoy (lo primero es editar los vídeos).
    -> {"ok", "motivo", "hoy", "max", "restantes_lite", "minimo"}"""
    import os
    from . import uso
    p = parametros(cfg)
    ahora = time.time() if ahora is None else ahora
    n = refrescos_hoy(dir_work, ahora)
    try:
        res = _resumen(cfg) if _resumen is not None else uso.resumen(cfg, ahora)
        restantes = (((res or {}).get("pools") or {}).get("respaldo") or {}).get("restantes")
    except Exception:                                          # noqa: BLE001
        restantes = None
    info = {"hoy": n, "max": int(p["refrescos_max_dia"]), "restantes_lite": restantes, "minimo": int(p["minimo_lite"])}
    if not p["activo"]:
        return {"ok": False, "motivo": "Las ideas están apagadas (ideas.activo en config.yaml).", **info}
    if _resumen is None and not os.environ.get("GEMINI_API_KEY"):
        return {"ok": False, "motivo": "No hay clave de Gemini: pégala en la pestaña «Uso».", **info}
    if n >= info["max"]:
        return {"ok": False, "motivo": f"Hoy ya refrescaste {n} ideas (el tope es {info['max']} al día). Mañana, más.", **info}
    if restantes is None:
        return {"ok": False, "motivo": "No sé cuántas peticiones de Flash-Lite te quedan hoy: mira la pestaña «Uso».", **info}
    if restantes < info["minimo"]:
        return {"ok": False, "motivo": f"Hoy te quedan {restantes} peticiones de Flash-Lite y los refrescos solo se piden con "
                                       f"{info['minimo']} o más: lo primero es editar bien tus vídeos. Mañana se reinicia.", **info}
    return {"ok": True, "motivo": "", **info}


# ------------------------------------------------------------------ los datos
def _fuentes_reales() -> dict:
    from . import youtube as yt
    return {"mis_videos": lambda: _mis_videos_yt(yt), "trafico": yt.trafico_detalle, "videos_con_canal": yt.videos_con_canal,
            "subidas": yt.subidas_de_canal, "populares": yt.busca_populares, "comentarios": yt.comentarios_del_canal,
            "mi_canal": lambda: (yt.channel_info() or {}).get("channel_id")}


def _mis_videos_yt(yt) -> list[dict]:
    subidos = yt.list_channel_uploads(400)
    stats = yt.video_stats([v["video_id"] for v in subidos])
    out = []
    for v in subidos:
        s = stats.get(v["video_id"]) or {}
        out.append({"video_id": v["video_id"], "titulo": s.get("title") or v.get("title", ""),
                    "publicado": s.get("published_at") or v.get("published_at"), "vistas": s.get("views"),
                    "duracion_s": s.get("duration_s"), "privacidad": s.get("privacy")})
    return out


def _fecha(iso) -> str:
    return str(iso or "")[:10]


def _ts(iso) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _miles(n) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


def _ruta_datos(tid: str, dir_work=None) -> Path:
    return _dir(dir_work) / f"datos_{tid}.json"


def _guarda_datos(tid: str, datos: dict, dir_work=None, conservar: int = 5) -> None:
    """Los datos de ESTA tanda (con sus códigos): los «↻» los reusan, así no gastan cuota de YouTube. Solo se guardan los de las
    últimas `conservar` tandas."""
    _guarda(_ruta_datos(tid, dir_work), {"ts": time.time(), "grupos": datos["grupos"], "mis_titulos": datos.get("mis_titulos") or []})
    for viejo in sorted(_dir(dir_work).glob("datos_*.json"))[:-max(1, conservar)]:
        try:
            viejo.unlink()
        except OSError:
            pass


def _lee_datos(tid: str, dir_work=None) -> dict | None:
    d = _lee(_ruta_datos(tid, dir_work))
    if not isinstance(d, dict) or not isinstance(d.get("grupos"), dict):
        return None
    por_codigo = {it["codigo"]: it for items in d["grupos"].values() for it in items if isinstance(it, dict) and it.get("codigo")}
    return {"grupos": d["grupos"], "por_codigo": por_codigo, "notas": [], "cuota": 0, "mis_titulos": d.get("mis_titulos") or []}


def junta_datos(cfg, p: dict | None = None, dir_work=None, _fuentes=None, progreso=None, ahora: float | None = None) -> dict:
    """Reúne los datos (cada fuente, por separado: si una falla, se sigue sin ella y se apunta en «notas»).
    -> {"grupos": {"V": [...], "B": [...], "T": [...], "O": [...], "C": [...], "A": [...], "S": [...], "P": [...]},
        "por_codigo": {código: texto}, "notas": [...], "cuota": unidades estimadas de YouTube}"""
    p = p or parametros(cfg, None, dir_work)
    f = _fuentes or _fuentes_reales()
    ahora = time.time() if ahora is None else ahora
    notas: list[str] = []
    cuota = 0
    canal_id = p.get("canal") or _canal_por_defecto(cfg)

    def paso(t):
        if progreso:
            progreso(t)

    def intenta(nombre, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:                               # noqa: BLE001
            notas.append(f"No se pudo leer {nombre} ({str(exc)[:120]}): se sigue sin eso.")
            return None

    # Los datos del PROPIO canal (vídeos, búsquedas que llegan a él, comentarios) son los del canal que está conectado a YouTube: si este
    # canal es otro, no se leen (saldrían los de otro tema) y las ideas salen de lo que se ve en YouTube sobre su tema y de su perfil.
    propios = bool(p.get("datos_propios", True))
    mios: list = []
    mi_canal = None
    busq: list = []
    relac: list = []
    if not propios:
        notas.append("Este canal no es el que tienes conectado a YouTube: sus ideas salen de lo que se ve en YouTube sobre su tema y de "
                     "su perfil, sin los datos del canal conectado.")
    else:
        # tus vídeos (caché de 12 h: son ~9 unidades y no cambian tanto)
        paso("Leyendo tus vídeos y sus vistas…")
        cache_p = _dir(dir_work) / "cache_mis_videos.json"
        cache = _lee(cache_p, {}) or {}
        if cache.get("videos") and ahora - float(cache.get("ts") or 0) < CACHE_H * 3600:
            mios = cache["videos"]
        else:
            mios = intenta("tus vídeos", f["mis_videos"])
            if mios:
                cuota += 10
                try:
                    _guarda(cache_p, {"ts": ahora, "videos": mios})
                except OSError:
                    pass
            else:
                mios = cache.get("videos") or []
        mios = [v for v in mios or [] if v.get("titulo")]
        mi_canal = intenta("tu canal", f["mi_canal"]) if "mi_canal" in f else None

        paso("Mirando qué busca la gente en YouTube para llegar a tu canal…")
        busq = intenta("las búsquedas de YouTube (YouTube Analytics)", f["trafico"], "YT_SEARCH", int(p["dias_trafico"]), 25) or []
        relac = intenta("los vídeos junto a los que YouTube te recomienda", f["trafico"], "RELATED_VIDEO",
                        int(p["dias_trafico"]), 25) or []
    mis_ids = {v.get("video_id") for v in mios}

    paso("Buscando qué vídeos de tu nicho están rindiendo…")
    outliers: list[dict] = []
    ids_rel = [r["detalle"] for r in relac if r.get("detalle") and r["detalle"] not in mis_ids]
    det_rel = (intenta("los vídeos relacionados", f["videos_con_canal"], ids_rel) or {}) if ids_rel else {}
    if ids_rel:
        cuota += 1
    cuenta: dict[str, int] = {}
    for vid in ids_rel:
        c = (det_rel.get(vid) or {}).get("canal_id")
        if c and c != mi_canal:
            cuenta[c] = cuenta.get(c, 0) + 1
    # los canales que vigilar siempre (`ideas.canales_referencia`) son los del autor: otro canal no los usa (serían de otro tema)
    refs = [str(c) for c in p.get("canales_referencia") or [] if c] if (p.get("perfil") or {}).get("origen") == "config" else []
    canales = list(refs)
    canales += [c for c, _n in sorted(cuenta.items(), key=lambda x: -x[1]) if c not in canales]
    canales = canales[:int(p["canales_relacionados"]) + len(refs)]
    for c in canales:
        sub = intenta("un canal relacionado", f["subidas"], c, 20)
        if not sub:
            continue
        cuota += 3
        vids = [v for v in sub.get("videos") or [] if v.get("vistas") is not None and (v.get("duracion_s") or 999) > 60]
        if len(vids) < 5:
            continue
        mediana = statistics.median(v["vistas"] for v in vids) or 1
        for v in vids:
            t = _ts(v.get("publicado"))
            if t and ahora - t <= float(p["outlier_dias"]) * 86400 and v["vistas"] >= float(p["outlier_x"]) * mediana:
                idioma = idioma_de(v.get("titulo"), v.get("idioma"))
                if idioma != "otro":
                    outliers.append({**v, "canal": sub.get("canal", ""), "x": round(v["vistas"] / mediana, 1), "idioma": idioma})
    outliers.sort(key=lambda v: -v["x"])

    paso("Viendo lo más visto del último mes, en español y en inglés…")
    tend: list[dict] = []
    vistos_t: set[str] = set()
    # cada tanda mira unas pocas búsquedas por idioma de un conjunto más grande (rotan: otras cada vez y en unas tandas, todas): más mercado sin más cuota
    vuelta = len(tandas(dir_work, canal=canal_id)) + len(borradas(dir_work, canal_id))
    k_busq = _entero(p.get("busquedas_por_idioma"), 4)
    q_es, q_en = _rota(p.get("busquedas") or [], k_busq, vuelta), _rota(p.get("busquedas_en") or [], k_busq, vuelta)
    if len(q_es) < len(p.get("busquedas") or []) or len(q_en) < len(p.get("busquedas_en") or []):
        notas.append("Lo más visto de esta tanda: " + " · ".join([f"«{q}»" for q in q_es] + [f"«{q}» (EN)" for q in q_en])
                     + " — cada tanda mira otras búsquedas.")
    busquedas = [(q, "es") for q in q_es] + [(q, "en") for q in q_en]
    for q, idioma_q in busquedas:
        r = intenta(f"lo más visto para «{q}»", f["populares"], q, int(p["dias_tendencia"]), 15, idioma_q)
        if r is None:
            continue
        cuota += 101
        for v in r:
            if v.get("video_id") in vistos_t or v.get("video_id") in mis_ids or v.get("canal_id") == mi_canal:
                continue
            vistos_t.add(v.get("video_id"))
            idioma = idioma_de(v.get("titulo"), v.get("idioma"))
            if idioma != "otro":              # un vídeo en otro idioma (hindi, ruso…) no dice nada de tu público
                tend.append({**v, "busqueda": q, "idioma": idioma})

    paso("Leyendo las preguntas de tus comentarios…")
    coms = (intenta("tus comentarios", f["comentarios"], 100) or []) if propios else []
    if coms:
        cuota += 2
    pide = [c for c in coms if _PIDE.search(normalize(c.get("texto") or ""))]
    pide.sort(key=lambda c: -int(c.get("likes") or 0))

    # tu web: artículos y servicios del canal
    from . import enlaces as enl
    info = (cfg.get_path(f"videos.canales.{canal_id}") or {}) if cfg is not None and canal_id else {}
    arts = enl.articulos_del_canal(info) if info else []
    servicios = _destinos(info, p.get("perfil") or {})

    # lo que ya se le propuso (para no repetir) y lo que le gustó o no: las 3 últimas tandas de ESTE canal, con lo que se refrescó
    est = estado(dir_work)
    # (las 3 tandas más recientes, estén o no borradas: de una borrada solo queda el recuerdo de sus títulos, «no le interesó» salvo lo que ya hubiera marcado)
    recientes = sorted([(float(t.get("ts") or 0), t, False) for t in tandas(dir_work, canal=canal_id)]
                       + [(float(b.get("ts") or 0), b, True) for b in borradas(dir_work, canal_id)], key=lambda x: -x[0])[:3]
    previas = []
    for _ts_t, t, borrada in recientes:
        if borrada:
            previas += [(i, i.get("estado") or "borrada") for i in t.get("ideas") or []]
        else:
            previas += [(i, (est.get(i.get("id")) or {}).get("estado") or "") for i in list(t.get("ideas") or []) + list(t.get("ideas_anteriores") or [])]

    def _top(items, n):
        return sorted(items, key=lambda v: -(v.get("vistas") or 0))[:n]

    # lo más visto: hasta 24 en español y 24 en inglés (que el idioma de un solo lado no tape al otro) y unos pocos sin idioma claro
    tend_ok = sorted(_top([v for v in tend if v["idioma"] == "es"], 24) + _top([v for v in tend if v["idioma"] == "en"], 24)
                     + _top([v for v in tend if v["idioma"] not in ("es", "en")], 6), key=lambda v: -(v.get("vistas") or 0))

    g: dict[str, list[dict]] = {k: [] for k in "VBTOCASPEH"}
    for v in sorted(mios, key=lambda v: _fecha(v.get("publicado")), reverse=True)[:220]:
        g["V"].append({"texto": f"{_fecha(v.get('publicado'))} · {_miles(v.get('vistas'))} vistas · {v['titulo']}",
                       "titulo": v["titulo"], "vistas": v.get("vistas")})
    for b in busq[:25]:
        g["B"].append({"texto": f"«{b['detalle']}» — {_miles(b['vistas'])} vistas desde el buscador de YouTube"})
    for v in tend_ok:
        g["T"].append({"texto": f"{v['titulo']} — {v.get('canal', '')}, {_miles(v.get('vistas'))} vistas "
                                f"({_fecha(v.get('publicado'))}, búsqueda «{v['busqueda']}»{_etiqueta_idioma(v['idioma'])})",
                       "url": _url_yt(v.get("video_id")), "idioma": v["idioma"], "titulo": v["titulo"]})
    for v in outliers[:15]:
        g["O"].append({"texto": f"{v['titulo']} — {v['canal']}: {_miles(v['vistas'])} vistas, {v['x']}× lo normal en su "
                                f"canal ({_fecha(v.get('publicado'))}{_etiqueta_idioma(v['idioma'])})",
                       "url": _url_yt(v.get("video_id")), "idioma": v["idioma"], "titulo": v["titulo"]})
    for c in pide[:40]:
        g["C"].append({"texto": re.sub(r"\s+", " ", c["texto"])[:220] + (f" ({c['likes']} ❤)" if c.get("likes") else ""),
                       "url": _url_yt(c.get("video_id"))})
    for a in arts[:60]:
        g["A"].append({"texto": a["titulo"], "url": a["url"]})
    for k, nombre, url in servicios:
        g["S"].append({"texto": nombre, "clave": k, "url": url})
    for s in p.get("semillas") or []:                          # SUS ideas favoritas: el modelo del estilo (no son «evidencia»)
        g["E"].append({"texto": f"{s['titulo']} (potencial {'🔥' * int(s.get('potencial') or 2)})", "titulo": s["titulo"]})
    for h in p.get("herramientas") or []:                      # las herramientas que YA tiene (para las ideas «Creé una herramienta…»)
        g["H"].append({"texto": f"{h['nombre']} — {h['que_hace']}", "clave": h["nombre"]})
    for e_ in eliminadas(dir_work, canal_id)[:80]:             # lo que ELIMINASTE: la IA no lo repite ni propone algo muy parecido
        g["P"].append({"texto": f"{e_['titulo']} ✖ (la eliminó: no la repitas ni propongas algo muy parecido)"})
    for i, e in previas[:60]:
        marca_e = {"grabar": " ✔ (la va a grabar)", "grabada": " ✔ (ya la grabó)", "descartada": " ✖ (no le interesó)",
                   "borrada": " ✖ (la borró de su lista: no le interesó)"}.get(e, "")
        g["P"].append({"texto": f"{i.get('titulo', '')}{marca_e}"})
    por_codigo: dict[str, dict] = {}
    for letra, items in g.items():
        for n, it in enumerate(items, 1):
            it["codigo"] = f"{letra}{n}"
            por_codigo[it["codigo"]] = it
    return {"grupos": g, "por_codigo": por_codigo, "notas": notas, "cuota": cuota,
            "mis_titulos": [v["titulo"] for v in mios]}


def _url_yt(vid) -> str:
    return f"https://www.youtube.com/watch?v={vid}" if vid else ""


def _canal_por_defecto(cfg) -> str:
    """El canal al que se refiere `ideas:` de config.yaml (`ideas.canal`, o «ejemplo» si existe) o, si no, el primero."""
    from .config import canales_visibles
    return perfiles.canal_con_config(cfg) or next(iter(canales_visibles(cfg)), "")


def _destinos(info: dict, perfil: dict) -> list[tuple[str, str, str]]:
    """A dónde llevar a la gente: [(clave, nombre, url)]. el autor: sus servicios de siempre (de los enlaces de su canal). Otro canal: los
    destinos de su perfil y los enlaces de su canal (llamada a la acción y web), sin repetir. La IA nunca escribe una URL."""
    enl = dict(info.get("enlaces") or {})
    if perfil.get("origen") == "config":
        return [(k, _SERVICIOS[k], enl[k]) for k in _SERVICIOS if enl.get(k)]
    out: list[tuple[str, str, str]] = []
    vistos: set = set()
    for i, d in enumerate(perfil.get("destinos") or [], 1):
        if d.get("url") and d["url"] not in vistos:
            vistos.add(d["url"])
            out.append((f"destino{i}", d.get("nombre") or "Mi enlace", d["url"]))
    for k, nombre in (("cta_url", enl.get("cta_texto") or "Mi enlace principal"), ("web", "Mi página web")):
        if enl.get(k) and enl[k] not in vistos:
            vistos.add(enl[k])
            out.append((k, nombre, enl[k]))
    return out


# ------------------------------------------------------------------ la petición
_SYSTEM = """Eres el estratega de contenido del canal de YouTube «{canal}». Propones ideas de vídeos concretas, con
demanda demostrada y alineadas con el negocio del autor. Respondes SOLO con JSON válido, sin markdown."""

_BLOQUES = (("B", "Lo que la gente escribe en el BUSCADOR DE YOUTUBE para llegar a su canal (últimos {dias} días)"),
            ("T", "Lo MÁS VISTO del último mes en YouTube para el nicho, {idiomas}"),
            ("O", "Vídeos de canales parecidos que rinden MUY POR ENCIMA de lo normal en su canal (la señal más fuerte)"),
            ("C", "Preguntas y peticiones en SUS COMENTARIOS"),
            ("V", "SUS VÍDEOS ya publicados (fecha · vistas · título): no repitas un tema que ya tenga salvo con un ángulo "
                  "claramente nuevo (dilo en «por_que»)"),
            ("A", "Artículos de SU WEB (para llevar a la gente; son pocos: las ideas generales NO tienen que salir de aquí)"),
            ("S", "{destinos}"),
            ("E", "SUS IDEAS FAVORITAS: el ESTILO que quiere. YA LAS TIENE: tus ideas nuevas tienen que tratar OTROS TEMAS con ese estilo (copiar una o cambiarle una palabra "
                  "es una repetición y se tira). Entre paréntesis, su potencial según él (🔥🔥🔥 = máximo)"),
            ("H", "HERRAMIENTAS que el autor YA ha creado (cada una puede dar un vídeo «Creé una herramienta…»; no inventes ninguna otra)"),
            ("P", "Ideas que YA se le propusieron (✔ la va a grabar o ya la grabó · ✖ no le interesó): no repitas "
                  "ninguna y aprende de lo que le gustó"))

# Qué es cada tipo de casilla sale del PERFIL del canal (`p["tipos"]`): para el autor, lo que él dijo el 2026-10-04 (negocio: quien podría
# contratar su SEO; aprender: futuros CEOs que quieren aprender SEO, su curso; general: lo que funciona en español Y en inglés).

_REGLAS_TITULO = ("EL TÍTULO ES LO MÁS IMPORTANTE: tiene que dar ganas de hacer clic. Reglas: máximo 70 caracteres (mejor 45-65), en "
                  "español CON tildes y ¿¡; enganchan SIN mentir ni ser sensacionalistas vacíos (el público busca contenido de "
                  "verdad); son concretos (nada de títulos genéricos tipo «Todo sobre…»); tienen un ángulo claro (beneficio directo, pregunta o "
                  "curiosidad, número o lista, error o mito); sin emojis; y son DISTINTOS entre sí: ni dos ideas con el mismo tema "
                  "ni con el mismo ángulo. Escríbelos como una frase normal en español (solo la primera letra en mayúscula y los nombres propios), NUNCA «Con Cada Palabra En "
                  "Mayúscula Inicial». Fíjate en los títulos de [V] con más vistas y, si hay, en sus ideas favoritas [E] para el estilo del canal.")

_ANTI_INVENTO = ("LOS DATOS son reales y cada uno lleva un código. Decide con ellos y CITA los códigos en \"evidencia\". NUNCA "
                 "inventes datos, cifras, noticias, lanzamientos, actualizaciones ni tendencias que no estén aquí: una idea sin dato "
                 "que la respalde tiene que ser un tema que siempre sirve (deja \"evidencia\" vacía y dilo en \"por_que\").")

# Sobre las ideas «general» y el estilo de TODAS (el autor, 2026-10-05): sus ideas favoritas [E] son el modelo; las nuevas se parecen en estilo y enfoque, sin repetirlas.
# Solo entra si el canal tiene ideas favoritas: un canal sin ellas ve el prompt de siempre.
_REGLAS_GENERAL = (
    "SOBRE LAS IDEAS «general» (y el estilo de TODAS): el autor tiene unas IDEAS FAVORITAS [E] y las nuevas tienen que sentirse de su misma familia PERO TRATAR OTROS "
    "TEMAS: copiar una favorita o cambiarle una palabra es una repetición y se tira (esas ya las tiene). Lo que las hace buenas: "
    "(1) un resultado o una curiosidad CONCRETA, nunca un tema genérico de manual («Cómo conseguir X que tu competencia consigue y tú NO», no «Todo sobre X» ni «Guía completa de X»); "
    "(2) experimentos y casos reales en primera persona («Hice X de mi propio proyecto y encontré esto», «Analicé un caso con resultados malos y esto estaba "
    "mal»): es una PROPUESTA de lo que el autor hará antes de grabar, así que NO inventes resultados ni cifras de resultados en el título («…y encontré esto», no «…y "
    "encontré 47 errores»); "
    "(3) métodos, trucos y herramientas GRATIS, y ChatGPT o la IA aplicados a tu tema «de verdad»; "
    "(4) lo que las herramientas no te muestran o lo que casi nadie revisa; "
    "(5) a veces (en MENOS de la mitad de los títulos, solo cuando aporta) UNA palabra en MAYÚSCULAS para dar énfasis (GRATIS, NO, REAL), como hace él; los demás "
    "títulos, como una frase normal sin ninguna; "
    "(6) listas de lo que él revisa o hace («10 cosas que reviso antes de…»). "
    "FORMAS que le funcionan (sirven para la ESTRUCTURA, no para el tema; mézclalas, no uses la misma en todas): «Creé una herramienta … GRATIS que …» · «Hice … de … y "
    "encontré esto» · «Analicé … y esto estaba mal» · «Cómo encontrar/descubrir/saber … GRATIS (o usando solo …)» · «Cómo usar ChatGPT para … REAL» · «N cosas que reviso "
    "antes de …» · «Cómo encontrar … que tus herramientas NO te muestran». "
    "Una idea de «Creé una herramienta…» solo puede ser sobre una de las herramientas [H] que YA ha creado (cita su código en «evidencia»; nunca inventes una herramienta "
    "que no tenga). "
    "Las ideas «general» NO tienen que salir de sus artículos [A] (solo tiene unos pocos y el tema es enorme): al menos la mitad se apoyan en lo que se está MOVIENDO ahora "
    "en el mercado de habla hispana y en el de Estados Unidos (los datos [T] y [O]: cítalos en «evidencia»; úsalos como señal del TEMA que se mueve y escríbelo con el "
    "estilo del autor, NUNCA copies el título de otro vídeo), y el resto pueden ser temas que siempre sirven con ese mismo estilo. En ellas «cta» puede ir vacío.")


# «Creé una herramienta…» (o un auditor, una extensión, un bot…) afirma que el autor TIENE algo: solo vale si cita una de sus herramientas [H] (nunca se inventa lo que tiene)
_CREO_HERRAMIENTA = re.compile(r"^\s*(?:cree|construi|programe|desarrolle|arme|hice)\s+(?:una?|mi)\s+(?:herramienta|extension|app|aplicacion|script|bot|chatbot|plugin|software|"
                               r"calculadora|dashboard|panel|auditor|analizador|generador|checker|agente|gpt|programa|sistema)\b")


def _regla_tema(p: dict) -> str:
    """Las ideas son del TEMA del canal (el autor, 13-oct-2026: entre los datos de tendencia había de todo —«el truco definitivo para usar ElevenLabs gratis»— y la IA lo convirtió en idea).
    Siempre va en el prompt (también el de «↻»)."""
    tema = str((p.get("perfil") or {}).get("tema") or "").strip() or "el tema del canal"
    extra = ""
    if any(t["id"] == "marketing" for t in p.get("tipos") or []):
        extra = " Única excepción: las ideas de tipo «marketing», que son de MARKETING DIGITAL en general."
    return (f"EL TEMA MANDA: toda idea tiene que tratar de {tema} (lo que se está moviendo ahora, lo que la gente busca y lo que explica cómo funciona).{extra} "
            "Entre los datos [T] y [O] hay vídeos de OTROS temas (herramientas de IA de voz o de imagen, gaming, productividad…): IGNÓRALOS por muy vistos que estén; "
            f"úsalos solo si tratan de {tema}, como señal de lo que se mueve. Una idea de otro tema, aunque tenga muchas vistas, es un error y no se acepta. "
            "Cada idea tiene que poder explicarse en una frase, sin conocer el vídeo.")


def _recordatorio_general(n: int) -> str:
    """Lo último que lee la IA antes de responder (en un prompt tan largo, lo del principio se diluye): las «general» son lo que más le importa al autor."""
    return (f"ANTES DE RESPONDER comprueba las {n} ideas «general»: (1) se parecen a las favoritas [E] (concretas, con un resultado o una curiosidad; a menudo en primera "
            "persona o «Cómo…» con un resultado claro), NO son temas genéricos de manual y tratan un TEMA DISTINTO al de cualquier favorita [E]; (2) ninguna copia el título "
            "de un vídeo de [T] u [O] ni de una favorita; (3) los títulos van como "
            f"una frase normal y solo en MENOS de la mitad hay UNA palabra en MAYÚSCULAS para dar énfasis; (4) son exactamente {n}; (5) TODAS tratan del tema del canal (nada de otros temas "
            "que aparezcan en [T] u [O]) y al menos 4 son informativas (qué es / cómo funciona un concepto).")


def _campos(p: dict) -> list[str]:
    return ["CADA IDEA lleva SOLO esto (corto: la lista se lee de un vistazo):",
            "- \"tipo\": " + " | ".join(f"\"{t['id']}\"" for t in p["tipos"] if t["n"] > 0),
            "- \"titulo\": el título provisional para YouTube (reglas de arriba)",
            "- \"de_que_va\": 1-2 frases (máx. 260 caracteres): qué le promete el vídeo al espectador y qué va a entender o poder hacer al terminar",
            "- \"puntos\": de 3 a 4 puntos MUY cortos (máx. 80 caracteres cada uno), en orden, de lo que contaría el vídeo (se ven al desplegar la idea)",
            "- \"por_que\": UNA frase (máx. 170 caracteres): por qué puede funcionar AHORA, apoyada en los datos",
            "- \"evidencia\": los CÓDIGOS de los datos que la respaldan, p. ej. [\"B3\", \"T5\"] (vacío si es un tema que siempre sirve; una idea de «Creé una "
            "herramienta…» cita su [H])",
            "- \"para_quien\": a quién atrae (máx. 60 caracteres)",
            "- \"cta\": el código del destino [S] o del artículo [A] al que llevar a la gente (en las «general» puede ir vacío: no tienen que llevar a un artículo)",
            "- \"formato\": \"pantalla\" (se ve la pantalla: tutorial, gameplay…) | \"cara\" (hablando a cámara) | \"mixto\"",
            "- \"dificultad\": \"baja\" | \"media\" | \"alta\" (cuánto le cuesta prepararlo y grabarlo)"]


def _bloques_datos(datos: dict, p: dict) -> list[str]:
    idiomas = "en ESPAÑOL y en INGLÉS (cada dato dice su idioma)" if p.get("idiomas") == "es+en" else "en ESPAÑOL"
    L: list[str] = []
    for letra, titulo in _BLOQUES:
        items = datos["grupos"].get(letra) or []
        if letra in ("E", "H") and not items:                  # (las tandas de antes y los canales sin favoritas no tienen estos bloques)
            continue
        L += ["", f"[{letra}] {titulo.format(dias=p['dias_trafico'], idiomas=idiomas, destinos=p['etiqueta_destinos'])}:"]
        L += [f"{it['codigo']}: {it['texto']}" for it in items] or ["(no hay datos de esto ahora)"]
    return L


def arma_prompt(datos: dict, p: dict, canal: str = "", contexto: str = "") -> str:
    tipos = [t for t in p["tipos"] if t["n"] > 0]
    total = sum(t["n"] for t in tipos)
    n_gen = next((t["n"] for t in tipos if t["id"] == perfiles.ID_GENERAL), 0)
    cada = max(2, n_gen // 3)
    reparto = []
    for t in tipos:
        extra = ""
        if t["id"] == perfiles.ID_GENERAL and p.get("idiomas") == "es+en":
            extra = (f" Que haya de las dos: al menos {cada} inspiradas en lo que funciona en ESPAÑOL y al menos {cada} en lo que "
                     "funciona en INGLÉS, el mercado de Estados Unidos (adaptadas al público hispano y escritas en español).")
        reparto.append(f"- {t['n']} de tipo \"{t['id']}\": {t['descripcion']}{extra}")
    orden = [f"las de {t['id']}" for t in tipos if t["id"] != perfiles.ID_GENERAL]
    orden_txt = ("primero " + ", luego ".join(orden) + " y al final las generales") if orden else "las generales"
    con_fav = bool(datos["grupos"].get("E"))                   # con ideas favoritas, las generales (las que más importan) van PRIMERO: son las que más cuidado piden
    if con_fav and orden and n_gen:
        orden_txt = "primero las generales (las que más le importan al autor: dales todo el cuidado), luego " + ", luego ".join(orden)
    L = [f"Canal: «{canal}». {contexto}".strip(), "",
         f"{p['etiqueta_objetivo']}: {p['objetivo']}", "",
         f"Tu trabajo: proponer EXACTAMENTE {total} ideas de vídeos NUEVOS que el autor pueda grabar pronto, con ESTE reparto "
         "(obligatorio):", *reparto, "", _REGLAS_TITULO, "", _ANTI_INVENTO]
    if con_fav:                                                # el canal tiene ideas favoritas: el estilo de las generales
        L += ["", _REGLAS_GENERAL]
    L += ["", _regla_tema(p)]
    L += _bloques_datos(datos, p)
    L += ["", *_campos(p), ""]
    if con_fav and n_gen:
        L += [_recordatorio_general(n_gen), ""]
    L += [f"Todo en español (también los títulos de las ideas que salen de un vídeo en inglés). Pon las ideas en "
          f"este orden: {orden_txt}. "
          "Responde SOLO con este JSON: {\"ideas\": [ … ]}"]
    return "\n".join(L)


def arma_prompt_refresco(datos: dict, p: dict, canal: str, contexto: str, vieja: dict, otras: list[dict],
                         anteriores: list[dict]) -> str:
    """El prompt de «↻»: UNA idea nueva para la casilla de `vieja`, sin repetir ninguna de la lista."""
    por_id = {t["id"]: t for t in p["tipos"]}
    tipo = vieja.get("tipo") if vieja.get("tipo") in por_id else perfiles.ID_GENERAL
    casilla = (por_id.get(tipo) or {}).get("descripcion") or ""
    L = [f"Canal: «{canal}». {contexto}".strip(), "",
         f"{p['etiqueta_objetivo']}: {p['objetivo']}", "",
         "El autor tiene una lista de ideas de vídeos y quiere CAMBIAR UNA. Tu trabajo: proponer UNA idea NUEVA para esa casilla.", "",
         f"LA CASILLA es de tipo \"{tipo}\": {casilla}", "",
         f"LA IDEA QUE SE CAMBIA (no la repitas ni propongas nada parecido): «{vieja.get('titulo', '')}»", "",
         "YA ESTÁN EN LA LISTA (no repitas ninguna ni su tema):"]
    L += [f"- {i.get('titulo', '')}" for i in otras]
    if anteriores:
        L += ["", "SE PROPUSIERON ANTES EN ESTA LISTA Y SE CAMBIARON (tampoco las repitas):"]
        L += [f"- {i.get('titulo', '')}" for i in anteriores][:30]
    if tipo == perfiles.ID_GENERAL and p.get("idiomas") == "es+en":
        gen = [i for i in otras if i.get("tipo") == perfiles.ID_GENERAL]
        en = sum(1 for i in gen if i.get("idioma") in ("en", "es+en"))
        es = sum(1 for i in gen if i.get("idioma") in ("es", "es+en"))
        L += ["", f"Entre las generales de la lista ahora hay {es} inspiradas en lo que funciona en español y {en} en inglés: " +
              ("esta mejor inspirada en INGLÉS." if en < es else "esta mejor inspirada en ESPAÑOL." if es < en else
               "da igual en cuál se inspire.")]
    L += ["", _REGLAS_TITULO, "", _ANTI_INVENTO]
    if tipo == perfiles.ID_GENERAL and (datos["grupos"].get("E") or []):
        L += ["", _REGLAS_GENERAL]
    L += ["", _regla_tema(p)]
    L += _bloques_datos(datos, p)
    L += ["", *_campos(p), "", "Todo en español. Responde SOLO con este JSON, con UNA sola idea: {\"ideas\": [ { … } ]}"]
    return "\n".join(L)


_ESQUEMA = None


def _esquema():
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from pydantic import BaseModel

            class Idea(BaseModel):
                tipo: str
                titulo: str
                de_que_va: str
                puntos: list[str]
                por_que: str
                evidencia: list[str]
                para_quien: str
                cta: str
                formato: str
                dificultad: str

            class Ideas(BaseModel):
                ideas: list[Idea]

            _ESQUEMA = Ideas
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQUEMA = False
    return _ESQUEMA or None


def _json_de(texto: str):
    t = str(texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1] if i >= 0 and j > i else t)


def _tokens(s: str) -> set[str]:
    return {w for w in normalize(s).split() if len(w) > 2 and w not in _VACIAS}


def _parecida(titulo: str, titulos: list[str]) -> str | None:
    a = _tokens(titulo)
    if len(a) < 2:
        return None
    for t in titulos:
        b = _tokens(t)
        comun = a & b
        if len(comun) >= 3 and len(comun) / len(a | b) >= 0.5:
            return t
    return None


def _txt(v, tope: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:tope]


def _tipo_de(v, tipos: list[dict]) -> str:
    """El tipo que dijo la IA -> uno de los del perfil (por su id, su nombre o el principio del id); si no cuadra, «general»."""
    t = normalize(str(v or "")).strip().replace(" ", "-")
    ids = [x["id"] for x in tipos]
    if t in ids:
        return t
    for x in tipos:
        if t == normalize(x["nombre"]).strip().replace(" ", "-") or (len(t) >= 5 and t[:5] == x["id"][:5]):
            return x["id"]
    return perfiles.ID_GENERAL if perfiles.ID_GENERAL in ids else ids[-1]


def parsea(texto: str, datos: dict, p: dict, minimo: int | None = None, maximo: int | None = None,
           tipo_fijo: str | None = None, ya: list[str] | None = None) -> list[dict]:
    """El JSON de la IA -> las ideas LIMPIAS: cada «evidencia» resuelta a su dato real (los códigos que no existen se
    tiran), la llamada a la acción solo si es un servicio [S] o un artículo [A] de la lista, el idioma de donde sale la idea
    (de sus datos de YouTube) y marcada la idea que se parece mucho a un vídeo que ya tiene. Una idea que repite o se parece
    mucho a otra (a las de `ya` o a una anterior de la misma respuesta) se tira. Lanza si quedan menos de `minimo` (por
    defecto, el 60 % de la mezcla): así se prueba otra vez / con otro modelo."""
    d = _json_de(texto)
    lista = d.get("ideas") if isinstance(d, dict) else d
    total = sum(int(v) for v in p["mezcla"].values())
    minimo = max(1, int(total * 0.6)) if minimo is None else minimo
    maximo = total + 4 if maximo is None else maximo
    out: list[dict] = []
    titulos: list[str] = list(ya or [])
    vistos: set[str] = {normalize(t) for t in titulos}
    for x in lista if isinstance(lista, list) else []:
        if not isinstance(x, dict):
            continue
        titulo = _txt(x.get("titulo"), 90).strip('"«»“”')
        if not titulo or normalize(titulo) in vistos or _parecida(titulo, titulos):
            continue
        vistos.add(normalize(titulo))
        titulos.append(titulo)
        tipo = tipo_fijo or _tipo_de(x.get("tipo"), p["tipos"])
        codigos = [str(c).strip().upper() for c in (x.get("evidencia") or []) if str(c).strip()]
        evid = [{"codigo": c, "texto": datos["por_codigo"][c]["texto"], "url": datos["por_codigo"][c].get("url", ""),
                 "idioma": datos["por_codigo"][c].get("idioma", "")}
                for c in dict.fromkeys(codigos) if c in datos["por_codigo"] and c[0] in "BTOCVAH"]
        if tipo == perfiles.ID_GENERAL and _CREO_HERRAMIENTA.match(normalize(titulo)) and not any(e["codigo"][0] == "H" for e in evid):
            continue                                           # «Creé una herramienta…» sin citar una de las suyas [H]: sería inventar que la tiene
        langs = {e["idioma"] for e in evid if e["codigo"][0] in "TO" and e["idioma"] in ("es", "en")}
        idioma = "es+en" if langs == {"es", "en"} else (next(iter(langs)) if langs else "")
        cta_c = str(x.get("cta") or "").strip().upper()
        cta = None
        if cta_c in datos["por_codigo"] and cta_c[0] in "SA":
            it = datos["por_codigo"][cta_c]
            cta = {"codigo": cta_c, "texto": it["texto"], "url": it.get("url", "")}
        pals = (p.get("perfil") or {}).get("palabras_tema")
        if pals and not re.search("|".join(pals), normalize(f"{titulo} {x.get('de_que_va') or ''} {x.get('por_que') or ''}")):
            continue                                           # de OTRO tema (SEO / marketing digital): no se acepta aunque la IA la haya devuelto
        puntos = [_txt(q, 110) for q in (x.get("puntos") or []) if _txt(q, 110)][:5] if isinstance(x.get("puntos"), list) else []
        out.append({"tipo": tipo, "titulo": titulo, "de_que_va": _txt(x.get("de_que_va"), 300), "puntos": puntos,
                    "por_que": _txt(x.get("por_que"), 260), "evidencia": evid, "para_quien": _txt(x.get("para_quien"), 100),
                    "cta": cta, "formato": _txt(x.get("formato"), 20).lower() or "mixto",
                    "dificultad": _txt(x.get("dificultad"), 10).lower() or "media", "idioma": idioma,
                    "parecida_a": _parecida(titulo, datos.get("mis_titulos") or [])})
    if len(out) < minimo:
        raise ValueError(f"la IA devolvió solo {len(out)} ideas utilizables")
    return out[:maximo]


def _ajusta_mezcla(ideas: list[dict], tipos: list[dict]) -> tuple[list[dict], list[str]]:
    """Las ideas ordenadas por tipo (como en el perfil: primero los públicos, al final las generales) y recortadas a lo pedido; si de un
    tipo salieron menos, lo dice. -> (ideas, notas)"""
    salida: list[dict] = []
    notas: list[str] = []
    for t in tipos:
        pedidas = int(t["n"])
        hay = [i for i in ideas if i["tipo"] == t["id"]]
        salida += hay[:pedidas]
        if len(hay) < pedidas:
            notas.append(f"Salieron {len(hay)} de {pedidas} ideas «{t['nombre']}».")
    return salida, notas


_AUTO = object()


def _llama_gemini(cfg, prompt: str, system: str, valida, lite: bool, etiqueta: str, esquema=_AUTO):
    """-> (resultado de `valida`, modelo). Mejor Flash (la tanda) o Flash-Lite (guion y «↻»); sin clave, excepción clara."""
    from . import modelos as _m
    from . import util as _util
    from .director import _gemini as _abre
    g = _abre(cfg)
    if not g:
        raise RuntimeError("No hay clave de Gemini (o la IA está apagada): pégala en la pestaña «Uso».")
    client, types = g[:2]
    if lite:
        cadena = _m.cadena_lite(cfg, client)
    else:        # SOLO los Flash buenos: el autor pidió «la IA más avanzada» para esto (si no responden, se dice, no se baja)
        cadena = [m for m in _m.cadena(cfg, client)
                  if (_m.analiza(m) or {}).get("familia") == "flash" and not (_m.analiza(m) or {}).get("alias")]
        if not cadena:
            raise RuntimeError("Ahora mismo no hay ningún Gemini Flash disponible: prueba más tarde.")
    if esquema is _AUTO:
        esquema = _esquema_guion() if lite else _esquema()
    res, modelo, _uso = _util.call_gemini_cadena(client, types, cadena, prompt, system=system, valida=valida,
                                                 reintento_s=60.0, pasadas=1, timeout_s=180.0, esquema=esquema,
                                                 etiqueta=etiqueta)
    return res, modelo


def genera(cfg, dir_work=None, _fuentes=None, _llama=None, _resumen=None, progreso=None, ahora: float | None = None,
           canal: str | None = None) -> dict:
    """Una tanda de ideas: freno -> datos -> UNA petición al mejor Flash -> se guarda (con los datos, para los «↻»). Nunca lanza.
    -> {"ok": True, "tanda": {...}} o {"ok": False, "error": qué pasó y qué hacer}.
    `_fuentes`/`_llama`/`_resumen` son solo para las pruebas (sin red)."""
    from .util import nombre_modelo
    ahora = time.time() if ahora is None else ahora
    p = parametros(cfg, canal, dir_work)
    canal_id = p["canal"]
    fr = puede_generar(cfg, dir_work, ahora, _resumen, canal=canal_id)
    if not fr["ok"]:
        return {"ok": False, "error": fr["motivo"]}
    try:
        datos = junta_datos(cfg, p, dir_work, _fuentes, progreso, ahora)
        info = (cfg.get_path(f"videos.canales.{canal_id}") or {}) if cfg is not None and canal_id else {}
        canal = str(info.get("nombre") or canal_id or "")
        prompt = arma_prompt(datos, p, canal, str(info.get("contexto") or p["perfil"].get("tema") or ""))
        if progreso:
            progreso(f"Pidiendo las {p['total']} ideas al mejor Gemini Flash…")
        from . import uso as _uso
        _uso.contexto(job="ideas")
        try:
            llama = _llama or (lambda pr, sy, va: _llama_gemini(cfg, pr, sy, va, False, "ideas"))
            ideas, modelo = llama(prompt, _SYSTEM.format(canal=canal or "de YouTube"),
                                  lambda t: parsea(t, datos, p, ya=[s["titulo"] for s in p.get("semillas") or []] + titulos_eliminados(dir_work, canal_id)))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Ideas de vídeos: {exc}")
        return {"ok": False, "error": _error_claro(exc)}
    ideas, notas_mezcla = _ajusta_mezcla(ideas, p["tipos"])
    tid = time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora))
    for n, i in enumerate(ideas, 1):
        i["id"] = f"{tid}-{n:02d}"
    cuenta = {t["id"]: sum(1 for i in ideas if i["tipo"] == t["id"]) for t in p["tipos"]}
    notas = list(datos["notas"]) + notas_mezcla
    tanda = {"id": tid, "ts": ahora, "version": VERSION, "canal": canal_id, "modelo": modelo, "nombre_modelo": nombre_modelo(modelo),
             "tipos": p["tipos"], "ideas": ideas, "ideas_anteriores": [], "cuenta": cuenta, "mezcla": p["mezcla"], "notas": notas,
             "cuota_youtube": datos["cuota"], "datos": {k: len(v) for k, v in datos["grupos"].items()}}
    try:
        _guarda_datos(tid, datos, dir_work)
    except OSError as exc:                                     # sin los datos no habría «↻», pero la lista sí sirve
        notas.append(f"No se pudieron guardar los datos de esta tanda ({exc}): no podrás refrescar sus ideas.")
    _guarda(_dir(dir_work) / f"tanda_{tid}.json", tanda)
    log(f"Ideas de vídeos: {len(ideas)} ideas ({nombre_modelo(modelo)}; ~{datos['cuota']} unidades de YouTube).")
    return {"ok": True, "tanda": tanda}


def refresca(idea_id: str, cfg, dir_work=None, _llama=None, _resumen=None, ahora: float | None = None) -> dict:
    """«↻» de una idea de la lista (el autor, 2026-10-04: «que todos se puedan refrescar»): la cambia por otra del MISMO tipo con UNA
    petición a Flash-Lite, con los datos que se guardaron con esa tanda (no gasta cuota de YouTube) y sin repetir ninguna de la
    lista. La idea cambiada no se pierde (`ideas_anteriores`: las siguientes tandas tampoco la repiten). Solo la tanda más
    reciente y de la lista nueva; una idea en «Por grabar» no se refresca. Con tope por día (`puede_refrescar`). Nunca lanza.
    -> {"ok": True, "idea", "modelo", "hoy", "max"} o {"ok": False, "error", "codigo": no_esta|antigua|grabar|tope|sin_datos|ia}"""
    from .util import nombre_modelo
    ahora = time.time() if ahora is None else ahora
    t = next((x for x in tandas(dir_work) if any(i.get("id") == idea_id for i in x.get("ideas") or [])), None)
    canal_id = _canal_de(t) if t else None
    ultima = (tandas(dir_work, canal=canal_id) or [None])[0] if t else None          # solo la tanda más reciente DE SU CANAL
    pos = next((k for k, i in enumerate(t.get("ideas") or []) if i.get("id") == idea_id), None) if t and ultima and ultima["id"] == t["id"] else None
    p = parametros(cfg, canal_id, dir_work)
    if t is None or pos is None:
        return {"ok": False, "codigo": "no_esta", "error": "Esa idea ya no está en la lista (¿ya la refrescaste, o es de una "
                                                            "tanda anterior?). Recarga la página."}
    if int(t.get("version") or 1) < 2:
        return {"ok": False, "codigo": "antigua", "error": "Esta tanda es del formato anterior: pide una tanda nueva («Dame "
                                                           f"{p['total']} ideas») para poder refrescar sus ideas."}
    vieja = t["ideas"][pos]
    if (estado(dir_work).get(idea_id) or {}).get("estado") in ("grabar", "grabada"):
        return {"ok": False, "codigo": "grabar", "error": "Esta idea está en «Por grabar»: no se refresca para que no desaparezca. "
                                                          "Quítala de «Por grabar» si quieres cambiarla."}
    fr = puede_refrescar(cfg, dir_work, ahora, _resumen)
    if not fr["ok"]:
        return {"ok": False, "codigo": "tope", "error": fr["motivo"]}
    datos = _lee_datos(t["id"], dir_work)
    if datos is None:
        return {"ok": False, "codigo": "sin_datos", "error": "No tengo guardados los datos de esta tanda: pide una tanda nueva."}
    try:
        info = (cfg.get_path(f"videos.canales.{canal_id}") or {}) if cfg is not None and canal_id else {}
        canal = str(info.get("nombre") or canal_id or "")
        if t.get("tipos"):                                    # los tipos con los que se hizo ESA tanda (si el perfil cambió después)
            p = {**p, "tipos": t["tipos"]}
        otras = [i for k, i in enumerate(t["ideas"]) if k != pos]
        anteriores = list(t.get("ideas_anteriores") or [])
        ya = [i.get("titulo", "") for i in t["ideas"]] + [i.get("titulo", "") for i in anteriores] + [s["titulo"] for s in p.get("semillas") or []] + titulos_eliminados(dir_work, canal_id)
        prompt = arma_prompt_refresco(datos, p, canal, str(info.get("contexto") or p["perfil"].get("tema") or ""), vieja, otras, anteriores)
        from . import uso as _uso
        _uso.contexto(job="ideas")
        try:
            llama = _llama or (lambda pr, sy, va: _llama_gemini(cfg, pr, sy, va, True, "ideas_refresco", esquema=_esquema()))
            nuevas, modelo = llama(prompt, _SYSTEM.format(canal=canal or "de YouTube"),
                                   lambda txt: parsea(txt, datos, p, minimo=1, maximo=1, tipo_fijo=vieja.get("tipo"), ya=ya))
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Ideas de vídeos: no se pudo refrescar {idea_id}: {exc}")
        return {"ok": False, "codigo": "ia", "error": _error_claro(exc, refresco=True)}
    nueva = dict(nuevas[0])
    usados = {i.get("id") for i in t["ideas"]} | {i.get("id") for i in anteriores}
    n = len(anteriores) + 1
    while f"{t['id']}-r{n:02d}" in usados:
        n += 1
    nueva["id"] = f"{t['id']}-r{n:02d}"
    nueva["refrescada_de"] = vieja.get("id")
    t["ideas"][pos] = nueva
    t["ideas_anteriores"] = anteriores + [{**vieja, "reemplazada_ts": ahora}]
    _guarda(_dir(dir_work) / f"tanda_{t['id']}.json", t)
    hoy = _cuenta_refresco(dir_work, ahora)
    log(f"Ideas de vídeos: «{vieja.get('titulo')}» -> «{nueva['titulo']}» ({nombre_modelo(modelo)}; refresco {hoy} de hoy).")
    return {"ok": True, "idea": nueva, "modelo": nombre_modelo(modelo), "hoy": hoy, "max": int(p["refrescos_max_dia"])}


def _error_claro(exc: Exception, refresco: bool = False) -> str:
    from .director import _motivo_corto, es_saturacion
    m = str(exc)
    if es_saturacion(exc):
        return "Gemini está saturado ahora mismo. Prueba en unos minutos (los intentos saturados no gastan tu cuota)."
    if "exceeded your current quota" in m.lower() or "quota exceeded" in m.lower():
        return "Se acabaron por hoy las peticiones gratis de ese modelo. Mañana se reinicia."
    if "clave" in m or "no hay ningún" in m.lower():
        return m
    if refresco:
        return f"No se pudo cambiar esa idea ({_motivo_corto(m)}). Prueba otra vez en un momento."
    return f"No se pudieron pedir las ideas ({_motivo_corto(m)}). Prueba otra vez en un momento."


# ------------------------------------------------------------------ 📝 guion de una idea (Flash-Lite)
_ESQ_GUION = None


def _esquema_guion():
    global _ESQ_GUION
    if _ESQ_GUION is None:
        try:
            from pydantic import BaseModel

            class Parte(BaseModel):
                parte: str
                que_decir: str
                en_pantalla: str

            class Guion(BaseModel):
                titulos: list[str]
                partes: list[Parte]
                llamada_a_la_accion: str
                duracion_min: float

            _ESQ_GUION = Guion
        except Exception:  # noqa: BLE001
            _ESQ_GUION = False
    return _ESQ_GUION or None


def parsea_guion(texto: str) -> dict:
    d = _json_de(texto)
    partes = [{"parte": _txt(x.get("parte"), 80), "que_decir": _txt(x.get("que_decir"), 900),
               "en_pantalla": _txt(x.get("en_pantalla"), 300)}
              for x in (d.get("partes") or []) if isinstance(x, dict) and _txt(x.get("que_decir"), 900)]
    if len(partes) < 3:
        raise ValueError("el guion salió demasiado corto")
    titulos = [_txt(t, 90) for t in (d.get("titulos") or []) if _txt(t, 90)][:3]
    try:
        dur = round(float(d.get("duracion_min") or 0), 1)
    except (TypeError, ValueError):
        dur = 0.0
    return {"titulos": titulos, "partes": partes[:12], "cta": _txt(d.get("llamada_a_la_accion"), 400), "duracion_min": dur}


def _prompt_guion(idea: dict, info: dict, canal_id: str, objetivo: str) -> str:
    """El prompt del guion. Las ideas de la lista nueva son cortas (sin gancho ni puntos): solo entra lo que tengan."""
    L = [f"Canal: «{info.get('nombre') or canal_id}». {info.get('contexto') or ''}".strip(),
         f"Objetivo del canal: {objetivo}", "",
         "Desarrolla el GUION de este vídeo, que el autor graba hablando (no leído palabra por palabra): para cada parte, "
         "qué decir (en su voz, tuteando, frases cortas) y qué enseñar en pantalla. Empieza por el gancho de los primeros "
         "15 segundos y termina con la llamada a la acción. Además, 3 títulos para YouTube (máx. 70 caracteres).", ""]
    for etiqueta, valor in (("Título provisional", idea.get("titulo")), ("Gancho", idea.get("gancho")),
                            ("De qué va", idea.get("de_que_va")), ("Por qué puede funcionar", idea.get("por_que")),
                            ("Su aporte", idea.get("tu_aporte")), ("Para quién", idea.get("para_quien"))):
        if valor:
            L.append(f"{etiqueta}: {valor}")
    if idea.get("puntos"):
        L.append("Puntos: " + " | ".join(str(x) for x in idea["puntos"]))
    datos = [str(e.get("texto") or "") for e in (idea.get("evidencia") or []) if e.get("texto")][:3]
    if datos:
        L.append("Datos en los que se apoya: " + " | ".join(datos))
    if idea.get("idioma") in ("en", "es+en"):
        L.append("Ojo: la idea sale de lo que funciona en inglés: adáptala al público hispano, en español.")
    L += [f"Llamada a la acción: {(idea.get('cta') or {}).get('texto', 'su web')}", "",
          "Responde SOLO con JSON: {\"titulos\": [...], \"partes\": [{\"parte\", \"que_decir\", \"en_pantalla\"}], "
          "\"llamada_a_la_accion\": \"...\", \"duracion_min\": número}"]
    return "\n".join(L)


def guion(idea_id: str, cfg, dir_work=None, _llama=None) -> dict:
    """«📝 Guion» de una idea: estructura de lo que decir parte por parte, qué enseñar en pantalla y 3 títulos. UNA
    petición a Flash-Lite (no toca las 20 de Flash). Se guarda con la idea. -> {"ok", "guion"} o {"ok": False, "error"}"""
    idea = idea_por_id(idea_id, dir_work)
    if idea is None:
        return {"ok": False, "error": "No encuentro esa idea (recarga la página)."}
    t = next((x for x in tandas(dir_work) if any(i.get("id") == idea_id for i in x.get("ideas") or [])), None)
    canal_id = _canal_de(t) if t else _canal_por_defecto(cfg)
    info = (cfg.get_path(f"videos.canales.{canal_id}") or {}) if cfg is not None and canal_id else {}
    prompt = _prompt_guion(idea, info, canal_id, parametros(cfg, canal_id, dir_work)["objetivo"])
    try:
        from . import uso as _uso
        _uso.contexto(job="ideas")
        try:
            llama = _llama or (lambda pr, sy, va: _llama_gemini(cfg, pr, sy, va, True, "ideas_guion"))
            g, _modelo = llama(prompt, "Eres guionista de YouTube. Respondes SOLO con JSON válido.", parsea_guion)
        finally:
            _uso.contexto(job=None)
    except Exception as exc:                                   # noqa: BLE001
        log(f"Guion de la idea {idea_id}: {exc}")
        return {"ok": False, "error": _error_claro(exc)}
    with _ESCRIBE:
        est = estado(dir_work)
        e = dict(est.get(idea_id) or {})
        e["guion"] = g
        est[idea_id] = e
        _guarda(_dir(dir_work) / "estado.json", est)
    return {"ok": True, "guion": g}


# ------------------------------------------------------------------ pruebas (sin red; corren en «Probar el sistema»)
def prueba() -> list[dict]:
    return _prueba_base() + _prueba_canales() + _prueba_semillas()


def _prueba_base() -> list[dict]:
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    class Cfg:
        def __init__(self, d):
            self.d = d

        def get_path(self, k, dflt=None):
            cur = self.d
            for parte in k.split("."):
                if not isinstance(cur, dict) or parte not in cur:
                    return dflt
                cur = cur[parte]
            return cur

    canales = {"ejemplo": {"nombre": "Mi canal", "contexto": "Canal de SEO.", "enlaces": {
        "cta_url": "https://ejemplo.com/consultor/", "web": "https://ejemplo.com/curso/"}}}
    cfg = Cfg({"ideas": {"canal": "ejemplo", "busquedas": ["SEO"], "busquedas_en": ["SEO 2026"], "palabras_tema": [r"\bseo\b", r"google", r"analytics", r"\bweb", r"redirec", r"search console", r"posicion", r"enlace", r"contenido", r"indexa", r"canonical", r"cliente", r"negocio", r"marketing", r"keyword", r"trafico", r"auditoria"]}, "videos": {"canales": canales}})
    resumen = lambda n, lite=500: (lambda _c: {"pools": {"mejores": {"restantes": n}, "respaldo": {"restantes": lite}}})  # noqa: E731
    t0 = 1_800_000_000.0

    # ---- parámetros e idioma
    p0 = parametros(cfg)
    caso("la mezcla de ahora: 25 ideas = 5 negocio + 5 aprender + 15 generales de SEO (sin la casilla de marketing digital)",
         p0["mezcla"] == {"negocio": 5, "aprender": 5, "general": 15} and sum(p0["mezcla"].values()) == 25, p0["mezcla"])
    vieja_cfg = Cfg({"ideas": {"canal": "ejemplo", "mezcla": {"negocio": 4, "crecimiento": 4, "autoridad": 2}}, "videos": {"canales": canales}})
    caso("una mezcla de antes (negocio/crecimiento/autoridad) en config.yaml no se mezcla con la nueva: manda la de ahora",
         parametros(vieja_cfg)["mezcla"] == {"negocio": 5, "aprender": 5, "general": 15}, parametros(vieja_cfg)["mezcla"])
    caso("el idioma de un vídeo: el que dice YouTube; si no, el del título (palabras y tildes); otro idioma = «otro»",
         idioma_de("x", "es-419") == "es" and idioma_de("x", "en-US") == "en" and idioma_de("x", "hi") == "otro"
         and idioma_de("YouTube's New SEO Update Changes EVERYTHING") == "en"
         and idioma_de("Your Website Won't Matter in 2 Years (Here's What to Build Instead)") == "en"
         and idioma_de("Adiós WordPress: así hacemos webs con IA en 2026") == "es"
         and idioma_de("Como posicionar tu web en Google") == "es" and idioma_de("SEO 2026") == ""
         and idioma_de("x", "und") == "", [idioma_de("SEO 2026"), idioma_de("x", "und")])

    with tempfile.TemporaryDirectory() as tmp:
        caso("con menos de 8 peticiones de los mejores modelos hoy, NO se piden ideas (lo primero es el vídeo)",
             not puede_generar(cfg, tmp, t0, resumen(7))["ok"] and "8" in puede_generar(cfg, tmp, t0, resumen(7))["motivo"])
        caso("con 8 o más, sí", puede_generar(cfg, tmp, t0, resumen(8))["ok"])

        llamadas: list = []

        def ahora_iso(dias):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(t0 - dias * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def populares(q, dias, n, idioma="es"):
            llamadas.append(("pop", q, idioma))
            if idioma == "es":
                return [{"video_id": "t1", "titulo": "Google cambia el SEO", "canal": "X", "canal_id": "UCx", "vistas": 50000,
                         "publicado": ahora_iso(3), "idioma": "es"},
                        {"video_id": "m1", "titulo": "(mío)", "canal_id": "UCyo", "vistas": 1}]
            return [{"video_id": "t2", "titulo": "How AI search is changing SEO", "canal": "Y", "canal_id": "UCy", "vistas": 188000,
                     "publicado": ahora_iso(5)},
                    {"video_id": "t3", "titulo": "क्या है एसईओ", "canal": "Z", "canal_id": "UCz", "vistas": 99000,
                     "publicado": ahora_iso(6), "idioma": "hi"}]

        fuentes = {
            "mis_videos": lambda: llamadas.append("mis") or [
                {"video_id": "m1", "titulo": "Cómo usar Search Console para SEO local", "publicado": ahora_iso(40), "vistas": 900},
                {"video_id": "m2", "titulo": "Qué es el SEO", "publicado": ahora_iso(400), "vistas": 5000}],
            "mi_canal": lambda: "UCyo",
            "trafico": lambda tipo, dias, n: ([{"detalle": "seo para restaurantes", "vistas": 320}] if tipo == "YT_SEARCH"
                                              else [{"detalle": "r1", "vistas": 50}, {"detalle": "r2", "vistas": 30},
                                                    {"detalle": "m1", "vistas": 99}]),
            "videos_con_canal": lambda ids: {"r1": {"canal_id": "UCotro"}, "r2": {"canal_id": "UCyo"}},
            "subidas": lambda c, n: {"canal": "Otro SEO", "videos": [
                {"video_id": f"o{k}", "titulo": f"Vídeo {k}", "vistas": 1000, "publicado": ahora_iso(10 + k), "duracion_s": 600}
                for k in range(8)] + [{"video_id": "ox", "titulo": "SEO con IA en 10 minutos", "vistas": 9000,
                                        "publicado": ahora_iso(5), "duracion_s": 700}]},
            "populares": populares,
            "comentarios": lambda n: [{"texto": "¿Podrías hacer un video sobre SEO para tiendas?", "likes": 3},
                                      {"texto": "Genial, gracias", "likes": 9}],
        }
        d = junta_datos(cfg, None, tmp, fuentes, ahora=t0)
        caso("datos: cada fuente con su código; el outlier (9× la mediana de su canal) sale, lo tuyo no cuenta como "
             "tendencia y de los comentarios solo las preguntas/peticiones",
             [it["codigo"] for it in d["grupos"]["O"]] == ["O1"] and "9.0×" in d["grupos"]["O"][0]["texto"]
             and len(d["grupos"]["C"]) == 1 and d["grupos"]["B"][0]["codigo"] == "B1"
             and len(d["grupos"]["S"]) == 2 and d["cuota"] >= 100, {k: len(v) for k, v in d["grupos"].items()})
        caso("lo más visto: se busca en español Y en inglés; cada vídeo lleva su idioma; el de otro idioma (hindi) y los tuyos "
             "no entran; el más visto primero",
             ("pop", "SEO", "es") in llamadas and ("pop", "SEO 2026", "en") in llamadas
             and [it["idioma"] for it in d["grupos"]["T"]] == ["en", "es"] and "en inglés" in d["grupos"]["T"][0]["texto"]
             and "en español" in d["grupos"]["T"][1]["texto"] and d["cuota"] >= 202, d["grupos"]["T"])
        caso("tu propio canal no se toma por «canal relacionado»",
             all("UCyo" not in str(v) for v in d["grupos"]["O"]))
        roto = dict(fuentes, comentarios=lambda n: (_ for _ in ()).throw(RuntimeError("sin permiso")))
        d2 = junta_datos(cfg, None, tmp, roto, ahora=t0)
        caso("si una fuente falla, se sigue con las demás y se apunta en las notas",
             d2["grupos"]["C"] == [] and any("comentarios" in n for n in d2["notas"]) and d2["grupos"]["B"], d2["notas"])
        caso("tus vídeos se guardan 12 h (no se gasta cuota en cada tanda)", llamadas.count("mis") == 1, llamadas)
        sin_en = dict(fuentes, populares=lambda q, dias, n, idioma="es": (_ for _ in ()).throw(RuntimeError("cuota")) if idioma == "en"
                      else populares(q, dias, n, idioma))
        d3 = junta_datos(cfg, None, tmp, sin_en, ahora=t0)
        caso("si falla la búsqueda en inglés, la de español sigue y se apunta en las notas",
             [it["idioma"] for it in d3["grupos"]["T"]] == ["es"] and any("SEO 2026" in n for n in d3["notas"]), d3["notas"])

        nombres = ("uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete "
                   "dieciocho diecinueve veinte").split()

        def respuesta(n_neg=5, n_apr=5, n_gen=15, extra=None, n_mkt=0):
            ideas, k_ = [], 0
            for tipo, n in (("negocio", n_neg), ("aprender", n_apr), ("marketing", n_mkt), ("general", n_gen)):
                for _ in range(n):
                    ideas.append({"tipo": tipo, "titulo": f"Idea {tipo} {nombres[k_ % len(nombres)]}", "de_que_va": "d de seo",
                                  "por_que": "p", "evidencia": ["B1", "T1", "Z9"], "para_quien": "q", "cta": "S1",
                                  "formato": "pantalla", "dificultad": "media"})
                    k_ += 1
            return json.dumps({"ideas": ideas + (extra or [])})

        ideas = parsea(respuesta(extra=[{"tipo": "raro", "titulo": "Cómo usar Search Console para SEO local paso a paso",
                                         "evidencia": [], "cta": "X1"}]), d, parametros(cfg))
        ult = ideas[-1]
        caso("parser: los códigos que no existen se tiran (Z9) y cada evidencia enseña el dato REAL con su idioma; la llamada "
             "a la acción solo si es un servicio/artículo de la lista; un tipo raro cae en «general»",
             [e["codigo"] for e in ideas[0]["evidencia"]] == ["B1", "T1"] and "restaurantes" in ideas[0]["evidencia"][0]["texto"]
             and ideas[0]["evidencia"][1]["idioma"] == "en" and ideas[0]["cta"]["url"] == "https://ejemplo.com/consultor/"
             and ult["cta"] is None and ult["tipo"] == "general", ideas[0])
        fuera = parsea(json.dumps({"ideas": [
            {"tipo": "general", "titulo": "El truco definitivo para usar ElevenLabs gratis", "de_que_va": "Voces de IA sin pagar.", "por_que": "Se ve mucho.", "evidencia": ["T1"]},
            {"tipo": "general", "titulo": "Qué es una redirección 302 y cuándo usarla", "de_que_va": "La diferencia con la 301, explicada simple.", "por_que": "Se busca mucho.", "evidencia": [],
             "puntos": ["Qué hace una 302", "301 vs 302", "Cuándo usar cada una", "Cómo comprobarlas"]},
            {"tipo": "general", "titulo": "Cómo medir las conversiones de tu web con Analytics", "de_que_va": "Un panel sencillo.", "por_que": "Siempre sirve.", "evidencia": []}]}),
            d, parametros(cfg), minimo=1)
        caso("parser: una idea de OTRO tema (el truco de ElevenLabs) se tira aunque la IA la devuelva; las de SEO se quedan, y los «puntos» de la idea se guardan",
             [i["titulo"] for i in fuera] == ["Qué es una redirección 302 y cuándo usarla", "Cómo medir las conversiones de tu web con Analytics"]
             and fuera[0]["puntos"] == ["Qué hace una 302", "301 vs 302", "Cuándo usar cada una", "Cómo comprobarlas"] and fuera[1]["tipo"] == "general" and fuera[1]["puntos"] == [], fuera)
        p_ref = arma_prompt_refresco(d, parametros(cfg), "Mi canal", "", {"titulo": "x", "tipo": "general"}, [], [])
        caso("el prompt (también el de «↻») dice que EL TEMA MANDA, que se ignoren los vídeos de otros temas de [T]/[O] y pide los «puntos» de cada idea",
             "EL TEMA MANDA" in p_ref and "IGNÓRALOS" in p_ref and "\"puntos\"" in p_ref and "informativas" in _recordatorio_general(10), p_ref[:200])
        caso("parser: la idea hereda el idioma de sus datos de YouTube (inglés / español / los dos / sin dato)",
             ideas[0]["idioma"] == "en" and ult["idioma"] == ""
             and parsea(json.dumps({"ideas": [{"tipo": "general", "titulo": "Otra distinta sobre enlaces rotos", "evidencia": ["T1", "T2"]}]}),
                        d, parametros(cfg), minimo=1)[0]["idioma"] == "es+en", [i["idioma"] for i in ideas[:2]])
        caso("parser: marca la idea que se parece mucho a un vídeo que ya tienes",
             ult["parecida_a"] == "Cómo usar Search Console para SEO local", ult.get("parecida_a"))
        dup = json.dumps({"ideas": [
            {"tipo": "negocio", "titulo": "Cómo medir el ROI real de tu SEO este año"},
            {"tipo": "negocio", "titulo": "Cómo medir el ROI real del SEO este año"},
            {"tipo": "negocio", "titulo": "Cómo medir el ROI real de tu SEO este año"},
            {"tipo": "negocio", "titulo": "Qué pedirle a una agencia de SEO antes de firmar"}]})
        caso("parser: una idea que repite o se parece mucho a otra de la misma respuesta se tira",
             [i["titulo"] for i in parsea(dup, d, parametros(cfg), minimo=1)] == [
                 "Cómo medir el ROI real de tu SEO este año", "Qué pedirle a una agencia de SEO antes de firmar"])
        try:
            parsea(json.dumps({"ideas": [{"titulo": "solo una"}]}), d, parametros(cfg))
            caso("con muy pocas ideas utilizables, falla (y se reintenta con otro modelo)", False)
        except ValueError:
            caso("con muy pocas ideas utilizables, falla (y se reintenta con otro modelo)", True)

        pedidos: list = []

        def llama(pr, sy, va):
            pedidos.append(pr)
            return va(respuesta()), "gemini-3.8-flash"

        r = genera(cfg, tmp, fuentes, llama, resumen(12), ahora=t0)
        tanda = (r.get("tanda") or {})
        caso("una tanda: 25 ideas 5/5/15 ordenadas por tipo, guardadas con su id, el modelo, lo que costó y la versión 2",
             r["ok"] and len(tanda["ideas"]) == 25 and tanda["cuenta"] == {"negocio": 5, "aprender": 5, "general": 15}
             and [i["tipo"] for i in tanda["ideas"]] == ["negocio"] * 5 + ["aprender"] * 5 + ["general"] * 15
             and tanda["ideas"][0]["id"].endswith("-01") and tanda["ideas"][24]["id"].endswith("-25") and tanda["version"] == 2
             and len(tandas(tmp)) == 1 and not tanda["notas"], r.get("error") or tanda.get("cuenta"))
        pr0 = pedidos[0]
        caso("el prompt lleva el negocio, el reparto 5/5/15 con lo que es cada tipo, las reglas del título, el aviso de no inventar "
             "noticias, el idioma de cada dato y los datos con código",
             "EXACTAMENTE 25 ideas" in pr0 and "5 de tipo \"negocio\"" in pr0 and "5 de tipo \"aprender\"" in pr0 and "15 de tipo \"general\"" in pr0
             and 'de tipo "marketing"' not in pr0 and "EL TEMA MANDA" in pr0 and "IGNÓRALOS" in pr0 and "INFORMATIVO" in pr0 and "\"puntos\"" in pr0
             and "POSIBLES CLIENTES" in pr0 and "APRENDER lo que tú sabes" in pr0 and "INGLÉS" in pr0
             and "al menos 5 inspiradas" in pr0 and "máximo 70 caracteres" in pr0 and "NUNCA inventes" in pr0
             and "noticias" in pr0 and "en inglés)" in pr0 and "B1: «seo para restaurantes»" in pr0 and "prioridad nº 1" in pr0
             and "SOLO esto" in pr0, pr0[:600])
        caso("con la tanda se guardan sus datos (con los códigos) para los «↻»; solo las últimas 5 tandas",
             (_lee_datos(tanda["id"], tmp) or {}).get("por_codigo", {}).get("T1", {}).get("idioma") == "en"
             and "B1" in (_lee_datos(tanda["id"], tmp) or {}).get("por_codigo", {}), None)
        for k in range(7):                                       # (ids de fecha: los de 2001 son «más viejos» que el de verdad)
            _guarda_datos(f"2001010{k}-000000", d, tmp)
        caso("…y las de más de 5 tandas atrás se borran (la tanda de ahora no)",
             len(list((Path(tmp) / "ideas").glob("datos_*.json"))) == 5
             and _lee_datos(f"20010106-000000", tmp) is not None and _lee_datos("20010101-000000", tmp) is None,
             sorted(p_.name for p_ in (Path(tmp) / "ideas").glob("datos_*.json")))
        _guarda_datos(tanda["id"], d, tmp)                       # (la prueba de arriba no debe romper la tanda de ahora)

        caso("con 7 peticiones restantes no se llega ni a leer los datos ni a llamar a Gemini",
             not genera(cfg, tmp, fuentes, llama, resumen(7), ahora=t0 + 60)["ok"] and len(pedidos) == 1)
        genera(cfg, tmp, fuentes, llama, resumen(12), ahora=t0 + 120)
        r3 = genera(cfg, tmp, fuentes, llama, resumen(12), ahora=t0 + 180)
        caso("como mucho 2 tandas al día", not r3["ok"] and "tope" in r3["error"] and len(pedidos) == 2, r3)

        def llama_mal(pr, sy, va):
            return va(respuesta(n_neg=8, n_apr=3, n_gen=15)), "gemini-3.8-flash"

        with tempfile.TemporaryDirectory() as tmp_m:
            rm = genera(cfg, tmp_m, fuentes, llama_mal, resumen(12), ahora=t0)
            caso("si la IA no clava el reparto: se recorta lo que sobra, se dice lo que falta y la lista sigue ordenada por tipo",
                 rm["ok"] and rm["tanda"]["cuenta"] == {"negocio": 5, "aprender": 3, "general": 15}
                 and any("3 de 5" in n and "aprender" in n for n in rm["tanda"]["notas"])
                 and [i["tipo"] for i in rm["tanda"]["ideas"]] == ["negocio"] * 5 + ["aprender"] * 3 + ["general"] * 15,
                 rm.get("error") or rm["tanda"]["notas"])
        with tempfile.TemporaryDirectory() as tmp_v:
            _guarda(Path(tmp_v) / "ideas" / "tanda_20261001-100000.json",
                    {"id": "20261001-100000", "ts": t0, "version": 1, "ideas": [{"id": "a", "titulo": "Vieja", "tipo": "crecimiento"}]})
            _guarda(Path(tmp_v) / "ideas" / "tanda_20261001-110000.json",
                    {"id": "20261001-110000", "ts": t0 + 5, "version": 1, "ideas": [{"id": "b", "titulo": "Vieja 2", "tipo": "autoridad"}]})
            caso("las tandas del formato anterior (10 ideas) no cuentan para el tope de tandas del día",
                 puede_generar(cfg, tmp_v, t0 + 60, resumen(12))["ok"] and puede_generar(cfg, tmp_v, t0 + 60, resumen(12))["hoy"] == 0)

        # ---- 🎬 La grabo y el director
        iid = tanda["ideas"][0]["id"]
        marca(iid, "grabar", tmp)
        caso("🎬 La grabo: la idea pasa a «Por grabar» y guarda su PROPIA copia (para sobrevivir a que se borre su tanda), sin el estado ni el guion",
             [i["id"] for i in por_grabar(tmp)] == [iid] and estado(tmp)[iid]["idea"]["titulo"] == "Idea negocio uno"
             and "estado" not in estado(tmp)[iid]["idea"] and "guion" not in estado(tmp)[iid]["idea"])
        marca(iid, "grabada", tmp, jid="2026-10-01-x--pro")
        caso("al subir su grabación sale de «Por grabar» y queda apuntado el vídeo",
             por_grabar(tmp) == [] and idea_por_id(iid, tmp)["jid"] == "2026-10-01-x--pro")
        caso("una marca desconocida o una idea que no existe no se guarda",
             not marca(iid, "cualquiera", tmp) and not marca("no-existe", "grabar", tmp))
        d4 = junta_datos(cfg, None, tmp, fuentes, ahora=t0 + 300)
        caso("las ideas que ya salieron vuelven como [P], con lo que le pareció (para no repetirlas)",
             any("Idea negocio uno" in it["texto"] and "✔" in it["texto"] for it in d4["grupos"]["P"]))

        def llama_guion(pr, sy, va):
            llamadas.append(("guion", pr))
            return va(json.dumps({"titulos": ["T1", "T2", "T3"], "partes": [
                {"parte": "Gancho", "que_decir": "x", "en_pantalla": "y"}, {"parte": "1", "que_decir": "x", "en_pantalla": ""},
                {"parte": "CTA", "que_decir": "x", "en_pantalla": ""}], "llamada_a_la_accion": "c", "duracion_min": 12})), "lite"

        iid_g = tanda["ideas"][14]["id"]                         # una general (viene de un dato en inglés)
        rg = guion(iid_g, cfg, tmp, llama_guion)
        pg = [x[1] for x in llamadas if isinstance(x, tuple) and x[0] == "guion"][-1]
        caso("📝 Guion: se guarda con la idea (partes, títulos y duración); con una idea corta, el prompt solo lleva lo que tiene "
             "(sin «Puntos»), con sus datos y el aviso de adaptar lo que viene del inglés",
             rg["ok"] and idea_por_id(iid_g, tmp)["guion"]["duracion_min"] == 12 and len(idea_por_id(iid_g, tmp)["guion"]["partes"]) == 3
             and "Puntos:" not in pg and "De qué va: d" in pg and "Datos en los que se apoya" in pg and "inglés" in pg, pg[:400])
        caso("el guion de una idea del formato anterior sigue llevando su gancho y sus puntos",
             "Gancho: g1" in _prompt_guion({"titulo": "T", "gancho": "g1", "puntos": ["a", "b"]}, {}, "", "obj")
             and "Puntos: a | b" in _prompt_guion({"titulo": "T", "gancho": "g1", "puntos": ["a", "b"]}, {}, "", "obj"))

    # ---- ↻ refrescar una idea (Flash-Lite, con tope)
    with tempfile.TemporaryDirectory() as tmp:
        caso("↻ el tope por día: 30 por defecto y nunca con menos de 150 peticiones de Flash-Lite hoy",
             puede_refrescar(cfg, tmp, t0, resumen(12, 500))["ok"] and puede_refrescar(cfg, tmp, t0, resumen(12, 500))["max"] == 30
             and not puede_refrescar(cfg, tmp, t0, resumen(12, 149))["ok"] and "150" in puede_refrescar(cfg, tmp, t0, resumen(12, 149))["motivo"]
             and puede_refrescar(cfg, tmp, t0, resumen(12, 150))["ok"] and not puede_refrescar(cfg, tmp, t0, lambda _c: {})["ok"])
        r0 = genera(cfg, tmp, fuentes, llama, resumen(12), ahora=t0)
        t_a = r0["tanda"]
        viejo = t_a["ideas"][7]                                  # una de «aprender»
        otra_aprender = {"tipo": "negocio", "titulo": "Tu primer informe de Search Console paso a paso", "de_que_va": "Del cero al informe.",
                         "por_que": "Lo piden quienes empiezan.", "evidencia": ["T2"], "para_quien": "emprendedores", "cta": "S2",
                         "formato": "pantalla", "dificultad": "baja"}
        vistos_ref: list = []

        def llama_ref(pr, sy, va):
            vistos_ref.append(pr)
            return va(json.dumps({"ideas": [otra_aprender]})), "gemini-3.5-flash-lite"

        rr = refresca(viejo["id"], cfg, tmp, llama_ref, resumen(12), ahora=t0 + 10)
        t_b = tandas(tmp)[0]
        caso("↻ cambia SOLO esa fila: misma posición, MISMO tipo (aunque la IA diga otro), id nuevo, y la vieja queda en el historial",
             rr["ok"] and len(t_b["ideas"]) == 25 and t_b["ideas"][7]["titulo"] == otra_aprender["titulo"]
             and t_b["ideas"][7]["tipo"] == "aprender" and t_b["ideas"][7]["id"].endswith("-r01")
             and t_b["ideas"][7]["refrescada_de"] == viejo["id"] and [i["titulo"] for i in t_b["ideas_anteriores"]] == [viejo["titulo"]]
             and all(t_b["ideas"][k]["id"] == t_a["ideas"][k]["id"] for k in range(25) if k != 7) and rr["hoy"] == 1
             and refrescos_hoy(tmp, t0 + 20) == 1 and refrescos_hoy(tmp, t0 + 3 * 86400) == 0, rr)
        caso("↻ el prompt lleva la casilla, la idea que se cambia, las otras 24 para no repetirlas y los datos de la tanda "
             "(sin volver a YouTube)",
             f"«{viejo['titulo']}»" in vistos_ref[0] and "tipo \"aprender\"" in vistos_ref[0] and "CAMBIAR UNA" in vistos_ref[0]
             and t_a["ideas"][0]["titulo"] in vistos_ref[0] and t_a["ideas"][24]["titulo"] in vistos_ref[0]
             and vistos_ref[0].count("- Idea ") == 24 and "B1: «seo para restaurantes»" in vistos_ref[0]
             and "UNA sola idea" in vistos_ref[0], vistos_ref[0][:300])
        d5 = junta_datos(cfg, None, tmp, fuentes, ahora=t0 + 400)
        caso("↻ la idea cambiada vuelve como [P] (las siguientes tandas no la repiten)",
             any(viejo["titulo"] in it["texto"] for it in d5["grupos"]["P"]) and any(otra_aprender["titulo"] in it["texto"] for it in d5["grupos"]["P"]))
        # una general: pide la que equilibre el idioma
        viejo_g = t_b["ideas"][12]
        vg: list = []

        def llama_g(pr, sy, va):
            vg.append(pr)
            return va(json.dumps({"ideas": [dict(otra_aprender, titulo="Por qué Google no indexa tu página: 8 causas reales", tipo="general")]})), "lite"

        rg2 = refresca(viejo_g["id"], cfg, tmp, llama_g, resumen(12), ahora=t0 + 20)
        caso("↻ una idea general: el prompt dice cómo va el reparto español/inglés de las generales",
             rg2["ok"] and "Entre las generales de la lista ahora hay" in vg[0] and "inspiradas en lo que funciona en español" in vg[0],
             vg[0][vg[0].find("Entre las"):][:200])
        # los rechazos
        def llama_repite(pr, sy, va):
            return va(json.dumps({"ideas": [{"tipo": "negocio", "titulo": otra_aprender["titulo"], "evidencia": []}]})), "lite"

        n_antes = refrescos_hoy(tmp, t0 + 30)
        rep = refresca(tandas(tmp)[0]["ideas"][3]["id"], cfg, tmp, llama_repite, resumen(12), ahora=t0 + 30)
        caso("↻ si la IA repite una idea que ya está en la lista, no se cambia nada y no cuenta como refresco",
             not rep["ok"] and rep["codigo"] == "ia" and refrescos_hoy(tmp, t0 + 30) == n_antes
             and [i["id"] for i in tandas(tmp)[0]["ideas"]][3] == t_a["ideas"][3]["id"], rep)
        marca(t_a["ideas"][4]["id"], "grabar", tmp)
        caso("↻ una idea en «Por grabar» no se refresca (no desaparece)",
             refresca(t_a["ideas"][4]["id"], cfg, tmp, llama_ref, resumen(12), ahora=t0 + 40)["codigo"] == "grabar")
        caso("↻ una idea que ya no está (o ya se refrescó) no se refresca",
             refresca(viejo["id"], cfg, tmp, llama_ref, resumen(12), ahora=t0 + 40)["codigo"] == "no_esta")
        caso("↻ con menos de 150 de Flash-Lite hoy no se pide nada",
             refresca(t_a["ideas"][5]["id"], cfg, tmp, llama_ref, resumen(12, 100), ahora=t0 + 40)["codigo"] == "tope" and len(vistos_ref) == 1)
        cfg2 = Cfg({"ideas": {"canal": "ejemplo", "busquedas": ["SEO"], "refrescos_max_dia": 2}, "videos": {"canales": canales}})
        caso("↻ el tope por día se respeta (con 2: el 3.º no)",
             refrescos_hoy(tmp, t0 + 50) == 2 and refresca(t_a["ideas"][5]["id"], cfg2, tmp, llama_ref, resumen(12), ahora=t0 + 50)["codigo"] == "tope")
        with tempfile.TemporaryDirectory() as tmp_v:
            _guarda(Path(tmp_v) / "ideas" / "tanda_20261001-100000.json",
                    {"id": "20261001-100000", "ts": t0, "version": 1, "ideas": [{"id": "a", "titulo": "Vieja", "tipo": "crecimiento"}]})
            ra = refresca("a", cfg, tmp_v, llama_ref, resumen(12), ahora=t0 + 40)
            caso("↻ las ideas del formato anterior (10 en tarjetas) no se refrescan: se pide una tanda nueva",
                 ra["codigo"] == "antigua" and "25" in ra["error"], ra)
        with tempfile.TemporaryDirectory() as tmp_s:
            rs = genera(cfg, tmp_s, fuentes, llama, resumen(12), ahora=t0)["tanda"]
            (Path(tmp_s) / "ideas" / f"datos_{rs['id']}.json").unlink()
            caso("↻ sin los datos guardados de la tanda lo dice (y no gasta nada)",
                 refresca(rs["ideas"][0]["id"], cfg, tmp_s, llama_ref, resumen(12), ahora=t0 + 40)["codigo"] == "sin_datos")
    return filas


def _prueba_canales() -> list[dict]:
    """Ideas por canal (4-oct-2026): un canal sin perfil, un canal de otro tema (gaming), sus datos propios, sus tandas aparte."""
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    class Cfg:
        def __init__(self, d):
            self.d = d

        def get_path(self, k, dflt=None):
            cur = self.d
            for parte in k.split("."):
                if not isinstance(cur, dict) or parte not in cur:
                    return dflt
                cur = cur[parte]
            return cur

    canales = {"ejemplo": {"nombre": "Mi canal", "contexto": "Canal de SEO.", "enlaces": {
        "cta_url": "https://ejemplo.com/consultor/", "web": "https://ejemplo.com/curso/"}},
               "gamer": {"nombre": "Canal Gamer", "contexto": "Canal gaming de Mundo Shooter.",
                          "perfil": {"tema": "Canal Gaming de Mundo Shooter"},
                          "enlaces": {"cta_url": "https://tienda.ejemplo.com", "cta_texto": "Mi tienda"}}}
    cfg = Cfg({"ideas": {"canal": "ejemplo", "busquedas": ["SEO"], "busquedas_en": ["SEO 2026"]}, "videos": {"canales": canales}})
    resumen = lambda n, lite=500: (lambda _c: {"pools": {"mejores": {"restantes": n}, "respaldo": {"restantes": lite}}})  # noqa: E731
    t0 = 1_800_000_000.0
    gaming = {"tema": "Canal Gaming de Mundo Shooter", "objetivo": "Hacer crecer el canal y conseguir patrocinios.",
              "vertientes": [{"nombre": "Principiantes", "descripcion": "Guías y consejos para empezar bien en MS.", "n": 5},
                             {"nombre": "Competitivos", "descripcion": "Tácticas avanzadas para subir de rango.", "n": 5}],
              "general": {"descripcion": "Ideas abiertas con base en lo más visto de MS.", "n": 10},
              "busquedas": ["mundo shooter", "ms guía"], "idiomas": "es",
              "destinos": [{"nombre": "Mi tienda", "url": "https://tienda.ejemplo.com"}], "datos_propios": False}

    with tempfile.TemporaryDirectory() as tmp:
        pj = parametros(cfg, "ejemplo", tmp)
        caso("el autor: su perfil sale de config.yaml (5 negocio + 5 aprender + 15 generales, español e inglés, datos de su canal) y no pide asistente",
             not pj["necesita_perfil"] and [t["id"] for t in pj["tipos"]] == ["negocio", "aprender", "general"] and pj["total"] == 25
             and pj["idiomas"] == "es+en" and pj["datos_propios"] and pj["busquedas_en"] == ["SEO 2026"]
             and pj["etiqueta_objetivo"].startswith("EL NEGOCIO") and "prioridad nº 1" in pj["objetivo"], pj["mezcla"])
        pg0 = parametros(cfg, "gamer", tmp)
        fr0 = puede_generar(cfg, tmp, t0, resumen(12), canal="gamer")
        caso("un canal sin perfil tiene que configurarlo la primera vez: no se le piden ideas (ni se gasta nada) hasta entonces",
             pg0["necesita_perfil"] and not fr0["ok"] and fr0["necesita_perfil"] and "cuéntame de tu canal" in fr0["motivo"]
             and not genera(cfg, tmp, {}, lambda *a: 1 / 0, resumen(12), ahora=t0, canal="gamer")["ok"], fr0["motivo"])
        perfiles.guarda("gamer", gaming, tmp)
        pg = parametros(cfg, "gamer", tmp)
        caso("con su perfil: sus públicos son sus tipos, suman 20, solo español y sin los datos del canal conectado",
             not pg["necesita_perfil"] and [t["id"] for t in pg["tipos"]] == ["principiantes", "competitivos", "general"] and pg["total"] == 20
             and pg["busquedas_en"] == [] and pg["idiomas"] == "es" and pg["datos_propios"] is False
             and pg["etiqueta_objetivo"].startswith("EL OBJETIVO DEL CANAL"), pg["mezcla"])
        caso("el tipo que dice la IA se lleva a los del perfil (por id, nombre o principio); lo que no cuadra es «general»",
             _tipo_de("principiantes", pg["tipos"]) == "principiantes" and _tipo_de("Competitivos", pg["tipos"]) == "competitivos"
             and _tipo_de("competitivo", pg["tipos"]) == "competitivos" and _tipo_de("raro", pg["tipos"]) == "general"
             and _tipo_de("negocio", pg["tipos"]) == "general")

        # ---- los datos de un canal que no es el conectado a YouTube
        llamadas: list = []

        def ahora_iso(dias):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(t0 - dias * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def pop_g(q, dias, n, idioma="es"):
            llamadas.append(("pop", q, idioma))
            return [{"video_id": "g1", "titulo": "Las mejores armas de MS para ganar", "canal": "Z", "canal_id": "UCz", "vistas": 80000,
                     "publicado": ahora_iso(3), "idioma": "es"}]

        fuentes_g = {"mis_videos": lambda: llamadas.append("mis") or [], "mi_canal": lambda: llamadas.append("mi_canal") or "UCx",
                     "trafico": lambda *a: llamadas.append("trafico") or [], "videos_con_canal": lambda ids: llamadas.append("vcc") or {},
                     "subidas": lambda c, n: {"canal": "", "videos": []}, "populares": pop_g,
                     "comentarios": lambda n: llamadas.append("coms") or []}
        dg = junta_datos(cfg, pg, tmp, fuentes_g, ahora=t0)
        caso("un canal que NO es el conectado a YouTube no lee ni un dato del canal conectado (vídeos, búsquedas, comentarios): solo lo más "
             "visto sobre SU tema, en español (sin búsquedas en inglés), y lo dice en las notas",
             not any(x in llamadas for x in ("mis", "mi_canal", "trafico", "coms")) and ("pop", "mundo shooter", "es") in llamadas
             and not any(isinstance(x, tuple) and x[2] == "en" for x in llamadas) and dg["grupos"]["V"] == [] and dg["grupos"]["B"] == []
             and len(dg["grupos"]["T"]) == 1 and any("no es el que tienes conectado" in n for n in dg["notas"]), llamadas)
        caso("sus destinos son los de su perfil y los enlaces de su canal, sin repetir (la IA nunca escribe una URL)",
             [(s["texto"], s["url"]) for s in dg["grupos"]["S"]] == [("Mi tienda", "https://tienda.ejemplo.com")], dg["grupos"]["S"])
        pr = arma_prompt(dg, pg, "Canal Gamer", "Canal gaming de Mundo Shooter.")
        caso("el prompt de un canal de gaming habla de SU objetivo y SUS públicos: sin SEO, sin dueños de negocio, sin inglés y con sus tipos",
             "EL OBJETIVO DEL CANAL" in pr and "patrocinios" in pr and "5 de tipo \"principiantes\": Guías y consejos" in pr
             and "10 de tipo \"general\": Ideas abiertas" in pr and "EXACTAMENTE 20 ideas" in pr and "SEO" not in pr
             and "DUEÑOS DE NEGOCIO" not in pr and "INGLÉS" not in pr and "\"tipo\": \"principiantes\" | \"competitivos\" | \"general\"" in pr
             and "SUS DESTINOS" in pr and "primero las de principiantes, luego las de competitivos y al final las generales" in pr
             and "en ESPAÑOL:" in pr, pr[:500])

        # ---- una tanda de otro canal y las tandas de cada canal por separado
        def respuesta_g():
            ideas = []
            for tipo, n in (("principiantes", 5), ("competitivos", 5), ("general", 10)):
                for k in range(n):
                    ideas.append({"tipo": tipo, "titulo": f"Idea {tipo} {('uno dos tres cuatro cinco seis siete ocho nueve diez').split()[k]}", "de_que_va": "d de seo", "por_que": "p",
                                  "evidencia": ["T1"], "para_quien": "q", "cta": "S1", "formato": "pantalla", "dificultad": "media"})
            return json.dumps({"ideas": ideas})

        pedidos: list = []

        def llama_g(pr_, sy, va):
            pedidos.append(pr_)
            return va(respuesta_g()), "gemini-3.8-flash"

        rg = genera(cfg, tmp, fuentes_g, llama_g, resumen(12), ahora=t0, canal="gamer")
        tg = rg.get("tanda") or {}
        caso("una tanda de otro canal: guarda su canal y sus tipos, cuenta por SUS tipos y las ordena como su perfil",
             rg["ok"] and tg["canal"] == "gamer" and tg["cuenta"] == {"principiantes": 5, "competitivos": 5, "general": 10}
             and [t["id"] for t in tg["tipos"]] == ["principiantes", "competitivos", "general"]
             and [i["tipo"] for i in tg["ideas"]] == ["principiantes"] * 5 + ["competitivos"] * 5 + ["general"] * 10 and not tg["notas"][1:],
             rg.get("error") or tg.get("cuenta"))

        def llama_j(pr_, sy, va):
            ideas = [{"tipo": t, "titulo": f"Idea {t} {k}", "de_que_va": "d de seo", "evidencia": ["B1"], "cta": "S1"}
                     for t, n in (("negocio", 5), ("aprender", 5), ("general", 15)) for k in range(n)]
            return va(json.dumps({"ideas": ideas})), "gemini-3.8-flash"

        fuentes_j = {"mis_videos": lambda: [{"video_id": "m1", "titulo": "Qué es el SEO", "publicado": ahora_iso(400), "vistas": 5000}],
                     "mi_canal": lambda: "UCyo", "trafico": lambda tipo, dias, n: ([{"detalle": "seo local", "vistas": 20}] if tipo == "YT_SEARCH" else []),
                     "videos_con_canal": lambda ids: {}, "subidas": lambda c, n: {"canal": "", "videos": []},
                     "populares": lambda q, dias, n, idioma="es": [], "comentarios": lambda n: []}
        rj = genera(cfg, tmp, fuentes_j, llama_j, resumen(12), ahora=t0 + 600)
        _guarda(Path(tmp) / "ideas" / "tanda_20260101-000000.json",
                {"id": "20260101-000000", "ts": t0 - 9e6, "version": 1, "ideas": [{"id": "viejo-1", "titulo": "Vieja del autor", "tipo": "crecimiento"}]})
        caso("cada canal tiene sus tandas aparte; una de antes de los perfiles (sin canal) es del autor; el tope de tandas del día es de todos",
             rj["ok"] and rj["tanda"]["canal"] == "ejemplo" and [t["id"] for t in tandas(tmp, canal="gamer")] == [tg["id"]]
             and {t["id"] for t in tandas(tmp, canal="ejemplo")} == {rj["tanda"]["id"], "20260101-000000"} and len(tandas(tmp)) == 3
             and not puede_generar(cfg, tmp, t0 + 700, resumen(12), canal="gamer")["ok"]
             and "tope" in puede_generar(cfg, tmp, t0 + 700, resumen(12), canal="gamer")["motivo"])
        d_j = junta_datos(cfg, parametros(cfg, "ejemplo", tmp), tmp, fuentes_j, ahora=t0 + 800)
        d_g = junta_datos(cfg, parametros(cfg, "gamer", tmp), tmp, fuentes_g, ahora=t0 + 800)
        caso("las ideas ya propuestas ([P]) son las de SU canal: las de gaming no salen en las del autor ni al revés",
             any("Idea negocio 0" in it["texto"] for it in d_j["grupos"]["P"]) and not any("Idea principiantes" in it["texto"] for it in d_j["grupos"]["P"])
             and any("Idea principiantes" in it["texto"] for it in d_g["grupos"]["P"]) and not any("Idea negocio" in it["texto"] for it in d_g["grupos"]["P"]))

        # ---- «↻» y guion en otro canal
        visto: list = []

        def llama_ref(pr_, sy, va):
            visto.append(pr_)
            return va(json.dumps({"ideas": [{"tipo": "xx", "titulo": "Cómo ganar siempre con el rifle de asalto", "evidencia": ["T1"], "cta": "S1"}]})), "lite"

        viejo = tg["ideas"][7]                                   # una de «competitivos»
        rr = refresca(viejo["id"], cfg, tmp, llama_ref, resumen(12), ahora=t0 + 900)
        caso("↻ en otro canal: aunque la tanda del autor sea más nueva, la idea de gaming se refresca (la última DE SU CANAL), con SU casilla "
             "y SU objetivo (sin SEO), y conserva su tipo",
             rr["ok"] and rr["idea"]["tipo"] == "competitivos" and "tipo \"competitivos\": Tácticas avanzadas" in visto[0]
             and "EL OBJETIVO DEL CANAL" in visto[0] and "SEO" not in visto[0] and "inspiradas en lo que funciona" not in visto[0]
             and tandas(tmp, canal="gamer")[0]["ideas"][7]["titulo"] == "Cómo ganar siempre con el rifle de asalto", rr)
        caso("↻ una idea de una tanda que ya no es la última de su canal no se refresca",
             refresca(tandas(tmp, canal="ejemplo")[-1]["ideas"][0]["id"], cfg, tmp, llama_ref, resumen(12), ahora=t0 + 950)["codigo"] in ("no_esta", "antigua"))
        pgu: list = []

        def llama_guion(pr_, sy, va):
            pgu.append(pr_)
            return va(json.dumps({"titulos": ["T1", "T2", "T3"], "partes": [{"parte": "Gancho", "que_decir": "x", "en_pantalla": "y"},
                                                                          {"parte": "1", "que_decir": "x", "en_pantalla": ""},
                                                                          {"parte": "Cierre", "que_decir": "x", "en_pantalla": ""}],
                                 "llamada_a_la_accion": "c", "duracion_min": 8})), "lite"

        guion(tg["ideas"][0]["id"], cfg, tmp, llama_guion)
        caso("📝 Guion de una idea de otro canal: lleva el objetivo de ESE canal (no el del autor)",
             "Objetivo del canal: Hacer crecer el canal y conseguir patrocinios." in pgu[0] and "SEO" not in pgu[0], pgu[0][:200])
        # ---- los canales de referencia de config.yaml son del autor: otro canal no los vigila
        cfg_ref = Cfg({"ideas": {"canal": "ejemplo", "canales_referencia": ["UCref"], "busquedas": ["SEO"], "busquedas_en": ["SEO 2026"]}, "videos": {"canales": canales}})
        vigilados: list = []
        fuentes_ref = dict(fuentes_j, subidas=lambda c, n: vigilados.append(c) or {"canal": "", "videos": []})
        junta_datos(cfg_ref, parametros(cfg_ref, "ejemplo", tmp), tmp, fuentes_ref, ahora=t0 + 1000)
        n_jose = list(vigilados)
        vigilados.clear()
        perfiles.guarda("gamer", {**gaming, "datos_propios": True}, tmp)
        junta_datos(cfg_ref, parametros(cfg_ref, "gamer", tmp), tmp, dict(fuentes_ref, trafico=lambda tipo, dias, n: [{"detalle": "x", "vistas": 1}]),
                    ahora=t0 + 1000)
        caso("los canales de referencia de config.yaml (`ideas.canales_referencia`) son los del autor: su canal los vigila y otro canal no",
             n_jose == ["UCref"] and vigilados == [], (n_jose, vigilados))
    return filas


def _prueba_semillas() -> list[dict]:
    """Tus ideas favoritas, el estilo de las generales (mercado hispano y de EE. UU.), la rotación de búsquedas y la ✖ de cada tanda (2026-10-05)."""
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    class Cfg:
        def __init__(self, d):
            self.d = d

        def get_path(self, k, dflt=None):
            cur = self.d
            for parte in k.split("."):
                if not isinstance(cur, dict) or parte not in cur:
                    return dflt
                cur = cur[parte]
            return cur

    favoritas = [{"titulo": "Cómo encontrar keywords GRATIS usando solo Google", "potencial": 3},
                 {"titulo": "10 cosas que reviso antes de hacer SEO a una web", "potencial": 2},
                 "Hice una auditoría SEO de mi propia web y encontré esto"]
    es = [f"búsqueda es {k}" for k in range(1, 13)]
    en = [f"search en {k}" for k in range(1, 13)]
    canales = {"ejemplo": {"nombre": "Mi canal", "contexto": "Canal de SEO.", "enlaces": {"cta_url": "https://ejemplo.com/consultor/"}}}
    herr = [{"nombre": "Medidor 5000", "que_hace": "analiza qué hacen las páginas que salen primero en Google para una keyword"}]
    cfg = Cfg({"ideas": {"canal": "ejemplo", "busquedas": es, "busquedas_en": en, "semillas": favoritas, "herramientas": herr}, "videos": {"canales": canales}})
    sin = Cfg({"ideas": {"canal": "ejemplo", "busquedas": ["SEO"], "busquedas_en": ["SEO 2026"]}, "videos": {"canales": canales}})
    t0 = 1_800_000_000.0
    ahora = t0 + 5 * 3600                  # (a media mañana del día de cuota: t0 cae justo en el cambio de día)
    resumen = lambda n: (lambda _c: {"pools": {"mejores": {"restantes": n}, "respaldo": {"restantes": 500}}})  # noqa: E731

    p = parametros(cfg)
    ids = [s["id"] for s in p["semillas"]]
    p_inv = parametros(Cfg({"ideas": {"canal": "ejemplo", "semillas": list(reversed(favoritas))}, "videos": {"canales": canales}}))
    raras = perfiles.semillas_de([{"titulo": "Una idea de prueba larga", "potencial": 9}, {"titulo": "Otra idea de prueba larga", "potencial": 0},
                                  "Una idea de prueba larga", "corta", 5, {"potencial": 3}])
    caso("tus ideas favoritas (config.yaml) llegan al perfil: id estable que sale del título (reordenar la lista no lo cambia), potencial de 1 a 3 (2 si no "
         "lo dices), sin repetidas ni vacías; un canal sin favoritas no tiene ninguna",
         [s["potencial"] for s in p["semillas"]] == [3, 2, 2] and len(set(ids)) == 3 and all(i.startswith("semilla-") for i in ids)
         and {s["titulo"]: s["id"] for s in p_inv["semillas"]} == {s["titulo"]: s["id"] for s in p["semillas"]}
         and [s["potencial"] for s in raras] == [3, 1] and parametros(sin)["semillas"] == [], (p["semillas"], raras))

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp) / "ideas"
        carpeta.mkdir()
        (carpeta / "cache_mis_videos.json").write_text(json.dumps({"ts": ahora, "videos": [
            {"video_id": "v1", "titulo": "Cómo encontrar keywords gratis usando Google", "publicado": "2026-09-01T00:00:00Z", "vistas": 10}]}),
            encoding="utf-8")
        se = semillas(cfg, None, tmp)
        caso("las favoritas salen como ideas «general» marcadas «semilla», con su potencial y sin gastar nada; una que se parece a un vídeo que ya subiste lo dice",
             len(se) == 3 and all(s["tipo"] == "general" and s["semilla"] and s["estado"] == "" for s in se)
             and se[0]["parecida_a"] and not se[1]["parecida_a"] and se[1]["potencial"] == 2, [(s["titulo"][:20], s["parecida_a"]) for s in se])
        caso("una favorita se marca («La grabo») como cualquier idea y aparece en «Por grabar»; una que no existe se rechaza; quitar la marca la saca",
             marca(ids[0], "grabar", tmp, cfg=cfg) and [i["id"] for i in por_grabar(tmp, cfg)] == [ids[0]]
             and idea_por_id(ids[0], tmp, cfg)["estado"] == "grabar" and not marca("semilla-00000000", "grabar", tmp, cfg=cfg)
             and marca(ids[0], "descartada", tmp, cfg=cfg) and por_grabar(tmp, cfg) == []
             and semillas(cfg, None, tmp)[0]["estado"] == "descartada" and marca(ids[0], "", tmp, cfg=cfg), por_grabar(tmp, cfg))

        with tempfile.TemporaryDirectory() as tmpx:
            sx = semillas(cfg, None, tmpx)
            marca(sx[1]["id"], "grabar", tmpx, cfg=cfg)
            ex = elimina_idea(sx[1]["id"], tmpx, cfg)
            caso("🗑 Eliminar una favorita de config.yaml: ya no sale ni en «Por grabar» (el archivo es tuyo y no se toca: solo se apunta que no salga)",
                 ex["ok"] and len(semillas(cfg, None, tmpx)) == 2 and sx[1]["id"] not in [x["id"] for x in semillas(cfg, None, tmpx)]
                 and por_grabar(tmpx, cfg) == [] and sx[1]["id"] not in estado(tmpx) and idea_por_id(sx[1]["id"], tmpx, cfg) is None, ex)

        # ---- los datos: [E] tus favoritas, [H] tus herramientas y la rotación de búsquedas
        llamadas: list = []
        fuentes = {"mis_videos": lambda: [], "mi_canal": lambda: "UCyo", "trafico": lambda tipo, dias, n: [],
                   "videos_con_canal": lambda ids_: {}, "subidas": lambda c, n: {"canal": "", "videos": []},
                   "populares": lambda q, dias, n, idioma="es": llamadas.append((q, idioma)) or [], "comentarios": lambda n: []}
        d = junta_datos(cfg, None, tmp, fuentes, ahora=ahora)
        caso("los datos llevan [E] tus favoritas (con su 🔥) y [H] tus herramientas, cada una con su código",
             [x["codigo"] for x in d["grupos"]["E"]] == ["E1", "E2", "E3"] and "🔥🔥🔥" in d["grupos"]["E"][0]["texto"]
             and "🔥🔥" in d["grupos"]["E"][1]["texto"] and d["grupos"]["H"][0]["codigo"] == "H1"
             and "Medidor 5000" in d["grupos"]["H"][0]["texto"], {k: len(v) for k, v in d["grupos"].items()})
        es_l = [q for q, i in llamadas if i == "es"]
        en_l = [q for q, i in llamadas if i == "en"]
        caso("lo más visto: cada tanda mira 4 búsquedas en español y 4 en inglés de un conjunto más grande, lo dice en las notas y no gasta más cuota de YouTube",
             len(es_l) == 4 and len(en_l) == 4 and es_l == es[:4] and en_l == en[:4] and d["cuota"] < 900
             and any("cada tanda mira otras" in n for n in d["notas"]), (es_l, d["cuota"], d["notas"]))
        v = [_rota(es, 4, k) for k in range(3)]
        caso("la rotación da la vuelta: en 3 tandas se ven las 12 búsquedas sin repetir ninguna y la 4.ª vuelve a empezar; con una lista corta se usan todas",
             sorted(sum(v, [])) == sorted(es) and _rota(es, 4, 3) == v[0] and _rota(["a", "b"], 4, 7) == ["a", "b"] and _rota(es, 0, 1) == es, v)

        # ---- el prompt
        p = parametros(cfg, None, tmp)
        prompt = arma_prompt(d, p, "Mi canal", "Canal de SEO.")
        d_sin = junta_datos(sin, None, tmp, fuentes, ahora=ahora)
        prompt_sin = arma_prompt(d_sin, parametros(sin, None, tmp), "Mi canal", "Canal de SEO.")
        caso("el prompt lleva tus favoritas de modelo de estilo, tus herramientas, que las generales NO salen de tus artículos y que miren el mercado hispano y "
             "el de EE. UU.; un canal sin favoritas ve el prompt de siempre",
             all((f["titulo"] if isinstance(f, dict) else f) in prompt for f in favoritas) and "[E] SUS IDEAS FAVORITAS" in prompt
             and "[H] HERRAMIENTAS" in prompt and "NO tienen que salir de sus artículos" in prompt and "mercado de habla hispana y en el de Estados Unidos" in prompt
             and "MAYÚSCULAS" in prompt and "Creé una herramienta" in prompt and "[E] SUS IDEAS FAVORITAS" not in prompt_sin
             and "[H] HERRAMIENTAS" not in prompt_sin and "SOBRE LAS IDEAS «general»" not in prompt_sin, prompt[:200])
        refresco = arma_prompt_refresco(d, p, "Mi canal", "", {"tipo": "general", "titulo": "Una vieja"}, [], [])
        refresco_neg = arma_prompt_refresco(d, p, "Mi canal", "", {"tipo": "negocio", "titulo": "Una vieja"}, [], [])
        caso("el «↻» de una idea general lleva las mismas reglas de estilo; el de las demás no",
             "SOBRE LAS IDEAS «general»" in refresco and "SOBRE LAS IDEAS «general»" not in refresco_neg)
        datos_p = {"por_codigo": {"H1": {"texto": "Medidor 5000 — x", "url": ""}, "T1": {"texto": "t", "url": "", "idioma": "en"}}, "mis_titulos": []}
        txt = json.dumps({"ideas": [
            {"tipo": "general", "titulo": "Cómo encontrar keywords GRATIS con solo Google", "evidencia": ["T1"], "cta": ""},
            {"tipo": "general", "titulo": "Creé un auditor SEO que revisa tu web en 1 minuto", "evidencia": ["H1", "T1"], "cta": ""}]})
        ok_p = parsea(txt, datos_p, p, minimo=1, ya=[s["titulo"] for s in p["semillas"]])
        caso("una idea casi idéntica a una de tus favoritas se descarta (ya la tienes); las demás se quedan y una «Creé una herramienta…» puede citar su [H]",
             [i["titulo"] for i in ok_p] == ["Creé un auditor SEO que revisa tu web en 1 minuto"] and ok_p[0]["evidencia"][0]["codigo"] == "H1"
             and ok_p[0]["cta"] is None, ok_p)
        txt2 = json.dumps({"ideas": [
            {"tipo": "general", "titulo": "Creé una herramienta que mide tu velocidad en Google", "evidencia": ["T1"], "cta": ""},
            {"tipo": "general", "titulo": "Creé un auditor de enlaces rotos con IA", "evidencia": [], "cta": ""},
            {"tipo": "general", "titulo": "Hice un experimento con ChatGPT y el SEO local de una pizzería", "evidencia": ["T1"], "cta": ""},
            {"tipo": "general", "titulo": "Creé una herramienta para tu canonical GRATIS", "evidencia": ["H1"], "cta": ""}]})
        ok2 = parsea(txt2, datos_p, p, minimo=1)
        caso("«Creé una herramienta…» (o un auditor, un bot…) solo sale si cita una de TUS herramientas [H]: no se inventa lo que tienes; un experimento sin herramienta, sí",
             [i["titulo"] for i in ok2] == ["Hice un experimento con ChatGPT y el SEO local de una pizzería", "Creé una herramienta para tu canonical GRATIS"], [i["titulo"] for i in ok2])

        # ---- la ✖ de cada tanda
        def tanda(tid, ts, titulos, anteriores=()):
            ideas = [{"id": f"{tid}-{k:02d}", "tipo": "general", "titulo": t, "de_que_va": "d de seo", "evidencia": []} for k, t in enumerate(titulos, 1)]
            _guarda(carpeta / f"tanda_{tid}.json", {"id": tid, "ts": ts, "version": 2, "canal": "ejemplo", "ideas": ideas,
                                                    "ideas_anteriores": [{"id": f"{tid}-r{k:02d}", "titulo": t} for k, t in enumerate(anteriores, 1)]})
            _guarda(carpeta / f"datos_{tid}.json", {"grupos": {}})
            return ideas
        a_id, b_id, c_id = "20270115-130000", "20270115-131000", "20270114-100000"
        tanda(a_id, ahora - 700, ["Idea de la A uno", "Idea de la A dos"], anteriores=["Idea cambiada con la flecha"])
        tanda(b_id, ahora - 100, ["Idea de la B uno", "Idea de la B dos"])
        tanda(c_id, ahora - 86400, ["Idea de la C uno"])
        marca(f"{b_id}-01", "descartada", tmp, cfg=cfg)
        caso("el tope de tandas al día cuenta las de hoy (2): con dos hechas hoy no se piden más",
             not puede_generar(cfg, tmp, ahora, resumen(20))["ok"] and puede_generar(cfg, tmp, ahora, resumen(20))["hoy"] == 2,
             puede_generar(cfg, tmp, ahora, resumen(20)))
        marca(f"{b_id}-02", "grabar", tmp, cfg=cfg)
        r_b = borra_tanda(b_id, tmp, ahora)
        caso("borrar una tanda con una idea en «Por grabar» YA NO se niega (el autor, 12-oct-2026): se borra todo MENOS esa idea, que sigue en el checklist con su copia, y de lo demás no queda marca",
             r_b == {"ok": True, "ideas": 1, "conservadas": 1} and not (carpeta / f"tanda_{b_id}.json").exists() and not (carpeta / f"datos_{b_id}.json").exists()
             and [i["id"] for i in por_grabar(tmp, cfg)] == [f"{b_id}-02"] and por_grabar(tmp, cfg)[0]["titulo"] == "Idea de la B dos"
             and f"{b_id}-01" not in estado(tmp) and idea_por_id(f"{b_id}-02", tmp, cfg)["titulo"] == "Idea de la B dos", r_b)
        caso("una idea «Por grabar» que ya no tiene tanda se puede quitar del checklist y NO deja ni la copia huérfana",
             marca(f"{b_id}-02", "", tmp, cfg=cfg) and por_grabar(tmp, cfg) == [] and f"{b_id}-02" not in estado(tmp) and idea_por_id(f"{b_id}-02", tmp, cfg) is None)
        r_a = borra_tanda(a_id, tmp, ahora)
        caso("borrar una tanda sin nada por grabar quita su archivo, sus datos y todas las marcas de sus ideas (también las que se cambiaron con ↻) y deja las otras tandas como estaban",
             r_a == {"ok": True, "ideas": 2, "conservadas": 0} and not (carpeta / f"tanda_{a_id}.json").exists() and not (carpeta / f"datos_{a_id}.json").exists()
             and (carpeta / f"tanda_{c_id}.json").is_file() and not any(k.startswith(a_id) for k in estado(tmp)), r_a)
        recuerdo = {b["id"]: b for b in borradas(tmp)}
        caso("de las tandas borradas solo queda la FECHA de cada una (sin sus ideas), para que borrar no sirva de truco contra el tope de tandas al día",
             set(recuerdo) == {a_id, b_id} and all(not b["ideas"] and b["ts"] for b in recuerdo.values()), recuerdo)
        elim_t = {e["titulo"] for e in eliminadas(tmp, "ejemplo")}
        caso("…y lo que se fue con ellas deja SOLO su título y su tipo en la lista de eliminadas (las que cambiaste con ↻ también; la que sigue en «Por grabar», no)",
             {"Idea de la A uno", "Idea de la A dos", "Idea cambiada con la flecha", "Idea de la B uno"} <= elim_t and set(next(iter(eliminadas(tmp))).keys()) == {"titulo", "tipo", "canal", "ts"},
             elim_t)
        caso("borrar tandas NO sirve para saltarse el tope de tandas al día (siguen contando las de hoy aunque ya no estén)",
             not puede_generar(cfg, tmp, ahora, resumen(20))["ok"] and puede_generar(cfg, tmp, ahora, resumen(20))["hoy"] == 2,
             puede_generar(cfg, tmp, ahora, resumen(20)))
        raros = [borra_tanda(x, tmp, ahora) for x in (a_id, "nada", "../tanda_x", "", None)]
        caso("borrar lo que ya no está, o un id raro, no rompe nada: «ya no está»",
             all((not r["ok"]) and r["codigo"] == "no_esta" for r in raros) and (carpeta / f"tanda_{c_id}.json").is_file(), raros)
        d_mem = junta_datos(cfg, None, tmp, fuentes, ahora=ahora)
        texto_p = "\n".join(x["texto"] for x in d_mem["grupos"]["P"])
        caso("lo eliminado se le cuenta a la IA como «la eliminó: no la repitas» (junto a lo que sigue en tus tandas)",
             "Idea de la A uno ✖ (la eliminó" in texto_p and "Idea de la B uno ✖ (la eliminó" in texto_p and "Idea cambiada con la flecha ✖ (la eliminó" in texto_p and "Idea de la C uno" in texto_p, texto_p)
        ok_el = parsea(json.dumps({"ideas": [{"tipo": "general", "titulo": "Idea de la A uno", "de_que_va": "x", "por_que": "y", "evidencia": []},
                                              {"tipo": "general", "titulo": "Otra cosa totalmente distinta aqui", "de_que_va": "x", "por_que": "y", "evidencia": []}]}),
                       {"por_codigo": {}}, {"mezcla": {"general": 2}, "tipos": [{"id": "general", "nombre": "g", "n": 2}], "semillas": []}, minimo=1, ya=titulos_eliminados(tmp, "ejemplo"))
        caso("el código tira la idea que repite una eliminada (aunque la IA la devuelva igual) y deja las nuevas", [i["titulo"] for i in ok_el] == ["Otra cosa totalmente distinta aqui"], ok_el)

        # --- 🗑 Eliminar una idea (el autor, 12-oct-2026): desaparece del todo
        d_id, e_id, f_id = "20270116-090000", "20270116-100000", "20270116-110000"
        tanda(d_id, ahora - 50, ["Idea D uno", "Idea D dos", "Idea D tres"])
        marca(f"{d_id}-01", "grabar", tmp, cfg=cfg)
        r_e = elimina_idea(f"{d_id}-02", tmp, cfg)
        td = next(t for t in tandas(tmp) if t["id"] == d_id)
        caso("🗑 Eliminar quita la idea de su tanda; las otras (y la que está por grabar) siguen y no queda ninguna marca suya",
             r_e == {"ok": True} and [i["id"] for i in td["ideas"]] == [f"{d_id}-01", f"{d_id}-03"] and f"{d_id}-02" not in estado(tmp)
             and [i["id"] for i in por_grabar(tmp, cfg)] == [f"{d_id}-01"], r_e)
        elimina_idea(f"{d_id}-01", tmp, cfg)
        caso("eliminar una idea que estaba «Por grabar» la saca del checklist del todo (con su copia)", por_grabar(tmp, cfg) == [] and f"{d_id}-01" not in estado(tmp), estado(tmp))
        elim_d = {e["titulo"] for e in eliminadas(tmp)}
        caso("🗑 Eliminar apunta el título de la idea (la «Por grabar» también) para que no vuelva; un título repetido no se apunta dos veces",
             {"Idea D dos", "Idea D uno"} <= elim_d and (elimina_idea("no-esta", tmp, cfg), len([e for e in eliminadas(tmp) if e["titulo"] == "Idea D dos"]))[1] == 1, elim_d)
        elimina_idea(f"{d_id}-03", tmp, cfg)
        caso("al eliminar la ÚLTIMA idea de una tanda se va la tanda entera con sus datos (no queda un archivo vacío)",
             not (carpeta / f"tanda_{d_id}.json").exists() and not (carpeta / f"datos_{d_id}.json").exists())
        r_no = elimina_idea("no-existe", tmp, cfg)
        caso("eliminar lo que ya no está dice «ya no está» sin romper nada", not r_no["ok"] and r_no["codigo"] == "no_esta" and not elimina_idea("", tmp, cfg)["ok"])
        # --- las antiguas «No me interesa» se eliminan del todo
        tanda(e_id, ahora - 40, ["Idea E uno", "Idea E dos"])
        marca(f"{e_id}-01", "descartada", tmp, cfg=cfg)
        n_desc = limpia_descartadas(tmp, cfg)
        te = next(t for t in tandas(tmp) if t["id"] == e_id)
        caso("lo que antes se marcó «✖ No me interesa» se ELIMINA del todo (ya no existe esa marca) y lo demás no se toca",
             n_desc == 1 and [i["id"] for i in te["ideas"]] == [f"{e_id}-02"] and f"{e_id}-01" not in estado(tmp) and limpia_descartadas(tmp, cfg) == 0)
        # --- «Borrar todas las anteriores»: se queda la más reciente, y lo de «Por grabar» sobrevive
        tanda(f_id, ahora - 30, ["Idea F uno", "Idea F dos"])
        marca(f"{e_id}-02", "grabar", tmp, cfg=cfg)
        r_ant = borra_anteriores("ejemplo", tmp, ahora)
        caso("🗑 Borrar todas las anteriores: solo queda la tanda MÁS RECIENTE; de las demás se va todo menos lo que está por grabar",
             r_ant["ok"] and r_ant["tandas"] == 2 and r_ant["ideas"] == 1 and r_ant["conservadas"] == 1 and [t["id"] for t in tandas(tmp, canal="ejemplo")] == [f_id]
             and [i["id"] for i in por_grabar(tmp, cfg)] == [f"{e_id}-02"] and por_grabar(tmp, cfg)[0]["titulo"] == "Idea E dos" and not (carpeta / f"tanda_{c_id}.json").exists(), r_ant)
        caso("una vez borradas, una segunda vez no hace nada", borra_anteriores("ejemplo", tmp, ahora) == {"ok": True, "tandas": 0, "ideas": 0, "conservadas": 0})
        caso("el checklist de «Por grabar» sale con lo ÚLTIMO que marcaste primero",
             (marca(f"{f_id}-01", "grabar", tmp, cfg=cfg), [i["id"] for i in por_grabar(tmp, cfg)])[1] == [f"{f_id}-01", f"{e_id}-02"])
        # --- una idea marcada «Por grabar» ANTES de que existiera su copia propia (marca vieja, sin «idea») también sobrevive a borrar su tanda
        g_id = "20270117-090000"
        tanda(g_id, ahora - 20, ["Idea G uno", "Idea G dos"])
        est_v = estado(tmp)
        est_v[f"{g_id}-01"] = {"estado": "grabar", "ts": ahora - 10}
        _guarda(carpeta / "estado.json", est_v)
        r_v = borra_tanda(g_id, tmp, ahora)
        caso("una marca «Por grabar» ANTIGUA (sin copia propia) también sobrevive a borrar su tanda: al borrarla se le guarda la copia",
             r_v == {"ok": True, "ideas": 1, "conservadas": 1} and f"{g_id}-01" in [i["id"] for i in por_grabar(tmp, cfg)]
             and next(i for i in por_grabar(tmp, cfg) if i["id"] == f"{g_id}-01")["titulo"] == "Idea G uno" and not (carpeta / f"tanda_{g_id}.json").exists(), r_v)

    # ---- guardar sin que un clic falle (5-oct-2026: «acceso denegado» al reemplazar estado.json, 2 veces)
    from . import util as _util
    with tempfile.TemporaryDirectory() as tmp2:
        carpeta2 = Path(tmp2) / "ideas"
        carpeta2.mkdir()

        class Duro:                    # un temporal «en uso»: las primeras `fallos` veces, replace da «acceso denegado»
            def __init__(self, real, fallos):
                self.real, self.fallos, self.n = real, fallos, 0

            def replace(self, destino):
                self.n += 1
                if self.n <= self.fallos:
                    raise PermissionError(5, "Acceso denegado")
                return self.real.replace(destino)

            def read_text(self, **k):
                return self.real.read_text(**k)

            def unlink(self, **k):
                return self.real.unlink(**k)

        dormir = _util.time.sleep
        _util.time.sleep = lambda s: None
        try:
            destino_a, tmp_a = carpeta2 / "a.json", carpeta2 / "a.tmp"
            tmp_a.write_text("nuevo-a", encoding="utf-8")
            dura_a = Duro(tmp_a, 3)
            _util.reemplaza(dura_a, destino_a)
            destino_b, tmp_b = carpeta2 / "b.json", carpeta2 / "b.tmp"
            tmp_b.write_text("nuevo-b", encoding="utf-8")
            dura_b = Duro(tmp_b, 99)
            _util.reemplaza(dura_b, destino_b)
        finally:
            _util.time.sleep = dormir
        caso("guardar con el archivo «en uso» (Windows: acceso denegado al reemplazar) reintenta y termina bien; si no cede, escribe directo: lo que se quería guardar no se pierde",
             destino_a.read_text(encoding="utf-8") == "nuevo-a" and dura_a.n == 4 and not tmp_a.exists()
             and destino_b.read_text(encoding="utf-8") == "nuevo-b" and not tmp_b.exists(), (dura_a.n, dura_b.n))
        tid2 = "20270116-100000"
        _guarda(carpeta2 / f"tanda_{tid2}.json", {"id": tid2, "ts": ahora, "version": 2, "canal": "ejemplo", "ideas_anteriores": [],
                                                   "ideas": [{"id": f"{tid2}-{k:02d}", "tipo": "general", "titulo": f"Idea número {k}", "evidencia": []} for k in range(1, 21)]})
        hilos = [threading.Thread(target=marca, args=(f"{tid2}-{k:02d}", "descartada", tmp2, cfg)) for k in range(1, 21)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        est_h = estado(tmp2)
        caso("20 clics a la vez («No me interesa» en varias ideas): se guardan las 20 marcas (ninguna pisa a otra) y no quedan temporales",
             sum(1 for k, v in est_h.items() if k.startswith(tid2) and v.get("estado") == "descartada") == 20 and not list(carpeta2.glob("*.tmp")), len(est_h))
    return filas
