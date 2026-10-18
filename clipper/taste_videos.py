"""Aprendizaje SEPARADO para los vídeos horizontales largos.

Nada que ver con el de Shorts (`taste.py`). Allí se aprende qué MOMENTOS
funcionan; aquí se aprende qué TEMÁTICAS mueven la aguja.

  - `record_seleccion()`  qué temáticas eliges publicar y cuáles descartas
  - `refresh_stats()`     vistas reales de cada vídeo (videos.list, gratis)
  - `profile()`           ranking de temáticas: cuáles rinden
  - `sugerencias()`       "de esto ya hablaste y funcionó / esto no tira"

Se guarda en `work/taste_videos/<juego>.json`. Con pocos datos no hace nada:
un directo da 2-5 vídeos, no 90 clips, así que esto aprende despacio a
propósito y nunca decide por ti.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ROOT
from .util import log, normalize

DIR = ROOT / "work" / "taste_videos"


def _fichero(juego: str) -> Path:
    return DIR / f"{(juego or 'general').strip() or 'general'}.json"


def _cargar(juego: str) -> list[dict]:
    try:
        d = json.loads(_fichero(juego).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _guardar(filas: list[dict], juego: str) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        _fichero(juego).write_text(json.dumps(filas, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    except OSError as exc:
        log(f"taste_videos: no se pudo guardar ({exc}).")


def _clave(tematica: str) -> str:
    return normalize(tematica or "").strip()[:80]


# ─────────────────────────────────────────────────────── registrar decisiones
def record_seleccion(juego: str, jid: str, elegidos, todos, manifest: dict) -> None:
    """`elegidos` = archivos que mandó a publicar; `todos` = los que se le
    ofrecieron. Elegido = +1, no elegido = -1. Es la señal principal."""
    elegidos = {Path(str(a)).name for a in (elegidos or [])}
    todos = {Path(str(a)).name for a in (todos or [])} | elegidos
    if not todos:
        return
    filas = _cargar(juego)
    confirmados = {f.get("archivo") for f in filas if f.get("video_id")}
    resto = [f for f in filas if f.get("archivo") in confirmados
             or f.get("archivo") not in todos or f.get("jid") != jid]
    cambios = 0
    for arch in todos:
        if arch in confirmados:
            continue
        m = manifest.get(arch) or {}
        resto.append({
            "label": 1 if arch in elegidos else -1,
            "jid": jid, "archivo": arch, "juego": juego,
            "tematica": _clave(m.get("tematica") or m.get("titulo", "")),
            "titulo": m.get("titulo", ""),
            "duracion": float(m.get("duracion", 0) or 0),
            "n_tramos": int(m.get("segments") and len(m["segments"]) or 0),
            "registrado": time.time(),
            "video_id": None, "views": 0, "likes": 0, "published_at": None,
        })
        cambios += 1
    if cambios:
        _guardar(resto, juego)
        pos = sum(1 for f in resto if f.get("label") == 1)
        log(f"Aprendizaje VÍDEOS [{juego}]: {len(elegidos)} elegidos / "
            f"{len(todos - elegidos)} no ({pos} positivos en total).")


def record_upload(juego: str, archivo: str, video_id: str) -> None:
    """Confirma que ese vídeo se subió de verdad (para poder pedir vistas)."""
    filas = _cargar(juego)
    for f in filas:
        if f.get("archivo") == archivo:
            f["video_id"] = video_id
            f["label"] = 1
            f["subido"] = time.time()
            _guardar(filas, juego)
            return


def refresh_stats(video_stats_fn) -> int:
    """Actualiza vistas/likes de todos los vídeos subidos, de todos los juegos."""
    if not DIR.is_dir():
        return 0
    tocados = 0
    for p in DIR.glob("*.json"):
        filas = _cargar(p.stem)
        ids = [f["video_id"] for f in filas if f.get("video_id")]
        if not ids:
            continue
        try:
            st = video_stats_fn(ids)
        except Exception as exc:                       # noqa: BLE001
            log(f"taste_videos: no se pudieron pedir vistas ({exc}).")
            continue
        for f in filas:
            s = st.get(f.get("video_id") or "")
            if s:
                f["views"] = s.get("views", f.get("views", 0))
                f["likes"] = s.get("likes", f.get("likes", 0))
                f["published_at"] = s.get("published_at") or f.get("published_at")
                tocados += 1
        _guardar(filas, p.stem)
    return tocados


# ──────────────────────────────────────────────────────────────── el perfil
def _por_dia(f: dict) -> float:
    """Vistas por día de vida: compara justo un vídeo de ayer con uno de hace un mes."""
    pa, v = f.get("published_at"), float(f.get("views", 0) or 0)
    if not pa or v <= 0:
        return 0.0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
        dias = max(0.5, (time.time() - dt.timestamp()) / 86400)
        return v / dias
    except (ValueError, TypeError):
        return 0.0


def profile(cfg, juego: str | None = None) -> dict:
    """Ranking de temáticas. Mezcla lo que ELIGES publicar con lo que MÁS SE VE,
    con los mismos pesos que el de Shorts (50/50 por defecto)."""
    juego = juego or str(cfg.get("_tema") or "general")
    filas = _cargar(juego)
    need = int(cfg.get_path("videos.min_subidos", 4))
    pos = [f for f in filas if int(f.get("label", 1)) == 1]
    neg = [f for f in filas if int(f.get("label", 1)) == -1]
    base = {"juego": juego, "n": len(pos), "n_neg": len(neg), "need": need,
            "listo": False, "temas": [], "evita": []}
    if not cfg.get_path("videos.aprendizaje", True) or len(pos) < need:
        return base

    pg = float(cfg.get_path("videos.peso_gusto", 0.5))
    pv = float(cfg.get_path("videos.peso_visitas", 0.5))
    vistas = [_por_dia(f) for f in pos]
    vmax = max(vistas) if vistas else 0.0

    agreg: dict[str, dict] = {}
    for f in filas:
        k = f.get("tematica") or ""
        if not k:
            continue
        a = agreg.setdefault(k, {"tema": k, "n": 0, "subidos": 0, "descartados": 0,
                                 "views": 0, "mejor": "", "vpd": 0.0})
        a["n"] += 1
        if int(f.get("label", 1)) == 1:
            a["subidos"] += 1
            a["views"] += int(f.get("views", 0) or 0)
            vpd = _por_dia(f)
            if vpd > a["vpd"]:
                a["vpd"], a["mejor"] = vpd, f.get("titulo", "")
        else:
            a["descartados"] += 1

    for a in agreg.values():
        gusto = a["subidos"] / max(1, a["n"])                 # % que decides publicar
        rinde = (a["vpd"] / vmax) if vmax > 0 else 0.0        # cómo rinde vs tu mejor
        a["puntos"] = round(pg * gusto + pv * rinde, 3)
        a["vpd"] = round(a["vpd"], 1)

    orden = sorted(agreg.values(), key=lambda a: -a["puntos"])
    base.update(listo=True, temas=orden[:12],
                evita=[a for a in orden if a["subidos"] == 0 and a["descartados"] >= 2][:5])
    return base


def sugerencias(cfg, juego: str | None = None, n: int = 5) -> list[dict]:
    """Para el día de mañana: «de esto ya hablaste y funcionó, vuelve a tocarlo».
    Devuelve [{tema, motivo, vpd}]. Vacío si aún no hay datos suficientes."""
    p = profile(cfg, juego)
    if not p.get("listo"):
        return []
    out = []
    for a in p["temas"][:n]:
        if a["subidos"] == 0:
            continue
        out.append({
            "tema": a["tema"], "vpd": a["vpd"], "views": a["views"],
            "motivo": (f"{a['subidos']} vídeo(s) publicados, "
                       f"~{a['vpd']:.0f} vistas/día el mejor"),
        })
    return out


def ordena(videos: list[dict], cfg, juego: str | None = None) -> list[dict]:
    """Reordena las temáticas detectadas poniendo delante las que históricamente
    rinden. Si aún no hay perfil, las deja como venían (por minutos hablados)."""
    p = profile(cfg, juego)
    if not p.get("listo"):
        return videos
    puntos = {a["tema"]: a["puntos"] for a in p["temas"]}
    evita = {a["tema"] for a in p["evita"]}
    for v in videos:
        k = _clave(v.get("tema", ""))
        v["_pts"] = puntos.get(k, 0.0) - (0.5 if k in evita else 0.0)
    videos.sort(key=lambda v: (-v.get("_pts", 0.0), -v.get("duracion", 0)))
    for v in videos:
        v.pop("_pts", None)
    return videos


def horas_sugeridas(cfg, perday: int = 1) -> dict:
    """Horas a las que publicar los VÍDEOS LARGOS. Nada que ver con las de los
    Shorts: otro formato, otro público, otro momento de consumo.

    Prioridad: 1) las horas a las que TUS vídeos han rendido mejor,
               2) las que tú sueles usar,  3) las de config.
    Devuelve {"horas": ["18:00", ...], "fuente": "rendimiento|costumbre|default"}.
    """
    v = (cfg.get("videos") or {}).get("publicar") or {}
    off = int(cfg.get_path("publish.zona_offset_horas", -5))
    porDefecto = [h.strip() for h in
                  str(v.get("horas", "18:00")).split(",") if h.strip()]

    # --- 1) por rendimiento real ---
    from datetime import datetime, timedelta
    cubos: dict[int, list[float]] = {}
    n = 0
    if DIR.is_dir():
        for f in DIR.glob("*.json"):
            for fila in _cargar(f.stem):
                pa, vis = fila.get("published_at"), float(fila.get("views", 0) or 0)
                if not pa or vis <= 0:
                    continue
                try:
                    dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
                except ValueError:
                    continue
                edad = max(0.5, (time.time() - dt.timestamp()) / 86400)
                cubos.setdefault((dt + timedelta(hours=off)).hour, []).append(vis / edad)
                n += 1
    minimo = int(v.get("horas_min", 5))
    if n >= minimo and cubos:
        orden = sorted(cubos.items(), key=lambda kv: -(sum(kv[1]) / len(kv[1])))
        horas = [f"{h:02d}:00" for h, _ in orden[:perday]]
        if horas:
            return {"horas": _completa(horas, perday, porDefecto), "fuente": "rendimiento"}

    # --- 2) por costumbre: a qué hora los sueles poner ---
    from collections import Counter
    c: Counter = Counter()
    if DIR.is_dir():
        for f in DIR.glob("*.json"):
            for fila in _cargar(f.stem):
                pa = fila.get("published_at")
                if not pa:
                    continue
                try:
                    dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
                except ValueError:
                    continue
                c[(dt + timedelta(hours=off)).hour] += 1
    if sum(c.values()) >= 3:
        horas = [f"{h:02d}:00" for h, _ in c.most_common(perday)]
        return {"horas": _completa(horas, perday, porDefecto), "fuente": "costumbre"}

    return {"horas": _completa(porDefecto, perday, ["18:00"]), "fuente": "default"}


def _completa(horas: list[str], n: int, respaldo: list[str]) -> list[str]:
    """Asegura que hay exactamente n horas, rellenando si hacen falta."""
    out = list(dict.fromkeys([h for h in horas if h]))
    i = 0
    while len(out) < n:
        cand = respaldo[i % len(respaldo)] if respaldo else "18:00"
        if cand not in out:
            out.append(cand)
        else:
            out.append(f"{(int(cand.split(':')[0]) + 3 + i) % 24:02d}:00")
        i += 1
    return out[:max(1, n)]
