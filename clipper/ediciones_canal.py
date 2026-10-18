"""🎛️ QUÉ EDICIONES LLEVA CADA CANAL (el autor, 2026-10-10): hasta ahora todo se hizo para Mi canal; quien cree un canal nuevo tiene que poder ELEGIR cuáles de las ediciones
quiere (escenas a pantalla completa, tarjetas, imágenes, Shorts…) y apagar las que no le sirven.

Cada canal puede traer un bloque `edicion: {id: true|false}` (en `canales/<canal>.yaml`, o en `videos.canales.<canal>` de config.yaml). Lo que no esté escrito se queda como diga
config.yaml (= Mi canal: todo encendido). Se aplica de dos formas, sin tocar el plan que guarda el director (así «Reintentar» o volver a encender algo no pierde nada):
  · `aplica(cfg, ed)`: sobre la configuración de ESA corrida (los interruptores de siempre: `videos.tarjetas_activo`, `videos.escenas.…`).
  · `filtra_camara` / `filtra_tarjetas`: sobre las listas del plan, justo donde se usan.
Los mismos ids salen en el asistente «Nuevo canal» (paso 5) y en «Editar canal».
"""
from __future__ import annotations

# (grupo, ayuda del grupo, [(id, nombre, qué hace)])
CATALOGO: tuple = (
    ("Cortes y ritmo", "Cómo se corta y cómo respira el vídeo.", (
        ("pausas", "Acortar las pausas de duda", "Las pausas largas a media frase («de… la… que…») se dejan en 0,3 s. Las de énfasis no se tocan."),
        ("palabras_clave", "Palabras clave en grande", "1-3 palabras que salen grandes, abajo, justo cuando las dices."),
        ("metronomo", "Metrónomo (que nunca pase mucho sin cambio)", "Reparte palabras clave para que no haya tramos de más de 15 s con el mismo plano."),
        ("movimiento_suave", "Movimiento suave de cámara", "Un acercamiento lento y sutil dentro de «cara», «pantalla» y «pantalla con zoom»."),
    )),
    ("Cámara y pantalla", "Cómo se alterna tu cara y lo que enseñas.", (
        ("zoom_pantalla", "Acercar la pantalla donde mueves el ratón", "«Pantalla con zoom»: se acerca a la zona de actividad sin enseñar tu cámara."),
        ("explicativa", "Escena «explicativa»", "Cuando explicas qué es algo: tu cara a un lado y la definición en una tarjeta grande."),
    )),
    ("Tarjetas y apoyos", "Lo que sale encima del vídeo.", (
        ("tarjeta_punto", "Tarjeta de punto (1, 2, 3…)", "Cuando enumeras: «primero… segundo…»."),
        ("tarjeta_herramienta", "Tarjeta de herramienta", "El nombre de una herramienta, web o producto que nombras."),
        ("tarjeta_definicion", "Tarjeta de definición", "Un término y su significado en una frase."),
        ("tarjeta_dato", "Tarjeta de dato", "Una cifra importante, en grande."),
        ("suscribete", "Tarjeta de «suscríbete»", "Con el logo de YouTube, una vez o varias, separadas 2 minutos."),
        ("like", "Tarjeta de «dale like»", "Una al 80 % del vídeo."),
        ("imagenes", "Imágenes ilustrativas", "Una foto de Openverse arriba a la derecha (la elige una IA mirando las fotos)."),
        ("ilustraciones", "Ilustraciones animadas", "Un dibujo animado de tu marca (lupa, gráfica, cohete…) cuando algo es muy visual."),
    )),
    ("Escenas a pantalla completa", "Tu cara se va un momento y la pantalla se llena con un gráfico.", (
        ("escena_ilustracion", "Con una ilustración grande", "Una ilustración de la biblioteca y la frase clave, a cámara lenta."),
        ("estilo_capitulo", "Capítulo numerado", "Cuando dices «número dos…», «paso 3…»: marco, número grande y título."),
        ("estilo_grafica", "Gráfica de un estudio", "Cuando citas un estudio o informe con cifras: barras animadas."),
        ("estilo_chat", "Chat de IA", "Un ejemplo de pregunta y respuesta de ChatGPT o similar."),
        ("estilo_veredicto", "Bien / mal", "Un ejemplo de lo que sí y lo que no, con ✔ y ✖."),
        ("estilo_cita", "Frase importante", "Una frase completa y de peso, en letras grandes."),
    )),
    ("Extras", "Lo que sale además del vídeo.", (
        ("shorts", "Shorts verticales", "Al terminar cada render, los mejores momentos como Shorts (ya con título y tags)."),
        ("miniaturas_ia", "Miniaturas con IA", "Tres miniaturas (A/B/C) hechas con Cloudflare y tu cara. Necesita su clave."),
        ("musica", "Música de fondo", "Tu carpeta de música (o Openverse). Apagada = sin música."),
        ("ia_ve_video", "Que la IA «vea» el vídeo", "Flash-Lite mira una copia pequeña y le cuenta al director qué hay en pantalla."),
    )),
)

