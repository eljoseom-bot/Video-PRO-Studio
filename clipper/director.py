"""El "director" del generador de VÍDEOS horizontales — SOLO vídeos, nada de
esto lo usan los Shorts ni lo toca.

Un Short sale de un pico de emoción; un vídeo largo de Mi canal es un
tutorial: la edición que le hace falta es otra. Este módulo le hace a Gemini
UNA sola llamada por vídeo (barato, y así puede razonar las cuatro cosas a la
vez en vez de contradecirse) pidiéndole que juegue de editor:

  1. `recortes`   — qué tramos son relleno/muletilla y sobran.
  2. `camara`     — cuándo el encuadre debe ir a tu CARA y cuándo a la
                    PANTALLA (según lo que estés diciendo).
  3. `tarjetas`   — títulos de tema, puntos numerados, herramientas
                    nombradas, definiciones: lo que reemplaza al subtítulo.
  4. `imagenes`   — momentos que piden una imagen ilustrativa (una analogía,
                    un objeto concreto) y qué buscar para ilustrarlos.

TODO opt-in: si `videos.director_activo` es false (por defecto), esto no se
llama nunca. Mientras esté en desarrollo, `pipeline.process_video()` solo
CACHEA y REGISTRA el plan — el renderizado (tarjetas/zoom de verdad) se
engancha después, cuando el plan ya se haya visto sobre un vídeo real de El
tu canal (con el de MS solo se puede probar que el JSON sale bien formado, no
si las decisiones son buenas: son dominios distintos).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .util import call_gemini_cadena, log, nombre_modelo, normalize

# Por qué falló el ÚLTIMO análisis (para que el panel lo enseñe): None si
# funcionó. Lo lee pipeline.py justo después de llamar a analiza().
ultimo_fallo: dict | None = None

_DIRECTOR_V = 1

_SYSTEM = """Eres un editor de vídeo profesional para YouTube, trabajando para el
canal "{canal}". De qué trata, a quién le habla, su tono y sus reglas propias
están en el "Contexto del canal" del mensaje: tómalo como la verdad del canal.
El formato es cámara del presentador + pantalla de su PC (captura de
escritorio, navegador o juego), grabados a la vez, lado a lado o con la cámara
en una esquina. Respondes SOLO con JSON válido, sin markdown."""

# Las reglas de los títulos sugeridos: las usa el director (dentro de `_PROMPT`) y «↻ Otros 3 títulos»
# (`clipper/titulos.py`), para que un set nuevo siga EXACTAMENTE las mismas reglas que el primero.
REGLAS_TITULOS = """   - "titulos_sugeridos": EXACTAMENTE 3 títulos para YouTube, en español,
     máximo 65 caracteres cada uno, ordenados del MEJOR al tercero (para
     hacer A/B/C test). Los 3 enganchan SIN mentir ni ser sensacionalistas
     vacíos — el público del canal busca contenido de verdad. Nada de emojis al final. Que sean
     genuinamente distintos entre sí, no variaciones de la misma frase:
     prueba ángulos distintos (beneficio directo, pregunta/curiosidad,
     número o lista...).
"""

_PROMPT = """Aquí tienes la transcripción de un tramo de un vídeo tutorial
sobre: "{tema}". Contexto del canal: {contexto}

La transcripción trae marcas [+123.4s] = segundo exacto desde el inicio del
DIRECTO (no del tramo). Úsalas: TODOS los tiempos que devuelvas deben ser de
esa misma escala, sacados de una marca real o interpolados entre dos marcas
cercanas. No inventes segundos que no puedas justificar con el texto de al
lado.

ANCLAJE (para que nada salga antes de tiempo): en CADA tarjeta, imagen e
ilustración añade "cita" = de 3 a 8 palabras COPIADAS TAL CUAL de la
transcripción (con sus errores de oído, sin corregir, resumir ni traducir) que
empiezan justo donde el presentador NOMBRA o empieza a explicar lo que ese
elemento muestra. El programa busca esa cita en el audio y pone el elemento en
el segundo exacto de esas palabras: apunta "t" lo mejor que puedas, pero lo que
de verdad cuenta es copiar bien la cita. Si la cita empieza antes de lo que se
muestra (el principio de la frase en vez del nombre), el elemento saldrá
adelantado.

REGLA #1, ANTES QUE CUALQUIER OTRA COSA — LEE ESTO PRIMERO: si la
transcripción empieza con una marca "[ATENCIÓN: ... silencio puro ... ]",
ESE tramo exacto (los segundos que la propia marca te da) va SIEMPRE en
"recortes", tal cual la marca lo dice. Es la regla más importante de
todas — revísala antes de pensar en cámara, tarjetas o nada más. NO la
confundas con la primera frase que SÍ dice algo justo después de la marca
— esa frase es el gancho del vídeo, contenido real, y NO se toca — SALVO
que sea justo el caso de "ARRANQUE CON VARIAS TOMAS" de más abajo (una
toma de arranque abandonada y repetida mejor poco después): ahí SÍ se
corta entera, saludo incluido, y el gancho real pasa a ser el de la toma
buena. El vídeo tiene que EMPEZAR ya con el presentador hablando con
energía — ni un segundo de silencio ni una toma de arranque abandonada
antes de eso: es lo que más importa para el engagement en los primeros
segundos.

LO QUE SE VE EN EL VÍDEO: {vision}

Haz OCHO trabajos a la vez sobre este tramo:

1) RECORTES (campo "recortes"): tramos que son relleno y deberían cortarse
   del vídeo final — muletillas repetidas ("o sea", "eh", "bueno entonces"),
   silencios largos, divagaciones que no aportan, arranques en falso ("no,
   espera, mejor te explico así..." cuando lo de antes se descarta). NO
   marques como relleno una pausa normal para respirar ni un silencio breve
   mientras hace algo en pantalla que SÍ se está explicando. Sé conservador:
   ante la duda, no lo marques — es peor cortar algo útil que dejar 2 s de
   más.
   ARRANQUE CON VARIAS TOMAS: cerca del inicio es muy común que el
   presentador salude o arranque, se quede callado un momento, y vuelva a
   saludar/arrancar OTRA vez — y esta segunda vez ya sigue fluido, sin más
   cortes, hasta bien entrado el vídeo. Cuando pase eso, TODA la primera
   toma (el saludo/arranque abandonado Y el silencio de alrededor) va en
   "recortes" — el vídeo debe empezar directo en la toma que de verdad
   continúa. No hace falta que las dos tomas usen casi las mismas palabras
   (a diferencia de "tomas repetidas" de más abajo, que sí lo exige): aquí
   basta con reconocer el patrón — un intento de arranque, un corte/pausa,
   y otro intento que esta vez sí sigue. Ante la duda de cuál es la buena,
   la buena es la que SIGUE hablando fluido después, sin otro parón. El
   presentador suele hacer VARIAS tomas falsas seguidas (dos o tres) antes
   de la buena: van TODAS en recortes.
   Lo que el programa midió del arranque (con la transcripción, sin IA):
   {tomas_arranque}
   ARRANQUE CON TROPIEZO (no es una toma nueva): si dice una o dos palabras
   sueltas, se calla y CONTINÚA la MISMA frase («Una… de las cosas que odio
   es…»), esas palabras SON parte de la frase: NO las pongas en recortes
   (sin ellas la frase queda coja: «de las cosas que odio…»). El silencio de
   en medio lo acorta el programa solo, midiendo el audio. Solo si REPITE
   esas palabras («Una… Una de las cosas…») la primera vez va en recortes.
   Tampoco hace falta que afines el primer segundo: el programa arranca el
   vídeo justo antes de la primera sílaba midiendo el audio (los segundos de
   la transcripción pueden ir ~1 s desviados justo al salir de un silencio).
   TOMAS REPETIDAS: lo mismo pero en CUALQUIER punto del vídeo (no solo al
   arrancar): es muy común que el presentador empiece a explicar algo, no
   le guste cómo le salió, y lo vuelva a explicar DESDE CERO poco después
   (mismo tema, casi las mismas palabras, una versión claramente mejor que
   la otra). Cuando eso pase, la toma MALA completa (no solo el silencio de
   alrededor) va en "recortes" — deja solo la versión buena. Es distinto de
   una repetición para dar énfasis (esa sí se queda).
   PAUSAS DE DUDA A MEDIA FRASE (las acorta el programa; tú solo decides
   cuáles NO): el programa midió en el audio las pausas de más de 0,7 s que
   caen en MEDIO de una frase (justo después de «de», «la», «que», «pero»,
   «eh»…: el presentador buscando la palabra) y las deja en 0,3 s él solo,
   sin cortar ninguna sílaba. NO las pongas tú en "recortes". Si alguna es
   INTENCIONAL — suspense justo antes de un dato o de un remate («y el
   resultado fue que… [pausa] ¡el triple!»), una pausa para que el
   espectador asimile algo importante, o el tiempo de algo que está pasando
   en pantalla —, pon su id (p. ej. "p123.4") en "pausas_enfasis" y se queda
   tal cual. Una duda normal («la clave de… [pausa] todo esto», «que eh…
   [pausa] Google») NO es intencional: esa se acorta. Las pausas ENTRE
   frases no están en la lista y el programa no las toca. Las medidas:
   {pausas_duda}
   Cada uno: {{"inicio": 123.4, "fin": 128.0, "motivo": "breve"}}.

2) CÁMARA (campo "camara"): para cada tramo con habla, di qué encuadre debe
   mostrarse: CARA del presentador, PANTALLA, PANTALLA CON ZOOM, o AMBOS.

   REGLA SIN EXCEPCIÓN, la más importante de este punto: si el presentador
   dice literalmente "acá" o "aquí" seguido de un verbo de mostrar (ven,
   vean, ve, miren, mira, pueden ver, está, hay, sale) — el modo es
   "pantalla" (o "pantalla_zoom", ver abajo) DESDE ESA PALABRA, sin
   excepción, aunque un segundo antes estuviera opinando en modo "cara".
   Esto pasa TODO EL TIEMPO en un tutorial y es el error más caro: el
   espectador oye "miren esto" y ve tu cara en vez de la pantalla.
   - "pantalla": está mostrando/explicando algo QUE SE VE en su PC, a
     tamaño normal. Frases que SIEMPRE son pantalla (además de la regla de
     arriba): "denle clic aquí", "esto que ven en la pantalla", nombra un
     botón/menú/campo concreto, "acá se suscriben", "acá abajo", "denle
     like/suscríbete acá", "vamos a [nombre de herramienta/sitio]".
   - "pantalla_zoom": lo mismo que "pantalla", pero además el detalle que
     señala es PEQUEÑO o está lejos del centro (un número en una tabla, un
     botón chico, un campo de texto) y de verdad conviene acercarse para
     que se lea bien. ÚSALO CON MUCHA MODERACIÓN — la mayoría de las veces
     "pantalla" normal ya se ve bien; "pantalla_zoom" es la excepción para
     cuando el detalle señalado se perdería sin acercarse.
   - "cara": está opinando, contando algo personal, o explicando un
     concepto SIN necesitar mostrar nada ("esto es importante entenderlo
     bien", una reflexión, una anécdota).
   - "ambos": no está claro, o alterna rápido — dos tomas (plano general).
   - "explicativa": está dedicando una explicación GENUINA y EXTENSA a
     definir o explicar QUÉ ES un término o concepto concreto (una sigla,
     una herramienta, una idea técnica) — no solo lo nombra de pasada
     dentro de una frase, se DETIENE a explicarlo con una definición que
     cabe en una frase corta. Ejemplos que SÍ son "explicativa": "¿qué es
     el SEO? es...", "un CMS es básicamente...", "eso se llama indexación,
     que es cuando...". Ejemplos que NO lo son: nombrar la herramienta de
     pasada ("uso Gemini para esto"), o cuando ya está mostrando algo en
     pantalla relacionado (eso es "pantalla", nunca los dos a la vez).
     Úsalo con MUCHA moderación: solo cuando de verdad hay un término que
     se beneficia de quedar escrito en pantalla, no para cada palabra
     técnica que se menciona — un vídeo típico tiene 0 a 3 momentos así,
     no más.
     Trae dos campos más, SOLO en este modo: "termino" (corto — una sigla
     o 2-3 palabras, ej. "SEO") y "definicion" (UNA frase clara y corta,
     que quepa en una tarjeta, no un párrafo).
   No hace falta cubrir cada segundo: solo pon un tramo NUEVO cuando el
   encuadre debería CAMBIAR respecto al anterior. A lo largo de TODO el
   vídeo, procura usar los modos que el contenido pida — un vídeo real casi
   siempre tiene momentos de opinión (cara) y de mostrar algo (pantalla);
   "pantalla_zoom", "ambos" y "explicativa" son más raros, úsalos solo
   cuando de verdad aplican, nunca para "rellenar variedad".
   Cada uno: {{"inicio": 12.0, "fin": 40.0,
   "modo": "pantalla|pantalla_zoom|cara|ambos|explicativa"}} — si "modo" es
   "explicativa", añade también "termino" y "definicion"; para los demás
   modos NO los incluyas.

   RETENCIÓN: prioridad alta (después de la regla #1 de arriba sobre el
   arranque). Un vídeo estático (misma cámara varios minutos seguidos, sin
   tarjeta) pierde espectadores. Tras poner las tarjetas (punto 3), revisa
   los tramos donde NO cae ninguna tarjeta: si un tramo así dura más de
   ~45-60s con la MISMA cámara, casi siempre hay una razón real para
   cambiarla (pasó de opinar a mostrar algo, o viceversa) — búscala y
   pon el cambio. No es forzar cortes porque sí ni saturar de movimiento
   (nada de cambiar cada 5s sin motivo: eso también cansa) — es que CADA
   minuto del vídeo tenga AL MENOS una tarjeta o un cambio de cámara que
   se justifique con lo que se dice en ese momento, para que el vídeo se
   sienta vivo sin sentirse editado a la fuerza.

   SEÑAL DE ACTIVIDAD EN PANTALLA (detectada por código analizando el vídeo
   mismo, no algo que tú infieras del texto): estas ventanas de tiempo
   tuvieron movimiento SOSTENIDO y real en la pantalla (cursor moviéndose,
   scroll, algo cambiando) — {actividad_pantalla}
   Si alguna de esas ventanas cae en un tramo donde el texto no deja claro
   si debería ser cara o pantalla, o el modo que ibas a poner ahí es
   "cara", esa actividad es una señal fuerte de que se está mostrando o
   señalando algo — usa "pantalla", o "pantalla_zoom" si además es un
   detalle concreto que conviene ver de cerca. NO la uses para forzar un
   cambio donde el CONTENIDO claramente pide cara (una reflexión, una
   anécdota) solo porque la mano rozó el mouse sin querer. Si arriba tienes
   «LO QUE SE VE EN EL VÍDEO» (otra IA lo miró), eso manda sobre esta señal
   y sobre lo que deduzcas solo del texto.

3) TARJETAS (campo "tarjetas"): momentos que merecen una tarjeta animada en
   pantalla en vez de depender solo de la voz. Cada tipo usa CAMPOS
   DISTINTOS — no los mezcles ni dejes "texto" cargando con todo:
   - "punto": OBLIGATORIO, sin excepción, cuando el presentador enumera un
     argumento o proceso de forma numérica u ordinal — "primero... segundo...
     tercero...", "el punto uno es...", "paso 1, paso 2...", "una: (razón),
     dos: (razón)...". Si detectas ese patrón (dos o más puntos seguidos de
     la misma enumeración), CADA punto lleva su propia tarjeta — no falta
     ni una. "texto" = el punto en pocas palabras. "numero" (1, 2, 3...)
     siempre que exista esa numeración.
     LISTAS SIN NÚMEROS, igual de OBLIGATORIO: si nombra seguidas 3 o más
     cosas de la MISMA clase (formas de optimizar, errores, pasos, factores,
     herramientas para lo mismo...) aunque NO diga «primero/segundo»
     — ej. real: «…temas de textos alternativos, temas de tiempo de carga de
     esas imágenes… y básicamente incluir contenido gráfico, de imágenes, de
     vídeos» —, cada una es una tarjeta "punto", numeradas 1, 2, 3 en el
     orden en que las dice, cada una en el segundo en que empieza a decirla,
     con SUS palabras resumidas ("Textos alternativos", "Tiempo de carga de
     las imágenes", "Contenido visual y vídeo").
   - "herramienta": OBLIGATORIO, sin excepción, cada vez que nombra una
     herramienta, web, producto, empresa o servicio digital CONCRETO por su
     nombre propio — no solo las del tema principal del canal: también hosting, CMS,
     editores de código, IA, diseño, automatización, redes, lo que sea
     (ejemplos reales: "Google Search Console", "Ahrefs", "Cloudflare",
     "WordPress", "Figma", "Claude", "un plugin de X"). El listón es bajo:
     ante la duda de si algo cuenta como herramienta, SÍ pon la tarjeta —
     es peor omitir una mención real que poner una tarjeta de más. "texto"
     = el nombre EXACTO tal cual se debe mostrar. No lleva "extra". La
     transcripción es automática y los nombres propios pueden venir MAL
     OÍDOS («Cloud» por «Claude», «Shares Console» por «Search Console»,
     «Vive Coding» por «Vibe Coding», «HGPT» por «ChatGPT»): deduce por el
     contexto cuál es y escríbelo BIEN — aquí y en títulos, capítulos y
     descripción.
   - "definicion": define un término técnico propio del tema del canal. "texto" = SOLO el
     término (2-4 palabras, ej. "Vibe Coding"). "extra" = la explicación
     corta del término (una frase). NUNCA metas el término y la explicación
     juntos en "texto" — van en campos separados porque se dibujan en dos
     líneas de tamaño distinto.
   - "dato": una cifra o estadística que dice en voz alta. "texto" = SOLO
     el número/cifra (ej. "73%", "2,5 M", "3,6"). "extra" = la frase corta que da
     contexto a esa cifra (ej. "de los clics van a los primeros 3
     resultados"). Igual que en "definicion": NUNCA los juntes en "texto".
     Los decimales, con COMA como se escribe en español ("3,6"), aunque la
     transcripción traiga punto ("3.6").
     ¿DATO o ESTIMADO? Mira cómo lo dice: si la cifra es un estimado u
     opinión suya y no un dato comprobable (una medida, un estudio, lo que
     marca una herramienta) — «muy posiblemente casi que el 90 %», «yo diría
     que la mitad», «unos 100 mil», «a ojo» —, pon "estimado": true (saldrá
     como «≈90%» y «Mi estimación», para que no parezca un dato inventado).
     Si ni siquiera aporta como estimado, no pongas la tarjeta.
   No pongas una tarjeta por cada frase — solo los momentos que de verdad
   ayudan a seguir el hilo. Como referencia, 1 tarjeta cada 45-90 s de vídeo
   hablado es un ritmo normal; si el vídeo es corto pon pocas, si es largo
   pon más, pero siempre proporcional a esa cadencia — nunca satures.
   Cada una: {{"t": 45.0, "tipo": "punto|herramienta|definicion|dato",
   "numero": 1, "texto": "Google Search Console", "extra": "", "duracion": 3.5,
   "cita": "abrimos Google Search Console"}}
   ("numero" solo si tipo es "punto"; "extra" solo si tipo es "definicion"
   o "dato"; "estimado" solo en "dato" — en los demás casos, omítelos o
   ponlos vacíos). Dos tarjetas seguidas NO conviven: cuando entra la
   siguiente, el programa quita la anterior.

4) IMÁGENES (campo "imagenes"): SOLO cuando el presentador hace una
   ANALOGÍA o menciona algo concreto y visualizable que NO está en su
   pantalla (un ejemplo, una comparación, un objeto, una persona/empresa
   conocida). "busqueda" = 2-4 palabras EN INGLÉS para buscar la imagen (el
   banco de imágenes es en inglés). "alternativas" = 1 o 2 búsquedas MÁS
   GENERALES en inglés por si la primera no encuentra ninguna foto libre (el
   banco es limitado: «albino bear in antarctica» no da nada, «polar bear» o
   «bear» sí) — de más concreta a más general, siempre algo que siga
   sirviendo para lo que se dice. No abuses: esto es la excepción, no la
   sirviendo para lo que se dice. No abuses: esto es la excepción, no la
   norma — la mayoría del vídeo ya tiene la pantalla o la cara.
   La imagen tiene que ser FOTOGRAFIABLE: un objeto, animal, lugar o escena
   concreta que alguien pueda fotografiar. "busqueda" describe lo que SE VE
   en la foto (sujeto + un detalle: «cracked phone screen», «snail on road»),
   no la idea. Las ideas abstractas («seo expert», «frustrated user», «slow
   website», «registro de dominio») no tienen foto: el banco devuelve
   infografías o gente al azar; para ESAS usa una ilustración animada (punto
   8) o ninguna. Y NO pongas imagen a una frase que el presentador da solo
   como EJEMPLO de lo que alguien teclea en Google («camisetas negras» en una
   lista de palabras clave): es un ejemplo de búsqueda, no algo que enseñar.
   Las fotos que salgan las MIRA después otra IA y descarta las que no
   ilustran: mejor pocas imágenes y claras.
   Cada una: {{"t": 80.0, "busqueda": "crying baby", "alternativas":
   ["crying child", "baby"], "duracion": 3.0, "motivo": "breve",
   "cita": "como un bebé llorando"}}

Si un campo no aplica a nada de este tramo, devuélvelo como lista vacía.

5) TÍTULOS Y MINIATURAS PARA A/B TEST (campos "titulos_sugeridos",
   "miniatura_prompts", "miniatura_titulares" y "miniatura_caras"): {instruccion_titulo}
