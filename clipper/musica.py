"""Música de fondo para Vídeos PRO — dos fuentes posibles:

1. `arma_pista_local`: TU carpeta local (`videos.musica_carpeta`), con
   canciones que ya descargaste tú mismo (p. ej. de la Librería de Audio de
   YouTube). Es la fuente PREFERIDA cuando tiene archivos — ni depende de
   la red ni de si Openverse tiene o no algo que sirva, y son canciones que
   ya elegiste tú.
2. `arma_pista`: Openverse (mismo mecanismo que clipper/imagenes.py, que
   también indexa audio, no solo imágenes) — respaldo automático SOLO si
   la carpeta local está vacía o no existe todavía. YouTube tiene su propia
   "Librería de Audio" pero es una función solo del sitio web: no tiene API
   pública para bajarla por código (investigado, no adivinado).

Las dos reparten el vídeo en pistas ENCADENADAS en orden RANDOM (nunca el
mismo orden dos veces) y, si hace falta más música de la que hay, repiten
el ciclo (reordenado) hasta cubrir el vídeo entero — nunca se corta la
música a mitad de vídeo.

Licencia (solo aplica a la vía Openverse): SOLO
license_type=commercial,modification (mismo criterio que imagenes.py). La
atribución que exige la licencia CC se añade a la descripción de YouTube.
Las canciones de tu carpeta local no llevan crédito automático: son tuyas.

Si ninguna de las dos fuentes da nada usable, se sigue sin música — nunca
debe tumbar el render de un vídeo que por lo demás está listo.

Solo canciones ESTABLES de fondo (el autor, 2026-09-24: «sin drops, sin subidones
tipo electrónica»): cada canción de tu carpeta se mide UNA vez (`perfil`,
caché en work/musica_perfil.json) y las que tienen un subidón/bajón fuerte o
un «break» casi en silencio no se usan (`estable`); el log dice cuáles y por
qué. Así cualquier canción que añadas pasa el mismo filtro, sin que nadie la
escuche antes. `python -m clipper.musica` enseña la tabla de tu carpeta.
"""
from __future__ import annotations

import json
import random
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from .util import log, run

_EXTENSIONES_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}

_API = "https://api.openverse.org/v1/audio/"
_TIMEOUT = 15
_UA = {"User-Agent": "video-edit-pro/1.0 (uso personal, no comercial masivo)"}


def busca(termino: str, pagina_size: int = 20) -> list[dict]:
    """Pistas reales de Openverse para `termino` (en inglés), con licencia
    que permite uso comercial Y modificación (hace falta poder recortarlas
    a la duración exacta que haga falta)."""
    if not termino:
        return []
    qs = urllib.parse.urlencode({
        "q": termino, "license_type": "commercial,modification",
        "page_size": pagina_size, "mature": "false",
    })
    try:
        req = urllib.request.Request(f"{_API}?{qs}", headers=_UA)
        data = json.loads(urllib.request.urlopen(req, timeout=_TIMEOUT).read())
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: Openverse (música) no respondió para «{termino}» ({exc}).")
        return []
    out = []
    for r in data.get("results") or []:
        if r.get("mature") or not r.get("url") or not r.get("duration"):
            continue
        out.append(r)
    return out


def _descarga(resultado: dict, destino: Path) -> Path | None:
    try:
        req = urllib.request.Request(resultado["url"], headers=_UA)
        raw = urllib.request.urlopen(req, timeout=30).read()
        destino.write_bytes(raw)
        return destino
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo descargar la pista «{resultado.get('title', '')}» ({exc}).")
        return None


