"""Transcripción con timestamps a nivel de palabra.

Dos backends:
  local -> faster-whisper (gratis; con GPU va 10-30x tiempo real)
  groq  -> API de Groq (~$0.04/hora; sin GPU, trocea el audio automáticamente)

Salida normalizada en ambos casos:
  [{"word": str, "start": float, "end": float}, ...]
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from .util import log, run


def transcribe(wav: Path, cfg, workdir: Path) -> list[dict]:
    cache = workdir / "words.json"
    marca = workdir / "cifras_revisadas.json"
    backend = cfg.get_path("transcribe.backend", "local")
    modelo = None
    if cache.exists():
        log("Transcripción encontrada en caché, la reuso.")
        words = json.loads(cache.read_text(encoding="utf-8"))
    else:
        if backend == "groq":
            words = _groq(wav, cfg, workdir)
        else:
            words, modelo = _local(wav, cfg)
        cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        log(f"Transcripción lista: {len(words)} palabras.")

    words = une_decimales(words)
    # (si las DOS revisiones tienen algo que escuchar, Whisper se carga una sola vez para las dos)
    marca_vocab = workdir / "vocab_revisado.json"
    cifras_pend = (backend != "groq" and not marca.exists() and cfg.get_path("transcribe.revisa_cifras", True)
                   and any(_CIFRA.fullmatch(str(w.get("word", "")).strip()) for w in words))
    vocab_pend = (backend != "groq" and not marca_vocab.exists() and cfg.get_path("transcribe.revisa_vocabulario", True)
                  and bool(candidatos_vocab(words, vocabulario(cfg))))
    if cifras_pend and vocab_pend and modelo is None:
        try:
            modelo = _modelo(cfg)
        except Exception as exc:  # noqa: BLE001 — sin Whisper no se revisa nada, la transcripción sigue igual
            log(f"AVISO: no pude cargar Whisper para revisar la transcripción ({exc}).")
            cifras_pend = vocab_pend = False
    # CIFRAS con decimales: una sola vez por grabación (también las ya transcritas: la marca dice si ya se hizo)
    if backend != "groq" and not marca.exists() and cfg.get_path("transcribe.revisa_cifras", True):
        try:
            cambios = revisa_cifras(wav, words, cfg, modelo) if cifras_pend else []
            if cambios:
                cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
                log("Cifras revisadas con el vocabulario: " + ", ".join(f"«{a}» -> «{b}»" for a, b in cambios) + ".")
            marca.write_text(json.dumps({"v": 1, "cambios": cambios}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — una cifra sin revisar nunca debe tumbar el vídeo
            log(f"AVISO: no pude revisar las cifras de la transcripción ({exc}); sigo con las de Whisper.")
    # NOMBRES del vocabulario (herramientas, términos) en TODO el vídeo, no solo en los primeros 30 s: una vez por grabación
    if backend != "groq" and not marca_vocab.exists() and cfg.get_path("transcribe.revisa_vocabulario", True):
        try:
            cambios = revisa_vocabulario(wav, words, cfg, modelo) if vocab_pend else []
            if cambios:
                cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
                log("Nombres revisados con el vocabulario: " + ", ".join(f"«{a}» -> «{b}»" for a, b in cambios) + ".")
            marca_vocab.write_text(json.dumps({"v": _VOCAB_V, "cambios": cambios}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — un nombre sin revisar nunca debe tumbar el vídeo
            log(f"AVISO: no pude revisar los nombres de la transcripción ({exc}); sigo con los de Whisper.")
    return words


# ----------------------------------------------------------------- cifras
# Whisper recibe el vocabulario del config (`transcribe.initial_prompt`) SOLO en su primera ventana de 30 s: con
# `condition_on_previous_text=False` (lo que evita bucles en audio largo) faster-whisper lo suelta después. Sin él
# escribe sin puntuación y pierde los decimales dichos con «coma»: «tres coma seis» -> «36», «minuto tres catorce» ->
# «314» (el autor lo vio: una tarjeta salía «36» en vez de «3,6» en 10 renders seguidos, 2026-09-24). Pasar el vocabulario
# como `hotwords` (en TODAS las ventanas) arregla los decimales pero, medido con su grabación de 23 min, se comía
# casi la mitad de las palabras en 7 minutos y se inventaba «gracias por ver este video, suscríbete» 6 veces: NO.
# Lo que sí funciona: volver a escuchar SOLO cada cifra en un trocito con el vocabulario (como una primera ventana) y
# cambiarla solo si Whisper oye la MISMA cifra con decimal. Medido en esa grabación: 25 cifras en 22 s, 3 cambios
# («36» -> «3,6», «314» -> «3.14», «742» -> «7.42»), los 3 bien, ninguno de más.
_CIFRA = re.compile(r"\d{2,}[.,%]?")


def une_decimales(words: list[dict]) -> list[dict]:
    """«12» + «,4» -> «12,4»: Whisper a veces parte un decimal en dos palabras (con la coma o el punto en la segunda).
    Idempotente."""
    out: list[dict] = []
    for w in words:
        tok = str(w.get("word", ""))
        if (out and re.fullmatch(r"[.,]\d+[.,%]?", tok) and re.search(r"\d$", str(out[-1]["word"]))
                and float(w["start"]) - float(out[-1]["end"]) < 0.6):
            out[-1] = {**out[-1], "word": str(out[-1]["word"]) + tok, "end": w["end"]}
        else:
            out.append(w)
    return out


def _decimal_para(original: str, t: float, oidas: list[tuple[str, float]]) -> str | None:
    """Entre las palabras que se oyeron de nuevo (`oidas`: [(texto, inicio)]), la MISMA cifra que `original` pero con
    decimal, cerca de `t` (±0,8 s). None si no la hay (la cifra se queda como estaba)."""
    digitos = re.sub(r"\D", "", original)
    for tok, s0 in oidas:
        if re.search(r"\d[.,]\d", tok) and re.sub(r"\D", "", tok) == digitos and abs(s0 - t) < 0.8:
            return tok
    return None


def revisa_cifras(wav: Path, words: list[dict], cfg, modelo=None, _oye=None) -> list[tuple[str, str]]:
    """Vuelve a escuchar cada cifra de 2+ dígitos en un trocito de ±4 s con el vocabulario del config y la cambia (en
    `words`, en su sitio) solo si se oye la misma cifra con decimal. Devuelve los cambios [(antes, después)].
    `_oye(inicio, fin)` -> [(texto, inicio_absoluto)] es para las pruebas (sin Whisper)."""
    idx = [i for i, w in enumerate(words) if _CIFRA.fullmatch(str(w.get("word", "")).strip())]
    if not idx:
        return []
    if _oye is None:
        import wave

        import numpy as np
        if modelo is None:
            modelo = _modelo(cfg)
        with wave.open(str(wav), "rb") as fh:
            sr = fh.getframerate()
            prompt = cfg.get_path("transcribe.initial_prompt", "").strip() or None

            def _oye(a: float, b: float) -> list[tuple[str, float]]:
                fh.setpos(int(a * sr))
                trozo = np.frombuffer(fh.readframes(int((b - a) * sr)), dtype=np.int16).astype(np.float32) / 32768.0
                segs, _ = modelo.transcribe(trozo, language=cfg.get_path("transcribe.language", "es"),
                                            word_timestamps=True, vad_filter=False, initial_prompt=prompt,
                                            beam_size=5, condition_on_previous_text=False)
                crudas = [{"word": x.word.strip(), "start": a + x.start, "end": a + x.end}
                          for s in segs for x in (s.words or []) if x.word.strip()]
                return [(w["word"], float(w["start"])) for w in une_decimales(crudas)]

            return _revisa(words, idx, _oye)
    return _revisa(words, idx, _oye)


def _revisa(words: list[dict], idx: list[int], oye) -> list[tuple[str, str]]:
    cambios: list[tuple[str, str]] = []
    for i in idx:
        w = words[i]
        t = float(w["start"])
        nuevo = _decimal_para(str(w["word"]), t, oye(max(0.0, t - 4.0), float(w["end"]) + 4.0))
        if nuevo and nuevo != w["word"]:
            cambios.append((str(w["word"]), nuevo))
            w["word"] = nuevo
    return cambios


# ----------------------------------------------------------------- los nombres del vocabulario, en todo el vídeo
# (el autor, 2026-09-25, mejora 7.) El vocabulario del config (`transcribe.initial_prompt`) solo le llega a Whisper en su
# PRIMERA ventana de 30 s (ver «cifras» arriba): después escribe «Google Shares Console», «Vive Coding», «HGPT». Mismo
# truco que las cifras: cada nombre CASI igual a uno del vocabulario se vuelve a escuchar en un trocito CON el
# vocabulario, y se cambia SOLO si Whisper oye ese nombre EXACTO en el mismo sitio. Medido en sus grabaciones reales:
# arregla «Google Shares Console», «Vive Coding» (×2) y «HGPT» y NO cambia ninguno de los falsos parecidos («veces» ~
# Vercel, «tema» ~ sitemap, «versión» ~ conversión, «clave» ~ Claude, «botón», «cuatro», «cursos»). «Cloud» por «Claude»
# NO lo arregla (así se oye): eso lo corrige el director por el contexto (su prompt lo pide).
_VOCAB_V = 1
# términos del vocabulario que son palabras normales del español (o siglas cortas): Whisper ya los escribe bien y
# compararlos solo daría falsos parecidos
_VOCAB_COMUNES = {"posicionamiento web", "google", "algoritmo", "palabras clave", "intencion de busqueda", "enlaces",
                  "dominio", "autoridad de dominio", "meta descripcion", "indexacion", "rastreo", "trafico organico",
                  "contenido", "marketing digital", "conversion", "prompt", "api", "seo", "serp", "ctr", "h1", "make",
                  "astro"}


def vocabulario(cfg) -> list[str]:
    """Los nombres que vale la pena vigilar: los «Términos:» de `transcribe.initial_prompt` y las
    `videos.herramientas_conocidas`, sin las palabras normales del español."""
    from .util import normalize
    ip = str(cfg.get_path("transcribe.initial_prompt", "") or "")
    terms = [t.strip(" .\n") for t in ip.split("Términos:")[-1].split(",")] if "Términos:" in ip else []
    terms += [str(t) for t in (cfg.get_path("videos.herramientas_conocidas") or {})]
    out: list[str] = []
    for t in terms:
        n = " ".join(normalize(t).split())
        if t and n not in _VOCAB_COMUNES and len(n.replace(" ", "")) >= 4 and t not in out:
            out.append(t)
    return out


def candidatos_vocab(words: list[dict], terms: list[str], umbral: float = 0.72) -> list[tuple[int, int, str]]:
    """[(i, n, término)]: las `n` palabras desde la `i` que se parecen MUCHO a un término sin ser él («vive coding» ~
    «Vibe Coding»). Una por sitio (gana el término más largo); lo que ya está bien escrito no es candidato."""
    from difflib import SequenceMatcher

    from .util import normalize
    toks = [" ".join(normalize(str(w.get("word", ""))).split()) for w in words]
    tn = [(t, " ".join(normalize(t).split())) for t in terms]
    hallados: list[tuple[int, int, str]] = []
    for i in range(len(toks)):
        for t, nt in tn:
            n = len(nt.split())
            if i + n > len(toks):
                continue
            ng = " ".join(x for x in toks[i:i + n] if x)
            if not ng or ng == nt or ng.replace(" ", "") == nt.replace(" ", ""):
                continue
            if SequenceMatcher(None, ng, nt).ratio() >= umbral:
                hallados.append((i, n, t))
    # SIGLAS dichas letra a letra («E-E-A-T» = "e e a t"): Whisper las junta en UNA palabra («EAT», que dura 1 s) o las parte raro
    # («ea t»), así que ni una ni dos palabras se parecen a las CUATRO del término. Se comparan sin espacios (el autor, 11-oct-2026: «dije
    # EEAT y salió EAT»). Es solo un candidato: el cambio lo decide `revisa_vocabulario` si Whisper, al volver a escuchar, oye el nombre exacto.
    for t, nt in tn:
        letras = nt.split()
        if len(letras) < 3 or any(len(x) != 1 for x in letras):
            continue
        compacto = "".join(letras)
        for i in range(len(toks)):
            for n in (1, 2):
                if i + n > len(toks):
                    continue
                trozos = toks[i:i + n]
                grupo = "".join(trozos).replace(" ", "")
                # y no más larga que ella («EAT», «ea t»; no «de EAT»: con ≥ 0,8 de parecido eso obliga a que sean sus letras)
                if 3 <= len(grupo) <= len(compacto) and grupo != compacto and SequenceMatcher(None, grupo, compacto).ratio() >= 0.8:
                    hallados.append((i, n, t))
    out: list[tuple[int, int, str]] = []
    for i, n, t in sorted(hallados, key=lambda x: (x[0], -len(x[2]))):
        if not out or i >= out[-1][0] + out[-1][1]:
            out.append((i, n, t))
    return out


def revisa_vocabulario(wav: Path, words: list[dict], cfg, modelo=None, _oye=None) -> list[tuple[str, str]]:
    """Vuelve a escuchar, con el vocabulario, cada nombre CASI igual a uno de `vocabulario(cfg)` y lo cambia (en `words`,
    en su sitio) solo si Whisper oye ese nombre EXACTO a menos de 1,2 s. Devuelve [(antes, después)]. `_oye(inicio, fin)`
    -> [(texto, inicio_absoluto)] es para las pruebas (sin Whisper)."""
    from .util import normalize
    cands = candidatos_vocab(words, vocabulario(cfg))
    if not cands:
        return []
    if _oye is None:
        import wave

        import numpy as np
        if modelo is None:
            modelo = _modelo(cfg)
        prompt = cfg.get_path("transcribe.initial_prompt", "").strip() or None

        def _oye(a: float, b: float) -> list[tuple[str, float]]:
            with wave.open(str(wav), "rb") as fh:
                sr = fh.getframerate()
                fh.setpos(min(fh.getnframes(), int(a * sr)))
                trozo = np.frombuffer(fh.readframes(max(0, int((b - a) * sr))), dtype=np.int16).astype(np.float32) / 32768.0
            segs, _ = modelo.transcribe(trozo, language=cfg.get_path("transcribe.language", "es"), word_timestamps=True,
                                        vad_filter=False, initial_prompt=prompt, beam_size=5,
                                        condition_on_previous_text=False)
            return [(x.word.strip(), a + x.start) for s in segs for x in (s.words or []) if x.word.strip()]

    cambios: list[tuple[str, str]] = []
    for i, n, t in sorted(cands, key=lambda c: -c[0]):             # de atrás adelante: los índices no se mueven
        t0, t1 = float(words[i]["start"]), float(words[i + n - 1]["end"])
        oidas = _oye(max(0.0, t0 - 3.0), t1 + 3.0)
        toks = [" ".join(normalize(x).split()) for x, _s in oidas]
        nt = " ".join(normalize(t).split()).split()
        visto = False
        for k in range(len(toks)):
            # el término, palabra a palabra (Whisper puede partirlo en varias o juntarlo en una: se compara sin espacios)
            for m in range(1, len(nt) + 2):
                if k + m <= len(toks) and "".join(toks[k:k + m]).replace(" ", "") == "".join(nt) \
                        and abs(oidas[k][1] - t0) <= 1.2:
                    visto = True
                    break
            if visto:
                break
        if not visto:
            continue
        antes = " ".join(str(w["word"]).strip() for w in words[i:i + n])
        final = str(words[i + n - 1]["word"]).strip()
        signo = final[-1] if final and final[-1] in ".,;:?!" else ""
        words[i:i + n] = [{**words[i], "word": t + signo, "start": t0, "end": t1}]
        cambios.append((antes, t + signo))
    return list(reversed(cambios))


# ----------------------------------------------------------------- el arranque, frase por frase
# el autor, 2026-09-25: «siempre debe arrancar con la toma buena». Whisper, con el audio ENTERO, funde las tomas del arranque:
# en su vídeo del 24-sep dice «¿Cómo estás?» [2 s de silencio] «¿Cómo estás apareciendo hoy día en las IAs?» y Whisper
# escribió UNA sola pregunta, con un «apareciendo» de 2,8 s encima del silencio; en el del 22-sep, a un tarareo («da da
# da», antes de «Una de las cosas que odio») le puso «Una». Gemini y `voz.tomas_arranque` leen ese texto: la toma falsa no
# existía para ellos, dijera lo que dijera el prompt. Escuchando cada FRASE del arranque por separado (partidas por los
# silencios medidos en el audio) salen tal cual. Medido en sus 4 grabaciones: aparecen la toma falsa del 24-sep y el
# tarareo del 22-sep; en las otras dos nada cambia (y los ruidos sueltos, en los que Whisper se inventa «¡Gracias!» o
# «Oh, mmm», se tiran: Whisper mismo dice que ahí no hay voz, `no_speech_prob` ≥ 0,6).
_ARRANQUE_V = 1
_HUECO_FRASE = 0.6          # un silencio así o más largo separa dos frases
_FRASE_LARGA = 8.0          # la primera frase de al menos esto (hablas de corrido: la toma buena) cierra la revisión
_VENTANA_ARRANQUE = 90.0    # s desde la primera palabra de Whisper en los que se busca esa frase larga
_ALUCINACIONES = ("gracias", "subtitulos", "suscribete", "amara", "musica")     # lo que Whisper se inventa en un ruido


def revisa_arranque(wav: Path, words: list[dict], cfg, workdir: Path, modelo=None, audio=None, _oye=None) -> list[dict]:
    """Las palabras con el ARRANQUE vuelto a escuchar frase por frase (el resto, tal cual). Se calcula UNA vez por
    grabación (`arranque_reoido.json`) y se aplica sobre `words` en cada vídeo: words.json (lo de Whisper) no se toca.
    `_oye(inicio, fin)` -> ([(texto, inicio_abs, fin_abs, probabilidad)], no_speech_prob) es para las pruebas."""
    marca = Path(workdir) / "arranque_reoido.json"
    try:
        d = json.loads(marca.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if not isinstance(d, dict) or d.get("v") != _ARRANQUE_V:
        d = _reescucha(wav, words, cfg, modelo, audio, _oye)
        try:
            marca.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        if d.get("cambio"):
            log("Arranque vuelto a escuchar frase por frase: "
                + " · ".join(f"[{f['ini']:.1f}s] «{f['texto'][:60]}»" for f in d.get("frases", [])[:5]))
    return aplica_arranque(words, d)


def aplica_arranque(words: list[dict], d: dict | None) -> list[dict]:
    """`words` con lo vuelto a escuchar puesto delante de la costura (idempotente: lo de delante se sustituye)."""
    if not d or not d.get("cambio"):
        return words
    costura = float(d["costura"])
    return [dict(w) for w in d.get("palabras") or []] + [w for w in words if float(w.get("start", 0)) >= costura - 1e-6]


def _reescucha(wav: Path, words: list[dict], cfg, modelo=None, audio=None, _oye=None) -> dict:
    """Parte el arranque en frases (hasta la primera frase larga: la toma que ya sigue de corrido), escucha cada una
    sola y cose lo oído con lo de Whisper donde vuelven a coincidir (la «costura»: la primera palabra de la frase larga
    que Whisper también oyó, a menos de 0,35 s). Sin costura, o si todo sale igual, no cambia nada."""
    from . import voz
    p = voz._params(cfg)
    ws = sorted((w for w in words if str(w.get("word", "")).strip()), key=lambda w: float(w["start"]))
    if not ws:
        return {"v": _ARRANQUE_V, "cambio": False, "motivo": "sin palabras"}
    audio = audio or voz.Audio(wav)
    nivel = voz.nivel_voz(audio, ws)
    limite = float(ws[0]["start"]) + _VENTANA_ARRANQUE
    sel: list[tuple[float, float]] = []
    larga = False
    for a, b in voz.frases(audio, 0.0, min(audio.dur, limite + 60.0), nivel, p, _HUECO_FRASE):
        if a > limite:
            break
        sel.append((a, b))
        if b - a >= _FRASE_LARGA:
            larga = True
            break
    if not larga:
        return {"v": _ARRANQUE_V, "cambio": False, "motivo": "no hay una frase larga al arrancar"}
    if _oye is None:
        _oye = _oido_frases(wav, cfg, modelo)
    oidas: list[dict] = []
    for j, (a, b) in enumerate(sel):
        if b - a < p["voz_min"]:
            continue                                                    # un clic
        if j == len(sel) - 1:
            b = min(b, a + 25.0)                   # de la frase larga basta el principio (ahí está la costura)
        pal, nsp = _oye(max(0.0, a - 0.15), b + 0.15)
        texto = " ".join(t for t, *_r in pal)
        norma = " ".join(re.sub(r"[^\w\s]", " ", texto.lower()).split())
        if b - a < 1.5 and (nsp >= 0.6 or not norma or any(norma.startswith(x) for x in _ALUCINACIONES)):
            continue                                                    # un ruido: Whisper se inventa «¡Gracias!»
        oidas.append({"ini": a, "fin": b, "texto": texto, "pal": pal})
    if not oidas or abs(oidas[-1]["ini"] - sel[-1][0]) > 1e-6:
        return {"v": _ARRANQUE_V, "cambio": False, "motivo": "la frase larga no se pudo escuchar"}
    from .util import normalize
    ultima = oidas[-1]
    orig = [w for w in ws if float(w["start"]) >= ultima["ini"] - 1.0]
    costura, k_cos = None, None
    for k, (t, s0, _e0, _pr) in enumerate(ultima["pal"][:30]):
        nt = normalize(t).strip()
        par = next((w for w in orig if normalize(str(w["word"])).strip() == nt and abs(float(w["start"]) - s0) <= 0.35),
                   None)
        if nt and par is not None:
            costura, k_cos = float(par["start"]), k
            break
    if costura is None:
        return {"v": _ARRANQUE_V, "cambio": False, "motivo": "lo oído no coincide con Whisper en ningún punto"}
    nuevas = []
    for f in oidas[:-1]:
        nuevas += [{"word": t, "start": round(s0, 3), "end": round(e0, 3), "p": round(pr, 2)} for t, s0, e0, pr in f["pal"]]
    nuevas += [{"word": t, "start": round(s0, 3), "end": round(min(e0, costura), 3), "p": round(pr, 2)}
               for t, s0, e0, pr in ultima["pal"][:k_cos]]
    antes = [w for w in ws if float(w["start"]) < costura - 1e-6]
    igual = ([normalize(str(w["word"])).split() for w in antes] == [normalize(str(w["word"])).split() for w in nuevas])
    return {"v": _ARRANQUE_V, "cambio": not igual, "costura": costura, "palabras": nuevas,
            "frases": [{"ini": round(f["ini"], 2), "fin": round(f["fin"], 2), "texto": f["texto"]} for f in oidas],
            "whisper_antes": " ".join(str(w["word"]).strip() for w in antes)}


def oido_con_cache(wav: Path, cfg, cache: Path, modelo=None):
    """`oye(inicio, fin)` -> [(texto, inicio, fin)]: Whisper (sin el vocabulario) sobre ese trocito del audio, guardado en
    `cache` — un trozo ya escuchado no se vuelve a escuchar («Reintentar», mejoras). Whisper se carga solo si hace falta.
    Lo usa `voz.ajusta_plan` para comprobar qué hay DENTRO de los cortes de «silencio / muletilla» de la IA."""
    try:
        datos = json.loads(Path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        datos = {}
    estado: dict = {}

    def oye(a: float, b: float) -> list[tuple[str, float, float]]:
        k = f"{a:.2f}-{b:.2f}"
        if k not in datos:
            if "oye" not in estado:
                estado["oye"] = _oido_frases(wav, cfg, modelo)
            pal, _nsp = estado["oye"](a, b)
            datos[k] = [[t, round(s0, 3), round(e0, 3)] for t, s0, e0, _pr in pal]
            try:
                Path(cache).write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return [(t, float(s0), float(e0)) for t, s0, e0 in datos[k]]

    return oye


def _oido_frases(wav: Path, cfg, modelo=None):
    """El «oído» de verdad: Whisper (el mismo modelo) sobre un trocito del audio, SIN el vocabulario del config (con él,
    en un ruido se inventa frases enteras: «¡Gracias por ver este video! ¡Suscríbete…»)."""
    import wave

    import numpy as np
    if modelo is None:
        modelo = _modelo(cfg)

    def oye(a: float, b: float):
        with wave.open(str(wav), "rb") as fh:               # (el audio.wav de siempre: 16 kHz, mono, 16 bits)
            sr = fh.getframerate()
            fh.setpos(min(fh.getnframes(), int(a * sr)))
            trozo = np.frombuffer(fh.readframes(max(0, int((b - a) * sr))), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = modelo.transcribe(trozo, language=cfg.get_path("transcribe.language", "es"), word_timestamps=True,
                                    vad_filter=False, initial_prompt=None, beam_size=5,
                                    condition_on_previous_text=False)
        segs = list(segs)
        pal = [(x.word.strip(), a + x.start, a + x.end, float(x.probability)) for s in segs for x in (s.words or [])
               if x.word.strip()]
        return pal, (float(segs[0].no_speech_prob) if segs else 1.0)

    return oye


def prueba() -> list[dict]:
    """Las cifras sin Whisper (un «oído» falso): unir decimales partidos, cambiar SOLO cuando se oye la misma cifra con
    decimal, y que la revisión se haga una sola vez por grabación (marca `cifras_revisadas.json`)."""
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    W = lambda p, a, b: {"word": p, "start": a, "end": b}                                     # noqa: E731
    u = une_decimales([W("de", 650.1, 650.3), W("12", 650.32, 650.5), W(",4", 650.52, 650.9), W("minutos", 650.92, 651.4)])
    caso("«12» + «,4» -> «12,4» (una palabra, con el principio de la 1.ª y el final de la 2.ª)",
         [w["word"] for w in u] == ["de", "12,4", "minutos"] and u[1]["start"] == 650.32 and u[1]["end"] == 650.9, u)
    caso("une_decimales es idempotente", une_decimales(u) == u)
    lejos = une_decimales([W("12", 1.0, 1.3), W(",4", 2.5, 2.8)])
    caso("con más de 0,6 s entre medias no se unen (no es el mismo número)", len(lejos) == 2, lejos)
    caso("«36» se cambia si se oye «3,6» a la vez", _decimal_para("36", 654.97, [("unas", 654.83), ("3,6", 655.0)]) == "3,6")
    caso("…pero no si se oye otra cifra («3,5») ni si está lejos en el tiempo",
         _decimal_para("36", 654.97, [("3,5", 655.0)]) is None and _decimal_para("36", 654.97, [("3,6", 657.0)]) is None)
    caso("«100» sigue siendo «100» si se oye «100» (sin decimal no se toca)", _decimal_para("100", 61.6, [("100", 61.6)]) is None)

    palabras = [W("unas", 654.83, 654.95), W("36", 654.97, 655.2), W("eventos", 655.27, 655.6), W("100", 700.0, 700.3)]
    oidas = {654.97: [("unas", 654.8), ("3,6", 655.0), ("eventos", 655.3)], 700.0: [("100", 700.0)]}
    cambios = revisa_cifras(Path("x.wav"), palabras, None, _oye=lambda a, b: oidas[round(a + 4.0, 2)])
    caso("revisa_cifras cambia «36» -> «3,6» en su sitio y deja «100»",
         cambios == [("36", "3,6")] and palabras[1]["word"] == "3,6" and palabras[3]["word"] == "100", (cambios, palabras))

    class _Cfg:
        def get_path(self, k, d=None):
            return {"transcribe.backend": "local", "transcribe.revisa_cifras": True}.get(k, d)

    llamadas: list[int] = []
    g = globals()
    real = g["revisa_cifras"]

    def falso(wav, words, cfg, modelo=None, _oye=None):
        llamadas.append(1)
        for w in words:
            if w["word"] == "36":
                w["word"] = "3,6"
                return [("36", "3,6")]
        return []

    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        (wd / "words.json").write_text(json.dumps(palabras[:1] + [W("36", 654.97, 655.2)]), encoding="utf-8")
        try:
            g["revisa_cifras"] = falso
            w1 = transcribe(wd / "audio.wav", _Cfg(), wd)
            w2 = transcribe(wd / "audio.wav", _Cfg(), wd)
        finally:
            g["revisa_cifras"] = real
        guardado = json.loads((wd / "words.json").read_text(encoding="utf-8"))
        caso("una transcripción YA guardada se revisa una vez (queda guardada con «3,6» y la marca) y no se repite",
             len(llamadas) == 1 and w1[1]["word"] == "3,6" and w2[1]["word"] == "3,6" and guardado[1]["word"] == "3,6"
             and (wd / "cifras_revisadas.json").is_file(), (llamadas, guardado))

    # el ARRANQUE vuelto a escuchar frase por frase (2026-09-25), con un audio sintético y un «oído» falso: el caso real
    # de su vídeo del 24-sep («¿Cómo estás?» [2 s] «¿Cómo estás apareciendo hoy día…», que Whisper fundía en una)
    from . import voz

    class _CfgA:
        def get_path(self, k, d=None):
            return d

    oidas: list[tuple[float, float]] = []

    def oido(a, b):
        oidas.append((a, b))
        if b - a < 1.0:
            return [("¡Gracias!", a + 0.1, a + 0.4, 0.6)], 0.92                   # un ruido: Whisper se inventa algo
        if a < 3.0:
            return [("¿Cómo", 2.0, 2.4, 0.9), ("estás?", 2.4, 2.9, 1.0)], 0.1
        return [("¿Cómo", 5.0, 5.3, 0.9), ("estás", 5.3, 5.6, 1.0), ("apareciendo", 5.6, 6.1, 1.0), ("hoy", 6.1, 6.4, 1.0),
                ("día", 6.4, 6.7, 1.0)], 0.05

    fundidas = ([W("¿Cómo", 1.9, 2.4), W("estás", 2.4, 2.9), W("apareciendo", 2.9, 5.9)]
                + [W("hoy", 6.02, 6.4), W("día", 6.4, 6.7)] + [W(f"x{i}", 6.8 + 0.4 * i, 7.1 + 0.4 * i) for i in range(100)])
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        voz._wav_sintetico(wd / "audio.wav", 60.0, [(2.0, 3.5), (5.0, 48.0)], ruidos=[(1.0, 1.42, -30)])
        nuevas = revisa_arranque(wd / "audio.wav", fundidas, _CfgA(), wd, _oye=oido)
        textos = [w["word"] for w in nuevas[:7]]
        caso("arranque vuelto a escuchar: aparecen las DOS tomas («¿Cómo estás?» y «¿Cómo estás apareciendo…»), el ruido "
             "donde Whisper se inventa «¡Gracias!» se tira y desde la costura («hoy») sigue lo de Whisper tal cual",
             textos == ["¿Cómo", "estás?", "¿Cómo", "estás", "apareciendo", "hoy", "día"] and nuevas[5]["start"] == 6.02
             and len(nuevas) == 5 + 2 + 100, (textos, len(nuevas)))
        caso("con las tomas a la vista, `voz.tomas_arranque` ya ve la falsa",
             (voz.tomas_arranque(nuevas)["reinicio"] or {}).get("t") == 5.0, voz.tomas_arranque(nuevas)["reinicio"])
        n_oidas = len(oidas)
        otra = revisa_arranque(wd / "audio.wav", fundidas, _CfgA(), wd, _oye=oido)
        caso("una vez por grabación: la segunda vez sale de `arranque_reoido.json` (sin volver a escuchar) y words.json "
             "no se toca", otra == nuevas and len(oidas) == n_oidas and not (wd / "words.json").exists(), len(oidas))
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        voz._wav_sintetico(wd / "audio.wav", 60.0, [(5.0, 48.0)])
        limpias = [W("¿Cómo", 5.0, 5.3), W("estás", 5.3, 5.6), W("apareciendo", 5.6, 6.1), W("hoy", 6.1, 6.4),
                   W("día", 6.4, 6.7)] + [W(f"x{i}", 6.8 + 0.4 * i, 7.1 + 0.4 * i) for i in range(100)]
        igual = revisa_arranque(wd / "audio.wav", limpias, _CfgA(), wd, _oye=oido)
        caso("un arranque limpio (lo oído coincide con Whisper) no cambia nada", igual == limpias)

    # los NOMBRES del vocabulario en todo el vídeo (mejora 7, 2026-09-25), con un «oído» falso que devuelve lo que Whisper
    # oyó DE VERDAD al volver a escuchar con el vocabulario en sus grabaciones reales
    class _CfgV:
        def get_path(self, k, d=None):
            return {"transcribe.initial_prompt": "Canal de SEO. Términos: SEO, contenido, Search Console, Claude, "
                                                 "ChatGPT, Vercel, Vibe Coding, sitemap.",
                    "videos.herramientas_conocidas": {"Google Search Console": "x"}}.get(k, d)

    reales = {100.0: [("hacer", 99.2), ("cosas", 99.5), ("con", 99.8), ("Vibe", 100.0), ("Coding", 100.3)],
              200.0: [("muchas", 199.7), ("veces", 200.0), ("de", 200.4)],
              300.0: [("de", 299.7), ("Cloud,", 300.0), ("de", 300.4), ("Gemini", 300.5)],
              400.0: [("claves,", 399.6), ("Google", 400.0), ("Search", 400.3), ("Console,", 400.6)],
              500.0: [("un", 499.7), ("tema", 500.0), ("de", 500.3)]}

    def oido_v(a, b):
        return next((v for t, v in reales.items() if a <= t <= b), [])

    frase = ([W("hacer", 99.2, 99.5), W("cosas", 99.5, 99.8), W("con", 99.8, 100.0), W("Vive", 100.0, 100.3),
              W("Coding", 100.3, 100.7), W("muchas", 199.7, 200.0), W("veces", 200.0, 200.4), W("de", 299.7, 300.0),
              W("Cloud,", 300.0, 300.4), W("Google", 400.0, 400.3), W("Shares", 400.3, 400.6), W("Console,", 400.6, 401.0),
              W("un", 499.7, 500.0), W("tema", 500.0, 500.3)])
    cambios_v = revisa_vocabulario(Path("x.wav"), frase, _CfgV(), _oye=oido_v)
    textos_v = [w["word"] for w in frase]
    caso("nombres: «Vive Coding» -> «Vibe Coding» y «Google Shares Console» -> «Google Search Console» (lo oye así con el "
         "vocabulario); «veces», «tema» y «Cloud» se quedan (no se inventa Vercel ni sitemap, y «Cloud» se oye así)",
         "Vibe Coding" in textos_v and "Google Search Console," in textos_v and "veces" in textos_v and "tema" in textos_v
         and "Cloud," in textos_v and len(cambios_v) == 2, (cambios_v, textos_v))
    # siglas letra a letra (11-oct-2026): «dije EEAT y salió EAT». Lo que Whisper oyó DE VERDAD al volver a escuchar sus dos menciones
    class _CfgE:
        def get_path(self, k, d=None):
            return {"transcribe.initial_prompt": "Canal de SEO. Términos: SEO, Search Console, Vibe Coding, E-E-A-T."}.get(k, d)

    oye_e = {195.0: [("E", 195.2), ("-E", 196.1), ("-A", 196.3), ("-T", 196.3), ("que", 196.5)],
             311.0: [("de", 311.6), ("E", 311.7), ("-A", 312.0), ("-T,", 312.0), ("como", 312.4)]}
    frase_e = [W("parámetros", 194.2, 194.9), W("de", 194.9, 195.2), W("EAT", 195.2, 196.3), W("que", 196.5, 196.7),
               W("generales", 310.9, 311.5), W("de", 311.6, 311.7), W("ea", 311.7, 312.0), W("t,", 312.0, 312.2), W("como", 312.4, 312.6),
               W("tipo", 320.0, 320.3), W("de", 320.3, 320.4), W("contenido", 320.4, 321.0)]
    cands_e = candidatos_vocab(frase_e, vocabulario(_CfgE()))
    caso("siglas: «EAT» (1 palabra) y «ea t» (2) son candidatas a «E-E-A-T»; el resto de la frase no",
         [(frase_e[i]["word"], n, t) for i, n, t in cands_e] == [("EAT", 1, "E-E-A-T"), ("ea", 2, "E-E-A-T")], cands_e)
    cambios_e = revisa_vocabulario(Path("x.wav"), frase_e, _CfgE(), _oye=lambda a, b: next((v for t, v in oye_e.items() if a <= t <= b), []))
    textos_e = [w["word"] for w in frase_e]
    caso("…y «EAT» pasa a «E-E-A-T» SOLO donde Whisper, al volver a escuchar, oye «E-E-A-T» (195,2); donde oye «E-A-T» (311,7) se queda como está",
         "E-E-A-T" in textos_e and cambios_e == [("EAT", "E-E-A-T")] and "ea" in textos_e and "t," in textos_e, (cambios_e, textos_e))
    caso("las palabras normales del español del vocabulario no se vigilan (Whisper ya las escribe bien)",
         "contenido" not in [t.lower() for t in vocabulario(_CfgV())] and "Vibe Coding" in vocabulario(_CfgV()))
    return filas


# ----------------------------------------------------------------- local
def _register_cuda_dlls() -> None:
    """En Windows CTranslate2 no ve cuBLAS/cuDNN si vienen de los paquetes
    pip `nvidia-*` (no están en el PATH). Los añadimos a la búsqueda de DLLs.
    Si esos paquetes no están instalados esto no hace nada."""
    if os.name != "nt":
        return
    import site

    roots: list[str] = list(site.getsitepackages())
    if hasattr(site, "getusersitepackages"):
        roots.append(site.getusersitepackages())
    for root in roots:
        for bindir in (Path(root) / "nvidia").glob("*/bin"):
            try:
                os.add_dll_directory(str(bindir))
            except OSError:
                pass
            os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]


def _cuda_available() -> bool:
    """Hay GPU usable por CTranslate2 (no depende de torch)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _pick_device(requested: str) -> tuple[str, str]:
    if requested in ("cuda", "cpu"):
        return requested, ("float16" if requested == "cuda" else "int8")
    # auto
    try:
        import torch  # noqa

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    if _cuda_available():
        return "cuda", "float16"
    return "cpu", "int8"