""" + REGLAS_TITULOS + """   - "miniatura_prompts": EXACTAMENTE 3 descripciones EN INGLÉS (5-7 frases
     cada una) que el autor pega tal cual en un generador de imágenes para
     obtener la MINIATURA DE YOUTUBE de este vídeo. Son las 3 variantes de
     su A/B/C test de miniaturas, en este ORDEN FIJO: #1 = A, #2 = B, #3 = C.
     En un A/B test, si las variantes se parecen el test no enseña nada: las
     3 tienen que verse DISTINTAS de un vistazo aunque se vean diminutas —
     otra composición, otra escena, otro objeto protagonista, otro titular y (B) otro color de fondo
     (si A es un portátil, B y C no son otro portátil, monitor ni móvil).
     Cada prompt lleva SIEMPRE lo siguiente, en este orden (lo más
     importante va PRIMERO, por si el texto se recorta):
       * ARRANCA declarando qué es: cada prompt empieza con algo como
         "YouTube thumbnail, 16:9 widescreen (1920x1080), eye-catching,
         scroll-stopping, extremely clickable, bold high-contrast look" —
         dilo explícito en los 3, nunca lo des por sobreentendido.
       * COMPOSICIÓN — CADA VARIANTE TIENE LA SUYA (regla obligatoria).
         el autor pega su cara recortada encima en Photoshop: su cara sale en
         TODAS las miniaturas (es su marca), así que cada formato deja una
         ZONA DE LA CARA donde no va nada importante (lo que quede ahí se
         tapa):
           - A (#1) · CARA A LA DERECHA: lo importante va a la IZQUIERDA y
             al centro-izquierda (≈60 % del ancho); la DERECHA (≈35-40 %)
             solo lleva fondo poco importante (trama oscura, ráfagas,
             chispas).
           - B (#2) · CARA EN EL CENTRO: composición SIMÉTRICA — elementos a
             la IZQUIERDA y a la DERECHA, idealmente un contraste del tema
             (antes/después, problema/solución, lo viejo frente a lo nuevo,
             una herramienta frente a otra), y la franja CENTRAL (≈ un tercio
             del ancho) sin nada importante: ahí va la cara, entre los dos
             lados.
           - C (#3) · MINIMALISTA: UN solo elemento protagonista grande (y,
             como mucho, 1-2 detalles pequeños), TODO AGRUPADO en un
             bloque compacto en los ≈2/3 izquierdos, sobre un fondo liso y
             tranquilo (tinta o crema, con una trama muy suave) con mucho
             aire. A la DERECHA (≈30 %) queda el hueco de la cara: no tiene
             que estar vacío (el fondo sigue), pero nada importante.
         Piensa cada escena ya para su formato, pero NO escribas tú la frase
         de la zona de la cara: el sistema añade al final de cada prompt la
         exacta de su formato (igual que la paleta).
       * COLORES EXACTOS DE LA MARCA — REGLA OBLIGATORIA en los 3: la
         miniatura usa SOLO esta paleta (nombre y hex exacto):
         {colores_marca}. Por lo general: naranja = color protagonista y de
         las ráfagas/destellos, tinta = fondo (en A y C) y contornos, crema = blancos
         y zonas claras, dorado = detalles muy pequeños. NO escribas tú la
         lista de colores ni los códigos hex: el sistema los añade exactos
         al final de cada prompt. Lo tuyo es que la ESCENA no use ningún
         color fuera de la paleta — nada de rojo, verde ni azul (una X que
         tacha algo o un check también van en naranja/crema/dorado/tinta),
         y las herramientas se representan con un PICTOGRAMA sencillo
         de esta paleta (nunca con su logo oficial: lleva letras).
       * LLAMATIVAS Y CON OTROS COLORES DE FONDO (regla obligatoria): las
         miniaturas tienen que ser LLAMATIVAS: que se distingan entre cientos
         en el feed, con colores saturados, mucho contraste y un elemento
         enorme y claro. Hoy casi todas salen con fondo negro/tinta (funciona
         muy bien) y en un A/B/C test una variante tiene que PROBAR OTROS
         COLORES. Por eso A y C siguen con el fondo tinta de siempre, pero
         B lleva un FONDO DE COLOR de la marca: naranja intenso, dorado o
         dividido en diagonal entre los dos, y la escena JUEGA con ese color
         (el protagonista y sus contornos en tinta o crema para que no se
         pierdan contra el fondo; sin salirte de la paleta de la marca).
         Elige el color que mejor encaje con el tema (urgencia o error:
         naranja; dinero, resultado o éxito: dorado; antes/después o
         problema/solución: dividido) y escríbelo explícito en el prompt de B
         ("flat vivid orange background", "flat gold background"...). En B NO
         escribas "dark", "black" ni "ink background": su fondo no es negro.
       * ENFOCADA EN EL TEMA CONCRETO DE ESTE VÍDEO: primero decide de qué
         va DE VERDAD este vídeo (mira la transcripción) y construye la
         escena con elementos visuales CONCRETOS de lo que se habla — las
         herramientas o marcas nombradas, los objetos, la idea central y el
         resultado o contraste que promete (antes/después, caro/barato,
         lento/rápido...). Nada genérico que valdría para cualquier vídeo
         del canal (una lupa, una bombilla, un portátil suelto) salvo que sea
         justo el tema.
       * Ilustración tipo CÓMIC/caricatura, no foto realista — trazo
         cartoon con contornos gruesos de tinta, expresión y gesto
         exagerados, al estilo de las miniaturas de MrBeast: llama la
         atención y se entiende de un vistazo aunque se vea diminuta.
       * ESTÉTICA de cómic ANTIGUO: trama de puntos tipo Ben-Day, alto
         contraste, ráfagas/líneas de explosión detrás del elemento
         protagonista. En A y B: UN elemento protagonista GRANDE y claro (lo
         primero que ve el ojo, con el naranja de la marca dominando). A es la
         versión COMPLETA y cargada, como las miniaturas que el autor ya publica:
         alrededor del protagonista, 6-10 elementos del tema en capas
         (pantallas o móviles con gráficas y flechas, pictogramas de las
         herramientas que se nombran, iconos en círculos, rayos,
         insignias, una mascota simpática). B lleva 3-5 elementos secundarios
         en tinta/crema con trama, repartidos entre los dos lados. En C, el mismo cómic pero LIMPIO:
         el protagonista naranja y casi nada más, sin ráfagas por todo el
         fondo. Ese contraste protagonista-naranja vs. resto sobrio es la
         técnica central: menciónalo explícitamente en cada uno de los 3
         prompts.
       * NINGÚN TEXTO DENTRO DE LA IMAGEN (regla obligatoria en los 3): los
         generadores de imágenes escriben mal las letras (salen palabras
         deformadas) y eso arruina la miniatura. Por eso NINGÚN prompt pide
         ni nombra texto: ni rótulo, banner o titular, ni palabras o cifras
         entre comillas, ni siglas en mayúsculas, ni el nombre de una
         herramienta junto a su logo; las pantallas, páginas, libros,
         carteles e insignias de la escena muestran SOLO formas, iconos y
         gráficas, sin escritura. El TITULAR lo escribe el programa encima,
         con la letra del canal y bien legible: tú solo propones su texto en
         "miniatura_titulares" (más abajo) y dejas en la escena el hueco
         para él, sin nada importante: en A arriba a la izquierda; en B
         arriba, a lo ancho del centro; en C arriba a la izquierda, sobre
         el bloque (el sistema añade la frase exacta de ese hueco, igual
         que la de la cara).
       * REFERENCIA de un estilo que al autor le gustó (adáptala al tema, no
         la copies literal): fondo tinta con trama, un objeto grande que
         muestra el tema en pantalla (p. ej. una laptop con el sitio y su
         código), 2-3 pictogramas de las herramientas nombradas, una
         mascota de cómic simpática, insignias pequeñas (sin letras) y un
         hueco libre arriba para el titular. SOLO A (#1) puede seguir esta
         receta casi tal cual;
         B y C cambian de escena y de objeto protagonista (con TODAS las
         reglas de arriba y la composición de su formato).
   - "miniatura_titulares": EXACTAMENTE 3 textos, uno por prompt y en el
     mismo orden (A, B, C): el TITULAR corto que el programa escribe sobre
     esa miniatura (el prompt NO lo lleva). 2-4 palabras, TODO EN
     MAYÚSCULAS, en español (salvo términos técnicos que se dicen en
     inglés), sin comillas, que se lea de un vistazo y que NO repita el
     título del vídeo. Cada uno con un gancho distinto (curiosidad,
     beneficio, problema...). Ejemplos: "¿NO APARECES?", "EL ERROR",
     "INDEXA YA".
   - "miniatura_caras": EXACTAMENTE 3 nombres, uno por prompt y en el mismo
     orden (A, B, C): la CARA del autor que el programa pega en esa miniatura
     (sus fotos reales, recortadas). Elige SOLO de esta lista: {caras_disponibles}.
     Elige según lo que muestra ESA miniatura y los títulos: algo que
     sorprende, un número enorme o algo muy brillante → sorpresa; un error,
     una pérdida o algo que duele → decepcion, triste o asco; una duda o un
     dilema → pensando; un resultado, un logro o un paso a paso → feliz. Si
     no lo tienes claro, "feliz" (la favorita del autor). Pueden repetirse.

6) CAPÍTULOS (campo "capitulos"): puntos donde CAMBIA de verdad el tema o
   subtema (para los capítulos de YouTube, no para las tarjetas). NO uno
   por minuto ni por frase — solo cuando arranca un bloque temático nuevo
   de verdad (una introducción, cada punto grande de una lista, un cambio
   de herramienta o de sección). En un vídeo de 10 min, 4-8 capítulos es lo
   normal; en uno más corto, menos — nunca inventes capítulos de relleno
   solo por rellenar la lista. Cada uno: {{"t": 105.0, "titulo": "Cómo usar
   Search Console"}} — "titulo" corto (3-6 palabras), en español, resume
   QUÉ se hace en ese tramo (no repitas la frase textual del presentador).

7) DESCRIPCIÓN DE YOUTUBE (campos "gancho_descripcion", "articulo_relacionado",
   "hashtag"): {instruccion_titulo}
   - "gancho_descripcion": las DOS primeras líneas de la descripción de
     YouTube — lo único que se ve ANTES de "mostrar más". En primera
     persona, tono cercano, anuncia de qué va el vídeo de forma que dé
     ganas de hacer clic en "mostrar más" (para el CTR) — SIN spoilear
     todo el contenido. 1-2 frases cortas, nada de hashtags ni emojis aquí.
   - "articulo_relacionado": copia EXACTO, letra por letra, el título de
     UNO de estos artículos si de verdad tiene que ver con el tema de este
     vídeo — si ninguno encaja de verdad, devuelve "" (no inventes ni
     fuerces uno que no pega):
     {lista_articulos}
   - "hashtag": UNO de estos, el que mejor encaje con el tema del vídeo (si
     ninguno aplica, el primero de la lista):
     {lista_hashtags}
   - "tags": EXACTAMENTE 5 etiquetas SEO de ESTE vídeo en concreto (no del
     canal en general), en español, de 1 a 4 palabras cada una, con sus
     tildes: lo que alguien escribiría en YouTube o Google para encontrar
     este vídeo — el tema principal, la herramienta o función concreta, el
     problema que resuelve, el término técnico clave. La primera, la más
     importante. Sin «#», sin repetirse entre sí y sin el nombre del canal
     (p. ej. "SEO para Google", "Search Console", "búsqueda multimodal").

8) ILUSTRACIONES ANIMADAS (campo "ilustraciones"): momentos en que lo que
   explica el presentador es MUY VISUAL o conceptual y un símbolo animado en
   la esquina ayuda a captarlo al instante (algo que sube o baja, buscar,
   seguridad, automatizar, una idea, un error…). NO son fotos (eso es
   "imagenes") ni texto (eso son las tarjetas): es un símbolo en movimiento.
   Elige "tipo" SOLO de esta lista de claves (cada una con lo que significa):
{lista_ilustraciones}
   Reglas: solo cuando el símbolo encaja de verdad con lo que se dice en ese
   instante; NO cuando está enseñando algo en pantalla que ya lo explica, ni
   justo donde ya hay una tarjeta o una imagen; como mucho una cada {ilus_sep}
   segundos, y nunca las fuerces — mejor pocas y buenas que muchas. "t" = el
   segundo en que se dice la palabra o frase que ilustra, no el principio de la
   idea (que el símbolo entre justo cuando lo dice); "duracion" en segundos (3-5); "etiqueta" = 1-3 palabras en
   español que verá el espectador bajo el símbolo (o "" si el símbolo habla
   solo).
   Cada una: {{"t": 32.0, "tipo": "grafica_sube", "duracion": 3.4,
   "etiqueta": "Más tráfico", "cita": "sube el tráfico de"}}

9) PALABRAS CLAVE (campo "palabras_clave"): texto que sale EN GRANDE abajo,
   en el centro de la pantalla, justo cuando el presentador lo dice, para
   subrayar el MENSAJE PRINCIPAL de ese momento (como en los vídeos de Alex
   Hormozi). Cada una tiene que entenderse SOLA y tener sentido completo:
   - o la FRASE corta entera que lleva ese mensaje, tal como la dice (una
     pregunta o una afirmación completa: "¿CÓMO APARECEMOS EN LAS IAS?",
     "NO VENDIÓ NADA", "LOS BLOGS EN INGLÉS GANAN");
   - o UNA palabra o cifra con un peso ÚNICO en el vídeo (el concepto central:
     "GEO"), y entonces con importancia 3.
   NUNCA un trozo cortado de una frase ("¿EN LAS IAS?", "DE LA WEB"): si la
   frase entera no cabe, elige otra cosa. Máximo 6 palabras y 36 caracteres
   (sale en dos líneas).
   Tiene que ser lo que DICE en ese instante (tal cual o casi: una cifra dicha
   con letras va en número), nunca un resumen inventado. Una cifra, solo si
   es un DATO: un estimado u opinión suya («casi que el 90 %») no se subraya.
   Propón MUCHAS, repartidas por TODO el vídeo (más o menos una cada 8-12 s de
   habla): el programa elige cuáles salen para que el vídeo nunca pase más de
   {max_sin_cambio} s sin que cambie algo en pantalla, y descarta las que
   chocan con una tarjeta o con una demostración en pantalla. No repitas lo
   que ya pone una tarjeta en ese momento (el nombre de una herramienta ya
   tiene su tarjeta) ni "SUSCRÍBETE" (tiene la suya). "t" = el segundo en que
   EMPIEZA a decir la primera de esas palabras; "importancia": 3 = el mensaje
   principal (tiene que salir), 2 = buena, 1 = de relleno.
   Cada una: {{"t": 62.4, "texto": "NO VENDIÓ NADA", "importancia": 3}}

10) ESCENAS A PANTALLA COMPLETA (campo "escenas"): el momento MÁS visual del
   vídeo se cuenta con un cambio de escena especial: la pantalla entera pasa a
   una ilustración grande que representa lo que dice (su voz sigue debajo) con
   la frase clave escrita. Es un recurso ESPECIAL, para casos particulares: pon
   AL MENOS 1 (el mejor momento del vídeo) y como mucho 1 cada ~{escena_cada}
   minutos. Solo cuando la idea es muy visual y encaja de verdad con una
   ilustración de la lista del punto 8 (algo que crece o cae, buscar,
   proteger, automatizar, una idea, dinero, gente…), NUNCA mientras enseña
   algo en la pantalla (ahí lo importante es la pantalla) ni en los primeros
   5 s. "t" = el segundo en que EMPIEZA a decir esa idea; "tipo" = la clave de
   la ilustración; "frase" = la frase corta que resume ESA idea tal como la
   dice (máximo 36 caracteres, p. ej. "ESTO VA A EVOLUCIONAR"); "duracion" 5-7.
   Cada una: {{"t": 30.9, "tipo": "grafica_sube", "frase": "ESTO VA A EVOLUCIONAR",
   "duracion": 6}}