IDS = tuple(i for _g, _a, items in CATALOGO for i, _n, _d in items)
ESTILOS_ESC = {"estilo_capitulo": "capitulo", "estilo_grafica": "grafica", "estilo_chat": "chat", "estilo_veredicto": "veredicto", "estilo_cita": "cita"}
TIPOS_TARJETA = {"tarjeta_punto": "punto", "tarjeta_herramienta": "herramienta", "tarjeta_definicion": "definicion", "tarjeta_dato": "dato"}

# dónde vive hoy cada interruptor en config.yaml (para decir cuál es el valor «de fábrica» de cada canal)
_RUTAS = {"pausas": "videos.pausas.activo", "palabras_clave": "videos.ritmo.palabras_activo", "suscribete": "videos.suscribete_activo", "like": "videos.like_activo",
          "imagenes": "videos.imagenes_activo", "ilustraciones": "videos.ilustraciones_activo", "escena_ilustracion": "videos.escenas.ilustracion_activo",
          "shorts": "videos.shorts_activo", "musica": "videos.musica_activo", "ia_ve_video": "videos.ia_ve_video", "miniaturas_ia": "miniaturas_ia.activo"}

PRESETS = {
    "todo": {"nombre": "Todo", "ayuda": "Todo encendido.", "valores": {i: True for i in IDS}},
    "equilibrado": {"nombre": "Equilibrado", "ayuda": "Cortes, tarjetas y apoyos; sin escenas a pantalla completa ni movimiento de cámara.",
                    "valores": {**{i: True for i in IDS}, "movimiento_suave": False, "escena_ilustracion": False, **{k: False for k in ESTILOS_ESC}}},
    "esencial": {"nombre": "Solo lo esencial", "ayuda": "Cortes limpios, tarjetas de punto y herramienta y suscríbete. Nada más.",
                 "valores": {**{i: False for i in IDS}, "pausas": True, "tarjeta_punto": True, "tarjeta_herramienta": True, "suscribete": True, "like": True}},
}


def _get(cfg, ruta: str, defecto=True):
    try:
        v = cfg.get_path(ruta)
    except Exception:  # noqa: BLE001
        return defecto
    return defecto if v is None else v


def defectos(cfg) -> dict:
    """El valor de cada edición si el canal no dice nada: lo que ponga config.yaml (en Mi canal, casi todo encendido)."""
    d = {i: True for i in IDS}
    for i, ruta in _RUTAS.items():
        d[i] = bool(_get(cfg, ruta, True))
    d["movimiento_suave"] = float(_get(cfg, "transiciones.ken_burns_zoom", 0.06) or 0) > 0
    d["metronomo"] = float(_get(cfg, "videos.ritmo.objetivo_por_min", 10) or 0) > 0
    return d


def catalogo_para_panel(cfg) -> list[dict]:
    """El catálogo para el asistente: grupos con sus ediciones y el valor de fábrica de cada una."""
    de = defectos(cfg)
    return [{"grupo": g, "ayuda": a, "items": [{"id": i, "nombre": n, "ayuda": d, "defecto": de.get(i, True)} for i, n, d in items]} for g, a, items in CATALOGO]


def valida(ed) -> dict:
    """Lo que manda el formulario -> {id: bool} solo con ids conocidos (lo demás se ignora)."""
    if not isinstance(ed, dict):
        return {}
    return {k: bool(v) for k, v in ed.items() if k in IDS}


def _pon(cfg, ruta: str, valor) -> None:
    nodo = cfg
    partes = ruta.split(".")
    for p in partes[:-1]:
        if not isinstance(nodo.get(p), dict):
            nodo[p] = {}
        nodo = nodo[p]
    nodo[partes[-1]] = valor


