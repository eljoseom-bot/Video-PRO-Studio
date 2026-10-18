"""Shorts verticales sacados del vídeo YA EDITADO (el autor, 2026-09-25). Se crean solos en cada render de Vídeo PRO y salen
abajo del todo en la página del vídeo («Shorts que salieron»); si ninguno se sostiene solo, «No salió ningún Short».
Este programa es de vídeos largos: los Shorts son un extra que nunca frena ni rompe el vídeo principal.

Sus dos preocupaciones y cómo se atacan:
1. «Que quede a medias»: la IA NO elige segundos, elige FRASES completas, numeradas por código a partir de las pausas
   reales del audio (`frases`); y el código comprueba que el principio se entiende solo (sin «como les decía…», «entonces…»)
   y que el final cierra la idea (sin «ahora les voy a mostrar…») — si no, amplía una o dos frases o lo descarta (`valida`).
2. «Que no diga nada interesante»: la IA justifica cada candidato (la idea en una frase, gancho, remate, por qué vale, nota
   de 1 a 5; solo pasan los de 4 o más) y una SEGUNDA opinión a ciegas (Flash-Lite, solo con el texto del Short, sin el
   resto del vídeo) confirma que se entiende solo y que la idea queda completa (`segunda_opinion`). Mejor 0 que uno flojo.

Coste (dicho antes de construir): 1 petición al mejor Flash por versión NUEVA (análisis o mejora) y 1 a Flash-Lite; se
guardan en `work/<base>--pro/shorts_<slug>.json`, así «Reintentar» no gasta nada. Duración libre, entre 15 s y 2 min
(el autor). Máximo 3 por vídeo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import util as _util
from .util import log, normalize

VERSION = 2
MIN_S, MAX_S = 15.0, 120.0          # duración de un Short (el autor: «libre, máximo dos minutos»)
MAX_SHORTS = 5
NOTA_MIN = 3                       # (17-oct: antes 4; ahora el autor revisa los propuestos y los edita luego en «Shorts · TikTok»)
FRASE_MAX_S = 20.0                  # una «frase» más larga que esto se parte en pausas más cortas

# un Short no puede EMPEZAR así: remite a algo anterior que el espectador del Short no vio
_ARRANQUES_COLGANTES = ("como les decia", "como te decia", "como les dije", "como dije", "como vimos", "como ya",
                        "lo que les decia", "esto que", "eso que", "entonces", "y ademas", "ademas", "tambien",
                        "por eso", "otra cosa", "y otra", "asi que", "o sea", "pero ", "y ", "igual", "lo mismo",
                        "de hecho", "en este caso", "aqui tambien", "aca tambien", "y bueno", "bueno y")
# ni ACABAR así: anuncia algo que ya no está en el Short
_FINALES_COLGANTES = ("les voy a mostrar", "les voy a ensenar", "te voy a mostrar", "te voy a ensenar", "vamos a ver",
                      "les muestro", "te muestro", "miren esto", "lo primero", "primero", "ahora les", "ahora te",
                      "a continuacion", "vamos con", "les explico", "te explico")
TIPOS = ("dato", "error_comun", "truco", "opinion", "comparacion", "antes_despues", "otro")

_SYSTEM = """Eres el editor de Shorts del canal de YouTube "{canal}": eliges trozos de un vídeo largo YA editado que
funcionen SOLOS como Short vertical. Eres exigente: un Short flojo o a medias le quita espectadores al canal. Respondes
SOLO con JSON válido, sin markdown."""

_PROMPT = """Contexto del canal: {contexto}

Este es el vídeo YA editado, frase por frase (F = número de frase, con su minuto en el vídeo):
{frases}

Capítulos del vídeo:
{capitulos}

Busca de 0 a {maximo} trozos que funcionen como SHORT por sí solos. Cada Short es un rango de frases CONSECUTIVAS («desde»
y «hasta» son números F). Reglas, todas obligatorias:
- Se entiende SOLO, sin haber visto el resto: no empieza remitiendo a algo anterior («como les decía», «entonces», «y
  además», «esto…») ni da por sabido lo que se explicó antes. Si una explicación buena necesita el contexto de las frases
  anteriores, EMPIEZA ANTES e inclúyelo (hasta 4 frases antes) en vez de descartarla: se prefiere un fragmento medianamente
  largo y con contexto (hasta {max_s} s, p. ej. una explicación completa de un tema como el interlinking) a uno corto y suelto.