11) ESCENAS DE ESTILO (campo "escenas_estilo"): cinco escenas a pantalla completa
   que SOLO salen cuando lo que dice encaja de verdad (si no encaja ninguna,
   deja la lista vacía: es lo normal en un vídeo que no habla de esto). Cada
   una cubre su cara un rato con un gráfico animado hecho con SUS palabras:
   - "capitulo": enumera un punto numerado EN VOZ ALTA («número dos», «paso 3»,
     «regla cinco»). "numero" = el número que dice (1-20); "titulo" = el nombre
     del punto como lo dice (máximo 44 caracteres).
   - "grafica": cuenta un ESTUDIO, informe, reporte, encuesta o análisis con 2-4
     cifras COMPARABLES dichas en voz alta. "titulo" = qué mide; "barras" =
     [{{"nombre": "Texto humano", "valor": 151, "etiqueta": "2:31"}}, ...] donde
     "valor" es un número comparable (misma unidad para todas) y "etiqueta" es
     la cifra tal como se escribe ("2:31", "14,5 %", "3 meses"). SOLO cifras que
     él dice: nunca inventes ni estimes; con una sola cifra no es una gráfica.
   - "chat": da un EJEMPLO de lo que una IA (ChatGPT, Gemini…) respondería o de
     lo que alguien le preguntaría. "pregunta" (máx. 90 caracteres),
     "respuesta" (máx. 190: lo que ÉL dice que respondería, con sus palabras y
     cifras), "fuente" (opcional: solo si nombra el sitio que cita la IA).
   - "veredicto": un ejemplo de bien/mal, sí/no, lo que sí y lo que no («esto
     lo cita la IA, esto no», «correcto / incorrecto»). "contexto" (opcional,
     máx. 110: la pregunta o la situación) e "items" = 1-3 de
     {{"texto": "Sin citar", "ok": false}} (texto máx. 24 caracteres).
   - "cita": una frase COMPLETA, corta y de mucho peso (una tesis, una regla,
     una conclusión) que merece verse en grande. "frase" = tal cual la dice (4 o
     más palabras, máx. 96 caracteres); "resalta" = 1-3 palabras de esa frase.
   TODAS llevan: "t" (el segundo en que EMPIEZA a decir eso), "duracion"
   (capitulo 3-4, grafica 6-8, chat 6-8, veredicto 4-6, cita 4-6) y "cita" = de
   4 a 14 palabras COPIADAS tal cual de la transcripción de lo que dice en ese
   momento (el programa las comprueba: si no están, la escena se descarta).
   Nunca mientras enseña algo en pantalla ni en los primeros 5 s; como mucho 1
   cada ~100 s en total. Ejemplo: {{"estilo": "veredicto", "t": 73.2,
   "duracion": 5, "cita": "esto sí lo cita ChatGPT y esto no", "contexto": "¿Cuánto
   dura un tejado de metal?", "items": [{{"texto": "Sin citar", "ok": false}},
   {{"texto": "Citado", "ok": true}}]}}

Devuelve SOLO este JSON (sin texto alrededor):
{{"recortes": [...], "camara": [...], "tarjetas": [...], "imagenes": [...],
 "ilustraciones": [...], "palabras_clave": [...], "escenas": [...],
 "escenas_estilo": [...],
 "titulos_sugeridos": ["...", "...", "..."],
 "miniatura_prompts": ["...", "...", "..."],
 "miniatura_titulares": ["...", "...", "..."],
 "miniatura_caras": ["feliz", "...", "..."],
 "capitulos": [...],
 "gancho_descripcion": "...", "articulo_relacionado": "...", "hashtag": "...",
 "tags": ["...", "...", "...", "...", "..."], "pausas_enfasis": [...]}}

TRANSCRIPCIÓN:
{transcripcion}
"""


def _caras_para_prompt(cfg) -> str:
    """Los gestos de las fotos del autor (`mis_caras/`, clipper/miniaturas_ia.py), para «miniatura_caras»."""
    try:
        from . import miniaturas_ia as _mia
        p = _mia.parametros(cfg)
        g = _mia.gestos(_mia.caras(cfg=cfg), p["cara_defecto"])
    except Exception:  # noqa: BLE001 — sin fotos o sin la carpeta: igual vale «feliz»
        g = []
    return ", ".join(f'"{x}"' for x in (g or ["feliz"]))


def _gemini(cfg):
    """(client, types, model) o None si no se puede usar la IA. Mismo patrón
    que temas.py / score.py — no hay tres formas distintas de abrir Gemini."""
    if not cfg.get_path("ai.enabled", True):
        return None
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    try:
        return (genai.Client(api_key=os.environ["GEMINI_API_KEY"]), types,
                cfg.get_path("ai.model", "gemini-3.1-flash-lite"))
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo abrir Gemini para el director ({exc}).")
        return None


def _cadena_modelos(cfg, client=None) -> list[str]:
    """Modelos de Gemini a probar EN ORDEN, del mejor al peor y POR CÓDIGO (clipper/modelos.py): se le pregunta a Google qué modelos ofrece,
    se ordenan por familia y versión (el modelo de mañana entra solo) y se aparta el que ahora está saturado o agotado.
    `ai.director_modelos` de config.yaml es solo el plan B si no se puede preguntar a Google."""
    from . import modelos as _m
    return _m.cadena(cfg, client) or [str(cfg.get_path("ai.model", "gemini-3.1-flash-lite"))]


def es_saturacion(exc_o_texto) -> bool:
    """¿El fallo es de SATURACIÓN de Google (pasa solo: vale la pena reintentarlo más tarde) y no la cuota DEL DÍA
    agotada, una clave mala o un error de verdad? Mismo criterio que la cascada (`util._GEMINI_TRANSIENT`), pero un
    429 que dice «quota exceeded» (el límite diario, p. ej. 20 al día en los Flash) no se arregla esperando 1 h."""
    from .util import _GEMINI_TRANSIENT
    m = str(exc_o_texto or "").lower()
    if "exceeded your current quota" in m or "quota exceeded" in m:
        return False
    return any(t in m for t in _GEMINI_TRANSIENT)


def _motivo_corto(texto: str) -> str:
    """El error crudo de Google viene como un diccionario ilegible; para la
    tarjeta del panel se deja «503 UNAVAILABLE: <el mensaje>»."""
    t = str(texto).replace("\n", " ")
    if t.startswith("Google ha bloqueado el proyecto"):         # util.ProyectoBloqueado: ya viene explicado y con lo que hacer (no recortar)
        return t[:900]
    cod = re.match(r"\s*(\d{3})\s+([A-Z_]+)", t)
    m = re.search(r"""['"]message['"]\s*:\s*['"](.+?)['"]\s*,\s*['"]status""", t)
    msg = m.group(1) if m else re.sub(r"^\s*\d{3}\s+[A-Z_]+\.?\s*", "", t)
    return ((f"{cod.group(1)} {cod.group(2)}: " if cod else "") + msg.strip())[:220]


_ESQUEMA = None


def _esquema_plan():
    """La forma EXACTA del plan que pide el prompt, como clase pydantic: se la damos a Gemini
    como `response_schema` para que el JSON salga válido por construcción. None si no hay
    pydantic (viene con google-genai) o se apagó en config (`ai.director_esquema: false`)."""
    global _ESQUEMA
    if _ESQUEMA is None:
        try:
            from typing import Literal
            from pydantic import BaseModel
        except ImportError:
            _ESQUEMA = False
            return None

        class Recorte(BaseModel):
            inicio: float
            fin: float
            motivo: str = ""

        class Camara(BaseModel):
            inicio: float
            fin: float
            modo: Literal["pantalla", "pantalla_zoom", "cara", "ambos", "explicativa"]
            termino: str | None = None
            definicion: str | None = None

        class Tarjeta(BaseModel):
            t: float
            tipo: Literal["punto", "herramienta", "definicion", "dato"]
            texto: str
            duracion: float = 3.5
            numero: int | None = None
            extra: str | None = None
            estimado: bool | None = None        # solo "dato": la cifra es un estimado del presentador (sale con «≈»)
            cita: str = ""                      # 3-8 palabras copiadas de la transcripción donde se nombra: el programa ancla el `t` ahí (anclaje.py)

        class Imagen(BaseModel):
            t: float
            busqueda: str
            alternativas: list[str] = []
            duracion: float = 3.0
            motivo: str = ""
            cita: str = ""

        class Capitulo(BaseModel):
            t: float
            titulo: str

        from .ilus_escenas import CATALOGO as _CAT
        from . import ilus_escenas2 as _e2  # noqa: F401
        _TipoIlus = Literal[tuple(_CAT)]        # las claves REALES de la biblioteca de ilustraciones

        class Ilustracion(BaseModel):
            t: float
            tipo: _TipoIlus
            duracion: float = 3.4
            etiqueta: str = ""
            cita: str = ""

        class PalabraClave(BaseModel):
            t: float
            texto: str
            importancia: int = 2

        class Escena(BaseModel):
            t: float
            tipo: _TipoIlus                     # una ilustración de la biblioteca, en grande (clipper/escena_completa.py)
            frase: str
            duracion: float = 6.0

        class Barra(BaseModel):
            nombre: str
            valor: float
            etiqueta: str

        class ItemVeredicto(BaseModel):
            texto: str
            ok: bool = True

        class EscenaEstilo(BaseModel):          # capitulo / grafica / chat / veredicto / cita (clipper/escenas_estilo.py)
            t: float
            estilo: Literal["capitulo", "grafica", "chat", "veredicto", "cita"]
            cita: str
            duracion: float = 5.0
            numero: int = 0
            titulo: str = ""
            barras: list[Barra] = []
            pregunta: str = ""
            respuesta: str = ""
            fuente: str = ""
            contexto: str = ""
            items: list[ItemVeredicto] = []
            frase: str = ""
            resalta: list[str] = []

        class Plan(BaseModel):
            recortes: list[Recorte]
            camara: list[Camara]
            tarjetas: list[Tarjeta]
            imagenes: list[Imagen]
            ilustraciones: list[Ilustracion]
            palabras_clave: list[PalabraClave] = []   # textos grandes que salen al decirlos (clipper/ritmo.py)
            escenas: list[Escena] = []                # escenas a pantalla completa (clipper/escena_completa.py)
            escenas_estilo: list[EscenaEstilo] = []   # capítulo / gráfica de un estudio / chat / bien-mal / frase (clipper/escenas_estilo.py)
            titulos_sugeridos: list[str]
            miniatura_prompts: list[str]
            miniatura_titulares: list[str] = []  # el titular de cada miniatura A/B/C: lo escribe el PROGRAMA con Rubik
            miniatura_caras: list[str] = []     # la cara del autor en cada miniatura A/B/C (clipper/miniaturas_ia.py)
            capitulos: list[Capitulo]
            gancho_descripcion: str
            articulo_relacionado: str
            hashtag: str
            tags: list[str] = []                # 5 etiquetas SEO de ESTE vídeo (descripción como hashtags y campo tags)
            pausas_enfasis: list[str] = []      # ids de las pausas de duda que NO se acortan (`voz.pausas_candidatas`)
            nota_cambios: str = ""              # solo en una REVISIÓN: qué entendió y qué cambió

        _ESQUEMA = Plan
    return _ESQUEMA or None