def aplica(cfg, ed: dict | None) -> list[str]:
    """Pone en la configuración de ESTA corrida lo que el canal eligió. -> las ediciones apagadas (para el registro). Nunca lanza."""
    ed = valida(ed or {})
    if not ed:
        return []
    apagadas = [i for i, v in ed.items() if not v]
    try:
        for i, v in ed.items():
            if i in _RUTAS:
                _pon(cfg, _RUTAS[i], v)
        if "palabras_clave" in ed and not ed["palabras_clave"]:
            _pon(cfg, "videos.ritmo.palabras_por_codigo", False)
            _pon(cfg, "videos.ritmo.pedir_para_planes_viejos", False)
        if "metronomo" in ed and not ed["metronomo"]:
            _pon(cfg, "videos.ritmo.max_sin_cambio_seg", 9999)
            _pon(cfg, "videos.ritmo.objetivo_por_min", 0)
        if "movimiento_suave" in ed and not ed["movimiento_suave"]:
            _pon(cfg, "transiciones.ken_burns_zoom", 0)
        # las escenas a pantalla completa: qué estilos están permitidos y si hay alguna
        if any(k in ed for k in ("escena_ilustracion", *ESTILOS_ESC)):
            todos = {**{"escena_ilustracion": bool(_get(cfg, "videos.escenas.ilustracion_activo", True))}, **{k: True for k in ESTILOS_ESC}}
            todos.update({k: v for k, v in ed.items() if k in todos})
            permitidos = [ESTILOS_ESC[k] for k in ESTILOS_ESC if todos[k]]
            _pon(cfg, "videos.escenas.estilos_permitidos", permitidos)
            _pon(cfg, "videos.escenas.estilo_activo", bool(permitidos))
            _pon(cfg, "videos.escenas.ilustracion_activo", todos["escena_ilustracion"])
            _pon(cfg, "videos.escenas.activo", bool(permitidos) or todos["escena_ilustracion"])
    except Exception:  # noqa: BLE001 — una edición sin aplicar nunca puede tumbar un vídeo
        pass
    return apagadas


def filtra_camara(camara: list[dict], ed: dict | None) -> list[dict]:
    """Los tramos de cámara del plan sin los modos que el canal apagó: «explicativa» y «pantalla con zoom» pasan a «cara» / «pantalla»."""
    ed = ed or {}
    if ed.get("explicativa", True) and ed.get("zoom_pantalla", True):
        return camara
    out = []
    for c in camara or []:
        c = dict(c)
        if c.get("modo") == "explicativa" and not ed.get("explicativa", True):
            c["modo"] = "cara"
            c.pop("termino", None)
            c.pop("definicion", None)
        elif c.get("modo") == "pantalla_zoom" and not ed.get("zoom_pantalla", True):
            c["modo"] = "pantalla"
        out.append(c)
    return out


def filtra_tarjetas(tarjetas: list[dict], ed: dict | None) -> list[dict]:
    """Las tarjetas del plan sin los tipos que el canal apagó."""
    ed = ed or {}
    quitar = {TIPOS_TARJETA[k] for k in TIPOS_TARJETA if not ed.get(k, True)}
    if not quitar:
        return tarjetas
    return [t for t in (tarjetas or []) if t.get("tipo") not in quitar]


def de_canal(cfg, canal: str) -> dict:
    """El bloque `edicion` guardado de un canal ({} si no tiene)."""
    try:
        info = (cfg.get_path("videos.canales") or {}).get(canal) or {}
        return valida(info.get("edicion"))
    except Exception:  # noqa: BLE001
        return {}


def permite(cfg, canal: str, id_: str) -> bool:
    """¿Esta edición está encendida para ese canal? (lo que el canal diga manda; si no, config.yaml)."""
    ed = de_canal(cfg, canal)
    if id_ in ed:
        return ed[id_]
    return bool(defectos(cfg).get(id_, True))


