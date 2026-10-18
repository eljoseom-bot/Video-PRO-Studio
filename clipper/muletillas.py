"""Tus MULETILLAS en cada vídeo, para mejorar cómo hablas (pedido del autor, 2026-09-24: «saber cuáles son tus muletillas…
para tenerlo en cuenta el día de mañana cuando haga un contenido nuevo»). Un botón pequeño en la página del vídeo
(`/video/<base>`, `GET /api/muletillas/<base>`): sin protagonismo, un extra.

Todo por código sobre la transcripción que ya existe (`words.json`): gratis, sin Gemini, en milisegundos. Con REGLAS de
contexto, porque la misma palabra puede ser muletilla o no: «**bueno**, vamos a ver» sí, «qué bueno» o «es bueno» no;
«¿**listo**?» sí, «ya está listo» no; «**tipo**, así» sí, «este tipo de contenido» no; «**este**…» con pausa sí, «este
vídeo» no. Calibrado con las transcripciones reales del autor (22-sep: «entonces», «bueno», «¿listo?», «o sea», «pues»,
«básicamente», «precisamente»…). Whisper casi nunca escribe los «eh» / «em»: esos no se pueden contar desde el texto
(sí las pausas de duda a media frase, que se miden en el audio: `voz.pausas_candidatas`).

También: tu ritmo (palabras por minuto), las palabras comodín que más repites («chévere», «brutal»…), 1-2 consejos
según el tipo de muletilla que más usas y cómo va este vídeo frente a tus otros (la tendencia).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .util import log

VERSION = 1                       # sube si cambian las reglas (la caché muletillas.json deja de valer)

# tras estas palabras «bueno» / «listo» son contenido («qué bueno», «ya está listo»), no muletilla
_ANTES_BUENO = {"muy", "es", "está", "están", "era", "sea", "qué", "que", "lo", "más", "tan", "un", "algo", "súper",
                "super", "bastante", "sería", "fue", "son", "estaba", "estar", "ser", "tiene", "tienes", "demasiado", "re"}
_ANTES_LISTO = {"está", "están", "estoy", "estamos", "estás", "esté", "estaba", "quedó", "queda", "quedan", "tener",
                "tenerlo", "ya", "todo", "muy", "más", "lo", "es", "sea", "fue", "estará", "dejarlo", "dejamos", "tienes"}
_ANTES_TIPO = {"el", "un", "este", "ese", "esa", "qué", "cualquier", "mismo", "otro", "ningún", "algún", "cada", "del",
               "al", "nuevo", "buen", "mal", "ese", "aquel", "mi", "tu", "su", "de"}
_COMODINES = ("chévere", "bacana", "chéveres", "chévere", "chéveres", "brutal", "brutales", "genial", "increíble",
              "espectacular", "potente", "potentísimo", "loco", "locura", "top", "interesante", "impresionante")
_DUDAS = {"eh", "ehh", "eeh", "em", "emm", "ehm", "mm", "mmm", "hmm", "este"}

# tipo de muletilla -> el consejo (1-2 por vídeo, según las que más usas)
_CONSEJOS = {
    "coletilla": "Cierras muchas frases pidiendo confirmación ({ej}). Termínalas con un punto firme: suenas más seguro. "
                 "Si quieres que el público participe, haz UNA pregunta concreta al final.",
    "arranque": "Empiezas muchas frases con {ej}. Arranca directo con la idea; si necesitas un segundo para pensar, "
                "calla: el programa ya acorta esas pausas.",
    "relleno": "{ej} aparecen cuando reformulas sobre la marcha. Di la idea corta a la primera; si hay que aclarar, "
               "pon un ejemplo concreto en vez de «o sea…».",
    "adverbio": "Los adverbios comodín ({ej}) casi nunca aportan: si los quitas, la frase dice lo mismo, más rápido.",
    "duda": "Los {ej} mientras piensas: cámbialos por un silencio corto. Se nota menos y el programa lo acorta.",
}


def _tok(crudo: str) -> str:
    return re.sub(r"[^\wáéíóúñü]", "", crudo.lower())


def analiza(words: list[dict]) -> dict:
    """Las muletillas de UNA transcripción (Whisper, `words.json`). Devuelve {"minutos", "palabras", "palabras_min",
    "total", "por_min", "cada_s", "top": [{"clave", "nombre", "tipo", "n", "por_min", "ejemplos": [s…]}],
    "comodines": [{"palabra", "n"}]}."""
    ws = [w for w in words if str(w.get("word", "")).strip()]
    ws.sort(key=lambda w: float(w.get("start", 0)))
    if not ws:
        return {"minutos": 0.0, "palabras": 0, "palabras_min": 0, "total": 0, "por_min": 0.0, "cada_s": None,
                "top": [], "comodines": []}
    crudo = [str(w["word"]).strip() for w in ws]
    tok = [_tok(c) for c in crudo]
    ini = [float(w.get("start", 0)) for w in ws]
    fin = [float(w.get("end", w.get("start", 0))) for w in ws]
    n = len(ws)

    def hueco_antes(i):
        return 99.0 if i == 0 else ini[i] - fin[i - 1]

    def hueco_despues(i):
        return 99.0 if i + 1 >= n else ini[i + 1] - fin[i]

    def abre_frase(i):                   # la palabra arranca frase: tras un punto/pregunta o tras una pausa
        return i == 0 or crudo[i - 1][-1:] in ".?!…:" or hueco_antes(i) >= 0.3

    def suelta(i):                       # va sola: con coma, puntos suspensivos o pausa detrás
        return crudo[i][-1:] in ",…." or hueco_despues(i) >= 0.3

    def ant(i):
        return tok[i - 1] if i > 0 else ""

    def sig(i):
        return tok[i + 1] if i + 1 < n else ""

    hallados: dict[str, list[float]] = {}
    nombres: dict[str, tuple[str, str]] = {}

    def apunta(clave, nombre, tipo, i):
        hallados.setdefault(clave, []).append(round(ini[i], 1))
        nombres[clave] = (nombre, tipo)

    for i in range(n):
        t, c = tok[i], crudo[i]
        pregunta = "?" in c
        if t == "o" and sig(i) == "sea":
            apunta("o sea", "o sea", "relleno", i)
        elif t == "como" and sig(i) == "tal":
            apunta("como tal", "como tal", "relleno", i)
        elif t == "la" and sig(i) == "verdad" and (i + 2 < n and tok[i + 2] == "es" or crudo[i + 1][-1:] == ","
                                                   or abre_frase(i)):
            apunta("la verdad", "la verdad (es que)", "relleno", i)
        elif t in ("digamos", "digámosle", "digámoslo"):
            apunta("digamos", "digamos / digámosle", "relleno", i)
        elif t == "tipo" and sig(i) != "de" and ant(i) not in _ANTES_TIPO:
            apunta("tipo", "tipo", "relleno", i)
        elif t in ("básicamente", "precisamente", "efectivamente", "literalmente", "prácticamente", "obviamente"):
            apunta(t, t, "adverbio", i)
        elif t == "listo" and pregunta and ant(i) not in _ANTES_LISTO:
            apunta("¿listo?", "¿listo?", "coletilla", i)
        elif t == "listo" and abre_frase(i) and suelta(i) and ant(i) not in _ANTES_LISTO:
            apunta("listo", "listo, (al arrancar frase)", "arranque", i)
        elif t in ("sí", "no", "cierto", "vale", "verdad", "ok") and pregunta:
            apunta(f"¿{t}?", f"¿{t}?", "coletilla", i)
        elif t in ("entiendes", "explico") and pregunta and ant(i) == "me":
            apunta("¿me entiendes?", "¿me entiendes? / ¿me explico?", "coletilla", i)
        elif t == "bueno" and ant(i) not in _ANTES_BUENO and (abre_frase(i) or suelta(i)):
            apunta("bueno", "bueno", "arranque", i)
        elif t == "entonces" and (abre_frase(i) or suelta(i) or ant(i) in ("y", "pues", "bueno", "sea")):
            apunta("entonces", "entonces (al arrancar frase)", "arranque", i)
        elif t == "pues":
            apunta("pues", "pues", "arranque", i)
        elif t == "nada" and ant(i) in ("y", "pues") and suelta(i):
            apunta("y nada", "y nada / pues nada", "arranque", i)
        elif t in _DUDAS and (t != "este" or suelta(i)):
            apunta("eh", "eh / este…", "duda", i)

    minutos = max(1e-6, (fin[-1] - ini[0]) / 60.0)
    total = sum(len(v) for v in hallados.values())
    top = sorted(({"clave": k, "nombre": nombres[k][0], "tipo": nombres[k][1], "n": len(v),
                   "por_min": round(len(v) / minutos, 2), "ejemplos": v[:12]} for k, v in hallados.items()),
                 key=lambda x: (-x["n"], x["clave"]))
    comodines: dict[str, int] = {}
    for t in tok:
        if t in _COMODINES:
            raiz = {"bacana": "chévere", "chéveres": "chévere", "chéveres": "chévere", "brutales": "brutal",
                    "potentísimo": "potente", "locura": "loco"}.get(t, t)
            comodines[raiz] = comodines.get(raiz, 0) + 1
    return {"minutos": round(minutos, 1), "palabras": n, "palabras_min": int(round(n / minutos)), "total": total,
            "por_min": round(total / minutos, 2), "cada_s": round(60.0 * minutos / total) if total else None,
            "top": top,
            "comodines": [{"palabra": k, "n": v} for k, v in sorted(comodines.items(), key=lambda kv: -kv[1])
                          if v >= max(3, minutos / 3)][:3]}


def consejos(res: dict) -> list[str]:
    """1-2 consejos: el del TIPO de muletilla que más usas y, si hay, el del segundo tipo (por nº de veces)."""
    por_tipo: dict[str, list[dict]] = {}
    for m in res.get("top") or []:
        por_tipo.setdefault(m["tipo"], []).append(m)
    orden = sorted(por_tipo.items(), key=lambda kv: -sum(m["n"] for m in kv[1]))
    out = []
    for tipo, ms in orden[:2]:
        if sum(m["n"] for m in ms) < 3:
            continue
        ej = ", ".join(f"«{m['clave']}»" for m in ms[:3])
        out.append(_CONSEJOS[tipo].format(ej=ej))
    if res.get("comodines"):
        c = res["comodines"][0]
        out.append(f"Repites mucho «{c['palabra']}» ({c['n']} veces): alterna con otra palabra o, mejor, di POR QUÉ algo es "
                   f"{c['palabra']} (un dato, un resultado).")
    return out[:3]


def _firma(ruta: Path) -> str:
    return hashlib.sha1(ruta.read_bytes()).hexdigest()[:16]


def de_trabajo(work: Path) -> dict | None:
    """El análisis de la carpeta de trabajo de una grabación (caché en `muletillas.json`, por versión y por el
    contenido de `words.json`). None si no hay transcripción."""
    wj = Path(work) / "words.json"
    if not wj.is_file():
        return None
    firma = _firma(wj)
    cache = Path(work) / "muletillas.json"
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("v") == VERSION and c.get("firma") == firma:
            return c
    except (OSError, ValueError):
        pass
    try:
        res = analiza(json.loads(wj.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        log(f"AVISO: no pude leer la transcripción para las muletillas ({exc}).")
        return None
    res.update(v=VERSION, firma=firma)
    try:
        cache.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def tendencia(dir_work: Path, firma_actual: str) -> dict | None:
    """Cómo va ESTE vídeo frente a tus OTRAS grabaciones. Cada grabación cuenta una vez aunque tenga varias carpetas de
    trabajo (la misma grabación subida varias veces: -4, -5, -7… del 22-sep), y la de este vídeo no cuenta en su propia
    media: se reconocen por el contenido de la transcripción. None si no hay otras."""
    from . import historial
    otras, vistas = [], {firma_actual}
    # también las de los vídeos que ya borraste del PC (la copia del historial); la misma grabación en los dos sitios
    # cuenta una vez (misma firma)
    for d in sorted(Path(dir_work).glob("*--pro")) + sorted(historial.dir_transcripciones(dir_work).glob("*")):
        if d.name.startswith("zz-") or not (d / "words.json").is_file():
            continue
        r = de_trabajo(d)
        if not r or not r.get("minutos") or r["firma"] in vistas:
            continue
        vistas.add(r["firma"])
        otras.append(r)
    if not otras:
        return None
    media = sum(r["por_min"] for r in otras) / len(otras)
    ritmo = sum(r["palabras_min"] for r in otras) / len(otras)
    return {"videos": len(otras), "media_por_min": round(media, 2), "mejor_por_min": round(min(r["por_min"] for r in otras), 2),
            "palabras_min": int(round(ritmo))}


def carpeta(dir_work: Path, base: str) -> Path:
    """Dónde está la transcripción de ese vídeo: su carpeta de trabajo (`work/<base>--pro`) o, si ya lo borraste del PC,
    la copia que guarda el historial (`historial.conserva_habla`; el autor, 2026-09-28: «dice este video aún no tiene
    transcripción… si todos tienen transcripción»)."""
    from . import historial
    work = Path(dir_work) / f"{base}--pro"
    if (work / "words.json").is_file():
        return work
    guardada = historial.dir_transcripciones(dir_work) / base
    return guardada if (guardada / "words.json").is_file() else work


def informe(dir_work: Path, base: str, pausas: dict | None = None) -> dict | None:
    """Todo lo que enseña el botón «🗣️ Tus muletillas» de `/video/<base>` y del Historial."""
    work = carpeta(dir_work, base)
    res = de_trabajo(work)
    if res is None:
        return None
    out = dict(res)
    out["consejos"] = consejos(res)
    out["tendencia"] = tendencia(Path(dir_work), res["firma"])
    out["pausas_duda"] = pausas
    return out


def pausas_de(work: Path, cfg=None) -> dict | None:
    """Las pausas de DUDA a media frase de esa grabación (`voz.pausas_candidatas`, medidas en el audio), guardadas en
    `pausas_duda.json`. None si no hay audio."""
    work = Path(work)
    cache = work / "pausas_duda.json"
    wav, wj = work / "audio.wav", work / "words.json"
    if not wj.is_file():
        return None
    firma = _firma(wj)
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("firma") == firma:
            return {"n": c["n"], "seg": c["seg"]}              # (también sin audio: la copia guardada del historial)
    except (OSError, ValueError, KeyError):
        pass
    if not wav.is_file():
        return None
    try:
        from . import voz
        act = []
        if (work / "actividad_pantalla.json").is_file():
            a = json.loads((work / "actividad_pantalla.json").read_text(encoding="utf-8"))
            act = a.get("ventanas", a) if isinstance(a, dict) else a
        cands = voz.pausas_candidatas(json.loads(wj.read_text(encoding="utf-8")), voz.Audio(wav), act, cfg)
        res = {"n": len(cands), "seg": round(sum(c["dur"] for c in cands), 1)}
        cache.write_text(json.dumps({**res, "firma": firma}), encoding="utf-8")
        return res
    except Exception as exc:  # noqa: BLE001 — es un extra: sin esto el informe sale igual
        log(f"AVISO: no pude medir las pausas de duda para el informe ({exc}).")
        return None


# ------------------------------------------------------------------ pruebas (sin red, sin Gemini)
def prueba() -> list[dict]:
    import shutil
    import tempfile
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    def habla(texto, t0=0.0, paso=0.35, pausas=None):
        """Palabras de Whisper: cada una 0,3 s; `pausas` = {índice: segundos de silencio ANTES de esa palabra}."""
        out, t = [], t0
        for j, p in enumerate(texto.split()):
            t += (pausas or {}).get(j, 0.0)
            out.append({"word": p, "start": round(t, 2), "end": round(t + 0.3, 2)})
            t += paso
        return out

    res = analiza(habla("Bueno, hoy vamos a ver esto. Qué bueno que llegaste. Es bueno saberlo. ¿Listo? Ya está listo "
                        "el vídeo. Entonces, lo primero es esto, o sea, la base. Este tipo de contenido es tipo, así. "
                        "Básicamente funciona. ¿Sí? Me entiendes? Como tal es fácil. Y entonces sigue."))
    k = {m["clave"]: m["n"] for m in res["top"]}
    r("«bueno» al arrancar frase SÍ cuenta; «qué bueno» y «es bueno» NO", k.get("bueno") == 1, k)
    r("«¿Listo?» SÍ (coletilla); «ya está listo» NO", k.get("¿listo?") == 1 and "listo" not in k, k)
    r("«tipo,» suelto SÍ; «este tipo de contenido» NO", k.get("tipo") == 1, k)
    r("«o sea», «como tal», «básicamente», «¿sí?», «entonces» al arrancar y «y entonces» cuentan",
      k.get("o sea") == 1 and k.get("como tal") == 1 and k.get("básicamente") == 1 and k.get("¿sí?") == 1
      and k.get("entonces") == 2, k)
    r("«Me entiendes?» cuenta como coletilla", k.get("¿me entiendes?") == 1, k)
    r("«este tipo»: el demostrativo no cuenta como «eh / este…»", "eh" not in k, k)
    duda = analiza(habla("vamos a ver este, cómo se hace. Eh vale este vídeo"))
    kd = {m["clave"]: m["n"] for m in duda["top"]}
    r("«este,» con pausa detrás y «Eh» cuentan como duda; «este vídeo» no", kd.get("eh") == 2, kd)
    r("el total, el ritmo (palabras por minuto) y «una cada N s» salen bien",
      res["total"] == sum(m["n"] for m in res["top"]) and res["palabras_min"] > 0 and res["cada_s"], res)
    cons = consejos({"top": [{"clave": "listo", "tipo": "coletilla", "n": 16}, {"clave": "¿sí?", "tipo": "coletilla", "n": 4},
                             {"clave": "entonces", "tipo": "arranque", "n": 12}, {"clave": "eh", "tipo": "duda", "n": 1}],
                     "comodines": [{"palabra": "chévere", "n": 14}]})
    r("consejos: primero el tipo que más usas (coletillas), luego el segundo, y la palabra comodín; nada de un tipo con < 3",
      len(cons) == 3 and cons[0].startswith("Cierras muchas frases") and "«listo»" in cons[0]
      and cons[1].startswith("Empiezas muchas frases") and "chévere" in cons[2], cons)
    com = analiza(habla(" ".join(["esto es chévere"] * 6 + ["y brutal"] * 1)))
    r("las palabras comodín que más repites (con 3 o más)", com["comodines"] == [{"palabra": "chévere", "n": 6}],
      com["comodines"])
    r("una transcripción vacía no revienta", analiza([])["total"] == 0)

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-muletillas-"))
    try:
        def trabajo(nombre, texto):
            d = tmp / f"{nombre}--pro"
            d.mkdir()
            (d / "words.json").write_text(json.dumps(habla(texto)), encoding="utf-8")
            return d
        trabajo("a", "bueno o sea esto es así " * 20)
        trabajo("a2", "bueno o sea esto es así " * 20)                  # la MISMA grabación en otra carpeta
        trabajo("b", "hoy vemos esto y lo otro sin más " * 20)
        trabajo("b2", "hoy vemos esto y lo otro sin más " * 20)         # la de ESTE vídeo, subida otra vez
        trabajo("zz-selftest-x", "o sea o sea o sea " * 30)             # el clip de «Probar el sistema»: no cuenta
        inf = informe(tmp, "b")
        r("la tendencia compara con tus OTRAS grabaciones: cada una una vez, sin la de este vídeo (aunque esté en otra "
          "carpeta) ni las de prueba", inf and inf["tendencia"] and inf["tendencia"]["videos"] == 1
          and inf["tendencia"]["media_por_min"] > 0 and inf["total"] == 0, inf and inf.get("tendencia"))
        r("se guarda en caché (muletillas.json) y se recalcula si cambia la transcripción",
          (tmp / "b--pro" / "muletillas.json").is_file() and de_trabajo(tmp / "b--pro")["firma"] == _firma(tmp / "b--pro" / "words.json"))
        (tmp / "b--pro" / "words.json").write_text(json.dumps(habla("o sea " * 10)), encoding="utf-8")
        r("…y con la transcripción nueva cambia el análisis", de_trabajo(tmp / "b--pro")["total"] == 10)
        r("sin transcripción: None (el botón dice que aún no hay datos)", informe(tmp, "no-existe") is None)

        # un vídeo que ya borraste del PC: la copia que guardó el historial (el autor, 2026-09-28)
        from . import historial as _hist
        antes_t = informe(tmp, "a")["tendencia"]["videos"]
        guardada = _hist.dir_transcripciones(tmp) / "c"
        guardada.mkdir(parents=True)
        (guardada / "words.json").write_text(json.dumps(habla("pues o sea esto va así " * 20)), encoding="utf-8")
        (guardada / "pausas_duda.json").write_text(json.dumps({"n": 3, "seg": 2.1, "firma": _firma(guardada / "words.json")}),
                                                   encoding="utf-8")
        inf_c = informe(tmp, "c", pausas=pausas_de(carpeta(tmp, "c")))
        r("un vídeo que ya borraste del PC sigue teniendo sus muletillas (la transcripción que guardó el historial) y sus "
          "pausas de duda medidas antes de borrarlo, aunque ya no esté el audio",
          carpeta(tmp, "c") == guardada and inf_c is not None and inf_c["total"] > 0
          and inf_c["pausas_duda"] == {"n": 3, "seg": 2.1}, inf_c and {k: inf_c[k] for k in ("total", "pausas_duda")})
        r("…y cuenta en la tendencia de los demás vídeos", informe(tmp, "a")["tendencia"]["videos"] == antes_t + 1,
          (antes_t, informe(tmp, "a")["tendencia"]))
        r("si el vídeo sigue en el PC, manda su carpeta de trabajo (no la copia)", carpeta(tmp, "a") == tmp / "a--pro")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return filas


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] != "--prueba":
        res_ = de_trabajo(Path(sys.argv[1]))
        print(json.dumps({**(res_ or {}), "consejos": consejos(res_ or {})}, ensure_ascii=False, indent=1))
    else:
        filas_ = prueba()
        for f in filas_:
            print(("OK   " if f["ok"] else "FALLA") + " " + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
        sys.exit(1 if any(not f["ok"] for f in filas_) else 0)