def _parsea_plan(raw: str) -> dict:
    """Texto de Gemini -> dict del plan. Si viene roto o no es un objeto, lanza:
    la cadena de modelos lo cuenta como fallo de ESE modelo y prueba el siguiente."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        crudo = json.loads(raw)
    except json.JSONDecodeError:
        # A veces el modelo añade texto (u otro JSON) DESPUÉS del objeto
        # ("Extra data") o algo de texto ANTES: se toma el primer objeto
        # completo en vez de tirar una respuesta que en realidad es buena.
        i = raw.find("{")
        if i < 0:
            raise
        crudo, _fin = json.JSONDecoder().raw_decode(raw[i:])
    if not isinstance(crudo, dict):
        raise ValueError("Gemini no devolvió un objeto JSON")
    if not any(k in crudo for k in ("recortes", "camara", "tarjetas", "titulos_sugeridos")):
        raise ValueError("el JSON de Gemini no trae ningún campo del plan")
    return crudo


def _transcripcion_marcada(words: list[dict], segs: list[tuple[float, float]],
                           gap: float = 0.30, cada: float = 2.0,
                           tope_chars: int = 200000) -> str:
    """Texto con marcas [+segundo_absoluto_del_directo] en cada pausa (y como
    mucho cada `cada` segundos si habla seguido). Mismo truco que ya probamos
    en candidates.py para que Gemini pueda devolver segundos EXACTOS en vez
    de "muévelo un poco": ahí sí funcionó, aquí es la misma idea.

    Los silencios se marcan EXPLÍCITOS (no solo con el número de segundo),
    sobre todo el del ARRANQUE: casi todo vídeo grabado empieza con unos
    segundos de "preparándose para hablar" antes de la primera palabra, y
    sin avisarlo aparte, el director tiende a no darse cuenta de que ahí
    hay algo que cortar — ve una marca de tiempo, no un silencio. También
    resetea el conteo de silencio en cada tramo nuevo: si `segs` viene de
    trozos NO contiguos del directo (el generador por temática), el hueco
    entre el final de un trozo y el inicio del siguiente no es un silencio
    real, es solo dónde se cortó para juntar temas."""
    piezas: list[str] = []
    ult_marca = -99.0
    for s, e in segs:
        piezas.append(f"\n--- [{s:.0f}s - {e:.0f}s del directo] ---")
        ult_fin = None
        for w in words:
            t0 = float(w.get("start", 0))
            if not (s <= t0 < e):
                continue
            if ult_fin is None and t0 - s >= 1.0:
                piezas.append(f" [ATENCIÓN: entre el segundo {s:.1f} y el segundo "
                              f"{t0:.1f} NO SE DICE NADA — {t0 - s:.1f}s de silencio "
                              f"puro justo al ARRANQUE de este tramo. Si esto es el "
                              f"inicio del vídeo, casi seguro va en \"recortes\" como "
                              f"{{\"inicio\": {s:.1f}, \"fin\": {t0:.1f}}}]")
            elif ult_fin is not None and t0 - ult_fin >= 1.5:
                piezas.append(f" [silencio de {t0 - ult_fin:.1f}s, del segundo "
                              f"{ult_fin:.1f} al {t0:.1f}]")
            gap_real = (t0 - ult_fin) if ult_fin is not None else 9.9
            if gap_real >= gap or t0 - ult_marca >= cada:
                piezas.append(f" [+{t0:.1f}s]")
                ult_marca = t0
            piezas.append(" " + str(w.get("word", "")).strip())
            ult_fin = float(w.get("end", t0))
    texto = "".join(piezas).strip()
    if len(texto) > tope_chars:
        log(f"  director: tramo largo ({len(texto)} chars), lo recorto a {tope_chars}.")
        texto = texto[:tope_chars]
    return texto


def _en_rango(t: float, segs: list[tuple[float, float]], margen: float = 1.0) -> bool:
    return any(s - margen <= t <= e + margen for s, e in segs)


def _clip(t: float, segs: list[tuple[float, float]]) -> float:
    """Deja `t` dentro del tramo más cercano (Gemini a veces se pasa un pelín
    del final de un tramo real)."""
    mejor, mejor_d = t, 1e9
    for s, e in segs:
        c = min(max(t, s), e)
        d = abs(c - t)
        if d < mejor_d:
            mejor, mejor_d = c, d
    return mejor


_APERTURA_MINIATURA = ("YouTube thumbnail, 16:9 widescreen (1920x1080), eye-catching, "
                       "scroll-stopping, extremely clickable, bold high-contrast look.")
# La ZONA DE LA CARA de cada variante del A/B/C (el autor, 2026-09-26: «las tres versiones tienen mucha similitud… los
# expertos del A/B test recomiendan que las variaciones sean distintas… una tal cual como está ahorita, otra con elementos
# a la derecha y a la izquierda y mi cara en el centro, otra más minimalista… siempre un espacio para colocar mi cara, que
# lo importante esté agrupado para que mi cara no lo tape»). La frase la pone el CÓDIGO al final de cada prompt, según su
# posición (A, B, C); la IA solo describe la escena pensada para ese formato.
_FORMATOS_MINIATURA = (
    ("derecha", "Keep the right 35-40% of the frame to low-importance background details only (dark halftone "
                "texture, speed lines, sparks): a face cutout of the presenter will be placed there, so nothing "
                "important goes on the right."),
    ("centro", "Symmetrical composition: the important elements sit on the left third and on the right third of the "
               "frame, while the central third, from the bottom up to about two thirds of the height, keeps only "
               "low-importance background (soft glow, halftone texture, speed lines): a face cutout of the presenter "
               "will be placed in the center, so nothing important goes in the middle."),
    ("minimalista", "Minimalist composition: the one main element and its few small details are grouped tightly "
                    "together in the left two thirds of the frame, on a calm plain background with generous negative "
                    "space; the right 30% keeps only that plain background (it does not need to be empty, just nothing "
                    "important): a face cutout of the presenter will be placed there and must not cover anything."),
)
# una frase de la zona de la cara escrita por la IA (o por una pasada anterior): se quita y va la exacta del formato — si
# la IA escribía la suya, salía DOS veces (pasó en el vídeo del 24-sep) y, con formatos distintos, se contradirían
_ZONA = r"(?:plac(?:e|ed|ing)|reserv\w*|keep|kept|free|clean|empty|low[- ]importance|nothing important)"
_POS_PCT = r"\b(?:right|left|central|center|middle)\b[^.!?]{0,25}?\d{1,2}\s*(?:[-–]\s*\d{1,2}\s*)?%"
# con un «% del cuadro», solo verbos de RESERVAR la zona: «clean»/«free» también son adjetivos normales de una escena
_ZONA_PCT = r"(?:reserv\w*|keep|kept|empty|low[- ]importance|nothing important)"
_FRASE_ZONA_CARA_RE = re.compile(
    r"[^.!?]*?(?:\b(?:cut-?out|presenter|portrait)\b[^.!?]*\b" + _ZONA + r"\b"
    r"|\b" + _ZONA + r"\b[^.!?]*\b(?:cut-?out|presenter|portrait)\b"
    r"|" + _POS_PCT + r"[^.!?]*\b" + _ZONA_PCT + r"\b"
    r"|\b" + _ZONA_PCT + r"\b[^.!?]*" + _POS_PCT + r")[^.!?]*[.!?]", re.I)
_COLOR_EN = {"naranja": "vivid orange", "crema": "cream", "beige": "beige",
             "dorado": "gold", "tinta": "near-black ink"}


def _paleta_hex(marca: dict) -> list[tuple[str, str]]:
    """Solo los COLORES de `videos.marca` (nombre, #HEX): ese bloque también
    trae las fuentes de las tarjetas (fuente_texto: FONTS/....TTF) y eso no
    es un color — colarlo en un prompt de miniatura no tiene sentido."""
    return [(str(k), str(v).upper()) for k, v in (marca or {}).items()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", str(v or "").strip())]


_BLOQUE_PALETA_RE = re.compile(r"\s*Use ONLY this exact color palette and nothing else:.*?never shifted toward red or yellow\.", re.S)
# La B prueba OTROS colores de fondo (el autor, 2026-10-04: «en el ABC test haya una que pruebe otros colores… que juegue con los colores del fondo, sin olvidar mis colores del tu marca»):
# su prompt lleva siempre esta frase (la pone el código, como la zona de la cara y la paleta); en el de FLUX (`miniaturas_ia.para_ia`) va la suya.
_FONDO_B_RE = re.compile(r"\s*Background: the whole background is a bold, saturated colou?r field.*?small accents\)\.", re.S | re.I)


def _frase_fondo_b(marca: dict) -> str:
    hexes = dict(_paleta_hex(marca))
    nar, dor, tin = hexes.get("naranja", "#FE330A"), hexes.get("dorado", "#FFC93C"), hexes.get("tinta", "#14100F")
    return (f"Background: the whole background is a bold, saturated colour field that makes the thumbnail pop in the feed: flat vivid red-orange {nar}, or flat gold {dor}, "
            f"or a clean diagonal split of those two, with a fine halftone dot texture and bold ink burst lines, and it is not black and not dark (near-black ink {tin} "
            f"is only for outlines, shadows and small accents).")


def _asegura_reglas_miniatura(prompt: str, marca: dict, formato: int = 0) -> str:
    """Las reglas del autor para la miniatura que NO se pueden dejar al
    criterio de la IA — se garantizan aquí por código, siempre: (1) el prompt
    dice explícito que es una miniatura de YouTube llamativa, (2) deja libre
    la ZONA DE LA CARA de su formato (`formato`: 0 = A, cara a la derecha;
    1 = B, cara en el centro; 2 = C, minimalista — ver _FORMATOS_MINIATURA),
    porque ahí el autor pega su cara, (3) usa SOLO los
    colores exactos de la marca (`videos.marca`, con su hex) — un generador
    de imágenes aproxima si solo le dices "orange", y sin esto se coló hasta
    un check verde y una X roja fuera de paleta. La IA pone lo creativo (la
    escena, el titular); si ya incluyó estas frases, no se duplican."""
    p = str(prompt or "").strip()
    if not p:
        return p
    # idempotente: en una revisión el prompt YA trae el bloque de la paleta de una pasada anterior (y sin quitarlo se
    # duplicaba en cada mejora: salió en la prueba real de «no cambies nada»)
    from . import miniaturas_ia as _mia
    sin_texto = getattr(_mia, "SIN_TEXTO", "")            # (la regla fija de «ningún texto»: sale en el prompt del panel también)
    idx = max(0, min(int(formato), len(_FORMATOS_MINIATURA) - 1))
    p = _BLOQUE_PALETA_RE.sub("", p).strip()
    p = _FONDO_B_RE.sub("", p)                              # (la frase fija del fondo de la B: se pone otra vez abajo, una sola vez)
    p = re.sub(r"\s{2,}", " ", _FRASE_ZONA_CARA_RE.sub(" ", p.replace(sin_texto, "") if sin_texto else p)).strip()
    if idx == 1:
        p = getattr(_mia, "sin_fondo_oscuro", lambda t: t)(p)    # la B prueba otros colores: su fondo ya no es negro
    if not p.lower().startswith("youtube thumbnail"):
        p = f"{_APERTURA_MINIATURA} {p}"
    p = f"{p.rstrip()} {_FORMATOS_MINIATURA[idx][1]}"
    if idx == 1:
        p = f"{p} {_frase_fondo_b(marca)}"
    if sin_texto:
        p = f"{p} {sin_texto}"
    colores = ", ".join(f"{_COLOR_EN.get(nombre.lower(), nombre)} {hex_}"
                        for nombre, hex_ in _paleta_hex(marca))
    if colores:
        # la paleta la fija el código: quita la lista de colores que haya
        # puesto la IA y sus "No blue/green colors" (nombrar colores
        # prohibidos a un generador de imágenes a veces los provoca)
        p = re.sub(r"\b(?:Color palette|Palette|Colors?):[^.]*\.\s*", "", p, flags=re.I)
        p = re.sub(r"\bNo (?:red|blue|green)\b[^.]*\.\s*", "", p, flags=re.I)
        p = (f"{p.rstrip()} Use ONLY this exact color palette and nothing else: {colores}. "
             f"Every element, including pictograms, check marks and crosses, is drawn in these "
             f"colors only. Keep the orange exactly as given, never shifted toward red or yellow.")
    return p


def limpia_titular(t, max_palabras: int = 5, max_chars: int = 40) -> str:
    """El titular de una miniatura, limpio: sin comillas ni saltos de línea, como mucho 5 palabras y 40 caracteres (lo escribe
    el programa con Rubik: tiene que leerse de un vistazo)."""
    s = re.sub(r"\s+", " ", str(t or "")).strip().strip("\"'“”«»‘’`").strip()
    return " ".join(s.split()[:max_palabras])[:max_chars].strip()


def limpia_titulares(v, tope_n: int = 3) -> list[str]:
    """Los titulares de las miniaturas A/B/C, CADA UNO EN SU POSICIÓN (un hueco queda vacío: el índice es la letra). Si no hay
    ninguno, lista vacía."""
    out = [limpia_titular(x) for x in (v if isinstance(v, list) else [])][:tope_n]
    return out if any(out) else []


def _prepara_miniaturas(plan: dict, marca: dict, previos=None) -> None:
    """Los prompts de miniatura del plan, LISTOS (modifica `plan`): cada uno SIN texto dibujado (la IA de imágenes escribe mal
    las letras: el autor, 2026-10-03) y con las reglas fijas de su posición (A, B, C: zona de la cara, «sin texto», paleta). El
    titular lo escribe el programa con Rubik: sale de `miniatura_titulares` o, si el director lo escribió DENTRO del prompt
    (el modo de antes), de ahí. `previos` = los prompts que una revisión deja TAL CUAL: no se tocan."""
    from . import miniaturas_ia as _mia
    previos = set(previos or [])
    prompts = [str(p) for p in (plan.get("miniatura_prompts") or [])]
    titulares = [limpia_titular(t) for t in (plan.get("miniatura_titulares") or [])][:3]
    titulares += [""] * (len(prompts) - len(titulares))
    listos = []
    for i, p in enumerate(prompts):
        if p in previos:
            listos.append(p)
            continue
        if not titulares[i]:
            titulares[i] = limpia_titular(_mia.titular_de(p))
        listos.append(_asegura_reglas_miniatura(_mia.sin_texto(p, siglas=False), marca, i))
    plan["miniatura_prompts"] = listos
    plan["miniatura_titulares"] = titulares[:len(listos)] if any(titulares) else []


def _lista_textos(v, tope_chars: int, tope_n: int = 3) -> list[str]:
    """Normaliza un campo que debería ser una lista de hasta `tope_n`
    strings no vacíos (títulos, prompts de miniatura): tira lo que no sea
    texto usable en vez de fallar, mismo criterio que el resto del módulo."""
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for x in v:
        s = str(x or "").strip()[:tope_chars]
        if s:
            out.append(s)
        if len(out) >= tope_n:
            break
    return out


def _valida(plan: dict, segs: list[tuple[float, float]], dur_total: float) -> dict:
    """Tira lo que no cuadra en vez de confiar a ciegas. Mismo criterio que
    en el resto del proyecto: un plan que inventa un segundo fuera del
    material real es peor que uno más corto pero cierto."""
    out = {"recortes": [], "camara": [], "tarjetas": [], "imagenes": [], "ilustraciones": [], "palabras_clave": [],
           "escenas": [], "escenas_estilo": [],
           "titulos_sugeridos": [], "miniatura_prompts": [], "capitulos": [],
           "gancho_descripcion": "", "articulo_relacionado": "", "hashtag": ""}
    tirados = 0

    for r in plan.get("recortes") or []:
        try:
            a, b = float(r["inicio"]), float(r["fin"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        a, b = min(a, b), max(a, b)
        if b - a < 0.3 or not (_en_rango(a, segs) and _en_rango(b, segs)):
            tirados += 1
            continue
        out["recortes"].append({"inicio": round(_clip(a, segs), 2),
                                "fin": round(_clip(b, segs), 2),
                                "motivo": str(r.get("motivo", ""))[:120]})

    for c in plan.get("camara") or []:
        try:
            a, b = float(c["inicio"]), float(c["fin"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        modo = str(c.get("modo", "")).strip().lower()
        if modo not in ("pantalla", "pantalla_zoom", "cara", "ambos", "explicativa") or b - a < 0.5:
            tirados += 1
            continue
        if not (_en_rango(a, segs) and _en_rango(b, segs)):
            tirados += 1
            continue
        item_cam = {"inicio": round(_clip(a, segs), 2),
                    "fin": round(_clip(b, segs), 2), "modo": modo}
        if modo == "explicativa":
            termino = str(c.get("termino", "")).strip()[:40]
            definicion = str(c.get("definicion", "")).strip()[:160]
            if termino and definicion:
                item_cam["termino"], item_cam["definicion"] = termino, definicion
            else:
                # sin término/definición no hay qué escribir en la tarjeta —
                # mejor "cara" (el modo de siempre) que una tarjeta vacía
                item_cam["modo"] = "cara"
        out["camara"].append(item_cam)

    tipos_ok = {"punto", "herramienta", "definicion", "dato"}
    for t in plan.get("tarjetas") or []:
        try:
            at = float(t["t"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        tipo = str(t.get("tipo", "")).strip().lower()
        texto = str(t.get("texto", "")).strip()
        if tipo not in tipos_ok or not texto or not _en_rango(at, segs):
            tirados += 1
            continue
        item = {"t": round(_clip(at, segs), 2), "tipo": tipo, "texto": texto[:80],
                "duracion": max(1.5, min(8.0, float(t.get("duracion", 3.5) or 3.5)))}
        if tipo == "punto":
            try:
                item["numero"] = int(t.get("numero") or 0) or None
            except (TypeError, ValueError):
                item["numero"] = None
        if tipo in ("definicion", "dato"):
            item["extra"] = str(t.get("extra", "")).strip()[:150]
        if tipo == "dato" and t.get("estimado") is True:
            item["estimado"] = True
        if limpia_cita(t.get("cita")):
            item["cita"] = limpia_cita(t.get("cita"))
        out["tarjetas"].append(item)

    out["titulos_sugeridos"] = _lista_textos(plan.get("titulos_sugeridos"), 80)
    out["miniatura_prompts"] = _lista_textos(plan.get("miniatura_prompts"), 1600)
    out["miniatura_caras"] = _lista_textos(plan.get("miniatura_caras"), 40)
    out["miniatura_titulares"] = limpia_titulares(plan.get("miniatura_titulares"))

    for c in plan.get("capitulos") or []:
        try:
            at = float(c["t"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        titulo = str(c.get("titulo", "")).strip()
        if not titulo or not _en_rango(at, segs):
            tirados += 1
            continue
        out["capitulos"].append({"t": round(_clip(at, segs), 2), "titulo": titulo[:60]})
    out["capitulos"].sort(key=lambda c: c["t"])

    out["gancho_descripcion"] = str(plan.get("gancho_descripcion", "") or "").strip()[:300]
    out["articulo_relacionado"] = str(plan.get("articulo_relacionado", "") or "").strip()[:150]
    out["hashtag"] = str(plan.get("hashtag", "") or "").strip()[:40]
    out["tags"] = limpia_tags(plan.get("tags"))
    out["nota_cambios"] = str(plan.get("nota_cambios", "") or "").strip()[:700]
    # las pausas de duda que la IA quiere conservar: solo ids con la forma de `voz.pausas_candidatas` («p123.4»)
    out["pausas_enfasis"] = sorted({str(x).strip() for x in plan.get("pausas_enfasis") or []
                                    if re.fullmatch(r"p\d+(\.\d+)?", str(x).strip())})[:200]

    for im in plan.get("imagenes") or []:
        try:
            at = float(im["t"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        busq = str(im.get("busqueda", "")).strip()
        if not busq or not _en_rango(at, segs):
            tirados += 1
            continue
        alternativas = [str(a).strip()[:40] for a in (im.get("alternativas") or []) if str(a).strip()][:2]
        out["imagenes"].append({
            "t": round(_clip(at, segs), 2), "busqueda": busq[:60],
            "alternativas": alternativas,
            "duracion": max(1.5, min(6.0, float(im.get("duracion", 3.0) or 3.0))),
            "motivo": str(im.get("motivo", ""))[:120],
            **({"cita": limpia_cita(im.get("cita"))} if limpia_cita(im.get("cita")) else {}),
        })

    # ILUSTRACIONES: la clave tiene que ser de la biblioteca (una inventada no se puede dibujar)
    from .ilus_escenas import CATALOGO as _CAT
    from . import ilus_escenas2 as _e2  # noqa: F401  (registra todas las escenas)
    for il in plan.get("ilustraciones") or []:
        try:
            at = float(il["t"])
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        tipo = str(il.get("tipo", "")).strip().lower()
        if tipo not in _CAT or not _en_rango(at, segs):
            tirados += 1
            continue
        try:
            dur_il = float(il.get("duracion", 3.4) or 3.4)
        except (TypeError, ValueError):
            dur_il = 3.4
        out["ilustraciones"].append({
            "t": round(_clip(at, segs), 2), "tipo": tipo,
            "duracion": max(_CAT[tipo].dur_min, min(6.0, dur_il)),
            "etiqueta": str(il.get("etiqueta", "") or "").strip()[:28],
            **({"cita": limpia_cita(il.get("cita"))} if limpia_cita(il.get("cita")) else {}),
        })
    out["ilustraciones"].sort(key=lambda i: i["t"])

    # PALABRAS CLAVE (clipper/ritmo.py): lo que cabe en dos líneas (≤ 36 caracteres, ≤ 6 palabras: una frase corta
    # ENTERA), sin llaves ni barras (en el .ass son órdenes) e importancia 1-3; cuáles salen, con qué filtro de calidad y
    # en qué segundo exacto lo decide el programa
    for pc in plan.get("palabras_clave") or []:
        try:
            at = float(pc["t"])
            imp = max(1, min(3, int(pc.get("importancia") or 2)))
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        texto = re.sub(r"\s+", " ", str(pc.get("texto", "") or "").replace("{", "").replace("}", "").replace("\\", "")).strip()
        if not texto or len(texto) > 36 or len(texto.split()) > 6 or not _en_rango(at, segs):
            tirados += 1
            continue
        out["palabras_clave"].append({"t": round(_clip(at, segs), 2), "texto": texto, "importancia": imp})
    out["palabras_clave"].sort(key=lambda x: x["t"])

    # ESCENAS A PANTALLA COMPLETA (clipper/escena_completa.py): una ilustración de la biblioteca y una frase que cabe
    for es in plan.get("escenas") or []:
        try:
            at = float(es["t"])
            dur_es = max(4.5, min(7.5, float(es.get("duracion") or 6.0)))
        except (KeyError, TypeError, ValueError):
            tirados += 1
            continue
        tipo = str(es.get("tipo", "")).strip().lower()
        frase = re.sub(r"\s+", " ", str(es.get("frase", "") or "").replace("{", "").replace("}", "").replace("\\", "")).strip()
        if tipo not in _CAT or not frase or len(frase) > 36 or not _en_rango(at, segs):
            tirados += 1
            continue
        out["escenas"].append({"t": round(_clip(at, segs), 2), "tipo": tipo, "frase": frase, "duracion": round(dur_es, 2)})
    out["escenas"].sort(key=lambda x: x["t"])

    # ESCENAS DE ESTILO (clipper/escenas_estilo.py): capítulo, gráfica de un estudio, chat, bien/mal, frase. Lo incompleto se tira; que la
    # `cita` esté de verdad en lo dicho lo comprueba el pipeline (aquí no hay transcripción)
    from . import escenas_estilo as _est
    for ee in plan.get("escenas_estilo") or []:
        c = _est.limpia(ee) if isinstance(ee, dict) else None
        if c is None or not _en_rango(c["t"], segs):
            tirados += 1
            continue
        c["t"] = round(_clip(c["t"], segs), 2)
        out["escenas_estilo"].append(c)
    out["escenas_estilo"].sort(key=lambda x: x["t"])

    # fusiona tramos de CAMARA seguidos con el mismo modo (Gemini a veces
    # parte "cara 4.7-18.2" en dos trozos identicos sin razon; un renderizador
    # de zoom no necesita saber eso, y menos cortes = menos ruido)
    fusion: list[dict] = []
    for c in sorted(out["camara"], key=lambda c: c["inicio"]):
        # dos "explicativa" seguidas NO se funden si el término es distinto —
        # fundirlas perdería el segundo término, la tarjeta se quedaría
        # mostrando solo el primero durante los dos tramos
        mismo_termino = True
        if fusion and c["modo"] == "explicativa":
            mismo_termino = c.get("termino") == fusion[-1].get("termino")
        if (fusion and fusion[-1]["modo"] == c["modo"] and mismo_termino
                and c["inicio"] <= fusion[-1]["fin"] + 0.3):
            fusion[-1]["fin"] = max(fusion[-1]["fin"], c["fin"])
        else:
            fusion.append(dict(c))
    out["camara"] = fusion

    if tirados:
        log(f"  director: {tirados} elemento(s) del plan descartados "
            f"(fuera de rango o mal formados).")
    return out


_INSTR_TITULO_COMPLETO = """Esta transcripción es el vídeo COMPLETO, de
   principio a fin (no un tramo suelto). Rellena de verdad títulos,
   miniaturas, capítulos, gancho de descripción, artículo relacionado y
   hashtag — no los dejes vacíos ni devuelvas menos de 3 en las listas que
   piden exactamente 3 ("articulo_relacionado" sí puede ser "" si de
   verdad no hay uno que encaje)."""
_INSTR_TITULO_PARCIAL = """Esta transcripción es SOLO UN TRAMO de un vídeo
   más largo. Devuelve "titulos_sugeridos", "miniatura_prompts", "miniatura_titulares" y "miniatura_caras" como listas
   vacías [], "capitulos" también vacío, y "gancho_descripcion" /
   "articulo_relacionado" / "hashtag" como "" — nada de esto tiene sentido
   sin ver el vídeo entero."""


# ---------------------------------------------------------------- REVISIÓN (el autor ya vio una versión)
_REVISION = """=== REVISIÓN: EL AUTOR YA VIO UNA VERSIÓN EDITADA DE ESTE VÍDEO Y QUIERE CAMBIOS ===
Ya existe la VERSIÓN {version} de este vídeo (su plan está más abajo). El autor la vio y te deja este comentario:

«{comentario}»

Tu trabajo: devolver el plan COMPLETO revisado (el mismo JSON de siempre) que aplique ese comentario, partiendo del PLAN ACTUAL.
- Cambia SOLO lo que pide el autor y lo mínimo para que quede coherente. Todo lo demás se queda IGUAL: copia tal cual los recortes, la cámara, las tarjetas, las imágenes, las ilustraciones… que no toque el comentario.
- NO vuelvas a analizar el vídeo de cero: el PLAN ACTUAL manda. Las reglas generales de más abajo solo dan el formato del JSON y los criterios de calidad; si algo que esas reglas cortarían o añadirían NO está en el plan actual, déjalo como está salvo que el comentario lo pida.
- El autor ve el vídeo EDITADO: cuando dice «en el 3:20» o «al minuto 5» habla del reloj VÍDEO. Tus respuestas van SIEMPRE en segundos del reloj ORIGINAL (así es el JSON). Cada línea de la transcripción trae los dos relojes: conviértelos tú.
- Dentro de cada línea hay marcas [+segundos] (reloj ORIGINAL) en cada pausa y cada ~2 s: úsalas para que un recorte empiece y acabe EXACTAMENTE donde empieza y acaba lo que pide el autor. Una línea trae varias frases: si el autor habla de UNA frase, no cortes la línea entera.
- Las líneas [silencio …] son tramos SIN voz (también el del principio y el del final del vídeo): si el autor pide quitar un silencio o «empezar antes / acabar antes», recorta ese tramo entero (deja ~0.2 s de aire antes de la primera palabra que sigue).
- Lo marcado [QUITADO] NO sale hoy en el vídeo. Si el autor pide recuperar algo, quita o acorta el recorte que lo tapa; si pide quitar algo más, añade un recorte.
- Los recortes con "manual": true los hizo el autor a mano en su editor: consérvalos SIEMPRE, tal cual.
- Si el autor dice que algo le GUSTÓ, consérvalo tal cual aunque a ti te parezca mejorable: lo que elogia no se toca.
- Si el comentario pide editar más fuerte o quitar bastante, puedes recortar más de lo habitual (esta indicación manda sobre la regla de ser conservador). Aun así, un recorte empieza y termina en una pausa o en un borde de frase, nunca dentro de una palabra ni dejando una frase a medias.
- Si el comentario no habla de los títulos, la miniatura, los capítulos, el gancho, el artículo o el hashtag, repítelos IGUAL que en el plan actual.
- "pausas_enfasis" = las pausas de duda de la lista de más abajo que el autor CONSERVA (el programa acorta las demás a 0,3 s). Si pide dejar una pausa tal cual, añade su id; si pide acortar una que está ahí, quítala; si no habla de pausas, cópialo igual.
- Rellena el campo "nota_cambios" con 1 a 3 frases en español, dirigidas al autor: QUÉ entendiste y QUÉ cambiaste (con los tiempos del reloj VÍDEO).

PLAN ACTUAL (versión {version}; todos los tiempos en segundos del ORIGINAL):
{plan_actual}

Sobre la transcripción de más abajo: cada línea muestra los DOS relojes; las marcas [+segundos] de las reglas generales se refieren al reloj ORIGINAL.
=== FIN DE LA REVISIÓN — a continuación, las reglas y la transcripción de siempre ===
"""

_CAMPOS_PLAN = ("recortes", "camara", "tarjetas", "imagenes", "ilustraciones", "palabras_clave", "escenas", "escenas_estilo",
                "titulos_sugeridos", "miniatura_prompts", "miniatura_titulares", "miniatura_caras", "capitulos",
                "gancho_descripcion",
                "articulo_relacionado", "hashtag", "tags", "pausas_enfasis")
# un comentario que habla de pausas o de ritmo manda sobre "pausas_enfasis"; si no, los vetos de antes se conservan
_HABLA_DE_PAUSAS = ("pausa", "silencio", "respir", "ritmo", "dinamic", "rapid", "lent")
# si el comentario no nombra nada de esto, esos campos se quedan como estaban (evita que la IA los reescriba de pasada)
_GRUPOS_TEXTO = (
    (("titulos_sugeridos",), ("titulo", "titulos")),
    (("miniatura_prompts", "miniatura_titulares", "miniatura_caras"), ("miniatura", "thumbnail", "portada")),
    (("capitulos",), ("capitulo", "indice")),
    (("gancho_descripcion", "articulo_relacionado", "hashtag", "tags"),
     ("descripcion", "gancho", "articulo", "hashtag", "etiqueta", "tag")),
    (("palabras_clave",), ("palabra", "texto grande", "textos grandes", "letras grandes", "rotulo")),
    (("escenas",), ("escena", "pantalla completa", "animacion", "ilustracion grande", "fondo naranja", "fondo crema")),
    (("escenas_estilo",), ("escena", "pantalla completa", "grafica", "estudio", "chat", "capitulo", "cita", "frase", "veredicto", "animacion")),
)


def limpia_cita(v) -> str:
    """La `cita` de una tarjeta, imagen o ilustración (palabras copiadas de lo dicho, para anclarla: clipper/anclaje.py): una línea, sin
    comillas ni puntos suspensivos de los lados, ≤ 120 caracteres. '' si no sirve."""
    t = re.sub(r"\s+", " ", str(v or "")).strip().strip("\"'“”«»…. ")
    return t[:120].strip() if len(t) >= 4 else ""


def limpia_tags(v, maximo: int = 5) -> list[str]:
    """Las etiquetas de un vídeo, limpias: sin «#», sin espacios de más, 2-40 caracteres, sin repetirse (comparando sin
    tildes ni mayúsculas), como mucho `maximo`."""
    out: list[str] = []
    vistos: set[str] = set()
    for x in v if isinstance(v, list) else []:
        t = re.sub(r"\s+", " ", str(x or "").replace("#", " ")).strip(" ,.;:")[:40].strip()
        n = " ".join(normalize(t).split())
        if len(t) >= 2 and n and n not in vistos:
            vistos.add(n)
            out.append(t)
        if len(out) >= maximo:
            break
    return out


def _mmss(t: float) -> str:
    m, sg = divmod(int(max(0.0, t)), 60)
    return f"{m}:{sg:02d}"


def _transcripcion_revision(words: list[dict], segs_vistos: list[tuple[float, float]],
                            gap_silencio: float = 1.5, max_palabras: int = 14, max_seg: float = 9.0,
                            fin_total: float | None = None) -> str:
    """La transcripción con los DOS relojes: el del vídeo EDITADO que el autor vio (VÍDEO) y el del ORIGINAL, y
    marcando lo que ya está QUITADO. Así el autor puede decir «en el 3:20» y la IA sabe a qué segundo original
    se refiere. Los silencios largos también se marcan (para «corta este silencio»), con los dos relojes, y
    también el del PRINCIPIO (hasta la primera palabra) y el del FINAL (si se da `fin_total`, la duración del
    original): «quita el silencio del principio» es de los comentarios más naturales y sin marca la IA no sabe que existe."""
    acum: list[tuple[float, float, float]] = []
    v = 0.0
    for a, b in segs_vistos:
        acum.append((a, b, v))
        v += b - a

    def a_v(t: float) -> float | None:
        for a, b, v0 in acum:
            if a - 1e-6 <= t <= b + 1e-6:
                return v0 + (t - a)
        return None

    def marca_silencio(o0: float, o1: float, donde: str = "") -> str:
        v0, v1 = a_v(o0), a_v(o1)
        if v0 is None and v1 is None:
            return f"[silencio de {o1 - o0:.1f}s{donde}, ORIGINAL {o0:.1f}–{o1:.1f}s (ya quitado)]"
        if v0 is not None and v1 is not None:
            rel = f" | VÍDEO {_mmss(v0)}–{_mmss(v1)}"
        elif v0 is not None:
            rel = f" | VÍDEO desde {_mmss(v0)}"
        else:
            rel = f" | VÍDEO hasta {_mmss(v1)}"
        return f"[silencio de {o1 - o0:.1f}s{donde}{rel} | ORIGINAL {o0:.1f}–{o1:.1f}s]"

    lineas: list[str] = []
    run: list[dict] = []
    estado = None
    ult_fin: float | None = None
    if words and float(words[0].get("start", 0)) >= gap_silencio:
        lineas.append(marca_silencio(0.0, float(words[0]["start"]), " al principio"))

    def cierra():
        nonlocal run
        if not run:
            return
        o0, o1 = run[0]["start"], run[-1]["end"]
        partes: list[str] = []
        ult_marca, prev_fin = o0, None
        for i, w in enumerate(run):
            # una marca [+segundo ORIGINAL] en cada pausa y como mucho cada 2 s: así la IA puede empezar/terminar un
            # recorte justo donde empieza/acaba UNA frase, sin tener que cortar la línea entera (que trae varias)
            if i > 0 and (w["start"] - prev_fin >= 0.30 or w["start"] - ult_marca >= 2.0):
                partes.append(f"[+{w['start']:.1f}]")
                ult_marca = w["start"]
            partes.append(str(w["word"]).strip())
            prev_fin = w["end"]
        txt = " ".join(partes)
        if estado == "v":
            v0 = a_v(o0) or 0.0
            v1 = a_v(o1) if a_v(o1) is not None else (a_v(o1 - 0.15) if a_v(o1 - 0.15) is not None else v0)
            lineas.append(f"[VÍDEO {_mmss(v0)}–{_mmss(v1)} | ORIGINAL {o0:.1f}–{o1:.1f}s] {txt}")
        else:
            lineas.append(f"[QUITADO (no sale en el vídeo) | ORIGINAL {o0:.1f}–{o1:.1f}s] {txt}")
        run = []

    for w in words:
        t0 = float(w.get("start", 0))
        t1 = float(w.get("end", t0))
        st = "v" if a_v(t0) is not None else "q"
        gap = (t0 - ult_fin) if ult_fin is not None else 0.0
        if (st != estado or len(run) >= max_palabras or (run and t1 - run[0]["start"] > max_seg)
                or (ult_fin is not None and gap >= gap_silencio)):
            cierra()
            if ult_fin is not None and gap >= gap_silencio:
                lineas.append(marca_silencio(ult_fin, t0))
            estado = st
        run.append({"start": t0, "end": t1, "word": w.get("word", "")})
        ult_fin = t1
    cierra()
    if fin_total and ult_fin is not None and fin_total - ult_fin >= gap_silencio:
        lineas.append(marca_silencio(ult_fin, float(fin_total), " al final"))
    return "\n".join(lineas)


def _bloque_revision(rev: dict) -> str:
    plan = {k: rev["plan"].get(k) for k in _CAMPOS_PLAN}
    return _REVISION.format(version=rev.get("version", 1), comentario=str(rev["texto"]).strip(),
                            plan_actual=json.dumps(plan, ensure_ascii=False, separators=(",", ":")))


def _fusiona_revision(nuevo: dict, rev: dict) -> dict:
    """Red de seguridad sobre lo que devuelve la IA en una revisión: los cortes MANUALES del autor no se pierden
    nunca, y lo que el comentario no menciona (títulos, miniatura, capítulos, descripción) se queda como estaba."""
    from .util import normalize
    previo, coment = rev["plan"], normalize(str(rev["texto"]))

    def _igual(r: dict, x: dict) -> bool:
        return abs(float(r["inicio"]) - float(x["inicio"])) < 0.06 and abs(float(r["fin"]) - float(x["fin"])) < 0.06

    # La IA devuelve los recortes SIN sus marcas (el esquema solo lleva inicio/fin/motivo): un corte tuyo que ella
    # repite tal cual volvía como si fuera de la IA (sin "manual": el tope del % podía tirarlo) y los del programa
    # (`clipper/voz.py`) perdían "auto"/"fin_ia"/"inicio_ia". Se recuperan del plan anterior por sus segundos.
    for x in nuevo.get("recortes") or []:
        for r in previo.get("recortes") or []:
            marcas = {k: r[k] for k in ("manual", "auto", "fin_ia", "inicio_ia") if k in r}
            if marcas and _igual(r, x):
                x.update(marcas)
                break
    for r in previo.get("recortes") or []:
        if r.get("manual") and not any(_igual(r, x) for x in nuevo.get("recortes") or []):
            nuevo.setdefault("recortes", []).append(dict(r))
    for campos, palabras in _GRUPOS_TEXTO:
        if not any(p in coment for p in palabras):
            for c in campos:
                if previo.get(c) not in (None, "", []):
                    nuevo[c] = previo[c]
    # las pausas que el autor conserva: si el comentario no habla de pausas ni de ritmo, no se pierde ninguna (la IA
    # a veces no repite un campo que no le piden tocar); qué pausas vio la IA lo apunta el programa, no ella
    if not any(p in coment for p in _HABLA_DE_PAUSAS):
        nuevo["pausas_enfasis"] = sorted(set(previo.get("pausas_enfasis") or []) | set(nuevo.get("pausas_enfasis") or []))
    if "pausas_vistas" in previo:
        nuevo["pausas_vistas"] = list(previo["pausas_vistas"])
    # la mejora de un plan de ANTES de las palabras clave: sin el campo (no «vacío»), así el render se las pide UNA vez a
    # Flash-Lite como a cualquier plan viejo (la IA de la revisión copia el plan actual, que no traía ninguna)
    if "palabras_clave" not in previo and not nuevo.get("palabras_clave"):
        nuevo.pop("palabras_clave", None)
    if "escenas" not in previo and not nuevo.get("escenas"):
        nuevo.pop("escenas", None)                 # (las escenas de un plan viejo salen de sus ilustraciones, por código)
    if "escenas_estilo" not in previo and not nuevo.get("escenas_estilo"):
        nuevo.pop("escenas_estilo", None)
    if previo.get("miniatura_prompts_anteriores"):          # los conceptos que ya salieron (clipper/conceptos_miniatura.py)
        nuevo["miniatura_prompts_anteriores"] = list(previo["miniatura_prompts_anteriores"])
    return nuevo


def tope_s(cfg, dur_total: float) -> float:
    """Cuánto se espera como mucho a UNA respuesta de Gemini. Crece con la duración del vídeo: con el de 23 min, 3.8 Flash
    tardó hasta 75 s en contestar (medido en «Uso», 2026-09-24), así que uno de 45-60 min (≈2-3 veces más texto que
    leer y que escribir) se habría cortado siempre a los 100 s fijos de antes y NINGÚN modelo habría llegado a
    responder. Un modelo saturado sigue fallando igual de rápido (el 503 llega a los ~110 s, antes del tope)."""
    return min(600.0, float(cfg.get_path("ai.director_timeout_seg", 100)) + 4.0 * max(0.0, dur_total) / 60.0)


def analiza(words: list[dict], segs: list[tuple[float, float]], cfg,
           tema: str = "", contexto: str = "", es_completo: bool = False,
           articulos: list[str] | None = None,
           hashtags: list[str] | None = None, canal: str = "",
           revision: dict | None = None,
           actividad_pantalla: list[dict] | None = None,
           arranque: str | None = None, pausas: str | None = None,
           vision: str | None = None) -> dict | None:
    """El plan de edición de UN vídeo. `es_completo=True` cuando `segs` es
    el vídeo entero de principio a fin (así lo llama Vídeo PRO: un archivo
    subido siempre se analiza entero) — entonces sí se pide título,
    miniatura, capítulos y descripción. `es_completo=False` (por defecto)
    es para un TRAMO suelto de un vídeo más largo (un tema detectado dentro
    de un directo): ahí nada de eso tiene sentido, así que esos campos
    vuelven vacíos. `articulos`/`hashtags`: las listas REALES de las que la
    IA elige para "articulo_relacionado"/"hashtag" (nunca inventa una URL
    ni un hashtag nuevo — ver clipper/enlaces.py). `actividad_pantalla`:
    ventanas [{"inicio","fin"}] de movimiento sostenido en pantalla que
    detectó `clipper/movimiento.py` (código, no Gemini) — una pista más
    para decidir "camara" que no depende de las palabras exactas que uses.
    `arranque`: lo que midió `clipper/voz.py` de las tomas del principio (las
    tomas separadas por pausas y si repites el arranque) — texto para el
    prompt, dentro de la MISMA llamada (cero peticiones más). `pausas`: la
    lista de pausas de duda a media frase (`voz.texto_pausas`) para que la IA
    marque en "pausas_enfasis" las que son a propósito (misma llamada).
    `vision`: lo que SE VE en el vídeo, descrito por otra IA que lo miró
    (`clipper/vision.py`, opción «La IA ve el vídeo»); None = como siempre.
    None si no hay IA disponible — el llamador debe seguir sin director,
    nunca sin vídeo."""
    global ultimo_fallo
    ultimo_fallo = None
    if not segs:
        return None
    g = _gemini(cfg)
    if not g:
        log("AVISO: sin IA no hay director de vídeo (recortes/cámara/tarjetas).")
        ultimo_fallo = {"motivo": "no hay clave de Gemini (GEMINI_API_KEY en el archivo .env) "
                                  "o la IA está apagada en config.yaml (ai.enabled)",
                        "modelos": []}
        return None
    client, types, _model = g
    texto = _transcripcion_marcada(words, segs, cada=float(cfg.get_path("ai.director_marcas_cada_seg", 2.0) or 2.0))
    if revision:                       # el autor ya vio una versión: la transcripción lleva los dos relojes
        texto = _transcripcion_revision(words, revision["segs_vistos"], fin_total=segs[-1][1]) or texto
    if not texto:
        ultimo_fallo = {"motivo": "la transcripción salió vacía (¿el video no tiene voz?)",
                        "modelos": []}
        return None
    dur_total = sum(e - s for s, e in segs)
    instr = _INSTR_TITULO_COMPLETO if es_completo else _INSTR_TITULO_PARCIAL
    lista_articulos = "\n".join(f'- "{a}"' for a in (articulos or [])) or "(sin artículos disponibles)"
    lista_hashtags = ", ".join(hashtags or []) or "#seo"
    # Paleta EXACTA de la marca (videos.marca en config.yaml) para los prompts
    # de miniatura: nunca escrita a mano aquí, así un cambio de colores en el
    # config se refleja solo. Sin paleta definida, la instrucción sigue
    # valiendo pero pide colores sobrios en vez de inventar unos.
    marca = cfg.get_path("videos.marca") or {}
    colores_marca = ", ".join(f"{nombre} {hex_}" for nombre, hex_ in _paleta_hex(marca)) \
        or "(sin paleta definida: usa tinta negra, crema y un solo color de acento)"
    from . import ilustraciones as _ilus
    from . import movimiento as _mov
    prompt = _PROMPT.format(tema=tema or canal or "el canal", contexto=contexto or "canal de YouTube",
                            transcripcion=texto, instruccion_titulo=instr,
                            lista_articulos=lista_articulos, lista_hashtags=lista_hashtags,
                            colores_marca=colores_marca, caras_disponibles=_caras_para_prompt(cfg),
                            lista_ilustraciones=_ilus.catalogo_para_prompt(),
                            ilus_sep=int(cfg.get_path("videos.ilustraciones.separacion_min_seg", 12)),
                            max_sin_cambio=int(float(cfg.get_path("videos.ritmo.max_sin_cambio_seg", 15) or 15)),
                            escena_cada=round(float(cfg.get_path("videos.escenas.cada_seg", 150) or 150) / 60.0, 1),
                            actividad_pantalla=_mov.texto_prompt(actividad_pantalla),
                            tomas_arranque=arranque or "(no medido: aplica la regla tú solo)",
                            pausas_duda=pausas or '(no medidas en este vídeo: deja "pausas_enfasis" vacío)',
                            vision=vision or "(no disponible en este vídeo: decide solo con la transcripción, como siempre)")
    if revision:
        prompt = _bloque_revision(revision) + "\n\n" + prompt
    modelos = _cadena_modelos(cfg, client)
    from . import modelos as _m
    mejor = (_m.cadena_base(cfg, client) or modelos)[0]              # el mejor de todos (aunque ahora se salte por saturado): «principal» = ese
    try:
        crudo, modelo_usado, uso = call_gemini_cadena(
            client, types, modelos, prompt, system=_SYSTEM.format(canal=canal or tema or "de YouTube"), valida=_parsea_plan,
            reintento_s=float(cfg.get_path("ai.director_reintento_seg", 90)),
            pasadas=int(cfg.get_path("ai.director_pasadas", 2)),
            timeout_s=tope_s(cfg, dur_total),
            esquema=_esquema_plan() if cfg.get_path("ai.director_esquema", True) else None,
            etiqueta="mejora" if revision else "analisis")
    except Exception as exc:  # noqa: BLE001
        motivo = _motivo_corto(str(exc))
        log(f"AVISO: el director no pudo analizar «{tema}» con ningún modelo de "
            f"Gemini ({', '.join(modelos)}): {motivo}")
        ultimo_fallo = {"motivo": motivo, "modelos": modelos, "transitorio": es_saturacion(exc)}
        return None

    plan = _valida(crudo, segs, dur_total)
    if revision:
        plan = _fusiona_revision(plan, revision)
        # una revisión puede pedir quitar mucho más de lo habitual: su tope de seguridad es propio
        plan["max_pct"] = float(cfg.get_path("videos.recortes_max_pct_mejora", 0.85))
    plan["ia"] = {"modelo": modelo_usado, "nombre": nombre_modelo(modelo_usado),
                  "principal": modelo_usado == mejor,
                  "tokens_entrada": uso["entrada"], "tokens_salida": uso["salida"]}
    if revision:
        plan["ia"]["mejora"] = True
        plan["ia"]["mejora_texto"] = str(revision.get("texto") or "")     # para reconocerla si se reintenta el render
    log(f"  analizado por {nombre_modelo(modelo_usado)}"
        f"{'' if modelo_usado == mejor else ' (modelo de respaldo: el mejor no respondió)'} — "
        f"{uso['entrada']} tokens de entrada y {uso['salida']} de salida.")
    # cada prompt, SIN texto dibujado (el titular lo escribe el programa) y con la composición de su posición (A, B, C). Los
    # que una revisión deja TAL CUAL del plan anterior no se tocan: ya pasaron por aquí, y los de antes del 26-sep eran los
    # tres «cara a la derecha» (su escena está pensada así)
    _prepara_miniaturas(plan, marca, ((revision or {}).get("plan") or {}).get("miniatura_prompts"))
    log(f"  director «{tema}»:{len(plan['recortes'])} recorte(s), "
        f"{len(plan['camara'])} cambio(s) de cámara, {len(plan['tarjetas'])} "
        f"tarjeta(s), {len(plan['imagenes'])} imagen(es), {len(plan.get('palabras_clave') or [])} palabra(s) clave "
        f"propuestas, {len(plan.get('escenas') or [])} escena(s) a pantalla completa, "
        f"{len(plan.get('escenas_estilo') or [])} de estilo (capítulo/gráfica/chat/bien-mal/frase).")
    if plan.get("titulos_sugeridos"):
        log("  títulos sugeridos: " + " | ".join(plan["titulos_sugeridos"]))
    if plan.get("miniatura_prompts"):
        log(f"  {len(plan['miniatura_prompts'])} prompt(s) de miniatura generados.")
    if plan.get("capitulos"):
        log(f"  {len(plan['capitulos'])} capítulo(s) detectados.")
    if plan.get("articulo_relacionado"):
        log(f"  artículo relacionado: {plan['articulo_relacionado']}")
    if plan.get("hashtag"):
        log(f"  hashtag: {plan['hashtag']}")
    return plan


def cache_path(work_dir: Path, slug: str) -> Path:
    return Path(work_dir) / f"director_{slug}.json"


def cargar(work_dir: Path, slug: str) -> dict | None:
    p = cache_path(work_dir, slug)
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if blob.get("_v") != _DIRECTOR_V:
        return None
    return blob.get("plan")


def guardar(work_dir: Path, slug: str, plan: dict) -> None:
    p = cache_path(work_dir, slug)
    try:
        p.write_text(json.dumps({"_v": _DIRECTOR_V, "plan": plan},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log(f"AVISO: no se pudo guardar el plan del director ({exc}).")


def prueba_valida() -> list[dict]:
    """`_valida()` con el modo de cámara "explicativa" (el autor, 2026-09-22): sin término/definición
    se degrada a "cara" en vez de dejar una tarjeta vacía, los campos se acortan a un tamaño que
    cabe en la tarjeta, y dos tramos seguidos NO se funden en uno si el término es distinto (se
    perdería el segundo). Sin red, sin Gemini: solo prueba la función pura."""
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    segs = [(0.0, 100.0)]

    def plan_camara(*entradas: dict) -> dict:
        return {"camara": list(entradas)}

    res = _valida(plan_camara({"inicio": 5.0, "fin": 15.0, "modo": "explicativa",
                               "termino": "SEO", "definicion": "Optimizar tu web para Google."}),
                  segs, 100.0)
    r("una explicativa con término y definición pasa tal cual",
      len(res["camara"]) == 1 and res["camara"][0]["modo"] == "explicativa"
      and res["camara"][0]["termino"] == "SEO"
      and res["camara"][0]["definicion"] == "Optimizar tu web para Google.", res["camara"])

    res = _valida(plan_camara({"inicio": 5.0, "fin": 15.0, "modo": "explicativa",
                               "definicion": "Le falta el término."}), segs, 100.0)
    r("sin término, se degrada a 'cara' (no una tarjeta vacía)",
      len(res["camara"]) == 1 and res["camara"][0]["modo"] == "cara"
      and "termino" not in res["camara"][0], res["camara"])

    res = _valida(plan_camara({"inicio": 5.0, "fin": 15.0, "modo": "explicativa",
                               "termino": "CMS"}), segs, 100.0)
    r("sin definición, también se degrada a 'cara'",
      len(res["camara"]) == 1 and res["camara"][0]["modo"] == "cara", res["camara"])

    largo_termino = "x" * 80
    largo_def = "y" * 300
    res = _valida(plan_camara({"inicio": 5.0, "fin": 15.0, "modo": "explicativa",
                               "termino": largo_termino, "definicion": largo_def}), segs, 100.0)
    c = res["camara"][0]
    r("término y definición se recortan a un tamaño que cabe en la tarjeta",
      len(c["termino"]) <= 40 and len(c["definicion"]) <= 160,
      (len(c["termino"]), len(c["definicion"])))

    res = _valida(plan_camara(
        {"inicio": 5.0, "fin": 15.0, "modo": "explicativa", "termino": "SEO", "definicion": "def A"},
        {"inicio": 15.0, "fin": 25.0, "modo": "explicativa", "termino": "SEO", "definicion": "def A"},
    ), segs, 100.0)
    r("dos tramos seguidos con el MISMO término se funden en uno solo",
      len(res["camara"]) == 1 and res["camara"][0]["fin"] == 25.0, res["camara"])

    res = _valida(plan_camara(
        {"inicio": 5.0, "fin": 15.0, "modo": "explicativa", "termino": "SEO", "definicion": "def A"},
        {"inicio": 15.0, "fin": 25.0, "modo": "explicativa", "termino": "CMS", "definicion": "def B"},
    ), segs, 100.0)
    r("dos tramos seguidos con término DISTINTO NO se funden (se perdería el segundo)",
      len(res["camara"]) == 2
      and {c["termino"] for c in res["camara"]} == {"SEO", "CMS"}, res["camara"])

    res = _valida(plan_camara({"inicio": 5.0, "fin": 15.0, "modo": "inventado"}), segs, 100.0)
    r("un modo que no existe se sigue descartando como siempre (sin relación con explicativa)",
      len(res["camara"]) == 0, res["camara"])

    class _Cfg:
        def get_path(self, k, d=None):
            return d
    r("la espera a Gemini crece con el vídeo (23 min: 192 s; 60 min: 340 s) y nunca pasa de 10 min",
      tope_s(_Cfg(), 23 * 60) == 192.0 and tope_s(_Cfg(), 60 * 60) == 340.0 and tope_s(_Cfg(), 5 * 3600) == 600.0,
      (tope_s(_Cfg(), 23 * 60), tope_s(_Cfg(), 60 * 60)))

    # PALABRAS CLAVE (clipper/ritmo.py, 2026-09-25)
    res = _valida({"palabras_clave": [
        {"t": 30.0, "texto": "NO VENDIÓ {NADA}", "importancia": 3},
        {"t": 10.0, "texto": "3,6%", "importancia": 9},
        {"t": 40.0, "texto": "UNA FRASE DEMASIADO LARGA PARA UNA LÍNEA", "importancia": 2},
        {"t": 500.0, "texto": "FUERA", "importancia": 2},
        {"t": 50.0, "texto": "  ", "importancia": 2}]}, segs, 100.0)
    r("palabras clave: sin llaves, importancia 1-3, en orden; fuera las largas, vacías o fuera del vídeo",
      res["palabras_clave"] == [{"t": 10.0, "texto": "3,6%", "importancia": 3},
                                {"t": 30.0, "texto": "NO VENDIÓ NADA", "importancia": 3}], res["palabras_clave"])
    esquema = _esquema_plan()
    r("el esquema de Gemini pide «palabras_clave» (si no, el JSON no podría traerlas)",
      esquema is None or "palabras_clave" in esquema.model_fields, list(getattr(esquema, "model_fields", {}))[:12])
    previo = {"palabras_clave": [{"t": 5.0, "texto": "GRATIS", "importancia": 3}], "recortes": []}
    nuevo = _fusiona_revision({"palabras_clave": [], "recortes": []}, {"plan": previo, "texto": "corta el minuto 2"})
    otro = _fusiona_revision({"palabras_clave": [], "recortes": []},
                             {"plan": previo, "texto": "quita las palabras clave, no me gustan"})
    r("en una mejora se conservan salvo que el comentario hable de ellas",
      nuevo["palabras_clave"] == previo["palabras_clave"] and otro["palabras_clave"] == [],
      (nuevo["palabras_clave"], otro["palabras_clave"]))
    # ESCENAS A PANTALLA COMPLETA (clipper/escena_completa.py, 2026-09-26)
    res = _valida({"escenas": [
        {"t": 30.9, "tipo": "grafica_sube", "frase": "ESTO VA A {EVOLUCIONAR}", "duracion": 20},
        {"t": 40.0, "tipo": "inventada", "frase": "NO", "duracion": 6},
        {"t": 50.0, "tipo": "lupa", "frase": "", "duracion": 6},
        {"t": 60.0, "tipo": "lupa", "frase": "UNA FRASE LARGUÍSIMA QUE NO CABE EN DOS LÍNEAS", "duracion": 6}]}, segs, 100.0)
    r("escenas: ilustración de la biblioteca, frase que cabe (sin llaves) y 4,5-7,5 s; lo demás fuera",
      res["escenas"] == [{"t": 30.9, "tipo": "grafica_sube", "frase": "ESTO VA A EVOLUCIONAR", "duracion": 7.5}],
      res["escenas"])
    r("el esquema de Gemini pide «escenas»", esquema is None or "escenas" in esquema.model_fields)
    res = _valida({"escenas_estilo": [
        {"estilo": "capitulo", "t": 20.0, "duracion": 9, "cita": "numero dos deja que un humano", "numero": 2, "titulo": "Deja que un humano escriba"},
        {"estilo": "grafica", "t": 40.0, "cita": "el estudio dice que los textos humanos", "titulo": "Tiempo en página",
         "barras": [{"nombre": "Humano", "valor": 151, "etiqueta": "2:31"}, {"nombre": "IA", "valor": 114, "etiqueta": "1:54"}]},
        {"estilo": "grafica", "t": 50.0, "cita": "el estudio dice que los textos", "titulo": "Una sola", "barras": [{"nombre": "a", "valor": 1, "etiqueta": "1"}]},
        {"estilo": "cita", "t": 500.0, "cita": "una frase muy importante de verdad", "frase": "Una frase muy importante de verdad"},
        {"estilo": "baile", "t": 10.0, "cita": "una dos tres cuatro"}]}, segs, 100.0)
    r("escenas_estilo: lo completo se queda (duración ajustada al estilo); sin lo imprescindible, fuera de rango o de un estilo inventado, se tira",
      [e["estilo"] for e in res["escenas_estilo"]] == ["capitulo", "grafica"] and res["escenas_estilo"][0]["duracion"] == 4.5, res["escenas_estilo"])
    r("el esquema de Gemini pide «escenas_estilo» (opcional: un plan viejo sin él sigue valiendo)",
      esquema is None or ("escenas_estilo" in esquema.model_fields and not esquema.model_fields["escenas_estilo"].is_required()))
    viejo_e = _fusiona_revision({"escenas_estilo": [], "recortes": []}, {"plan": {"recortes": []}, "texto": "corta el 2:00"})
    r("la mejora de un plan de ANTES (sin escenas de estilo) sigue sin el campo", "escenas_estilo" not in viejo_e, viejo_e)
    res = _valida({"miniatura_caras": ["feliz", "sorpresa", "  ", "pensando", "x" * 80]}, segs, 100.0)
    r("miniatura_caras: hasta 3 nombres cortos (la cara del autor en cada miniatura A/B/C)",
      res["miniatura_caras"] == ["feliz", "sorpresa", "pensando"], res["miniatura_caras"])
    r("el esquema de Gemini pide «miniatura_caras» (opcional: un plan viejo sin él sigue valiendo)",
      esquema is None or ("miniatura_caras" in esquema.model_fields
                          and not esquema.model_fields["miniatura_caras"].is_required()))
    conserva = _fusiona_revision({"miniatura_caras": ["what"], "recortes": []},
                                 {"plan": {"miniatura_caras": ["feliz", "feliz", "feliz"], "recortes": []},
                                  "texto": "quita el corte del 2:00"})
    r("en una mejora que no habla de miniaturas, las caras elegidas se quedan como estaban",
      conserva["miniatura_caras"] == ["feliz", "feliz", "feliz"], conserva["miniatura_caras"])
    viejo = _fusiona_revision({"palabras_clave": [], "recortes": []}, {"plan": {"recortes": []}, "texto": "corta el 2:00"})
    r("la mejora de un plan de ANTES (sin el campo) sigue sin él: el render se las pide una vez a Flash-Lite",
      "palabras_clave" not in viejo, viejo)
    # NINGÚN texto dibujado por la IA en las miniaturas: el titular va en su campo y lo escribe el programa (2026-10-03)
    res = _valida({"miniatura_titulares": ["  «¿NO APARECES?» ", "", "indexa ya, hoy mismo, por favor, ahora mismo", "extra"]},
                  segs, 100.0)
    r("miniatura_titulares: uno por miniatura EN SU POSICIÓN (un hueco queda vacío), sin comillas, ≤ 5 palabras y 40 caracteres, "
      "como mucho 3", res["miniatura_titulares"] == ["¿NO APARECES?", "", "indexa ya, hoy mismo, por"], res["miniatura_titulares"])
    r("sin titulares (o todos vacíos) la lista queda vacía: un plan viejo sigue valiendo",
      _valida({"miniatura_titulares": ["", " "]}, segs, 100.0)["miniatura_titulares"] == []
      and _valida({}, segs, 100.0)["miniatura_titulares"] == [])
    r("el esquema de Gemini pide «miniatura_titulares» (opcional: un plan viejo sin él sigue valiendo)",
      esquema is None or ("miniatura_titulares" in esquema.model_fields
                          and not esquema.model_fields["miniatura_titulares"].is_required()))
    c2 = _fusiona_revision({"miniatura_titulares": ["x", "y", "z"], "recortes": []},
                           {"plan": {"miniatura_titulares": ["A", "B", "C"], "recortes": [],
                                     "miniatura_prompts_anteriores": [{"letra": "B", "prompt": "p"}]},
                            "texto": "quita el corte del 2:00"})
    r("en una mejora que no habla de miniaturas, los titulares se quedan y la lista de conceptos anteriores no se pierde",
      c2["miniatura_titulares"] == ["A", "B", "C"] and c2["miniatura_prompts_anteriores"] == [{"letra": "B", "prompt": "p"}], c2)
    c3 = _fusiona_revision({"miniatura_titulares": ["x", "y", "z"], "recortes": []},
                           {"plan": {"miniatura_titulares": ["A", "B", "C"], "recortes": []}, "texto": "cambia las miniaturas"})
    r("…y si el comentario habla de la miniatura, valen los titulares nuevos", c3["miniatura_titulares"] == ["x", "y", "z"], c3)

    # 🎯 la CITA de tarjetas, imágenes e ilustraciones (anclaje.py): pasa limpia, o no pasa
    pl = _valida({"tarjetas": [{"t": 10.0, "tipo": "herramienta", "texto": "Google Developers", "cita": "  «hacia google developers…»  "},
                               {"t": 20.0, "tipo": "herramienta", "texto": "Astro", "cita": "ab"},
                               {"t": 30.0, "tipo": "herramienta", "texto": "Vercel"}],
                  "imagenes": [{"t": 40.0, "busqueda": "polar bear", "cita": "un oso polar"}],
                  "ilustraciones": [{"t": 50.0, "tipo": "grafica_sube", "etiqueta": "Más tráfico", "cita": "sube el tráfico"}]}, segs, 100.0)
    r("la cita de una tarjeta pasa sin comillas ni «…»; una demasiado corta o ausente no deja rastro",
      pl["tarjetas"][0].get("cita") == "hacia google developers" and "cita" not in pl["tarjetas"][1] and "cita" not in pl["tarjetas"][2], pl["tarjetas"])
    r("…y también la de una imagen y la de una ilustración", pl["imagenes"][0].get("cita") == "un oso polar"
      and pl["ilustraciones"][0].get("cita") == "sube el tráfico", (pl["imagenes"], pl["ilustraciones"]))
    esquema = _esquema_plan()
    r("el esquema de Gemini lleva el campo `cita` (opcional) en tarjetas, imágenes e ilustraciones: un plan de antes sigue valiendo",
      bool(esquema) and all(not c.model_fields["cita"].is_required() for c in (
          esquema.model_fields["tarjetas"].annotation.__args__[0], esquema.model_fields["imagenes"].annotation.__args__[0],
          esquema.model_fields["ilustraciones"].annotation.__args__[0])))
    r("el prompt pide la cita y el ancla (y se sigue formando con sus llaves)",
      "ANCLAJE" in _PROMPT and '"cita": "abrimos Google Search Console"' in _PROMPT)
    marcas = _transcripcion_marcada([{"word": f"p{i}", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(60)], [(0.0, 24.0)], cada=2.0)
    n_marcas = marcas.count("[+")
    r("las marcas de tiempo de la transcripción salen cada ~2 s seguidos (antes cada 5: Gemini se pegaba a la anterior)",
      10 <= n_marcas <= 14, (n_marcas, marcas[:80]))

    return out


def prueba_revision() -> list[dict]:
    """Casos de la REVISIÓN (el botón «Mejorar»): los dos relojes, lo QUITADO, lo que la IA no puede tocar (cortes
    manuales, títulos no mencionados) y que el comentario del autor llega al prompt. Gemini FALSO: no gasta nada."""
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    W = lambda w, a, b: {"word": w, "start": a, "end": b}                                     # noqa: E731
    words = [W("hola", 1.0, 1.4), W("mundo", 1.5, 2.0), W("quitado", 12.0, 12.5), W("sigue", 16.0, 16.4), W("bien", 16.5, 17.0)]
    segs = [(0.0, 10.0), (15.0, 30.0)]                    # se ve 0-10 y 15-30 del original; el 10-15 está cortado
    t = _transcripcion_revision(words, segs)
    caso("una palabra del original a los 16 s sale en el reloj del VÍDEO a los 0:11 (10 s del primer tramo + 1)",
         "VÍDEO 0:11" in t and "ORIGINAL 16.0" in t, t)
    caso("lo que está cortado sale marcado [QUITADO] con su tiempo original", "[QUITADO" in t and "12.0–12.5" in t)
    caso("un silencio largo se marca con su tiempo", "silencio de" in t and "ORIGINAL 2.0–12.0s" in t)
    _m = {"naranja": "#FE330A", "crema": "#FFFBF2", "beige": "#F1E9DA", "dorado": "#FFC93C", "tinta": "#14100F"}
    _p1 = _asegura_reglas_miniatura("A robot with a lightbulb pointing at a web logo. A face cutout will be placed on the right.", _m)
    _p2 = _asegura_reglas_miniatura(_p1, _m)
    _p3 = _asegura_reglas_miniatura(_p1 + " " + _p1[_p1.index("Use ONLY"):], _m)          # (un duplicado que ya se hubiera colado)
    caso("los prompts de miniatura son idempotentes: en una revisión no se duplica el bloque de la paleta",
         _p1 == _p2 == _p3 and _p1.count("Use ONLY this exact color palette") == 1, _p2[-200:])
    # el A/B/C de miniaturas con TRES composiciones (el autor, 2026-09-26): la zona de la cara la pone el código por posición
    _ia = ["On the left, a giant laptop with a rising chart and a robot. The right 35-40% of the frame is kept clean with "
           "only dark halftone texture. Keep the right 35-40% of the frame to low-importance background details only "
           "(dark halftone texture, speed lines, sparks) — a face cutout will be placed there.",
           "An old dusty magnifying glass on one side and a glowing AI chip on the other. Headline 'SEO PARA IA' top left.",
           "One huge orange search bar on a flat cream background, headline 'SALES EN LA IA' right next to it."]
    _abc = [_asegura_reglas_miniatura(x, _m, i) for i, x in enumerate(_ia)]
    caso("A, B y C llevan cada una SU zona de la cara (derecha, centro, minimalista) y ninguna la de otra",
         all(f in _abc[i] and sum(g in _abc[i] for _, g in _FORMATOS_MINIATURA) == 1
             for i, (_, f) in enumerate(_FORMATOS_MINIATURA)), [x[-330:-250] for x in _abc])
    caso("la frase de la zona de la cara que escribe la IA (con o sin «cutout») se quita: no sale dos veces ni se contradice",
         _abc[0].count("35-40%") == 1 and "kept clean" not in _abc[0] and "giant laptop" in _abc[0]
         and "old dusty magnifying glass" in _abc[1] and "huge orange search bar" in _abc[2], _abc[0][:400])
    _escena = "A giant clean desktop fills the left 60% of the frame, with a free trial badge."
    caso("una frase de la ESCENA que dice «clean»/«free» y un % del cuadro no se toma por la de la zona de la cara",
         _escena in _asegura_reglas_miniatura(_escena, _m, 0))
    caso("cada formato es idempotente (una revisión que lo vuelve a pasar no cambia nada)",
         all(_asegura_reglas_miniatura(x, _m, i) == x for i, x in enumerate(_abc)))
    # la B prueba OTROS colores de fondo (el autor, 2026-10-04: «las miniaturas tienen que ser llamativas… que en el ABC test haya una que pruebe otros colores, sin olvidar mis colores»)
    from . import miniaturas_ia as _mia0
    _oscura = ("A calendar crossed out on the left and a rising arrow on the right. Ink background with cream halftone patterns. "
               "Thick black ink outlines and a dark ink-colored background behind the arrow.")
    _pb, _pa, _pc = (_asegura_reglas_miniatura(_oscura, _m, i) for i in (1, 0, 2))
    caso("la B lleva SIEMPRE un fondo de COLOR de la marca (naranja, dorado o dividido; no negro) y su «Ink background» pasa a fondo de color; los contornos «black ink outlines» se quedan",
         "whole background is a bold, saturated colour field" in _pb and "flat gold #FFC93C" in _pb and "flat vivid red-orange #FE330A" in _pb and "not black and not dark" in _pb
         and "Ink background" not in _pb and "dark ink-colored background" not in _pb and "bold flat colour background" in _pb and "Thick black ink outlines" in _pb, _pb[-520:])
    caso("…la A y la C no se tocan (siguen con su fondo tinta y sin esa frase)",
         "whole background" not in _pa and "whole background" not in _pc and "Ink background" in _pa and "Ink background" in _pc, (_pa[-200:], _pc[-200:]))
    caso("…y es idempotente (una revisión que lo vuelve a pasar no duplica la frase) y va ANTES de «sin texto» y de la paleta",
         _asegura_reglas_miniatura(_pb, _m, 1) == _pb and _pb.count("whole background is a bold") == 1
         and _pb.index("whole background") < _pb.index(_mia0.SIN_TEXTO) < _pb.index("Use ONLY this exact color palette"), _pb[-300:])
    caso("el prompt del director pide miniaturas LLAMATIVAS y la B con FONDO DE COLOR (A y C con la tinta de siempre)",
         "LLAMATIVAS" in _PROMPT and "B lleva un FONDO DE COLOR de la marca" in _PROMPT and "A y C siguen con el fondo tinta" in _PROMPT and "otro color de fondo" in _PROMPT)
    # NINGÚN texto dibujado por la IA en las miniaturas (el autor, 2026-10-03: «la IA es súper mala poniendo textos»)
    from . import miniaturas_ia as _mia
    _crudo = ["A giant stopwatch on the left. Bold text banner on the top left reading '¿CUÁNTO TARDA?'. A Google Search "
              "Console logo on a laptop screen.",
              "A calendar marked with '15 DÍAS' crossed out on the left and a rising arrow on the right. Banner 'LA VERDAD'.",
              "One huge orange hourglass on a flat dark background."]
    _plan = {"miniatura_prompts": list(_crudo), "miniatura_titulares": ["", "GANCHO DEL DIRECTOR", ""]}
    _prepara_miniaturas(_plan, _m)
    _pp = _plan["miniatura_prompts"]
    caso("los prompts de miniatura salen SIN texto dibujado (ni banner, ni entrecomillados, ni «logo») y con la regla fija de «ningún texto»",
         all(not re.search(r"banner|reading|CUÁNTO|15 DÍAS|VERDAD|\blogo\b|'", x.replace(_mia.SIN_TEXTO, "")) and _mia.SIN_TEXTO in x
             for x in _pp), [x[:200] for x in _pp])
    caso("…y cada uno conserva SU escena y las reglas fijas de su posición (zona de la cara, la paleta una sola vez)",
         "giant stopwatch" in _pp[0] and "calendar" in _pp[1] and "hourglass" in _pp[2]
         and all(_FORMATOS_MINIATURA[i][1] in _pp[i] and _pp[i].count("Use ONLY this exact color palette") == 1 for i in range(3)))
    caso("el titular sale del campo del director o, si no lo dio, del entrecomillado del prompt (el modo de antes); sin ninguno, vacío",
         _plan["miniatura_titulares"] == ["¿CUÁNTO TARDA?", "GANCHO DEL DIRECTOR", ""], _plan["miniatura_titulares"])
    _otra = {"miniatura_prompts": list(_pp), "miniatura_titulares": list(_plan["miniatura_titulares"])}
    _prepara_miniaturas(_otra, _m, _pp)
    caso("los prompts que una revisión deja tal cual no se tocan", _otra["miniatura_prompts"] == _pp, _otra["miniatura_prompts"])
    _sola = {"miniatura_prompts": [_crudo[2]]}
    _prepara_miniaturas(_sola, _m)
    caso("sin titulares en ninguna parte la lista queda vacía: la miniatura sale sin ningún texto", _sola["miniatura_titulares"] == [])
    caso("el prompt del director pide miniaturas SIN texto y los titulares aparte (ya no pide el rótulo entre comillas)",
         "NINGÚN TEXTO DENTRO DE LA IMAGEN" in _PROMPT and '"miniatura_titulares"' in _PROMPT
         and "rótulo/banner" not in _PROMPT and "EXACTO entre comillas" not in _PROMPT)
    _w = [{"word": "hola", "start": 24.6, "end": 25.0}, {"word": "que", "start": 25.1, "end": 25.3}, {"word": "tal", "start": 25.8, "end": 26.2},
          {"word": "adios", "start": 40.0, "end": 40.5}]
    t2 = _transcripcion_revision(_w, [(0.0, 46.0)], fin_total=46.0)
    caso("el silencio del PRINCIPIO (hasta la primera palabra) se marca con los dos relojes",
         t2.splitlines()[0].startswith("[silencio de 24.6s al principio | VÍDEO 0:00–0:24 | ORIGINAL 0.0–24.6s]"), t2.splitlines()[0])
    caso("el silencio del FINAL (tras la última palabra) también", "al final | VÍDEO 0:40–0:46 | ORIGINAL 40.5–46.0s" in t2 or "al final | VÍDEO 0:41–0:46 | ORIGINAL 40.5–46.0s" in t2, t2.splitlines()[-1])
    caso("dentro de una línea hay marcas [+s] tras una pausa (para cortar UNA frase y no la línea entera)", "[+25.8]" in t2 and "[+25.1]" not in t2, t2)
    t3 = _transcripcion_revision(_w, [(24.5, 46.0)], fin_total=46.0)
    caso("si el silencio del principio ya está quitado lo dice, y el reloj VÍDEO arranca en 0:00 en la primera palabra",
         "(ya quitado)" in t3.splitlines()[0] or "VÍDEO hasta 0:00" in t3.splitlines()[0], t3.splitlines()[0])
    plan = {"recortes": [{"inicio": 10.0, "fin": 15.0, "motivo": "relleno"}, {"inicio": 40.0, "fin": 42.0, "motivo": "manual", "manual": True}],
            "camara": [], "tarjetas": [], "imagenes": [], "ilustraciones": [], "titulos_sugeridos": ["A", "B", "C"],
            "miniatura_prompts": ["x", "y", "z"], "capitulos": [], "gancho_descripcion": "gancho viejo",
            "articulo_relacionado": "art", "hashtag": "#seo"}
    b = _bloque_revision({"plan": plan, "texto": "quita la parte de WordPress", "version": 2})
    caso("el bloque lleva el comentario, la versión y el plan actual", "quita la parte de WordPress" in b and "VERSIÓN 2" in b and '"gancho_descripcion":"gancho viejo"' in b)
    nuevo = {"recortes": [{"inicio": 10.0, "fin": 15.0, "motivo": "relleno"}], "titulos_sugeridos": ["N1", "N2", "N3"],
             "miniatura_prompts": ["n"], "capitulos": [], "gancho_descripcion": "gancho nuevo", "articulo_relacionado": "otro", "hashtag": "#x"}
    f1 = _fusiona_revision(dict(nuevo), {"plan": plan, "texto": "quita la parte de WordPress"})
    caso("un corte MANUAL del autor no se pierde aunque la IA lo haya soltado", any(r.get("manual") and r["inicio"] == 40.0 for r in f1["recortes"]))
    caso("si el comentario no habla de títulos/descripción, se quedan como estaban",
         f1["titulos_sugeridos"] == ["A", "B", "C"] and f1["gancho_descripcion"] == "gancho viejo" and f1["hashtag"] == "#seo")
    f2 = _fusiona_revision(dict(nuevo), {"plan": plan, "texto": "cámbiame el título por algo más corto"})
    caso("si el comentario SÍ habla de los títulos, se aceptan los nuevos", f2["titulos_sugeridos"] == ["N1", "N2", "N3"] and f2["gancho_descripcion"] == "gancho viejo")
    # la IA repite los cortes SIN sus marcas (el esquema no las lleva): se recuperan del plan anterior por sus segundos
    previo_m = {"recortes": [{"inicio": 0.0, "fin": 2.87, "motivo": "silencio", "fin_ia": 4.0},
                             {"inicio": 4.09, "fin": 5.67, "motivo": "pausa al arrancar", "auto": "arranque"},
                             {"inicio": 40.0, "fin": 42.0, "motivo": "manual", "manual": True}]}
    eco = {"recortes": [{"inicio": 0.0, "fin": 2.87, "motivo": "silencio"}, {"inicio": 4.09, "fin": 5.67, "motivo": "pausa"},
                        {"inicio": 40.0, "fin": 42.0, "motivo": "manual"}]}
    f3 = _fusiona_revision(eco, {"plan": previo_m, "texto": "pon una tarjeta en el 3:00"})
    caso("un corte tuyo que la IA repite tal cual sigue siendo MANUAL (antes volvía como de la IA y el tope del % "
         "podía tirarlo), y los del programa conservan su marca", len(f3["recortes"]) == 3
         and f3["recortes"][2].get("manual") is True and f3["recortes"][1].get("auto") == "arranque"
         and f3["recortes"][0].get("fin_ia") == 4.0, f3["recortes"])
    # las pausas de duda (clipper/voz.py): los vetos de la IA y los bordes de sus cortes que llevó el programa al silencio
    previo_p = {"recortes": [{"inicio": 10.06, "fin": 13.42, "motivo": "silencio", "inicio_ia": 9.8, "fin_ia": 13.7}],
                "pausas_enfasis": ["p33.0"], "pausas_vistas": ["p13.0", "p33.0"]}
    f4 = _fusiona_revision({"recortes": [{"inicio": 10.1, "fin": 13.4, "motivo": "silencio"}], "pausas_enfasis": []},
                           {"plan": previo_p, "texto": "pon una tarjeta en el 3:00"})
    caso("mejora que no habla de pausas: la pausa que la IA conservaba por énfasis se sigue conservando (aunque no la "
         "repita), se sabe qué pausas vio y el corte recupera sus segundos originales (inicio_ia/fin_ia)",
         f4["pausas_enfasis"] == ["p33.0"] and f4["pausas_vistas"] == ["p13.0", "p33.0"]
         and f4["recortes"][0].get("inicio_ia") == 9.8 and f4["recortes"][0].get("fin_ia") == 13.7, f4)
    f5 = _fusiona_revision({"recortes": [], "pausas_enfasis": []},
                           {"plan": previo_p, "texto": "hazlo más dinámico: acorta también la pausa del 0:30"})
    caso("mejora que SÍ habla de pausas: manda lo que devuelve la IA", f5["pausas_enfasis"] == [], f5)
    v = _valida({"recortes": [], "pausas_enfasis": ["p123.4", "p5", "hola", 7, " p12.3 ", "p1e5", "p12.3"]},
                [(0.0, 200.0)], 200.0)
    caso("«pausas_enfasis» solo guarda ids de pausa bien formados, sin repetir",
         v["pausas_enfasis"] == ["p12.3", "p123.4", "p5"], v["pausas_enfasis"])

    # analiza() completo con Gemini falso: el comentario y los dos relojes llegan al prompt
    from . import config
    cap: dict = {}
    real = call_gemini_cadena
    g = globals()

    def falso(client, types, modelos, prompt, system=None, valida=None, **k):
        cap["p"] = prompt
        return ({"recortes": [{"inicio": 12.0, "fin": 12.5, "motivo": "x"}], "camara": [], "tarjetas": [], "imagenes": [], "ilustraciones": [],
                 "titulos_sugeridos": ["a", "b", "c"], "miniatura_prompts": ["x", "y", "z"], "capitulos": [], "gancho_descripcion": "g",
                 "articulo_relacionado": "", "hashtag": "", "nota_cambios": "Quité lo de WordPress (0:11)."}, modelos[0], {"entrada": 1, "salida": 1})
    try:
        g["call_gemini_cadena"] = falso
        cfg = config.load()
        res = analiza(words, [(0.0, 30.0)], cfg, tema="T", contexto="ctx", es_completo=True, articulos=[], hashtags=["#seo"], canal="C",
                      revision={"plan": plan, "texto": "quita la parte de WordPress", "version": 2, "segs_vistos": segs},
                      pausas="- p13.0 (1.4s): «…la clave de» [PAUSA] «todo esto…»")
        if res is None:
            caso("el comentario del autor y los dos relojes llegan al prompt de Gemini (omitido: no hay clave de Gemini)", True)
        else:
            p = cap["p"]
            caso("el comentario del autor y los dos relojes llegan al prompt de Gemini, y el plan sale con su nota y su tope propio",
                 "REVISIÓN" in p and "quita la parte de WordPress" in p and "[QUITADO" in p and "ORIGINAL 16.0" in p
                 and res.get("nota_cambios", "").startswith("Quité") and res.get("max_pct") == 0.85 and res["ia"].get("mejora") is True
                 and res["ia"].get("mejora_texto") == "quita la parte de WordPress"      # así un reintento reconoce la misma mejora
                 and any(r.get("manual") for r in res["recortes"]), p[:120])
            caso("la lista de pausas de duda llega al prompt, en la misma llamada",
                 "p13.0 (1.4s)" in p and "pausas_enfasis" in p, p[p.find("PAUSAS DE DUDA"):][:200])
    finally:
        g["call_gemini_cadena"] = real
    return filas