# ------------------------------------------------------------------ pruebas
def prueba() -> list[dict]:
    out: list[dict] = []

    def caso(n, ok, d=""):
        out.append({"caso": n, "ok": bool(ok), "detalle": str(d)[:300]})

    from .config import Config
    base = Config({"videos": {"pausas": {"activo": True}, "ritmo": {"activo": True, "palabras_activo": True, "max_sin_cambio_seg": 15, "objetivo_por_min": 10},
                              "tarjetas_activo": True, "suscribete_activo": True, "like_activo": True, "imagenes_activo": True, "ilustraciones_activo": True,
                              "escenas": {"activo": True}, "shorts_activo": True, "musica_activo": False, "ia_ve_video": False},
                   "transiciones": {"ken_burns_zoom": 0.06}, "miniaturas_ia": {"activo": True}})
    caso("el catálogo no repite ids y cada preset solo usa ids conocidos",
         len(IDS) == len(set(IDS)) and all(set(p["valores"]) <= set(IDS) for p in PRESETS.values()), len(IDS))
    d = defectos(base)
    caso("los valores de fábrica salen de config.yaml (la música y «la IA ve el vídeo» apagadas ahí, el resto encendido)",
         d["musica"] is False and d["ia_ve_video"] is False and d["shorts"] and d["movimiento_suave"] and d["metronomo"] and d["estilo_grafica"], d)
    cfg = Config(__import__("copy").deepcopy(dict(base)))
    apag = aplica(cfg, {"shorts": False, "suscribete": False, "movimiento_suave": False, "metronomo": False, "palabras_clave": False, "miniaturas_ia": False,
                        "inventada": False})
    caso("aplica: apaga lo elegido (Shorts, suscríbete, movimiento, metrónomo, palabras clave, miniaturas IA) y deja lo demás; ignora ids desconocidos",
         cfg.get_path("videos.shorts_activo") is False and cfg.get_path("videos.suscribete_activo") is False and cfg.get_path("transiciones.ken_burns_zoom") == 0
         and cfg.get_path("videos.ritmo.objetivo_por_min") == 0 and cfg.get_path("videos.ritmo.max_sin_cambio_seg") == 9999
         and cfg.get_path("videos.ritmo.palabras_activo") is False and cfg.get_path("miniaturas_ia.activo") is False
         and cfg.get_path("videos.like_activo") is True and cfg.get_path("videos.tarjetas_activo") is True and "inventada" not in apag, (cfg.get("videos"), apag))
    cfg2 = Config(__import__("copy").deepcopy(dict(base)))
    aplica(cfg2, {"estilo_chat": False, "estilo_veredicto": False})
    caso("escenas: apagar dos estilos deja los otros tres permitidos y la ilustración como estaba",
         cfg2.get_path("videos.escenas.estilos_permitidos") == ["capitulo", "grafica", "cita"] and cfg2.get_path("videos.escenas.ilustracion_activo") is True
         and cfg2.get_path("videos.escenas.activo") is True, cfg2.get_path("videos.escenas"))
    cfg3 = Config(__import__("copy").deepcopy(dict(base)))
    aplica(cfg3, {**{k: False for k in ESTILOS_ESC}, "escena_ilustracion": False})
    caso("escenas: sin ninguna (ilustración ni estilos) se apagan las escenas a pantalla completa por completo",
         cfg3.get_path("videos.escenas.activo") is False and cfg3.get_path("videos.escenas.estilo_activo") is False and cfg3.get_path("videos.escenas.estilos_permitidos") == [], cfg3.get_path("videos.escenas"))
    cam = [{"modo": "cara", "inicio": 0, "fin": 5}, {"modo": "explicativa", "inicio": 5, "fin": 15, "termino": "CMS", "definicion": "x"},
           {"modo": "pantalla_zoom", "inicio": 15, "fin": 25}]
    f = filtra_camara(cam, {"explicativa": False, "zoom_pantalla": False})
    caso("cámara: sin «explicativa» pasa a «cara» (sin término) y sin zoom a «pantalla»; el plan original no se toca; sin cambios devuelve el mismo",
         [c["modo"] for c in f] == ["cara", "cara", "pantalla"] and "termino" not in f[1] and cam[1]["modo"] == "explicativa" and filtra_camara(cam, {}) is cam, f)
    tj = [{"tipo": "punto"}, {"tipo": "herramienta"}, {"tipo": "dato"}]
    caso("tarjetas: se quitan solo los tipos apagados", [t["tipo"] for t in filtra_tarjetas(tj, {"tarjeta_dato": False, "tarjeta_punto": False})] == ["herramienta"]
         and filtra_tarjetas(tj, {}) is tj)
    caso("valida: solo ids conocidos y siempre booleanos", valida({"shorts": 0, "musica": "si", "x": True}) == {"shorts": False, "musica": True} and valida(None) == {})
    caso("permite: lo que diga el canal manda sobre config.yaml",
         permite(Config({"videos": {"canales": {"c": {"edicion": {"shorts": False}}}}}), "c", "shorts") is False
         and permite(Config({"videos": {"canales": {"c": {}}}, "miniaturas_ia": {"activo": True}}), "c", "miniaturas_ia") is True)
    return out