def _modelo(cfg):
    """Carga Whisper (GPU si la hay; si CUDA falla, CPU)."""
    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    device, compute = _pick_device(cfg.get_path("transcribe.device", "auto"))
    model_name = cfg.get_path("transcribe.model", "large-v3")

    log(f"Whisper: modelo={model_name} device={device} compute={compute}")
    if device == "cpu":
        hint = ""
        if _cuda_available():
            hint = (" Tienes GPU pero faltan las libs CUDA: "
                    "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        log("AVISO: transcripción en CPU, lento para directos largos "
            "(~2.5x el audio). Pon transcribe.backend=groq en config.yaml." + hint)

    try:
        return WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:  # noqa: BLE001
        if device != "cuda":
            raise
        log(f"AVISO: no se pudo iniciar Whisper en CUDA ({exc}). "
            "Sigo en CPU; si el directo es largo usa transcribe.backend=groq.")
        return WhisperModel(model_name, device="cpu", compute_type="int8")


def _local(wav: Path, cfg):
    """(palabras, modelo): el modelo se devuelve para reusarlo al revisar las cifras sin cargarlo dos veces."""
    model = _modelo(cfg)

    segments, info = model.transcribe(
        str(wav),
        language=cfg.get_path("transcribe.language", "es"),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        initial_prompt=cfg.get_path("transcribe.initial_prompt", "").strip() or None,
        beam_size=5,
        condition_on_previous_text=False,  # evita bucles en audio largo
    )

    words: list[dict] = []
    last_report = 0.0
    for seg in segments:
        if seg.start - last_report > 300:
            log(f"  ... transcribiendo, voy en {seg.start/60:.0f} min")
            last_report = seg.start
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if token:
                words.append(
                    {"word": token, "start": float(w.start), "end": float(w.end)}
                )
    return words, model