def _duracion_s(path: Path) -> float | None:
    """Duración real de un archivo de audio local vía ffprobe (los locales
    no traen un campo "duration" como los resultados de Openverse)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _cicla_hasta_cubrir(pistas: list[tuple[Path, int]],
                        objetivo_ms: float) -> tuple[list[Path], int]:
    """Encadena `pistas` (archivo, duración ms) en orden random, repitiendo
    el ciclo completo (reordenado cada vuelta) hasta cubrir `objetivo_ms` —
    nunca deja el vídeo sin música a mitad por falta de pistas."""
    orden: list[Path] = []
    cubierto_ms = 0
    ciclo = list(pistas)
    while cubierto_ms < objetivo_ms:
        random.shuffle(ciclo)
        for p, dur in ciclo:
            if cubierto_ms >= objetivo_ms:
                break
            orden.append(p)
            cubierto_ms += dur
    return orden, cubierto_ms


def _encadena(orden: list[Path], dur_objetivo_s: float, salida: Path) -> bool:
    """Encadena `orden` (ya en el orden final, con repeticiones si hacía
    falta) en un solo WAV de exactamente `dur_objetivo_s` — re-decodifica
    cada pista (llegan en formatos distintos) y recorta al segundo exacto
    del vídeo; si sobró duración, mejor que falte: nunca alargar el vídeo
    por la música."""
    entradas: list[str] = []
    for p in orden:
        entradas += ["-i", str(p)]
    n = len(orden)
    fc = "".join(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[m{i}];"
                for i in range(n))
    fc += "".join(f"[m{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[mout]"
    try:
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *entradas, "-filter_complex", fc, "-map", "[mout]",
            "-t", f"{dur_objetivo_s:.3f}",
            "-ar", "48000", "-ac", "2", str(salida),
        ])
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no se pudo armar la pista de música de fondo ({exc}).")
        return False
    return salida.is_file()


# ------------------------------------------------------------------ ¿la canción es ESTABLE para ir de fondo?
_PERFIL_V = 1
UMBRAL_VARIA_DB = 8.0      # cuánto varía su volumen en el cuerpo (percentil 95 − 10 de ventanas de 3 s)
UMBRAL_SALTO_DB = 12.0     # un subidón o un bajón de más de esto en 4 s (un «drop»)
UMBRAL_MUDOS_S = 6         # segundos casi en silencio en medio de la canción (el «break» antes del drop)
_BORDE_S = 8               # la intro y el final no cuentan (casi todas empiezan y acaban en silencio)


def perfil(path: Path) -> dict | None:
    """Cómo se mueve el volumen de una canción, en el cuerpo (sin los primeros ni los últimos `_BORDE_S` s):
    {"varia_db", "subida_db", "t_subida", "bajada_db", "t_bajada", "mudos_s", "dur_s"}. Medido con las 10 canciones
    del autor (2026-09-24): las estables varían 1-6 dB; «Lion» (Dyalla) cae 17 dB en 1:52 y se queda 13 s casi muda
    antes de volver a subir 17 dB; «Tiny Shell» varía 10,7 dB con 15 s casi mudos. None si no se puede leer."""
    try:
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:"],
                             capture_output=True, timeout=300).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        n = len(x) // 16000
        if n < 2 * _BORDE_S + 10:
            return None
        db = 10 * np.log10((x[: n * 16000].reshape(n, 16000) ** 2).mean(axis=1) + 1e-10)      # cada segundo
        st = np.array([10 * np.log10(np.mean(10 ** (db[max(0, i - 2): i + 1] / 10))) for i in range(n)])  # ventana de 3 s
        cuerpo = st[_BORDE_S: n - _BORDE_S]
        sube = [(float(cuerpo[i + 4] - cuerpo[i]), i + _BORDE_S) for i in range(len(cuerpo) - 4)]
        baja = [(float(cuerpo[i] - cuerpo[i + 4]), i + _BORDE_S) for i in range(len(cuerpo) - 4)]
        (s, ts), (b, tb) = max(sube), max(baja)
        return {"varia_db": round(float(np.percentile(cuerpo, 95) - np.percentile(cuerpo, 10)), 1),
                "subida_db": round(s, 1), "t_subida": int(ts), "bajada_db": round(b, 1), "t_bajada": int(tb),
                "mudos_s": int((cuerpo < np.median(cuerpo) - 10).sum()), "dur_s": int(n)}
    except Exception:  # noqa: BLE001 — sin medir, la canción se usa como siempre
        return None


def estable(p: dict | None) -> tuple[bool, str]:
    """(¿sirve de fondo?, por qué no). Una canción sin medir se da por buena."""
    if not p:
        return True, ""
    mmss = lambda t: f"{t // 60}:{t % 60:02d}"                                     # noqa: E731
    s, b = p["subida_db"], p["bajada_db"]
    if s > UMBRAL_SALTO_DB and b > UMBRAL_SALTO_DB:
        return False, f"un «drop»: cae {b:.0f} dB en el {mmss(p['t_bajada'])} y vuelve a subir {s:.0f} dB"
    if b > UMBRAL_SALTO_DB:
        return False, f"bajón de {b:.0f} dB en el {mmss(p['t_bajada'])}"
    if s > UMBRAL_SALTO_DB:
        return False, f"subidón de {s:.0f} dB en el {mmss(p['t_subida'])}"
    if p["mudos_s"] > UMBRAL_MUDOS_S:
        return False, f"{p['mudos_s']} s casi en silencio en medio (un «break»)"
    if p["varia_db"] > UMBRAL_VARIA_DB:
        return False, f"su volumen cambia mucho ({p['varia_db']:.0f} dB)"
    return True, ""


def perfiles(archivos: list[Path], cache: Path | None = None) -> dict[str, dict | None]:
    """`perfil` de cada canción, midiendo solo las nuevas o cambiadas (caché por nombre, tamaño y fecha)."""
    datos: dict = {}
    if cache is not None:
        try:
            datos = json.loads(Path(cache).read_text(encoding="utf-8"))
            if datos.get("v") != _PERFIL_V:
                datos = {}
        except (OSError, ValueError):
            datos = {}
    pistas = datos.setdefault("pistas", {})
    out, cambio = {}, False
    for p in archivos:
        st = p.stat()
        clave = f"{st.st_size}:{int(st.st_mtime)}"
        prev = pistas.get(p.name)
        if prev and prev.get("clave") == clave:
            out[p.name] = prev.get("perfil")
            continue
        out[p.name] = perfil(p)
        pistas[p.name] = {"clave": clave, "perfil": out[p.name]}
        cambio = True
    if cache is not None and cambio:
        try:
            datos["v"] = _PERFIL_V
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
            Path(cache).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return out


def arma_pista_local(dur_objetivo_s: float, carpeta: Path,
                     work_dir: Path, solo_estables: bool = True,
                     cache_perfiles: Path | None = None) -> tuple[Path | None, list[str]]:
    """Música de fondo desde TU carpeta local de canciones (ver
    `videos.musica_carpeta` en config.yaml) — la fuente preferida sobre
    Openverse cuando tiene archivos: nada de red, y son canciones que ya
    elegiste tú. Cada vídeo las encadena en un orden random DISTINTO
    (nunca el mismo orden dos veces), repitiendo el ciclo si hace falta
    para cubrir el vídeo entero. Los archivos de la carpeta NUNCA se
    borran (son tuyos, se reusan en cada vídeo — a diferencia de
    `arma_pista`, que sí borra lo que descargó porque es solo temporal).
    Sin créditos: son tuyos, no traen licencia CC que atribuir. Devuelve
    (None, []) si la carpeta no existe o no tiene audio legible — en ese
    caso el que llama cae a Openverse."""
    if not carpeta.is_dir():
        return None, []
    archivos = sorted(p for p in carpeta.iterdir()
                      if p.is_file() and p.suffix.lower() in _EXTENSIONES_AUDIO)
    if not archivos:
        return None, []

    pistas: list[tuple[Path, int]] = []
    for p in archivos:
        dur = _duracion_s(p)
        if dur and dur > 1.0:
            pistas.append((p, int(dur * 1000)))
    if not pistas:
        log(f"  música de fondo: {carpeta} no tiene pistas legibles, sigo sin música.")
        return None, []
    if solo_estables:
        perf = perfiles([p for p, _ms in pistas], cache_perfiles)
        fuera = [(p, estable(perf.get(p.name))[1]) for p, _ms in pistas if not estable(perf.get(p.name))[0]]
        if fuera and len(fuera) < len(pistas):
            log(f"  música: {len(fuera)} canción(es) con subidones o bajones fuertes NO se usan de fondo: "
                + "; ".join(f"«{p.stem}» ({motivo})" for p, motivo in fuera) + ".")
            nombres_fuera = {p.name for p, _m in fuera}
            pistas = [(p, ms) for p, ms in pistas if p.name not in nombres_fuera]
        elif fuera:
            log("  AVISO: todas tus canciones tienen subidones o bajones fuertes; las uso igual (mejor eso que sin música).")

    work_dir.mkdir(parents=True, exist_ok=True)
    orden, cubierto_ms = _cicla_hasta_cubrir(pistas, dur_objetivo_s * 1000)
    salida = work_dir / "musica_fondo.wav"
    if not _encadena(orden, dur_objetivo_s, salida):
        return None, []
    log(f"  música de fondo (tu librería local): {len(pistas)} canción(es) disponibles, "
        f"{len(orden)} tramo(s) encadenado(s) en orden random.")
    return salida, []


def arma_pista(dur_objetivo_s: float, terminos: list[str], work_dir: Path,
              volumen_rel_db: float = -18.0,
              max_pistas: int = 8) -> tuple[Path | None, list[str]]:
    """Busca, descarga y encadena pistas hasta cubrir `dur_objetivo_s`
    (la duración del vídeo ya montado), en orden RANDOM dentro de lo que
    encontró — como pidió el autor, sin que suene a la misma canción en bucle
    todo el vídeo. Devuelve (ruta del WAV final, créditos CC) — o
    (None, []) si Openverse no dio nada usable.

    `terminos`: 2-3 búsquedas (géneros/estados de ánimo) para variar un poco
    entre vídeos; se prueban en orden hasta juntar candidatos de sobra.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    candidatos: list[dict] = []
    for t in terminos:
        candidatos += busca(t)
        if len(candidatos) >= max_pistas * 3:      # de sobra para elegir random
            break
    if not candidatos:
        log("  música de fondo: Openverse no devolvió pistas usables, sigo sin música.")
        return None, []

    random.shuffle(candidatos)
    elegidas: list[tuple[Path, int]] = []   # (archivo, duración ms) — descargas únicas
    creditos: list[str] = []
    cubierto_ms = 0
    objetivo_ms = dur_objetivo_s * 1000
    for i, r in enumerate(candidatos):
        if cubierto_ms >= objetivo_ms or len(elegidas) >= max_pistas:
            break
        destino = work_dir / f"pista_{i:02d}.audio"
        p = _descarga(r, destino)
        if p is None:
            continue
        dur = int(r.get("duration") or 0)
        elegidas.append((p, dur))
        cubierto_ms += dur
        atrib = r.get("attribution")
        if atrib:
            creditos.append(atrib)

    if not elegidas:
        return None, []

    # Si Openverse se quedó corto (menos pistas disponibles que duración de
    # vídeo), repite las ya descargadas — sin volver a bajarlas — en orden
    # random hasta cubrir el vídeo entero: el autor pidió explícitamente que la
    # música cubra toda la duración, nunca que se corte a mitad de vídeo.
    orden, cubierto_ms = _cicla_hasta_cubrir(elegidas, objetivo_ms)
    salida = work_dir / "musica_fondo.wav"
    ok = _encadena(orden, dur_objetivo_s, salida)
    for p, _ in elegidas:          # descargas temporales: sí se borran (no las locales)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    if not ok:
        return None, []

    log(f"  música de fondo: {len(elegidas)} pista(s) distinta(s), {len(orden)} tramo(s) "
        f"encadenado(s) ({cubierto_ms / 1000:.0f}s conseguidos para {dur_objetivo_s:.0f}s de vídeo).")
    return salida, creditos


