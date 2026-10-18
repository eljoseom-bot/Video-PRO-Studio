"""Grabaciones repetidas en inbox/: la MISMA grabación subida varias veces ocupaba una copia completa cada vez.

Por qué existe (el autor, 2026-09-24): la carpeta del proyecto pesaba 61 GB aunque había borrado todos los vídeos
terminados. Eran sus originales: 12 copias de solo 2 grabaciones (la del 22-sep subida 7 veces, la del 16-sep 5), 53 GB
de los que 44 eran repetidos. Borrar las copias habría roto los trabajos que las usan («Reintentar», el editor de
cortes, las mejoras…), así que en vez de eso:

- `deduplica(inbox)`: cada copia idéntica (comprobado byte a byte con SHA-256 del archivo ENTERO) pasa a ser un ENLACE
  DURO al mismo archivo del disco. Siguen existiendo los 12 nombres y todo funciona igual, pero ocupan como 2. Nada se
  borra: si se borra un nombre, los demás siguen (el disco libera el espacio solo cuando no queda ningún nombre).
  Ojo: el Explorador de Windows suma cada nombre, así que la carpeta sigue «pesando» lo mismo en sus Propiedades; lo
  que cambia es el espacio libre del disco (y «Probar el sistema», que cuenta cada archivo físico una vez).
- `al_subir(archivo, …)`: al empezar un vídeo nuevo, si su archivo es idéntico a otro que ya estaba en inbox, se
  enlaza a él (no ocupa espacio de nuevo) y se copian de su trabajo la transcripción, el audio, las cifras revisadas y
  la actividad en pantalla: Whisper y el análisis de pantalla no se repiten (minutos de GPU). El análisis de Gemini SÍ
  es nuevo, como siempre que subes un vídeo.

Las huellas (SHA-256) se guardan en work/huellas_inbox.json: un archivo de 4-5 GB se lee una sola vez. Solo se calcula
la huella si hay otro archivo del MISMO tamaño (lo normal —un vídeo nuevo de verdad— no cuesta nada).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

from .util import log

EXTENSIONES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
# lo que es igual si el vídeo es igual (vision.json es lo que la IA VIO de la grabación —`clipper/vision.py`—: describe
# el vídeo, no decide la edición, y el autor quiere que lo mire UNA sola vez)
CACHES = ("words.json", "cifras_revisadas.json", "audio.wav", "actividad_pantalla.json", "cursor.json", "vision.json",
          "arranque_reoido.json", "cortes_reoidos.json")
_BLOQUE = 8 * 1024 * 1024


def _clave(p: Path) -> str:
    st = p.stat()
    return f"{p.name}|{st.st_size}|{int(st.st_mtime)}"


def huella(p: Path, cache: dict | None = None) -> str:
    """SHA-256 del archivo ENTERO (no una muestra: de esto depende que dos nombres pasen a ser el mismo archivo)."""
    k = _clave(p)
    if cache is not None and k in cache:
        return cache[k]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(_BLOQUE)
            if not b:
                break
            h.update(b)
    d = h.hexdigest()
    if cache is not None:
        cache[k] = d
    return d


def _carga(ruta: Path | None) -> dict:
    if ruta is None:
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guarda(ruta: Path | None, cache: dict) -> None:
    if ruta is None:
        return
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    except OSError:
        pass


def mismo_archivo(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def enlaza(copia: Path, original: Path) -> bool:
    """`copia` pasa a ser un enlace duro a `original` (quien llama ya comprobó que su contenido es idéntico). Atómico:
    el enlace se crea con otro nombre y luego SUSTITUYE a la copia de golpe (si algo falla, la copia sigue intacta).
    False si ya eran el mismo archivo."""
    if mismo_archivo(copia, original):
        return False
    tmp = copia.with_name(copia.name + ".enlace_tmp")
    if tmp.exists() and mismo_archivo(tmp, original):
        tmp.unlink()                                      # un enlace a medias de un intento anterior (mismos datos)
    os.link(original, tmp)
    os.replace(tmp, copia)
    return True


def _videos(carpeta: Path) -> list[Path]:
    return sorted((p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES),
                  key=lambda p: (p.stat().st_mtime, p.name))


def grupos_repetidos(carpeta: Path, cache: dict | None = None) -> list[list[Path]]:
    """Grupos de archivos con el MISMO contenido (el primero de cada grupo, el más antiguo = el que se queda como
    original). Solo se lee (huella) lo que comparte tamaño con otro archivo, y un archivo físico una sola vez."""
    por_tam: dict[int, list[Path]] = defaultdict(list)
    for p in _videos(carpeta):
        por_tam[p.stat().st_size].append(p)
    grupos: list[list[Path]] = []
    for ps in por_tam.values():
        if len(ps) < 2:
            continue
        por_hash: dict[str, list[Path]] = defaultdict(list)
        ya: dict[tuple[int, int], str] = {}
        for p in ps:
            st = p.stat()
            fisico = (st.st_dev, st.st_ino)
            if fisico not in ya:
                ya[fisico] = huella(p, cache)
            por_hash[ya[fisico]].append(p)
        grupos += [g for g in por_hash.values() if len(g) >= 2]
    return grupos


def deduplica(carpeta: Path, cache_ruta: Path | None = None) -> tuple[int, list[tuple[str, str]]]:
    """Enlaza cada copia repetida de `carpeta` al original de su grupo. Devuelve (bytes liberados, [(copia, original)])."""
    cache = _carga(cache_ruta)
    liberado, hechos = 0, []
    try:
        for g in grupos_repetidos(carpeta, cache):
            original = g[0]
            for copia in g[1:]:
                tam = copia.stat().st_size
                if enlaza(copia, original):
                    liberado += tam
                    hechos.append((copia.name, original.name))
    finally:
        _guarda(cache_ruta, cache)
    return liberado, hechos


def al_subir(archivo: Path, carpeta: Path, work_nuevo: Path, work_de, cache_ruta: Path | None = None) -> Path | None:
    """Si `archivo` (en `carpeta`, inbox) es idéntico a otro que ya estaba: lo enlaza a ese y copia a `work_nuevo` (su
    carpeta de trabajo) las cachés de la grabación (`CACHES`: transcripción, audio, movimiento y lo que la IA vio del
    vídeo, no los planes de edición) desde la del gemelo (`work_de(ruta)`), las que le falten. Devuelve el gemelo o None. Nunca revienta: si algo falla, el vídeo sigue como un archivo más (solo se
    pierde el ahorro)."""
    try:
        archivo = Path(archivo)
        if archivo.parent.resolve() != Path(carpeta).resolve() or not archivo.is_file():
            return None
        tam = archivo.stat().st_size
        candidatos = [p for p in _videos(carpeta) if p != archivo and p.stat().st_size == tam]
        if not candidatos:
            return None                                    # lo normal: un vídeo nuevo de verdad, sin leer nada
        cache = _carga(cache_ruta)
        mia = huella(archivo, cache)
        gemelo = next((p for p in candidatos if mismo_archivo(p, archivo) or huella(p, cache) == mia), None)
        _guarda(cache_ruta, cache)
        if gemelo is None:
            return None
        if enlaza(archivo, gemelo):
            log(f"Este archivo es idéntico a «{gemelo.name}» (ya lo habías subido): no ocupa espacio de nuevo "
                f"({tam / 1e9:.1f} GB ahorrados).")
        destino, origen = Path(work_nuevo), work_de(gemelo)
        copiadas = []
        for nombre in CACHES:
            src, dst = origen / nombre, destino / nombre
            if src.is_file() and not dst.exists():
                destino.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copiadas.append(nombre)
        if "words.json" in copiadas:
            log("  Reuso su transcripción (Whisper no se repite); el análisis de Gemini sí es nuevo.")
        return gemelo
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: no pude comprobar si este vídeo ya estaba subido ({exc}); sigo normal.")
        return None


def tamano_real(carpeta: Path) -> int:
    """Lo que de verdad ocupa `carpeta` en el disco: cada archivo físico una sola vez (el Explorador de Windows y un
    `sum(st_size)` cuentan cada enlace duro como si fuera otro archivo)."""
    vistos, total = set(), 0
    for f in carpeta.rglob("*"):
        try:
            if f.is_file():
                st = f.stat()
                if (st.st_dev, st.st_ino) not in vistos:
                    vistos.add((st.st_dev, st.st_ino))
                    total += st.st_size
        except OSError:
            pass
    return total


def prueba() -> list[dict]:
    """Con archivos pequeños en una carpeta temporal (sin tocar inbox/)."""
    import tempfile
    filas: list[dict] = []

    def caso(n, ok, d=""):
        filas.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        inbox, work = raiz / "inbox", raiz / "work"
        inbox.mkdir()
        a, b, c = inbox / "grab.mkv", inbox / "grab-2.mkv", inbox / "otra.mkv"
        a.write_bytes(b"X" * 300_000)
        b.write_bytes(b"X" * 300_000)
        c.write_bytes(b"X" * 299_999 + b"Y")              # mismo tamaño, contenido distinto: NO es copia
        os.utime(a, (1000, 1000))
        antes = tamano_real(inbox)
        lib, hechos = deduplica(inbox, work / "huellas.json")
        caso("la copia idéntica pasa a ser el MISMO archivo que el original (y la de contenido distinto no)",
             mismo_archivo(a, b) and not mismo_archivo(a, c) and hechos == [("grab-2.mkv", "grab.mkv")], hechos)
        caso("los 3 nombres siguen existiendo y con su contenido de siempre",
             a.read_bytes() == b.read_bytes() == b"X" * 300_000 and c.read_bytes().endswith(b"Y"))
        caso("el espacio real baja justo una copia (y así lo cuenta `tamano_real`)",
             lib == 300_000 and tamano_real(inbox) == antes - 300_000, (antes, tamano_real(inbox)))
        caso("repetirlo no hace nada (ya son el mismo archivo)", deduplica(inbox, work / "huellas.json") == (0, []))
        # subir OTRA vez la misma grabación: se enlaza y se reusan las cachés del trabajo del gemelo
        (work / "w_grab").mkdir(parents=True)
        (work / "w_grab" / "words.json").write_text('[{"word": "hola", "start": 0.1, "end": 0.4}]', encoding="utf-8")
        (work / "w_grab" / "audio.wav").write_bytes(b"RIFF")
        d = inbox / "grab-3.mkv"
        d.write_bytes(b"X" * 300_000)
        gem = al_subir(d, inbox, work / "w_nuevo", lambda p: work / ("w_" + p.stem.split("-")[0]), work / "huellas.json")
        caso("una subida repetida se enlaza al archivo que ya estaba (no ocupa espacio de nuevo)",
             gem is not None and mismo_archivo(d, a), gem)
        caso("…y hereda la transcripción y el audio de ese trabajo (Whisper no se repite)",
             (work / "w_nuevo" / "words.json").read_text(encoding="utf-8").startswith('[{"word": "hola"')
             and (work / "w_nuevo" / "audio.wav").is_file())
        e = inbox / "nueva.mkv"
        e.write_bytes(b"Z" * 123)
        caso("un vídeo nuevo de verdad (sin otro del mismo tamaño) no se toca ni se lee",
             al_subir(e, inbox, work / "w_e", lambda p: work / p.stem, work / "huellas.json") is None
             and not mismo_archivo(e, a))
    return filas


if __name__ == "__main__":
    import sys
    from . import config
    P = config.paths()
    if "--prueba" in sys.argv:
        for f in prueba():
            print(("OK   " if f["ok"] else "FALLA") + " " + f["caso"] + ("" if f["ok"] else f"  -> {f['detalle']}"))
    else:
        cache = P["work"] / "huellas_inbox.json"
        if "--aplica" in sys.argv:
            libre0 = shutil.disk_usage(P["inbox"]).free
            lib, hechos = deduplica(P["inbox"], cache)
            for copia, orig in hechos:
                print(f"  {copia}  ->  enlace a {orig}")
            print(f"{len(hechos)} copia(s) enlazadas · {lib / 1e9:.1f} GB liberados · espacio libre del disco: "
                  f"{libre0 / 1e9:.1f} -> {shutil.disk_usage(P['inbox']).free / 1e9:.1f} GB")
        else:
            c = _carga(cache)
            for g in grupos_repetidos(P["inbox"], c):
                print(f"iguales: {g[0].name} (se queda) <- " + ", ".join(p.name for p in g[1:]))
            _guarda(cache, c)
            print("(en seco: añade --aplica para enlazarlas)")
