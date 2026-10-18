"""Ejemplos animados de LO QUE EDITA el panel: cambios de escena, tarjetas, imágenes, suscríbete y like.

Igual que la galería de ilustraciones, pero para las demás ediciones. Se dibuja una grabación de
EJEMPLO (una pantalla con tu web y una cámara en la esquina, hecha con Pillow: no es un vídeo tuyo, así
sirve a cualquiera y no depende de ningún archivo) y sobre ella se aplica LO MISMO que hace el render:

  · escenas   -> los recortes REALES de `render.py`: «cara» = el recuadro de tu cámara (`camara`) agrandado
                 `zoom_cara` veces con el mismo centro; «pantalla_zoom» = el rectángulo `pantalla_zoom`;
                 «pantalla»/«ambos» = el plano completo. El corte es SECO (`transiciones.visual: no`);
                 lo que marca el cambio es el «whoosh» (que aquí no se oye: está explicado en la galería).
                 «explicativa» = tu cara al tercio derecho + la tarjeta de término/definición
                 (`tarjetas.dibuja_explicativa`) a los otros dos tercios, con el MISMO reparto que el render.
                 Las variantes «· movimiento suave» aplican el Ken Burns de verdad: la ventana sale de
                 `render.ken_burns_ventana` (LA misma fórmula que arma las expresiones de ffmpeg), solo que
                 acelerada — en el vídeo real dura toda la escena, aquí 5 s.
  · tarjetas,
    suscribete,
    like        -> la MISMA imagen de `tarjetas.dibuja`, abajo a la izquierda (5 % / 6 % de margen), con el
                 fundido de opacidad lineal de `render.py` (`fdur = clamp(duración/4, 0.12, 0.35)`).
  · imagenes  -> el MISMO marco de `imagenes.enmarca` (borde+sombra, igual que las tarjetas), arriba a la
                 derecha (para no chocar con ellas) — la FOTO en sí es un icono genérico dibujado a mano
                 (este módulo no llama a Openverse ni depende de ningún archivo: ver más abajo), pero el
                 marco y el sitio donde se pega son los reales.

Cada escena es una función pura del tiempo (mismo t -> mismo fotograma) y los bucles cierran limpios.
La caché (work/ejemplos_previas/, WebP animado) se invalida sola si cambian la marca, la cámara o VERSION.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import config, render, tarjetas

VERSION = 5                       # súbelo al cambiar el dibujo: invalida la caché
W0, H0 = 2560, 1440               # el lienzo de la grabación real (config `camara` es fracción de esto)
SALIDA = (800, 450)               # tamaño del ejemplo en la galería
FPS = 20
SS = 2                            # supersampling de lo que se mueve (cursor, boca, etiqueta)

_CANAL = "ejemplo"


# ------------------------------------------------------------------ catálogo (lo que ve la gente)
@dataclass(frozen=True)
class Ejemplo:
    clave: str
    seccion: str                  # escenas | tarjetas | imagenes | suscribete | like
    nombre: str
    cuando: tuple                 # cuándo lo usa el director (frases cortas)
    que_hace: str                 # qué se ve en el vídeo
    dice: str = ""                # la frase de ejemplo que lo dispara (se ve como etiqueta en el ejemplo)
    extra: dict = field(default_factory=dict)


CATALOGO: dict[str, Ejemplo] = {e.clave: e for e in (
    Ejemplo("escena_pantalla", "escenas", "Pantalla",
            ("Enseñas algo que se ve en tu ordenador: «acá pueden ver…», «denle clic aquí».",
             "Nombras un botón, un menú o una herramienta: «vamos a Google Search Console».",
             "Es el plano de partida; si no está claro el director lo llama «ambos»."),
            "Se ve toda tu grabación: la pantalla, con tu cámara pequeña en la esquina.",
            "«Acá pueden ver el rendimiento…»", {"antes": "cara", "modo": "pantalla"}),
    Ejemplo("escena_pantalla_kb", "escenas", "Pantalla · movimiento suave",
            ("Es la misma escena «Pantalla» de arriba, pero con el plano acercándose despacio mientras hablas.",
             "Lo decide el programa, no la IA: en cada tipo de escena, una sí y una no lleva este movimiento."),
            "El mismo plano de siempre, pero se acerca despacio durante toda la escena (un 6 % en total) y se desliza "
            "un poquito de lado, para que no se sienta congelado. Aquí va acelerado para que se note: en tu vídeo dura "
            "lo que dura la escena.",
            "«Acá pueden ver el rendimiento…»", {"antes": "cara", "modo": "pantalla", "kb": 1}),
    Ejemplo("escena_cara", "escenas", "Cara",
            ("Opinas, cuentas una anécdota o explicas una idea sin necesidad de enseñar nada.",
             "«Esto es importante entenderlo bien…»: el espectador te ve a ti, no a la pantalla."),
            "Acerca tu cámara: llena el vídeo con tu cara (el recuadro de tu cámara con más aire alrededor).",
            "«Esto es importante entenderlo bien…»", {"antes": "pantalla", "modo": "cara"}),
    Ejemplo("escena_cara_kb", "escenas", "Cara · movimiento suave",
            ("Es la misma escena «Cara» de arriba, pero con tu cámara acercándose despacio mientras hablas.",
             "Lo decide el programa, no la IA: en cada tipo de escena, una sí y una no lleva este movimiento."),
            "El mismo plano de siempre, pero se acerca despacio durante toda la escena (un 6 % en total) y se desliza "
            "un poquito de lado, para que no se sienta congelado. Aquí va acelerado para que se note: en tu vídeo dura "
            "lo que dura la escena.",
            "«Esto es importante entenderlo bien…»", {"antes": "pantalla", "modo": "cara", "kb": -1}),
    Ejemplo("escena_zoom", "escenas", "Pantalla con zoom",
            ("Señalas un detalle pequeño o lejano: un número en una tabla, un botón chico, un campo de texto.",
             "El director la usa con mucha moderación: solo si el detalle se perdería sin acercarse."),
            "Acerca la parte de la pantalla que importa (sin tu cámara) para que se lea bien.",
            "«Fíjense en este número…»", {"antes": "pantalla", "modo": "pantalla_zoom"}),
    Ejemplo("escena_zoom_kb", "escenas", "Pantalla con zoom · movimiento suave",
            ("Es la misma escena «Pantalla con zoom» de arriba, pero con el plano acercándose un poco más, despacio.",
             "Lo decide el programa, no la IA: en cada tipo de escena, una sí y una no lleva este movimiento."),
            "El mismo zoom de siempre, pero se acerca despacio durante toda la escena (un 6 % en total) y se desliza "
            "un poquito de lado. Aquí va acelerado para que se note: en tu vídeo dura lo que dura la escena.",
            "«Fíjense en este número…»", {"antes": "pantalla", "modo": "pantalla_zoom", "kb": 1}),
    Ejemplo("escena_explicativa", "escenas", "Explicativa",
            ("Te detienes a explicar QUÉ ES un término o un concepto: «un CMS es básicamente…», «eso se llama indexación, que es cuando…».",
             "No sale por nombrar algo de pasada, ni cuando ya estás enseñando algo en pantalla: solo en una explicación de verdad "
             "(un vídeo típico tiene de 0 a 3)."),
            "Tu cara a un tercio de la pantalla (a la derecha) y, en los otros dos tercios, una tarjeta grande con el término "
            "y su definición en una frase. Esta escena no lleva movimiento suave.",
            "«Un CMS es básicamente un sistema para gestionar contenido…»",
            {"antes": "pantalla", "modo": "explicativa", "termino": "CMS",
             "definicion": "Sistema para crear y gestionar el contenido de una web sin programar."}),
    # la ESCENA A PANTALLA COMPLETA (clipper/escena_completa.py, 2026-09-26): el autor preguntó por qué no salía aquí
    Ejemplo("escena_completa", "escenas", "A pantalla completa",
            ("Dices algo MUY visual o una idea clave que merece su momento: «la tendencia va subiendo», «esto es como un "
             "embudo…». Al menos una por vídeo y como mucho una cada ~2,5 minutos.",
             "Nunca mientras enseñas algo en tu pantalla ni en los primeros 5 segundos."),
            "Tu cara se va: la pantalla entera se llena con una ilustración grande de lo que dices (a cámara lenta) y la frase "
            "clave debajo, con tu voz sonando. Entra y sale con un «whoosh». Los fondos se alternan: la 1.ª naranja, la 2.ª "
            "crema, la 3.ª naranja…",
            "«…y la tendencia va subiendo»",
            {"antes": "cara", "modo": "completa", "tipo": "grafica_sube", "frase": "LA TENDENCIA SUBE", "tono": "naranja",
             "t_poster": 3.0}),
    Ejemplo("escena_completa_crema", "escenas", "A pantalla completa · fondo crema",
            ("La misma escena cuando es la 2.ª (o la 4.ª…) del vídeo: los fondos se alternan.",),
            "Igual que la naranja, con el fondo crema de tu marca.",
            "«…un modelo que se entrena con tus datos»",
            {"antes": "cara", "modo": "completa", "tipo": "robot", "frase": "ENTRENAMIENTO IA", "tono": "crema",
             "t_poster": 3.0}),
    # las ESCENAS DE ESTILO (clipper/escenas_estilo.py, 2026-10-10): a pantalla completa como la anterior, pero «aplicadas»: solo salen cuando tu tema encaja
    Ejemplo("escena_capitulo", "escenas", "Capítulo numerado",
            ("Enumeras un punto EN VOZ ALTA: «número dos…», «paso 3…», «regla cinco…».",
             "Solo con un número dicho; el título es el nombre del punto tal como lo dices. Dura unos 3-4 segundos."),
            "Un mosaico de píxeles se aclara, un marco dorado se dibuja alrededor, el número aparece grande con brillo y el título entra palabra a palabra. "
            "Tu voz sigue debajo.",
            "«Número dos: deja que un humano escriba la versión final»",
            {"antes": "cara", "modo": "completa", "estilo": "capitulo", "dur_total": 5.0, "t_poster": 3.2,
             "datos": {"numero": 2, "titulo": "Deja que un humano escriba la versión final"}}),
    Ejemplo("escena_grafica", "escenas", "Gráfica de un estudio",
            ("Hablas de un ESTUDIO, informe, reporte o encuesta y das 2-4 cifras que se pueden comparar.",
             "Solo con cifras que DICES (nunca inventadas ni estimadas); con una sola cifra no sale. No sale en todas las ediciones: solo cuando citas datos."),
            "Barras que crecen una tras otra, con la cifra contando hasta su valor, el título de lo que mide y un empuje de cámara muy suave. "
            "Va en los colores de tu marca.",
            "«Según el estudio, el texto humano se lee 2:31 y el de IA 1:54»",
            {"antes": "cara", "modo": "completa", "estilo": "grafica", "dur_total": 8.2, "t_poster": 6.2,
             "datos": {"titulo": "Tiempo medio en la página: humano vs. IA",
                       "barras": [{"nombre": "Texto humano", "valor": 151, "etiqueta": "2:31"}, {"nombre": "Escrito por IA", "valor": 114, "etiqueta": "1:54"}]}}),
    Ejemplo("escena_chat", "escenas", "Chat de IA",
            ("Das un EJEMPLO de lo que una IA (ChatGPT, Gemini…) respondería o de lo que alguien le preguntaría: «si le preguntas cuánto tarda el SEO, te dice que entre 3 y 6 meses».",
             "Solo con tus palabras: la pregunta y la respuesta son lo que tú dices; la fuente solo si la nombras."),
            "Una ventana de chat con estilo de ChatGPT: la pregunta se teclea, la respuesta se escribe y, si nombraste la fuente, sale su etiqueta.",
            "«Si le preguntas a ChatGPT cuánto tarda el SEO, te dice que entre 3 y 6 meses…»",
            {"antes": "cara", "modo": "completa", "estilo": "chat", "dur_total": 8.2, "t_poster": 7.2,
             "datos": {"pregunta": "¿Cuánto tarda el SEO en dar resultados?",
                       "respuesta": "Normalmente entre 3 y 6 meses, según la competencia y la autoridad de tu sitio.", "fuente": "tu-sitio.example"}}),
    Ejemplo("escena_veredicto", "escenas", "Bien / mal",
            ("Pones un EJEMPLO de lo que sí y lo que no: «esto sí lo cita la IA y esto no», «correcto / incorrecto».",
             "De 1 a 3 etiquetas con ✔ o ✖; es para ejemplos concretos, no para cualquier comparación."),
            "Una tarjeta con la situación y, debajo, píldoras que entran con rebote: «Sin citar» con ✖ y «Citado» con ✔.",
            "«Aquí la IA no te cita… y aquí sí»",
            {"antes": "cara", "modo": "completa", "estilo": "veredicto", "dur_total": 6.6, "t_poster": 5.0,
             "datos": {"contexto": "¿Cuánto dura un tejado de metal?", "items": [{"texto": "Sin citar", "ok": False}, {"texto": "Citado", "ok": True}]}}),
    Ejemplo("escena_cita", "escenas", "Frase importante",
            ("Dices una frase COMPLETA y de mucho peso: una tesis, una regla, una conclusión.",
             "Va de la mano de las escenas con ilustración: esta es para cuando lo importante es la frase. Los fondos se alternan, naranja y crema."),
            "La frase en letras muy grandes, palabra a palabra, y un marcador que pasa por lo que más importa.",
            "«La IA solo cita lo que un experto podría responder»",
            {"antes": "cara", "modo": "completa", "estilo": "cita", "dur_total": 6.6, "t_poster": 5.0, "tono": "crema",
             "datos": {"frase": "La IA solo cita lo que un experto podría responder", "resalta": ["experto", "responder"]}}),
    Ejemplo("tarjeta_punto", "tarjetas", "Punto",
            ("Enumeras: «primero… segundo… tercero…», «el punto uno es…», «paso 1, paso 2».",
             "Cada punto de la enumeración lleva su propia tarjeta, con su número."),
            "Una tarjeta con el número y el punto en pocas palabras, abajo a la izquierda.",
            "«Primero: investiga tus palabras clave…»",
            {"tarjeta": {"tipo": "punto", "numero": 1, "texto": "Investiga tus palabras clave"}, "dur": 3.5}),
    Ejemplo("tarjeta_herramienta", "tarjetas", "Herramienta",
            ("Nombras una herramienta, web, producto o servicio concreto: Google Search Console, WordPress, Figma…",
             "El listón es bajo: ante la duda, se pone (es peor omitir una mención real)."),
            "El nombre exacto de la herramienta, bien visible mientras la nombras.",
            "«Vamos a abrir Google Search Console…»",
            {"tarjeta": {"tipo": "herramienta", "texto": "Google Search Console"}, "dur": 3.5}),
    Ejemplo("tarjeta_definicion", "tarjetas", "Definición",
            ("Defines un término técnico: «esto se llama Vibe Coding, que es…».",
             "El término y su explicación corta van en dos líneas de distinto tamaño."),
            "El término destacado y, debajo, su explicación en una frase.",
            "«Esto se llama Vibe Coding…»",
            {"tarjeta": {"tipo": "definicion", "texto": "Vibe Coding",
                         "extra": "Crear software hablando con la IA"}, "dur": 3.5}),
    Ejemplo("tarjeta_dato", "tarjetas", "Dato",
            ("Dices una cifra o una estadística en voz alta: «el 73 % de los clics…».",
             "La cifra en grande y, debajo, la frase que le da contexto."),
            "La cifra bien grande, con su contexto justo debajo.",
            "«El 73 % de los clics va a los primeros tres…»",
            {"tarjeta": {"tipo": "dato", "texto": "73%",
                         "extra": "de los clics van a los 3 primeros resultados"}, "dur": 3.5}),
    Ejemplo("suscribete_60", "suscribete", "La de siempre",
            ("Sale SIEMPRE una, al 60 % del vídeo, aunque no la pidas ni la menciones.",
             "Fondo naranja en vez de crema: se nota de un vistazo que no es una tarjeta educativa."),
            "«¿Te está gustando?» y el llamado a suscribirse, 4 segundos.",
            "(al 60 % del vídeo, siempre)",
            {"etq": "AUTOMÁTICA", "tarjeta": {"tipo": "suscribete", "texto": "¡SUSCRÍBETE!",
                         "extra": "Si te está gustando el video, no te pierdas el próximo"}, "dur": 4.0}),
    Ejemplo("suscribete_voz", "suscribete", "Cuando tú lo dices",
            ("Sale otra cada vez que dices «suscríbete» (o «suscripción», «suscríbanse»…) en el vídeo.",
             "Se detecta por el TEXTO de lo que dices, no por IA. Máximo 3 por vídeo y con 30 s como mínimo entre una y otra."),
            "La misma tarjeta, justo cuando lo dices, para que el llamado y la imagen coincidan.",
            "«…y suscríbete al canal…»",
            {"tarjeta": {"tipo": "suscribete", "texto": "¡SUSCRÍBETE!",
                         "extra": "Si te está gustando el video, no te pierdas el próximo"}, "dur": 4.0}),
    Ejemplo("like_80", "like", "La de siempre",
            ("Sale SIEMPRE una, al 80 % del vídeo, aunque no la pidas ni la menciones — en TODOS los vídeos.",
             "Fondo verde claro: se distingue de un vistazo de la de suscríbete (roja), aunque salgan las dos."),
            "«¿Te sirvió este vídeo?» y el llamado a dar like, 4 segundos.",
            "(al 80 % del vídeo, siempre)",
            {"etq": "AUTOMÁTICA", "tarjeta": {"tipo": "like", "texto": "¡DALE LIKE!",
                         "extra": "Me ayuda muchísimo a que más gente lo encuentre"}, "dur": 4.0}),
    Ejemplo("imagen_ilustrativa", "imagenes", "Imagen ilustrativa",
            ("Haces una analogía o mencionas algo concreto que no está en tu pantalla: «es como un embudo…», «esto es un CDN».",
             "El director apunta en inglés qué buscar; se trae de Openverse una foto libre de derechos DE VERDAD "
             "(licencia que permite usarla y recortarla) — si no encuentra una buena, se omite sin más."),
            "Una foto real, enmarcada igual que las tarjetas, arriba a la derecha (para no chocar con ellas).",
            "«…es como si fuera un embudo de ventas»",
            {"dur": 3.0}),
)}

SECCIONES = {
    "escenas": ("Cambios de escena", "🎬",
                "El director decide, tramo a tramo, qué encuadre se ve: tu pantalla, tu cara, un zoom, una tarjeta de explicación "
                "o, en los momentos clave, una escena a pantalla completa con una ilustración (fondo naranja o crema). "
                "El corte es seco y suena un «whoosh» suave (solo cuando cambia de verdad el encuadre). Además, en una de cada dos "
                "escenas de pantalla, cara o zoom el plano se acerca despacio (movimiento suave) para que no se sienta congelado. "
                "Las muestras están quietas: dale clic a una para verla animada."),
    "tarjetas": ("Tarjetas", "🏷️",
                 "En vez de subtítulos, aparece una tarjeta animada abajo a la izquierda cuando dices algo que merece verse: "
                 "un punto, una herramienta, una definición o una cifra. Entra y sale con un fundido y suena un tono etéreo suave. "
                 "Ritmo normal: una cada 45–90 segundos de vídeo hablado."),
    "imagenes": ("Imágenes ilustrativas", "🖼️",
                 "Cuando haces una analogía o mencionas algo concreto que no está en pantalla, aparece una foto real "
                 "(de Openverse, libre de derechos) arriba a la derecha, enmarcada igual que las tarjetas. La atribución "
                 "que pide la licencia va en la descripción de YouTube, nunca sobre el vídeo."),
    "suscribete": ("Suscríbete", "🔔",
                   "La tarjeta que pide suscripción. La pone el panel, no hace falta pedirla: una fija al 60 % del vídeo y otra "
                   "cada vez que lo dices tú (máximo 3 en total). Suena un «pop», distinto del tono de las demás tarjetas."),
    "like": ("Dale like", "👍",
             "La tarjeta que pide like. La pone el panel, no hace falta pedirla ni mencionarla: una fija al 80 % del "
             "vídeo, en TODOS los vídeos. Suena un «pop», distinto del tono de las demás tarjetas."),
}


# ------------------------------------------------------------------ configuración
def _marca() -> dict:
    """`videos.marca` con las rutas de fuente resueltas contra la carpeta del proyecto."""
    m = dict(config.load().get_path("videos.marca") or {})
    for k in ("fuente_texto", "fuente_titulo", "fuente_numero"):
        v = str(m.get(k, ""))
        if v and not Path(v).is_absolute() and not Path(v).exists():
            m[k] = str(config.ROOT / v)
    return m


def _canal() -> dict:
    return dict(config.load().get_path(f"videos.canales.{_CANAL}") or {})


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fuente(nombre: str, px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(config.ROOT / "fonts" / nombre), px)
    except OSError:
        return ImageFont.load_default(size=px)


def clave_cache(clave: str) -> str:
    c = _canal()
    huella = "|".join(str(x) for x in (VERSION, clave, sorted(_marca().items()), c.get("camara"), c.get("pantalla_zoom"),
                                       SALIDA, FPS))
    return hashlib.sha1(huella.encode("utf-8")).hexdigest()[:10]


# ------------------------------------------------------------------ la grabación de ejemplo
_LOCK = Lock()
_BASES: dict[str, Image.Image] = {}


def _caja_camara(c: dict) -> tuple[int, int, int, int]:
    cam = c.get("camara") or {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847}
    return (round(cam["x"] * W0), round(cam["y"] * H0), round(cam["w"] * W0), round(cam["h"] * H0))


def _presentador(im: Image.Image, caja: tuple[int, int, int, int], marca: dict) -> None:
    """Un presentador de dibujos (sin boca: la boca se anima aparte) en el recuadro de la cámara."""
    x, y, w, h = caja
    d = ImageDraw.Draw(im)
    tinta, crema, naranja = _rgb(marca["tinta"]), _rgb(marca["crema"]), _rgb(marca["naranja"])
    piel, pelo = (240, 185, 141), (43, 27, 23)
    d.rectangle((x, y, x + w, y + h), fill=(231, 222, 203))                          # pared
    for cx_, cy_, r in ((0.15, 0.22, 26), (0.30, 0.10, 20), (0.86, 0.20, 26)):        # luces hexagonales de la pared
        c = (x + cx_ * w, y + cy_ * h)
        d.polygon([(c[0] + r * math.cos(math.radians(60 * k)), c[1] + r * math.sin(math.radians(60 * k))) for k in range(6)],
                  fill=(246, 241, 230), outline=(214, 205, 184), width=3)
    d.rounded_rectangle((x + .10 * w, y + .44 * h, x + .90 * w, y + 1.05 * h), radius=40, fill=(42, 37, 35))   # silla
    d.rounded_rectangle((x + .18 * w, y + .70 * h, x + .82 * w, y + 1.06 * h), radius=46, fill=crema, outline=tinta, width=5)
    for k in range(0, int(.40 * h), 30):                                             # camiseta de rayas
        d.rectangle((x + .18 * w + 8, y + .72 * h + k, x + .82 * w - 8, y + .72 * h + k + 11), fill=(48, 43, 41))
    d.rectangle((x + .44 * w, y + .58 * h, x + .56 * w, y + .74 * h), fill=piel, outline=tinta, width=4)     # cuello
    hx, hy, rx, ry = x + .50 * w, y + .41 * h, .175 * w, .25 * h
    for k in range(11):                                                              # pelo rizado
        a = math.radians(196 + k * 15.4)
        px_, py_ = hx + rx * 1.02 * math.cos(a), hy + ry * 1.0 * math.sin(a)
        d.ellipse((px_ - 30, py_ - 30, px_ + 30, py_ + 30), fill=pelo)
    d.ellipse((hx - rx, hy - ry, hx + rx, hy + ry), fill=piel, outline=tinta, width=6)                       # cara
    d.pieslice((hx - rx - 4, hy - ry - 8, hx + rx + 4, hy + ry * .35), 188, 352, fill=pelo)                  # flequillo
    d.arc((hx - rx * 1.10, hy - ry * 1.08, hx + rx * 1.10, hy + ry * .5), 194, 346, fill=tinta, width=26)    # arco de los auriculares
    d.arc((hx - rx * 1.10, hy - ry * 1.08, hx + rx * 1.10, hy + ry * .5), 194, 346, fill=crema, width=14)
    for sg in (-1, 1):                                                                # almohadillas
        ex = hx + sg * rx * 1.04
        d.rounded_rectangle((ex - 20, hy - 34, ex + 20, hy + 46), radius=16, fill=crema, outline=tinta, width=5)
    for sg in (-1, 1):                                                                # gafas y ojos
        gx = hx + sg * .085 * w
        d.rounded_rectangle((gx - 32, hy - 24, gx + 32, hy + 22), radius=14, outline=tinta, width=5)
        d.ellipse((gx - 6, hy - 6, gx + 6, hy + 6), fill=tinta)
        d.line((gx - 20, hy - 40, gx + 18, hy - 44), fill=pelo, width=6)              # ceja
    d.line((hx - 6, hy - 12, hx + 6, hy - 12), fill=tinta, width=5)                   # puente
    d.arc((hx - 12, hy + 6, hx + 12, hy + 28), 20, 160, fill=tinta, width=4)          # nariz
    d.rounded_rectangle((x + .68 * w, y + .52 * h, x + .77 * w, y + .80 * h), radius=26, fill=(40, 36, 34), outline=tinta, width=4)   # micro
    d.line((x + .725 * w, y + .80 * h, x + .70 * w, y + 1.0 * h), fill=(40, 36, 34), width=10)
    d.rectangle((x, y, x + w, y + h), outline=naranja, width=10)                      # marco naranja de la cámara
    lx, ly = x + w - 40, y + 40                                                       # insignia de la marca
    d.ellipse((lx - 27, ly - 27, lx + 27, ly + 27), fill=naranja, outline=tinta, width=4)
    d.ellipse((lx - 12, ly - 12, lx + 6, ly + 6), outline=crema, width=5)
    d.line((lx + 4, ly + 4, lx + 15, ly + 15), fill=crema, width=6)


def _base(marca: dict, c: dict) -> Image.Image:
    """La grabación de ejemplo a 2560×1440 (como la real): navegador con la web y la cámara en la esquina."""
    tinta, crema, beige, naranja, dorado = (_rgb(marca[k]) for k in ("tinta", "crema", "beige", "naranja", "dorado"))
    im = Image.new("RGB", (W0, H0), crema)
    d = ImageDraw.Draw(im)
    f = lambda n, px: _fuente(n, px)                                                   # noqa: E731
    # navegador
    d.rectangle((0, 0, W0, 50), fill=(220, 224, 229))
    x = 22
    for nombre, act in (("MI CANAL: Aprende SEO Gratis", True), ("Panel de contenido", False), ("PageSpeed Insights", False)):
        d.rounded_rectangle((x, 12, x + 380, 52), radius=14, fill=(255, 255, 255) if act else (203, 208, 214))
        d.text((x + 20, 20), nombre, font=f("Rubik-Medium.ttf", 20), fill=tinta)
        x += 392
    d.rectangle((0, 50, W0, 86), fill=(255, 255, 255))
    d.rounded_rectangle((120, 56, 980, 82), radius=13, fill=(238, 240, 243))
    d.text((146, 58), "tu-sitio.example", font=f("Rubik-Medium.ttf", 20), fill=(60, 64, 70))
    # página: cabecera naranja con el título de la marca
    d.rectangle((0, 86, W0, 560), fill=naranja)
    for i in range(0, W0, 26):                                                         # trama de puntos cómic
        for j in range(120, 560, 26):
            d.ellipse((i, j, i + 4, j + 4), fill=(255, 96, 60))
    d.rounded_rectangle((250, 138, 690, 186), radius=8, fill=crema, outline=tinta, width=4)
    d.text((272, 146), "APRENDE SEO GRATIS", font=f("Rubik-ExtraBold.ttf", 26), fill=tinta)
    for dx, dy, col in ((10, 10, tinta), (0, 0, crema)):
        d.text((250 + dx, 200 + dy), "MI CANAL", font=f("Rubik-Black.ttf", 230), fill=col)
    d.rounded_rectangle((250, 452, 1240, 548), radius=20, fill=crema, outline=tinta, width=5)
    d.text((280, 464), "¿Quieres saber qué es el SEO y cómo", font=f("Rubik-Medium.ttf", 32), fill=tinta)
    d.text((280, 504), "aplicarlo en tus proyectos para sacarle el máximo?", font=f("Rubik-Medium.ttf", 32), fill=tinta)
    # tres tarjetas de contenido
    for i, (tit, sub) in enumerate((("SEO en Google", "21 entradas"), ("SEO en YouTube", "7 entradas"), ("Extensión SEO", "3 entradas"))):
        x0 = 250 + i * 550
        d.rounded_rectangle((x0 + 8, 618, x0 + 508, 868), radius=16, fill=tinta)
        d.rounded_rectangle((x0, 610, x0 + 500, 860), radius=16, fill=(255, 255, 255), outline=tinta, width=4)
        d.rectangle((x0 + 4, 614, x0 + 496, 730), fill=beige)
        d.ellipse((x0 + 190, 632, x0 + 310, 712), fill=dorado, outline=tinta, width=4)
        d.text((x0 + 26, 750), tit, font=f("Rubik-ExtraBold.ttf", 34), fill=tinta)
        d.text((x0 + 26, 800), sub, font=f("Rubik-Bold.ttf", 22), fill=naranja)
    # métricas pequeñas (para que el zoom tenga algo que enseñar) + gráfico
    d.rounded_rectangle((258, 908, 1308, 1238), radius=18, fill=tinta)
    d.rounded_rectangle((250, 900, 1300, 1230), radius=18, fill=(255, 255, 255), outline=tinta, width=4)
    d.arc((300, 940, 520, 1160), 0, 360, fill=(215, 240, 224), width=22)
    d.arc((300, 940, 520, 1160), -90, 240, fill=(18, 122, 69), width=22)
    d.text((372, 1010), "92", font=f("Rubik-Black.ttf", 84), fill=(18, 122, 69))
    d.text((338, 1170), "Rendimiento", font=f("Rubik-Bold.ttf", 28), fill=tinta)
    filas = (("First Contentful Paint", "0.5 s"), ("Total Blocking Time", "220 ms"), ("Speed Index", "0.6 s"), ("Cumulative Layout Shift", "0"))
    for k, (a, b) in enumerate(filas):
        yy = 950 + k * 68
        d.text((620, yy), a, font=f("Rubik-Medium.ttf", 27), fill=(70, 66, 62))
        d.text((1120, yy), b, font=f("Rubik-ExtraBold.ttf", 31), fill=(18, 122, 69) if k != 1 else (200, 120, 0))
        d.line((620, yy + 46, 1260, yy + 46), fill=(226, 218, 204), width=2)
    d.rounded_rectangle((1358, 908, 1858, 1238), radius=18, fill=beige, outline=tinta, width=4)
    for k, alto in enumerate((70, 110, 150, 205, 250, 285)):
        d.rectangle((1396 + k * 74, 1200 - alto, 1396 + k * 74 + 52, 1200), fill=naranja if k % 2 else dorado, outline=tinta, width=3)
    # barra de tareas
    d.rectangle((0, 1398, W0, H0), fill=(28, 32, 42))
    for i in range(14):
        d.ellipse((520 + i * 62, 1408, 550 + i * 62, 1430), fill=(90 + (i * 23) % 120, 110, 150))
    _presentador(im, _caja_camara(c), marca)
    return im


def base() -> Image.Image:
    marca, c = _marca(), _canal()
    k = hashlib.sha1(f"{VERSION}|{sorted(marca.items())}|{c.get('camara')}".encode()).hexdigest()[:8]
    with _LOCK:
        if k not in _BASES:
            _BASES.clear()
            _BASES[k] = _base(marca, c)
        return _BASES[k]


# ------------------------------------------------------------------ encuadres (lo mismo que render.py)
def _cubrir(rect: tuple[float, float, float, float], aspecto: float) -> tuple[float, float, float, float]:
    """`scale=…:force_original_aspect_ratio=increase,crop=W:H`: el recorte centrado de `rect` con esa proporción."""
    x, y, w, h = rect
    if w / h > aspecto:
        nw = h * aspecto
        return (x + (w - nw) / 2, y, nw, h)
    nh = w / aspecto
    return (x, y + (h - nh) / 2, w, nh)


def _dos_tercios(ancho: int) -> int:
    """El ancho de la tarjeta de «explicativa»: 2/3 del lienzo, en par. LA misma cuenta que hace
    `pipeline._tarjetas_explicativa` (la tarjeta) y `render.render_merged` (`ancho_tercio` = lo que
    sobra hasta el ancho total: el hueco de la cara) — si cambia una, tienen que cambiar las tres."""
    return max(2, int(ancho * 2 / 3) // 2 * 2)


def vista(modo: str, c: dict | None = None, salida: tuple[int, int] = SALIDA) -> tuple[float, float, float, float]:
    """El rectángulo de la grabación (px de 2560×1440) que se ve en pantalla en cada modo, calculado con
    las MISMAS cuentas que `render.render_merged`. En «explicativa» es el trozo de tu cara que ocupa el
    TERCIO derecho (más estrecho que alto), no el lienzo entero."""
    c = c or _canal()
    aspecto = salida[0] / salida[1]
    cam = c.get("camara") or {"x": 0.7672, "y": 0.6583, "w": 0.2172, "h": 0.2847, "zoom_cara": 1.7}
    if modo == "cara":
        z = max(1.0, float(cam.get("zoom_cara", 1.0)))
        wf, hf = min(1.0, float(cam["w"]) * z), min(1.0, float(cam["h"]) * z)
        cxc, cyc = float(cam["x"]) + float(cam["w"]) / 2, float(cam["y"]) + float(cam["h"]) / 2
        xf = min(max(0.0, cxc - wf / 2), 1.0 - wf)
        yf = min(max(0.0, cyc - hf / 2), 1.0 - hf)
        rect = (xf * W0, yf * H0, wf * W0, hf * H0)
    elif modo == "explicativa":                                 # tu cámara, centrada, con la proporción VERTICAL del tercio
        aspecto = (salida[0] - _dos_tercios(salida[0])) / salida[1]
        fx, fy, fw, fh = render.explicativa_rect(cam, aspecto, W0, H0)
        rect = (fx * W0, fy * H0, fw * W0, fh * H0)
    elif modo == "pantalla_zoom":
        pz = c.get("pantalla_zoom") or {"x": 0.08, "y": 0.08, "w": 0.66, "h": 0.78}
        pw, ph = min(1.0, max(0.05, float(pz["w"]))), min(1.0, max(0.05, float(pz["h"])))
        px, py = min(max(0.0, float(pz["x"])), 1.0 - pw), min(max(0.0, float(pz["y"])), 1.0 - ph)
        rect = (px * W0, py * H0, pw * W0, ph * H0)
    else:                                                       # «pantalla» y «ambos»: el plano completo
        rect = (0.0, 0.0, float(W0), float(H0))
    return _cubrir(rect, aspecto)


def _imagen_vista(modo: str, salida: tuple[int, int]) -> Image.Image:
    """El recorte de la grabación que se ve en ese modo, a su tamaño en pantalla: el lienzo entero, salvo en
    «explicativa», donde es solo el tercio derecho (el resto lo tapa la tarjeta)."""
    x, y, w, h = vista(modo, salida=salida)
    destino = (salida[0] - _dos_tercios(salida[0]), salida[1]) if modo == "explicativa" else salida
    return base().crop((round(x), round(y), round(x + w), round(y + h))).resize(destino, Image.LANCZOS)


# ------------------------------------------------------------------ lo que se mueve: cursor, boca, etiqueta
def _parche(w: int, h: int, dibuja) -> Image.Image:
    """Dibuja algo pequeño a SS× y lo reduce: bordes suaves sin pagar un lienzo entero en cada fotograma."""
    capa = Image.new("RGBA", (max(1, w) * SS, max(1, h) * SS), (0, 0, 0, 0))
    dibuja(ImageDraw.Draw(capa))
    return capa.resize((max(1, w), max(1, h)), Image.LANCZOS)


def _etiqueta(textos: tuple, marca: dict, minimo: str | None = None) -> Image.Image:
    """La etiqueta de la muestra (NO forma parte del vídeo real: explica qué se está viendo). `minimo` = un
    texto tan ancho como el que más lo sea, para que la etiqueta no «tiemble» de ancho cuando una línea
    cambia de cifras de un fotograma a otro (el zoom que va subiendo en el movimiento suave)."""
    tinta, crema, dorado = _rgb(marca["tinta"]), _rgb(marca["crema"]), _rgb(marca["dorado"])
    f1, f2 = _fuente("Rubik-Black.ttf", 17 * SS), _fuente("Rubik-Medium.ttf", 16 * SS)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lineas = [(textos[0], f1, dorado)] + [(t, f2, crema) for t in textos[1:]]
    anchos = [tmp.textbbox((0, 0), t, font=fo)[2] for t, fo, _ in lineas]
    if minimo:
        anchos.append(tmp.textbbox((0, 0), minimo, font=f2)[2])
    ancho = max(anchos) + 24 * SS
    alto = sum(fo.size + 5 * SS for _, fo, _ in lineas) + 10 * SS

    def dibuja(d):
        d.rounded_rectangle((0, 0, ancho - 1, alto - 1), radius=10 * SS, fill=tinta + (232,))
        yy = 8 * SS
        for t, fo, col in lineas:
            d.text((12 * SS, yy), t, font=fo, fill=col)
            yy += fo.size + 5 * SS
    return _parche(ancho // SS, alto // SS, dibuja)


def _icono_imagen_generica(marca: dict) -> Image.Image:
    """Una foto DE EJEMPLO — un paisaje sencillo dibujado a mano con los colores de la marca, no una
    foto real de Openverse: este módulo no depende de ningún archivo ni llamada de red (ver cabecera).
    En un vídeo real aquí va la foto que trajo Openverse para lo que dijo el presentador."""
    beige, naranja, dorado = (_rgb(marca[k]) for k in ("beige", "naranja", "dorado"))
    W, H = 640, 480
    im = Image.new("RGB", (W, H), beige)
    d = ImageDraw.Draw(im)
    d.ellipse((W - 170, 40, W - 40, 170), fill=dorado)                                 # sol
    oscuro = tuple(max(0, ch - 35) for ch in naranja)
    d.polygon([(0, H), (170, 210), (300, 360), (420, 230), (640, H)], fill=naranja)     # montaña de atrás
    d.polygon([(0, H), (160, 300), (260, 380), (0, H)], fill=oscuro)                    # montaña de delante
    return im


def _imagen_sprite(marca: dict, salida: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """El marco REAL de `imagenes.enmarca` (mismo borde+sombra que las tarjetas) sobre el icono de
    ejemplo, colocado donde lo pega `render.py`: arriba a la derecha, mismo margen que las tarjetas
    (5 % de ancho, 6 % de alto) para que se note que es la esquina opuesta."""
    from . import imagenes as _im
    foto = _icono_imagen_generica(marca)
    img = _im.enmarca(foto, marca, alto_canvas=1080)
    k = salida[0] / 1920.0
    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    margen_x, margen_y = round(salida[0] * 0.05), round(salida[1] * 0.06)
    return img, (salida[0] - margen_x - img.width, margen_y)


class _Ctx:
    """Lo que no cambia de un fotograma a otro: se calcula UNA vez por muestra (el motor es puro en `t`)."""

    def __init__(self, clave: str, salida: tuple[int, int]):
        self.e, self.salida = CATALOGO[clave], salida
        self.marca, self.c = _marca(), _canal()
        self.cfg = config.load()                     # (para el zoom del movimiento suave: `render.ken_burns_ventana`)
        self._vistas: dict = {}
        self._etq: dict = {}
        self.sprite = None
        self.tarjeta = None                          # la tarjeta de término+definición de «explicativa»
        if self.e.seccion == "imagenes":
            self.sprite = _imagen_sprite(self.marca, salida)
        elif self.e.seccion != "escenas":
            self.sprite = _tarjeta_sprite(self.e.extra["tarjeta"], self.marca, salida)
        elif self.e.extra.get("modo") == "explicativa":
            self.tarjeta = tarjetas.dibuja_explicativa(self.e.extra["termino"], self.e.extra["definicion"], self.marca,
                                                       _dos_tercios(salida[0]), salida[1])
        elif self.e.extra.get("modo") == "completa":
            from .ilustraciones import paleta
            self.pal = paleta(self.marca)             # la de la escena a pantalla completa (la MISMA que en el render)

    def vista(self, modo: str):
        if modo not in self._vistas:
            rect = vista(modo, self.c, self.salida)
            img = _imagen_vista(modo, self.salida).convert("RGBA")
            if modo == "explicativa":                # la cara al tercio derecho; el resto, negro a la espera de la tarjeta
                lienzo = Image.new("RGBA", self.salida, (0, 0, 0, 255))
                lienzo.paste(img, (self.salida[0] - img.width, 0))
                img = lienzo
            self._vistas[modo] = (rect, img)
        return self._vistas[modo]

    def etiqueta(self, textos: tuple, minimo: str | None = None) -> Image.Image:
        clave = (textos, minimo)
        if clave not in self._etq:
            self._etq[clave] = _etiqueta(textos, self.marca, minimo)
        return self._etq[clave]

    def boca(self, rect, ph: float, ancho: int | None = None, dx: int = 0):
        """El parche de la boca del presentador (habla: apertura irregular pero periódica, el bucle cierra limpio).
        `ancho` = el ancho en pantalla del recorte `rect` (el lienzo entero, salvo en «explicativa», que es
        el tercio derecho) y `dx` = dónde empieza ese recorte en el fotograma."""
        x0, y0, w, _h = rect
        an = ancho or self.salida[0]
        s = an / w
        cx, cy, cw_, ch_ = _caja_camara(self.c)
        mx, my = (cx + .5 * cw_ - x0) * s, (cy + .535 * ch_ - y0) * s
        if not (-50 < mx < an + 50 and -50 < my < self.salida[1] + 50):
            return None
        ap = 0.12 + 0.88 * abs(math.sin(2 * math.pi * (14 * ph) + 0.9 * math.sin(2 * math.pi * 3 * ph)))
        bw, bh, tinta = 58 * s, max(3.0, 34 * s * ap), _rgb(self.marca["tinta"])
        pw, pa = int(bw) + 10, int(34 * s) + 10

        def dibuja(d):
            d.ellipse(((pw * SS - bw * SS) / 2, (pa * SS - bh * SS) / 2, (pw * SS + bw * SS) / 2, (pa * SS + bh * SS) / 2),
                      fill=(140, 47, 47), outline=tinta, width=max(2, round(4 * s * SS)))
        return _parche(pw, pa, dibuja), (round(mx - pw / 2) + dx, round(my - pa / 2))

    def cursor(self, rect, ph: float):
        """El cursor recorre las métricas pequeñas de la pantalla."""
        x0, y0, w, _h = rect
        s = self.salida[0] / w
        qx = (800 + 330 * math.sin(2 * math.pi * ph) - x0) * s
        qy = (1088 + 100 * math.sin(2 * math.pi * 2 * ph + 1.0) - y0) * s
        if not (0 < qx < self.salida[0] - 24 and 0 < qy < self.salida[1] - 32):
            return None
        k = 3.1 * s
        pts = [(0, 0), (0, 17), (4.5, 13), (8, 20), (11, 18.5), (7.5, 12), (13, 12)]
        tam, tinta = int(21 * k) + 6, _rgb(self.marca["tinta"])

        def dibuja(d):
            d.polygon([(2 * SS + a * k * SS, 2 * SS + b * k * SS) for a, b in pts], fill=(255, 255, 255), outline=tinta,
                      width=max(2, round(2.4 * SS)))
        return _parche(tam, tam, dibuja), (round(qx - 2), round(qy - 2))


# ------------------------------------------------------------------ fotogramas
def _tarjeta_sprite(tj: dict, marca: dict, salida: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """La tarjeta REAL (`tarjetas.dibuja`, pensada para 1080 de alto) reducida al tamaño de la muestra y
    colocada donde la pega `render.py`: abajo a la izquierda, con un margen del 5 % de ancho y el 6 % de alto."""
    img = tarjetas.dibuja(tj, marca, 1080)
    k = salida[0] / 1920.0
    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    return img, (round(salida[0] * 0.05), salida[1] - round(salida[1] * 0.06) - img.height)


def alfa_tarjeta(t: float, ini: float, fin: float) -> float:
    """Opacidad de la tarjeta en `t`: el fundido lineal de `render.py` (`fdur = clamp(dur/4, 0.12, 0.35)`)."""
    fdur = max(0.12, min(0.35, (fin - ini) / 4))
    if t < ini or t >= fin:
        return 0.0
    return max(0.0, min(1.0, min((t - ini) / fdur, (fin - t) / fdur)))


T_TARJETA = 0.6                   # segundo en que entra la tarjeta dentro de la muestra


def duracion(clave: str) -> float:
    e = CATALOGO[clave]
    if e.seccion == "escenas":
        return float(e.extra.get("dur_total", 6.0))
    return T_TARJETA + float(e.extra.get("dur", 3.5)) + 1.4


_NOMBRES_VISTA = {"pantalla": "PANTALLA", "cara": "CARA", "pantalla_zoom": "ZOOM A LA PANTALLA",
                  "explicativa": "EXPLICATIVA", "completa": "A PANTALLA COMPLETA"}

T_ENTRA = 0.6                     # las muestras con movimiento suave y la «explicativa»: segundo en que entra la escena nueva


def _fase(e: Ejemplo, t: float) -> tuple[str, bool, float]:
    """De una muestra de «escenas» en `t`: (el modo que se ve, ¿ya en la escena nueva?, avance 0..1 del movimiento).
    Las clásicas empiezan y acaban en el encuadre nuevo y en medio cortan al anterior; las de movimiento suave y la
    «explicativa» entran a los 0,6 s (un corte, como en el vídeo) y ya no se van: el bucle cierra con OTRO corte, así
    el zoom, que solo va hacia dentro, nunca tiene que «volver» de golpe a mitad de la escena."""
    if e.extra.get("kb") or e.extra.get("modo") in ("explicativa", "completa"):
        en_modo = t >= T_ENTRA
        p = min(1.0, max(0.0, (t - T_ENTRA) / (duracion(e.clave) - T_ENTRA))) if en_modo else 0.0
    else:
        en_modo, p = not (2.2 <= t < 3.8), 0.0
    return (e.extra["modo"] if en_modo else e.extra["antes"]), en_modo, p


def _mueve(fr: Image.Image, p: float, direccion: int, cfg) -> Image.Image:
    """El movimiento suave sobre el fotograma: la MISMA ventana que en el render (`render.ken_burns_ventana`),
    estirada a pantalla completa con interpolación bicúbica de sub-píxel (el mismo tipo de interpolación que
    el filtro `perspective` del render)."""
    v = render.ken_burns_ventana(p, direccion, cfg, fr.width, fr.height)
    if not v:
        return fr
    return fr.transform(fr.size, Image.EXTENT, v, resample=Image.BICUBIC)


def fotograma(clave: str, t: float, salida: tuple[int, int] = SALIDA, ctx: _Ctx | None = None) -> Image.Image:
    """El fotograma de una muestra en el instante `t` (función pura de t)."""
    ctx = ctx or _Ctx(clave, salida)
    e = ctx.e
    T = duracion(clave)
    t = t % T
    ph = t / T
    kb, p = 0, 0.0
    if e.seccion == "escenas":
        actual, en_modo, p = _fase(e, t)
        kb = int(e.extra.get("kb") or 0) if en_modo else 0
        if kb:
            zoom, _pan = render._ken_burns_params(ctx.cfg)
            textos = (_NOMBRES_VISTA[actual] + " · MOVIMIENTO SUAVE", e.dice,
                      f"zoom +{zoom * p * 100:.1f} %  ·  aquí va acelerado")
        else:
            textos = (_NOMBRES_VISTA[actual],) + ((e.dice,) if en_modo else ())
    else:
        actual = "pantalla"
        textos = ((e.extra.get("etq", "DICES"), e.dice) if T_TARJETA - 0.25 <= t < T_TARJETA + float(e.extra["dur"]) else ())
    if actual == "completa":
        # la escena a pantalla completa: EL MISMO dibujo que el render (`escena_completa.fotograma`), desde que entra
        from . import escena_completa as _esc
        x = e.extra
        if x.get("estilo"):                          # capítulo / gráfica / chat / bien-mal / frase (clipper/escenas_estilo.py)
            from . import escenas_estilo as _est
            fr = _est.fotograma(x["estilo"], t - T_ENTRA, T - T_ENTRA, salida[0], salida[1], ctx.pal, x["datos"], x.get("tono", "crema")).convert("RGBA")
        else:
            fr = _esc.fotograma(x["tipo"], t - T_ENTRA, T - T_ENTRA, salida[0], salida[1], ctx.pal, x.get("frase", ""),
                                x.get("tono", "naranja"), float(x.get("lento", _esc.LENTO))).convert("RGBA")
    else:
        rect, imagen = ctx.vista(actual)
        fr = imagen.copy()
        if actual == "explicativa":
            tercio = salida[0] - _dos_tercios(salida[0])
            parche = ctx.boca(rect, ph, ancho=tercio, dx=salida[0] - tercio)
            if parche:
                fr.paste(parche[0], parche[1], parche[0])
            fr.alpha_composite(ctx.tarjeta, (0, 0))   # la tarjeta va DENTRO del tramo: entra y sale con el corte, sin fundido
        else:
            for parche in (ctx.boca(rect, ph), ctx.cursor(rect, ph)):
                if parche:
                    fr.paste(parche[0], parche[1], parche[0])
    if kb:
        fr = _mueve(fr, p, kb, ctx.cfg)               # ANTES de la etiqueta: la etiqueta no es parte del vídeo
    if textos:
        pill = ctx.etiqueta(textos, "zoom +8.8 %  ·  aquí va acelerado" if kb else None)
        fr.paste(pill, (12, 12), pill)
    if ctx.sprite is not None:
        a = alfa_tarjeta(t, T_TARJETA, T_TARJETA + float(e.extra["dur"]))
        if a > 0:
            sprite = ctx.sprite[0].copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda v: round(v * a)))
            fr.alpha_composite(sprite, ctx.sprite[1])
    return fr.convert("RGB")


def _carpeta_previas() -> Path:
    return config.ROOT / "work" / "ejemplos_previas"


def previa(clave: str, salida: tuple[int, int] = SALIDA, fps: int = FPS) -> Path:
    """WebP animado de una muestra, cacheado en work/ejemplos_previas/."""
    if clave not in CATALOGO:
        raise KeyError(clave)
    carpeta = _carpeta_previas()
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{clave}-{clave_cache(clave)}.webp"
    with _candado(clave):                                     # dos peticiones a la vez no dibujan dos veces
        if not destino.is_file():
            ctx = _Ctx(clave, salida)
            t0 = 0.0 if CATALOGO[clave].seccion == "escenas" else 1.6      # el primer fotograma ya enseña la tarjeta
            frames = [fotograma(clave, k / fps + t0, salida, ctx) for k in range(int(round(duracion(clave) * fps)))]
            tmp = destino.with_suffix(f".{os.getpid()}.tmp")
            frames[0].save(tmp, format="WEBP", save_all=True, append_images=frames[1:],
                           duration=int(1000 / fps), loop=0, lossless=False, quality=80, method=3)
            os.replace(tmp, destino)
    return destino


T_POSTER = 1.0                    # «escenas»: el fotograma quieto que se ve ANTES del clic (ya en el plano nuevo)


def poster(clave: str, salida: tuple[int, int] = SALIDA) -> Path:
    """El fotograma QUIETO de una muestra (WebP), cacheado junto a la animación. La galería de «Cambios de
    escena» enseña esto y solo reproduce la animación cuando se le da clic a una muestra: con seis o siete
    animaciones en bucle a la vez, cada una cortando entre planos, no había manera de mirar una tranquilo."""
    if clave not in CATALOGO:
        raise KeyError(clave)
    carpeta = _carpeta_previas()
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{clave}-{clave_cache(clave)}.poster.webp"
    with _candado(clave):
        if not destino.is_file():
            # (la escena a pantalla completa va a cámara lenta: en el segundo 1 su dibujo aún está empezando)
            t = CATALOGO[clave].extra.get("t_poster", T_POSTER) if CATALOGO[clave].seccion == "escenas" else 1.6
            tmp = destino.with_suffix(f".{os.getpid()}.tmp")
            fotograma(clave, t, salida).save(tmp, format="WEBP", quality=88, method=3)
            os.replace(tmp, destino)
    return destino


_CANDADOS: dict[str, Lock] = {}


def _candado(clave: str) -> Lock:
    with _LOCK:
        return _CANDADOS.setdefault(clave, Lock())


def faltan() -> list[str]:
    """Las muestras que aún no están dibujadas en la caché (la animación o su fotograma quieto)."""
    carpeta = _carpeta_previas()
    return [c for c in CATALOGO
            if not (carpeta / f"{c}-{clave_cache(c)}.webp").is_file()
            or not (carpeta / f"{c}-{clave_cache(c)}.poster.webp").is_file()]


def precalienta() -> None:
    """Dibuja las que falten, una a una (para que la galería no espere la primera vez)."""
    for c in faltan():
        for dibuja in (previa, poster):
            try:
                dibuja(c)
            except Exception:                                 # noqa: BLE001 — una que falle no tumba a las demás
                pass


# ------------------------------------------------------------------ pruebas automáticas (corren en «Probar el sistema»)
def prueba(salida: tuple[int, int] = (320, 180)) -> list[dict]:
    """Comprueba que los ejemplos son FIELES al render y no se rompen: encuadres, cambio de escena, fundido de
    las tarjetas, bucle limpio y determinismo. Devuelve [{caso, ok, detalle}]."""
    import numpy as np
    filas: list[dict] = []

    def caso(nombre, ok, detalle=""):
        filas.append({"caso": nombre, "ok": bool(ok), "detalle": str(detalle)})

    c = _canal()
    cam = _caja_camara(c)
    r_full, r_cara, r_zoom = vista("pantalla", c, salida), vista("cara", c, salida), vista("pantalla_zoom", c, salida)
    caso("«pantalla» es el plano completo", r_full == (0.0, 0.0, float(W0), float(H0)), r_full)
    contiene = (r_cara[0] <= cam[0] + 1 and r_cara[0] + r_cara[2] >= cam[0] + cam[2] - 1
                and r_cara[1] <= cam[1] + 1 and r_cara[1] + r_cara[3] >= cam[1] + cam[3] - 1)
    caso("«cara»: 16:9, dentro del lienzo y con toda tu cámara dentro",
         contiene and abs(r_cara[2] / r_cara[3] - 16 / 9) < 0.01 and r_cara[0] >= -1 and r_cara[0] + r_cara[2] <= W0 + 1
         and r_cara[2] < W0 * 0.6, r_cara)
    caso("«zoom a la pantalla»: 16:9, más cerca que el plano completo y SIN tu cámara",
         abs(r_zoom[2] / r_zoom[3] - 16 / 9) < 0.01 and r_zoom[2] < W0 * 0.9 and r_zoom[0] + r_zoom[2] <= cam[0] + 1, r_zoom)

    def dif(a, b, caja=None):
        x = np.asarray(a, dtype=np.int16) if caja is None else np.asarray(a.crop(caja), dtype=np.int16)
        y = np.asarray(b, dtype=np.int16) if caja is None else np.asarray(b.crop(caja), dtype=np.int16)
        return float(np.abs(x - y).mean())

    mitad = (0, salida[1] // 2, salida[0], salida[1])              # la etiqueta va arriba: se compara solo lo de abajo
    for clave in [k for k, e in CATALOGO.items() if e.seccion == "escenas"]:
        e = CATALOGO[clave]
        ctx = _Ctx(clave, salida)
        if e.extra.get("kb") or e.extra["modo"] in ("explicativa", "completa"):
            # línea de tiempo nueva: en `antes` hasta los 0,6 s, corte a la escena, y ya no se va (el bucle cierra con OTRO corte)
            anterior, dentro = fotograma(clave, 0.3, salida, ctx), fotograma(clave, 1.0, salida, ctx)
            caso(f"{clave}: el corte a la escena cambia de verdad lo que se ve",
                 dif(anterior, dentro, mitad) > 12, f"diferencia {dif(anterior, dentro, mitad):.1f}")
            caso(f"{clave}: es determinista (el mismo instante da el mismo fotograma)",
                 fotograma(clave, 3.3, salida, ctx).tobytes() == fotograma(clave, 3.3, salida, ctx).tobytes())
        else:
            antes, en_modo, cierre = fotograma(clave, 1.0, salida, ctx), fotograma(clave, 3.0, salida, ctx), fotograma(clave, 5.95, salida, ctx)
            caso(f"{clave}: el corte cambia de verdad lo que se ve", dif(antes, en_modo, mitad) > 12, f"diferencia {dif(antes, en_modo, mitad):.1f}")
            caso(f"{clave}: el bucle cierra (vuelve al mismo plano)", dif(antes, cierre, mitad) < 9, f"diferencia {dif(antes, cierre, mitad):.1f}")

    # --- la escena a pantalla completa: EL MISMO dibujo que el render, en sus dos fondos (el autor, 2026-09-26) ---
    from . import escena_completa as _esc
    from .ilustraciones import paleta as _paleta
    for clave in ("escena_completa", "escena_completa_crema"):
        x = CATALOGO[clave].extra
        real = _esc.fotograma(x["tipo"], 2.0 - T_ENTRA, duracion(clave) - T_ENTRA, salida[0], salida[1], _paleta(_marca()),
                              x["frase"], x["tono"]).convert("RGB")
        caso(f"{clave}: es EXACTAMENTE el fotograma del render (fuera de la etiqueta del ejemplo)",
             dif(fotograma(clave, 2.0, salida), real, mitad) < 0.5, f"diferencia {dif(fotograma(clave, 2.0, salida), real, mitad):.2f}")
    naranja = np.asarray(fotograma("escena_completa", 5.0, salida).crop(mitad), dtype=np.int16).mean(axis=(0, 1))
    crema = np.asarray(fotograma("escena_completa_crema", 5.0, salida).crop(mitad), dtype=np.int16).mean(axis=(0, 1))
    caso("los dos fondos se distinguen: el del color principal de la marca es otro que el crema, que es claro",
         float(np.abs(naranja - crema).sum()) > 120 and crema.mean() > 190, f"principal {naranja.round()} · crema {crema.round()}")

    # --- movimiento suave: la ventana ES la del render, y se nota de verdad al final pero no al principio ---
    from dataclasses import replace as _replace
    for clave in [k for k, e in CATALOGO.items() if e.extra.get("kb")]:
        e = CATALOGO[clave]
        con, sin = _Ctx(clave, salida), _Ctx(clave, salida)
        sin.e = _replace(e, extra={**e.extra, "kb": 0})            # la MISMA escena, sin el movimiento
        ini = dif(fotograma(clave, T_ENTRA + 0.05, salida, con), fotograma(clave, T_ENTRA + 0.05, salida, sin), mitad)
        fin = dif(fotograma(clave, 5.9, salida, con), fotograma(clave, 5.9, salida, sin), mitad)
        caso(f"{clave}: al entrar la escena el movimiento aún no se nota (igual que sin él), y al final SÍ",
             ini < 3.0 and fin > 3.0 and fin > 8 * ini, f"inicio {ini:.2f} · final {fin:.2f}")
        v = render.ken_burns_ventana(1.0, int(e.extra["kb"]), con.cfg, salida[0], salida[1])
        zoom, _pan = render._ken_burns_params(con.cfg)
        caso(f"{clave}: al final la ventana es 1/(1+zoom) del fotograma, la MISMA fórmula que el render",
             v is not None and abs((v[2] - v[0]) - salida[0] / (1 + zoom)) < 1e-6 and 0 <= v[0] and v[2] <= salida[0], v)

    # --- «explicativa»: la tarjeta va dentro del tramo (corte seco, SIN fundido — no pasa por negro) y la cara al tercio derecho ---
    e_ex = "escena_explicativa"
    ctx_ex = _Ctx(e_ex, salida)
    crema = _rgb(ctx_ex.marca["crema"])
    esq = (3, salida[1] - 4)                                       # esquina inferior izquierda: solo la tarjeta llega ahí
    for t_ex, que in ((T_ENTRA + 0.02, "justo al entrar (sin fundido desde negro)"), (3.0, "a mitad"),
                      (5.98, "justo antes de salir (sin fundido a negro)")):
        px = fotograma(e_ex, t_ex, salida, ctx_ex).getpixel(esq)
        caso(f"{e_ex}: el panel izquierdo es la tarjeta crema {que}",
             max(abs(a - b) for a, b in zip(px, crema)) <= 2, f"{px} vs {crema}")
    fr_ex = fotograma(e_ex, 3.0, salida, ctx_ex)
    tercio = salida[0] - _dos_tercios(salida[0])
    caso(f"{e_ex}: la cara ocupa el tercio derecho (distinto de la tarjeta) y la tarjeta los otros dos tercios",
         dif(fr_ex.crop((salida[0] - tercio, salida[1] // 3, salida[0], salida[1])),
             Image.new("RGB", (tercio, salida[1] - salida[1] // 3), crema)) > 15 and tercio == salida[0] - max(2, int(salida[0] * 2 / 3) // 2 * 2),
         f"tercio={tercio}px")
    cx_e, cy_e, cw_e, ch_e = _caja_camara(c)
    rx, ry, rw, rh = vista("explicativa", c, salida)
    caso(f"{e_ex}: la ventana que se ve es tu cámara CENTRADA (dentro de su caja, con proporción vertical)",
         rx >= cx_e - 1 and rx + rw <= cx_e + cw_e + 1 and ry >= cy_e - 1 and ry + rh <= cy_e + ch_e + 1
         and abs((rx + rw / 2) - (cx_e + cw_e / 2)) < 2 and rw < rh, (rx, ry, rw, rh))

    # --- el fotograma quieto (poster) y la animación se cachean aparte; `faltan()` pide las dos cosas ---
    import shutil
    import tempfile
    from unittest.mock import patch
    tmp_ej = Path(tempfile.mkdtemp(prefix="zz_ejemplos_"))
    try:
        with patch("clipper.ejemplos._carpeta_previas", lambda: tmp_ej):
            k = "escena_pantalla_kb"
            caso("sin nada en la caché, `faltan()` lista la muestra", k in faltan())
            pp = poster(k, (160, 90))
            caso("con solo el fotograma quieto hecho, `faltan()` la sigue pidiendo (le falta la animación)", k in faltan())
            pa = previa(k, (160, 90), fps=2)
            caso("con las dos cosas hechas, ya no falta", k not in faltan())
            with Image.open(pp) as im_p, Image.open(pa) as im_a:
                caso("el poster es UN solo fotograma (quieto) y la animación tiene varios",
                     getattr(im_p, "n_frames", 1) == 1 and getattr(im_a, "n_frames", 1) > 1,
                     (getattr(im_p, "n_frames", 1), getattr(im_a, "n_frames", 1)))
    finally:
        shutil.rmtree(tmp_ej, ignore_errors=True)
    for clave in [k for k, e in CATALOGO.items() if e.seccion != "escenas"]:
        e = CATALOGO[clave]
        ctx = _Ctx(clave, salida)
        sin = _Ctx(clave, salida)
        sin.sprite = None
        sprite, (px, py) = ctx.sprite
        caja = (px, py, px + sprite.width, py + sprite.height)
        cabe = px >= 0 and py >= 0 and px + sprite.width <= salida[0] and py + sprite.height <= salida[1]
        if e.seccion == "imagenes":
            caso(f"{clave}: la imagen cabe dentro del fotograma y va ARRIBA a la derecha (para no chocar con las tarjetas)",
                 cabe and px > salida[0] * 0.45 and py < salida[1] * 0.1, f"{caja}")
        else:
            caso(f"{clave}: la tarjeta cabe dentro del fotograma y va abajo a la izquierda",
                 cabe and px < salida[0] * 0.1 and py > salida[1] * 0.45, f"{caja}")
        dur = float(e.extra["dur"])
        ts = [T_TARJETA - 0.1, T_TARJETA + 0.08, T_TARJETA + 0.2, T_TARJETA + 0.4, T_TARJETA + dur / 2, T_TARJETA + dur + 0.1]
        vals = [dif(fotograma(clave, t, salida, ctx), fotograma(clave, t, salida, sin), caja) for t in ts]
        caso(f"{clave}: la tarjeta NO está antes, entra con fundido, se mantiene y desaparece",
             vals[0] < 0.01 and 0.01 < vals[1] < vals[2] <= vals[3] + 0.5 and vals[4] > vals[1] and vals[5] < 0.01,
             " · ".join(f"{v:.1f}" for v in vals))
    a1, a2 = fotograma("tarjeta_punto", 1.234), fotograma("tarjeta_punto", 1.234)
    caso("es determinista: el mismo instante da el mismo fotograma", a1.tobytes() == a2.tobytes())
    caso("el fundido es el de render.py (lineal, entre 0,12 y 0,35 s)",
         alfa_tarjeta(0.6, 0.6, 4.1) == 0 and abs(alfa_tarjeta(0.6 + 0.175, 0.6, 4.1) - 0.5) < 0.01
         and alfa_tarjeta(2.0, 0.6, 4.1) == 1.0 and alfa_tarjeta(4.1, 0.6, 4.1) == 0.0)
    caso("todas las muestras tienen su texto (cuándo / qué se ve)",
         all(e.cuando and e.que_hace and e.dice for e in CATALOGO.values()))
    return filas