# ------------------------------------------------------------------ groq
GROQ_CHUNK_SECONDS = 900  # 15 min por trozo, cómodo bajo el límite de tamaño


def _groq(wav: Path, cfg, workdir: Path) -> list[dict]:
    import requests

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("Falta GROQ_API_KEY en el entorno.")

    from .ingest import probe

    duration = float(run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(wav),
    ], capture=True).strip())

    n_chunks = max(1, math.ceil(duration / GROQ_CHUNK_SECONDS))
    log(f"Groq: {n_chunks} trozo(s) de hasta {GROQ_CHUNK_SECONDS//60} min")

    words: list[dict] = []
    for i in range(n_chunks):
        offset = i * GROQ_CHUNK_SECONDS
        piece = workdir / f"groq_chunk_{i:03d}.mp3"
        if not piece.exists():
            run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(offset), "-t", str(GROQ_CHUNK_SECONDS),
                "-i", str(wav),
                "-ac", "1", "-ar", "16000", "-b:a", "64k",
                str(piece),
            ])

        log(f"  subiendo trozo {i+1}/{n_chunks} ...")
        with open(piece, "rb") as fh:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (piece.name, fh, "audio/mpeg")},
                data={
                    "model": "whisper-large-v3",
                    "language": cfg.get_path("transcribe.language", "es"),
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                    "prompt": cfg.get_path("transcribe.initial_prompt", "")[:800],
                },
                timeout=600,
            )
        resp.raise_for_status()
        data = resp.json()

        for w in data.get("words", []):
            token = (w.get("word") or "").strip()
            if token:
                words.append({
                    "word": token,
                    "start": float(w["start"]) + offset,
                    "end": float(w["end"]) + offset,
                })
        piece.unlink(missing_ok=True)

    return words