- Cuenta UNA idea COMPLETA: arranca con algo que engancha en los primeros segundos (una pregunta, un dato, un error, una
  afirmación fuerte) y termina con el REMATE — la frase donde se cumple lo prometido. No termina anunciando algo que ya no
  está («ahora les voy a mostrar…»).
- Dura entre {min_s} y {max_s} segundos (mira los minutos de las frases).
- Vale la pena por una razón CONCRETA: "dato" (una cifra o un hecho sorprendente), "error_comun", "truco" (algo que se
  puede aplicar ya), "opinion" (fuerte y argumentada), "comparacion", "antes_despues"; si no es nada de eso, NO lo pongas.
- Nota de 1 a 5: 5 = alguien que no conoce el canal lo vería entero y aprendería algo; 3 o menos = no lo propongas.
- Si NINGÚN trozo cumple todo, devuelve la lista vacía y di por qué en "ninguno_por_que". Es mejor cero que uno flojo.

Para cada Short: "desde", "hasta", "remate" (el número F del remate), "idea" (lo que aprende alguien que SOLO ve el Short,
en una frase), "gancho" (texto de 3 a 7 palabras para poner arriba en pantalla los primeros segundos), "titulo" (título
del Short, máximo 60 caracteres, con tildes, sin emojis), "tipo" y "por_que" (una frase).
Responde SOLO con este JSON: {{"candidatos": [{{"desde": 12, "hasta": 19, "remate": 18, "idea": "...", "gancho": "...",
"titulo": "...", "tipo": "truco", "nota": 5, "por_que": "..."}}], "ninguno_por_que": ""}}"""

_PROMPT_REVISION = """Estos son textos de Shorts de YouTube (vídeos verticales cortos). Léelos como alguien que NO ha visto
nada más del canal ni del vídeo del que salen. Para cada uno responde: "n" (su número), "se_entiende_solo" (true si se
entiende sin contexto previo), "idea_completa" (true si la idea termina, sin quedarse a medias ni anunciar algo que no
llega) y "que_aprende" (en una frase, qué aprende quien lo ve).

{textos}