# ------------------------------------------------------------------ revisar tu carpeta a mano y pruebas
def tabla(carpeta: Path, cache: Path | None = None) -> list[dict]:
    """Cada canción de la carpeta con su perfil y si se usa (para `python -m clipper.musica`)."""
    archivos = sorted(p for p in Path(carpeta).iterdir() if p.is_file() and p.suffix.lower() in _EXTENSIONES_AUDIO)
    perf = perfiles(archivos, cache)
    return [{"cancion": p.stem, **(perf.get(p.name) or {}), "usa": estable(perf.get(p.name))[0],
             "motivo": estable(perf.get(p.name))[1]} for p in archivos]


def prueba() -> list[dict]:
    """El filtro de canciones estables con audios sintéticos (sin red): una estable, una con un «drop»."""
    import shutil
    import tempfile
    import wave

    import numpy as np
    filas: list[dict] = []

    def r(caso, ok, detalle=""):
        filas.append({"caso": caso, "ok": bool(ok), "detalle": str(detalle)})

    def wav(ruta: Path, niveles_db: list[tuple[float, float]], dur: float = 60.0, sr: int = 16000):
        """Un «ritmo» (clics de percusión + un tono) con el volumen por tramos: [(desde_s, dBFS)]."""
        t = np.arange(int(dur * sr)) / sr
        x = 0.4 * np.sin(2 * np.pi * 110 * t) * (0.6 + 0.4 * (np.sin(2 * np.pi * 2 * t) > 0))
        gan = np.zeros_like(t)
        for desde, db in niveles_db:
            gan[t >= desde] = 10 ** (db / 20)
        pcm = np.clip(x * gan * 32767, -32768, 32767).astype(np.int16)
        with wave.open(str(ruta), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())

    tmp = Path(tempfile.mkdtemp(prefix="zz-prueba-musica-"))
    try:
        c = tmp / "canciones"
        c.mkdir()
        wav(c / "estable.wav", [(0, -18), (30, -17)])
        wav(c / "con drop.wav", [(0, -18), (25, -60), (40, -16)])
        pe, pd = perfil(c / "estable.wav"), perfil(c / "con drop.wav")
        r("una canción de volumen estable sirve de fondo", estable(pe)[0], pe)
        r("una con un «drop» (bajón a casi silencio y vuelta a subir) no, y dice dónde",
          not estable(pd)[0] and "drop" in estable(pd)[1] and "0:24" in estable(pd)[1], (pd, estable(pd)))
        usadas: list = []
        real = globals()["_encadena"]
        globals()["_encadena"] = lambda orden, dur, salida: (usadas.append({p.name for p in orden}), True)[1]
        try:
            arma_pista_local(200.0, c, tmp / "w", cache_perfiles=tmp / "perfil.json")
            r("al armar la música del vídeo solo entran las estables", usadas[-1] == {"estable.wav"}, usadas)
            veces = []
            real_perfil = globals()["perfil"]
            globals()["perfil"] = lambda p: (veces.append(p.name), real_perfil(p))[1]
            arma_pista_local(200.0, c, tmp / "w", cache_perfiles=tmp / "perfil.json")
            r("cada canción se mide una sola vez (caché): la segunda vez no se vuelve a medir", veces == [], veces)
            globals()["perfil"] = real_perfil
            (c / "estable.wav").unlink()
            arma_pista_local(200.0, c, tmp / "w", cache_perfiles=tmp / "perfil.json")
            r("si TODAS tienen subidones, se usan igual (mejor eso que un vídeo sin música)",
              usadas[-1] == {"con drop.wav"}, usadas)
            arma_pista_local(200.0, c, tmp / "w", solo_estables=False)
            r("con el filtro apagado (videos.musica_solo_estables: false) entra todo", usadas[-1] == {"con drop.wav"})
        finally:
            globals()["_encadena"] = real
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return filas


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--prueba":
        filas_ = prueba()
        for f in filas_:
            print(("OK   " if f["ok"] else "FALLA") + " " + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
        sys.exit(1 if any(not f["ok"] for f in filas_) else 0)
    from . import config as _config
    carpeta_ = Path(sys.argv[1]) if len(sys.argv) > 1 else _config.ROOT / "musica_fondo"
    for fila in tabla(carpeta_, _config.paths()["work"] / "musica_perfil.json"):
        estado = "✔ se usa" if fila["usa"] else f"✖ NO se usa: {fila['motivo']}"
        print(f"{fila['cancion'][:42]:42s} varía {fila.get('varia_db', '?'):>4} dB · {estado}")