Responde SOLO con este JSON: {{"veredictos": [{{"n": 1, "se_entiende_solo": true, "idea_completa": true,
"que_aprende": "..."}}]}}"""


# ------------------------------------------------------------------ las frases del vídeo montado
def _tiempo_final(t: float, segs: list[tuple[float, float]]) -> float | None:
    acum = 0.0
    for a, b in segs:
        if a <= t <= b:
            return acum + (t - a)
        acum += b - a
    return None


def frases(words: list[dict], segs: list[tuple[float, float]], audio=None, nivel: float | None = None) -> list[dict]:
    """El vídeo YA MONTADO partido en FRASES completas: [{"id", "ini", "fin" (segundos del vídeo montado), "texto"}]. Los
    finales de frase salen de las pausas REALES del audio (`pipeline._limites_de_frase`: un punto o una pausa ≥ 0,6 s, nunca
    tras «que», «de»…); una frase de más de `FRASE_MAX_S` se parte también en las pausas cortas. Un corte grande del
    montaje (≥ 2 s quitados: se quitó contenido) también cierra la frase."""
    from .pipeline import _limites_de_frase
    finales: set[float] = set()
    cortos: set[float] = set()
    for a, b in segs:
        for t, fuerza in _limites_de_frase(words, a, b, audio, nivel):
            (finales if fuerza >= 2 else cortos).add(round(t, 3))
    saltos = {round(b, 3) for (_a, b), (a2, _b2) in zip(segs, segs[1:]) if a2 - b >= 2.0}
    ws = []
    for w in sorted(words, key=lambda w: float(w["start"])):
        c = (float(w["start"]) + float(w["end"])) / 2
        tf = _tiempo_final(c, segs)
        if tf is not None and str(w.get("word", "")).strip():
            ws.append((w, tf))
    out: list[dict] = []
    actual: list[tuple[dict, float]] = []

    def cierra():
        if actual:
            ini = _tiempo_final(float(actual[0][0]["start"]), segs)
            fin = _tiempo_final(float(actual[-1][0]["end"]), segs)
            ini = actual[0][1] - 0.2 if ini is None else ini
            fin = actual[-1][1] + 0.2 if fin is None else fin
            out.append({"id": len(out) + 1, "ini": round(max(0.0, ini), 2), "fin": round(fin, 2),
                        "ini_orig": round(float(actual[0][0]["start"]), 3), "fin_orig": round(float(actual[-1][0]["end"]), 3),
                        "texto": " ".join(str(w["word"]).strip() for w, _t in actual)})
            actual.clear()

    for k, (w, tf) in enumerate(ws):
        actual.append((w, tf))
        if k + 1 >= len(ws):
            break
        e, s2 = float(w["end"]), float(ws[k + 1][0]["start"])
        entre = [t for t in finales | saltos if e - 0.5 <= t <= s2 + 0.5]
        corto = [t for t in cortos if e - 0.5 <= t <= s2 + 0.5]
        largo = ws[k + 1][1] - actual[0][1] > FRASE_MAX_S
        if entre or (largo and corto) or str(w["word"]).strip()[-1:] in ".?!" and s2 - e >= 0.3:
            cierra()
    cierra()
    return out


def texto_frases(fr: list[dict]) -> str:
    def mmss(t):
        return f"{int(t) // 60}:{int(t) % 60:02d}"
    return "\n".join(f"[F{f['id']} {mmss(f['ini'])}] {f['texto']}" for f in fr)


# ------------------------------------------------------------------ las reglas por código
def _colgante_al_empezar(texto: str) -> bool:
    t = " ".join(normalize(texto).split()) + " "
    return any(t.startswith(x if x.endswith(" ") else x + " ") or t.startswith(x) and x.endswith(" ")
               for x in _ARRANQUES_COLGANTES)


def _colgante_al_acabar(texto: str) -> bool:
    crudo = texto.strip()
    t = " ".join(normalize(texto).split())
    ultimas = " ".join(t.split()[-8:])
    return crudo.endswith(":") or any(x in ultimas for x in _FINALES_COLGANTES)


def valida(candidatos: list, fr: list[dict]) -> tuple[list[dict], list[str]]:
    """Los candidatos de la IA -> los que cumplen las reglas por código (arreglando lo que se pueda): rango de frases
    válido, principio que se entiende solo (si no, hasta 2 frases antes), final que cierra (si no, hasta 2 frases
    después), remate en la segunda mitad, 15 s-2 min, nota ≥ 4, sin solaparse, máximo 3. Devuelve (buenos, notas de lo
    descartado)."""
    por_id = {f["id"]: f for f in fr}
    notas: list[str] = []
    buenos: list[dict] = []
    for c in candidatos if isinstance(candidatos, list) else []:
        if not isinstance(c, dict):
            continue
        try:
            d, h, rem, nota = int(c.get("desde")), int(c.get("hasta")), int(c.get("remate", c.get("hasta"))), int(c.get("nota", 0))
        except (TypeError, ValueError):
            continue
        titulo = str(c.get("titulo") or c.get("idea") or "").strip()[:60]
        if d not in por_id or h not in por_id or d > h:
            notas.append(f"«{titulo}»: frases fuera de rango")
            continue
        if nota < NOTA_MIN:
            notas.append(f"«{titulo}»: nota {nota} (hace falta {NOTA_MIN})")
            continue
        dur = lambda a, b: por_id[b]["fin"] - por_id[a]["ini"]      # noqa: E731
        # principio que se entiende solo
        vueltas = 0
        while _colgante_al_empezar(por_id[d]["texto"]) and vueltas < 4 and d - 1 in por_id and dur(d - 1, h) <= MAX_S:
            d, vueltas = d - 1, vueltas + 1
        if _colgante_al_empezar(por_id[d]["texto"]):
            notas.append(f"«{titulo}»: empieza remitiendo a algo anterior («{por_id[d]['texto'][:40]}…»)")
            continue
        # final que cierra la idea
        vueltas = 0
        while _colgante_al_acabar(por_id[h]["texto"]) and vueltas < 2 and h + 1 in por_id and dur(d, h + 1) <= MAX_S:
            h, vueltas = h + 1, vueltas + 1
        if _colgante_al_acabar(por_id[h]["texto"]):
            notas.append(f"«{titulo}»: acaba anunciando algo que no está («…{por_id[h]['texto'][-40:]}»)")
            continue
        if not d <= rem <= h:
            rem = h
        if h > d and (rem - d) / (h - d) < 0.4:
            notas.append(f"«{titulo}»: el remate llega demasiado pronto (el resto sobra)")
            continue
        if not MIN_S <= dur(d, h) <= MAX_S:
            notas.append(f"«{titulo}»: dura {dur(d, h):.0f} s (entre {MIN_S:.0f} y {MAX_S:.0f})")
            continue
        tipo = str(c.get("tipo") or "otro")
        buenos.append({"desde": d, "hasta": h, "remate": rem, "ini": por_id[d]["ini"], "fin": por_id[h]["fin"],
                       "ini_orig": por_id[d].get("ini_orig"), "fin_orig": por_id[h].get("fin_orig"),
                       "idea": str(c.get("idea") or "").strip()[:200], "gancho": str(c.get("gancho") or "").strip()[:60],
                       "titulo": titulo, "tipo": tipo if tipo in TIPOS else "otro", "nota": nota,
                       "por_que": str(c.get("por_que") or "").strip()[:200],
                       "texto": " ".join(por_id[k]["texto"] for k in range(d, h + 1))})
    elegidos: list[dict] = []
    for c in sorted(buenos, key=lambda c: (-c["nota"], c["ini"])):
        if len(elegidos) >= MAX_SHORTS:
            notas.append(f"«{c['titulo']}»: ya hay {MAX_SHORTS} Shorts mejores")
        elif any(c["ini"] < e["fin"] and e["ini"] < c["fin"] for e in elegidos):
            notas.append(f"«{c['titulo']}»: se pisa con otro mejor")
        else:
            elegidos.append(c)
    return sorted(elegidos, key=lambda c: c["ini"]), notas


# ------------------------------------------------------------------ Gemini
_ESQ: dict = {}


def _esquemas():
    if not _ESQ:
        try:
            from typing import Literal

            from pydantic import BaseModel

            class Candidato(BaseModel):
                desde: int
                hasta: int
                remate: int
                idea: str
                gancho: str
                titulo: str
                tipo: Literal["dato", "error_comun", "truco", "opinion", "comparacion", "antes_despues", "otro"]
                nota: int
                por_que: str

            class Seleccion(BaseModel):
                candidatos: list[Candidato]
                ninguno_por_que: str = ""

            class Veredicto(BaseModel):
                n: int
                se_entiende_solo: bool
                idea_completa: bool
                que_aprende: str

            class Revision(BaseModel):
                veredictos: list[Veredicto]

            _ESQ.update(seleccion=Seleccion, revision=Revision)
        except Exception:  # noqa: BLE001 — sin pydantic: sin esquema (el parser tolera el JSON suelto)
            _ESQ.update(seleccion=None, revision=None)
    return _ESQ["seleccion"], _ESQ["revision"]


def _json(texto: str):
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    return json.loads(t)


def segunda_opinion(buenos: list[dict], client, types, modelos: list[str]) -> tuple[list[dict], list[str]]:
    """Flash-Lite lee SOLO el texto de cada Short (sin el resto del vídeo, como un espectador de Shorts) y dice si se
    entiende solo y si la idea queda completa. Fuera los que no. Si la revisión falla (saturado…), se quedan los que
    pasaron las reglas por código: la segunda opinión es un filtro más, no una condición para que haya Shorts."""
    if not buenos:
        return buenos, []
    textos = "\n\n".join(f"SHORT {n}:\n{c['texto']}" for n, c in enumerate(buenos, 1))
    try:
        crudo, _m, _u = _util.call_gemini_cadena(
            client, types, modelos, _PROMPT_REVISION.format(textos=textos), valida=_json, reintento_s=20.0, pasadas=1,
            timeout_s=90.0, esquema=_esquemas()[1], etiqueta="shorts_revision")
    except Exception as exc:  # noqa: BLE001
        log(f"  Shorts: la segunda opinión no respondió ({exc}); me quedo con las reglas por código.")
        return buenos, []
    ver = {int(v.get("n", 0)): v for v in (crudo.get("veredictos") or []) if isinstance(v, dict)}
    fuera, notas = [], []
    for n, c in enumerate(buenos, 1):
        v = ver.get(n)
        if v and (not v.get("se_entiende_solo") or not v.get("idea_completa")):
            fuera.append(n)
            notas.append(f"«{c['titulo']}»: a ciegas no se entiende solo o queda a medias "
                         f"(«{str(v.get('que_aprende', ''))[:60]}»)")
        elif v:
            c["que_aprende"] = str(v.get("que_aprende") or "")[:200]
    return [c for n, c in enumerate(buenos, 1) if n not in fuera], notas


def selecciona(words: list[dict], segs: list[tuple[float, float]], plan: dict, cfg, work: Path, slug: str,
               canal: str = "", contexto: str = "", audio=None, nivel: float | None = None, capitulos: str = "",
               _gemini=None, _modelos=None, _lite=None) -> dict:
    """Los Shorts de ESTA versión: {"v", "shorts": [...], "motivo" (si no hay), "notas": [...]}. De caché si ya se
    eligieron (`shorts_<slug>.json`: «Reintentar» no gasta); si no, 1 petición al mejor Flash (elegir) + 1 a Flash-Lite
    (segunda opinión). Nunca lanza: un fallo = sin Shorts, con el motivo."""
    cache = Path(work) / f"shorts_{slug}.json"
    try:
        previo = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(previo, dict) and previo.get("v") == VERSION:
            return previo
    except (OSError, ValueError):
        pass
    fr = frases(words, segs, audio, nivel)
    if sum(f["fin"] - f["ini"] for f in fr) < MIN_S:
        return {"v": VERSION, "shorts": [], "motivo": "el vídeo es demasiado corto para sacar Shorts", "notas": []}
    try:
        if _gemini is None:
            from .director import _gemini as _abre
            g = _abre(cfg)
            if not g:
                return {"v": VERSION, "shorts": [], "motivo": "no hay clave de Gemini", "notas": []}
            _gemini = g[:2]
        client, types = _gemini
        if _modelos is None:
            from .director import _cadena_modelos
            _modelos = _cadena_modelos(cfg, client)
        if _lite is None:
            from . import modelos as _m
            _lite = _m.cadena_lite(cfg, client)
        capitulos = capitulos or "(sin capítulos)"
        prompt = _PROMPT.format(contexto=contexto or "canal de YouTube", frases=texto_frases(fr), capitulos=capitulos,
                                maximo=MAX_SHORTS, min_s=int(MIN_S), max_s=int(MAX_S))
        crudo, modelo, _uso = _util.call_gemini_cadena(
            client, types, _modelos, prompt, system=_SYSTEM.format(canal=canal or "de YouTube"), valida=_json,
            reintento_s=float(cfg.get_path("ai.director_reintento_seg", 90)), pasadas=1, timeout_s=180.0,
            esquema=_esquemas()[0], etiqueta="shorts")
    except Exception as exc:  # noqa: BLE001 — sin Shorts, el vídeo sigue igual
        motivo = f"Gemini no respondió al elegir Shorts ({str(exc)[:120]})"
        log(f"  Shorts: {motivo}.")
        return {"v": VERSION, "shorts": [], "motivo": motivo, "notas": []}   # (no se guarda: se reintenta la próxima vez)
    buenos, notas = valida(crudo.get("candidatos") or [], fr)
    buenos, notas2 = segunda_opinion(buenos, client, types, _lite)
    notas += notas2
    motivo = "" if buenos else (str(crudo.get("ninguno_por_que") or "").strip()
                                or "ningún trozo se sostiene solo como Short")
    out = {"v": VERSION, "shorts": buenos, "motivo": motivo, "notas": notas, "modelo": modelo}
    try:
        cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ de la selección a los vídeos
def piezas_de(ini_orig: float, fin_orig: float, segs: list[tuple[float, float]], audio=None,
              nivel: float | None = None) -> list[tuple[float, float]]:
    """Los tramos de la grabación ORIGINAL que forman el Short: [ini_orig, fin_orig] con los cortes del montaje ACTUAL
    (así, si el vídeo se vuelve a montar con otros cortes, el Short los sigue). Los bordes se llevan al sonido real (sin
    comerse la primera ni la última sílaba: los tiempos de Whisper fallan hasta ~0,5 s al salir de un silencio)."""
    out = [(max(a, ini_orig), min(b, fin_orig)) for a, b in segs if b > ini_orig and a < fin_orig]
    out = [(a, b) for a, b in out if b - a > 0.05]
    if not out or audio is None or nivel is None:
        return out
    from . import voz
    seg_ini = next(a for a, b in segs if a <= out[0][0] <= b)
    seg_fin = next(b for a, b in segs if a <= out[-1][1] <= b)
    a0, b0 = out[0]
    isl = voz.islas(audio, max(0.0, a0 - 1.5), a0 + 1.0, nivel)
    pisa = [I for I in isl if I.fin > a0 - 0.8 and I.ini < a0 + 0.3]
    a0 = max(seg_ini, (pisa[0].ini if pisa else a0) - 0.15)
    a1, b1 = out[-1] if len(out) > 1 else (a0, b0)
    isl = voz.islas(audio, max(0.0, b1 - 1.0), b1 + 1.5, nivel)
    pisa = [I for I in isl if I.ini < b1 + 0.8 and I.fin > b1 - 0.3]
    b1 = min(seg_fin, (pisa[-1].fin if pisa else b1) + 0.25)
    if len(out) == 1:
        return [(round(a0, 3), round(b1, 3))]
    return [(round(a0, 3), out[0][1])] + out[1:-1] + [(out[-1][0], round(b1, 3))]


def crea(video: Path, words: list[dict], segs: list[tuple[float, float]], plan: dict, cfg, work: Path, slug: str,
         out_dir: Path, meta: dict, camara: dict | None, cursor, actividad, marca: dict, canal: str = "",
         contexto: str = "", capitulos: str = "", audio=None, nivel: float | None = None, forzar: bool = True) -> dict:
    """Elige (`selecciona`) y monta (`shorts_render.render_short`) los Shorts de esta versión en `out_dir`
    (short_1.mp4…). Devuelve {"shorts": [para el manifest], "motivo": si no hay}. Un Short que falla al montarse se
    salta (los demás siguen); nada de esto puede tumbar el vídeo largo (quien llama lo envuelve en try)."""
    from . import shorts_render
    sel = selecciona(words, segs, plan, cfg, work, slug, canal=canal, contexto=contexto, audio=audio, nivel=nivel,
                     capitulos=capitulos)
    for n in sel.get("notas") or []:
        log(f"  Shorts: descartado — {n}")
    hechos: list[dict] = []
    for k, c in enumerate(sel.get("shorts") or [], 1):
        if c.get("ini_orig") is None:
            continue
        piezas = piezas_de(float(c["ini_orig"]), float(c["fin_orig"]), segs, audio, nivel)
        dur = sum(b - a for a, b in piezas)
        if not piezas or dur < MIN_S - 1.0 or dur > MAX_S + 2.0:
            log(f"  Shorts: «{c['titulo']}» ya no encaja en este montaje ({dur:.0f} s): se salta.")
            continue
        destino = Path(out_dir) / f"short_{k}.mp4"
        try:
            if forzar or not destino.is_file():
                log(f"  Shorts: montando «{c['titulo']}» ({dur:.0f} s)…")
                if cfg.get_path("videos.shorts_crudo", True):       # solo el corte (la edición la hace «Shorts · TikTok»)
                    shorts_render.render_crudo(video, piezas, destino, cfg)
                else:
                    shorts_render.render_short(video, piezas, destino, cfg, meta, camara, cursor, words, c.get("gancho", ""),
                                               marca, fps=30, actividad=actividad)
        except Exception as exc:  # noqa: BLE001
            log(f"  Shorts: «{c['titulo']}» no se pudo montar ({str(exc)[:160]}); sigo con los demás.")
            continue
        hechos.append({"archivo": destino.name, "miniatura": destino.with_suffix(".jpg").name, "titulo": c["titulo"],
                       "gancho": c.get("gancho", ""), "idea": c.get("idea", ""), "por_que": c.get("por_que", ""),
                       "que_aprende": c.get("que_aprende", ""), "tipo": c.get("tipo", "otro"), "nota": c.get("nota"),
                       "duracion": round(dur, 1), "desde_min": round(float(c["ini"]), 1)})
    motivo = "" if hechos else (sel.get("motivo") or "ninguno se pudo montar")
    if hechos:
        try:        # título + tags de YouTube y de TikTok de cada Short (una petición a Flash-Lite; sin ella, por código)
            from . import shorts_meta
            from .pipeline import titulo_del_plan
            r = shorts_meta.para_version(hechos, {"titulo": titulo_del_plan(plan), "tags": list(plan.get("tags") or []), "canal": canal,
                                                  "contexto": contexto}, out_dir, cfg, work, slug)
            for h, m in zip(hechos, r["metas"]):
                h["meta"] = m
        except Exception as exc:  # noqa: BLE001 — esto nunca puede quitarle los Shorts al vídeo
            log(f"  Shorts: no se pudieron escribir títulos y tags ({str(exc)[:140]}).")
    if hechos:
        log(f"Shorts: {len(hechos)} listo(s) — " + " · ".join(f"«{h['titulo']}» ({h['duracion']:.0f} s)" for h in hechos))
    else:
        log(f"Shorts: no salió ninguno ({motivo}).")
    return {"shorts": hechos, "motivo": motivo}


# ------------------------------------------------------------------ pruebas (sin Gemini: uno falso)
def prueba() -> list[dict]:
    import tempfile
    out: list[dict] = []

    def r(caso, ok, detalle=""):
        out.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)[:600]})

    # frases del vídeo montado: sin audio, los huecos y los puntos de Whisper
    words = []
    t = 0.0
    for k, texto in enumerate(["¿Sabías que el 60 % de las búsquedas acaban sin clic?", "Eso es un dato de Google.",
                               "Entonces tienes que optimizar para las respuestas.", "Te explico cómo hacerlo:"]):
        for j, p in enumerate(texto.split()):
            words.append({"word": p, "start": t, "end": t + 0.35})
            t += 0.4
        t += 0.8
    fr = frases(words, [(0.0, t)])
    r("frases: una por cada frase dicha (cortadas en su punto y su pausa), con su tiempo en el vídeo montado",
      len(fr) == 4 and fr[0]["texto"].startswith("¿Sabías") and fr[3]["texto"].endswith("hacerlo:"), fr)
    r("el texto para la IA numera las frases con su minuto", "[F1 0:00]" in texto_frases(fr), texto_frases(fr)[:80])

    # reglas por código
    fr2 = [{"id": i, "ini": (i - 1) * 8.0, "fin": i * 8.0, "texto": tx} for i, tx in enumerate([
        "Hoy hablamos de SEO.", "¿Sabías que Google ya muestra respuestas con IA?", "Eso cambia cómo te encuentran.",
        "El truco es responder la pregunta en la primera frase.", "Así Google te cita como fuente.",
        "Entonces, como les decía antes.", "Y ahora les voy a mostrar cómo hacerlo:", "Primero abres Search Console.",
        "Buscas las consultas con preguntas.", "Y respondes cada una arriba del todo.", "Con eso subí un 30 %.",
        "Bueno, gracias por verlo."], 1)]
    ok1, _ = valida([{"desde": 2, "hasta": 5, "remate": 5, "nota": 5, "titulo": "Responde en la primera frase",
                      "idea": "x", "gancho": "El truco de las respuestas IA", "tipo": "truco", "por_que": "y"}], fr2)
    r("un candidato bueno pasa tal cual (empieza con gancho, acaba en el remate, 32 s)",
      len(ok1) == 1 and (ok1[0]["desde"], ok1[0]["hasta"]) == (2, 5), ok1)
    ok2, n2 = valida([{"desde": 6, "hasta": 11, "remate": 11, "nota": 5, "titulo": "Colgante", "tipo": "truco"}], fr2)
    r("si empieza remitiendo a algo anterior («Entonces, como les decía…») se amplía hacia atrás o se descarta",
      not ok2 or ok2[0]["desde"] < 6, (ok2, n2))
    ok3, n3 = valida([{"desde": 8, "hasta": 7 + 0, "remate": 7, "nota": 5, "titulo": "Al revés"},
                      {"desde": 3, "hasta": 7, "remate": 7, "nota": 5, "titulo": "Acaba anunciando"}], fr2)
    r("si acaba anunciando algo («ahora les voy a mostrar…:») se amplía hasta que llega o se descarta; un rango al revés, fuera",
      all(c["hasta"] > 7 for c in ok3), (ok3, n3))
    ok4, n4 = valida([{"desde": 2, "hasta": 5, "remate": 5, "nota": 2, "titulo": "Flojo"},
                      {"desde": 2, "hasta": 2, "remate": 2, "nota": 5, "titulo": "Corto"}], fr2)
    r("nota menor de 3 (mínimo NOTA_MIN) o menos de 15 s: fuera (mejor ninguno que uno flojo)", ok4 == [] and len(n4) == 2, n4)
    ok5, _ = valida([{"desde": 2, "hasta": 5, "remate": 5, "nota": 5, "titulo": "A"},
                     {"desde": 4, "hasta": 6 + 3, "remate": 9, "nota": 4, "titulo": "B pisa"},
                     {"desde": 8, "hasta": 11, "remate": 11, "nota": 4, "titulo": "C"}], fr2)
    r("no se pisan: gana el de más nota", [c["titulo"] for c in ok5] == ["A", "C"], ok5)

    # la selección entera con un Gemini falso, y la caché («Reintentar» no gasta)
    llamadas: list[str] = []

    def falso(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
        llamadas.append(etiqueta)
        if etiqueta == "shorts":
            return {"candidatos": [{"desde": 2, "hasta": 5, "remate": 5, "idea": "Responder arriba hace que Google te cite",
                                    "gancho": "El truco para salir en la IA", "titulo": "Sal en las respuestas de Google",
                                    "tipo": "truco", "nota": 5, "por_que": "accionable"}], "ninguno_por_que": ""}, modelos[0], {}
        return {"veredictos": [{"n": 1, "se_entiende_solo": True, "idea_completa": True, "que_aprende": "a responder arriba"}]}, modelos[0], {}

    real = _util.call_gemini_cadena
    words2 = []
    t = 0.0
    for f in fr2:
        for p in f["texto"].split():
            words2.append({"word": p, "start": t, "end": t + 0.7})
            t += 0.8
        t += 0.8
    try:
        _util.call_gemini_cadena = falso
        with tempfile.TemporaryDirectory() as tmp:
            class _Cfg:
                def get_path(self, k, d=None):
                    return d
            res = selecciona(words2, [(0.0, t)], {}, _Cfg(), Path(tmp), "pro", _gemini=(object(), object()),
                             _modelos=["gemini-3.8-flash"], _lite=["gemini-3.5-flash-lite"])
            res2 = selecciona(words2, [(0.0, t)], {}, _Cfg(), Path(tmp), "pro", _gemini=(object(), object()),
                              _modelos=["gemini-3.8-flash"], _lite=["gemini-3.5-flash-lite"])
        r("un Short bueno sale con su título, gancho e idea; elegir va al mejor Flash y la segunda opinión a Flash-Lite",
          len(res["shorts"]) == 1 and res["shorts"][0]["gancho"] and llamadas == ["shorts", "shorts_revision"], (res, llamadas))
        r("«Reintentar»: sale de la caché, sin otra petición", res2 == res and llamadas == ["shorts", "shorts_revision"],
          llamadas)

        def falso_ninguno(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            return {"candidatos": [], "ninguno_por_que": "todo depende de la pantalla"}, modelos[0], {}
        _util.call_gemini_cadena = falso_ninguno
        with tempfile.TemporaryDirectory() as tmp:
            res3 = selecciona(words2, [(0.0, t)], {}, _Cfg(), Path(tmp), "pro", _gemini=(object(), object()),
                              _modelos=["m"], _lite=["l"])
        r("si ningún trozo se sostiene: cero Shorts y el motivo (no pasa nada)",
          res3["shorts"] == [] and "pantalla" in res3["motivo"], res3)

        def falso_ciego(client, types, modelos, prompt, system=None, valida=None, etiqueta="", **kw):
            if etiqueta == "shorts":
                return falso(client, types, modelos, prompt, etiqueta="shorts")
            return {"veredictos": [{"n": 1, "se_entiende_solo": False, "idea_completa": True, "que_aprende": "no sé de qué habla"}]}, modelos[0], {}
        _util.call_gemini_cadena = falso_ciego
        with tempfile.TemporaryDirectory() as tmp:
            res4 = selecciona(words2, [(0.0, t)], {}, _Cfg(), Path(tmp), "pro", _gemini=(object(), object()),
                              _modelos=["m"], _lite=["l"])
        r("si la segunda opinión a ciegas no lo entiende solo: fuera", res4["shorts"] == [] and res4["notas"], res4)
    finally:
        _util.call_gemini_cadena = real
    return out
