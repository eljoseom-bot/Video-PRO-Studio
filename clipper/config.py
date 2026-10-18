"""Carga y valida config.yaml."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path | None = None) -> None:
    """Carga sencilla de un .env (KEY=VALUE por línea) en os.environ.
    No pisa variables que ya estén puestas. Sin dependencias nuevas.
    Así no hay que exportar GEMINI_API_KEY / GROQ_API_KEY en cada sesión."""
    path = path or ROOT / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


class Config(dict):
    """dict con acceso por punto anidado: cfg.get_path('detect.max_clips')"""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _mezcla_canales_usuario(data: dict, carpeta: Path | None = None) -> None:
    """Los canales creados desde el panel viven en canales/<nombre>.yaml (uno por canal) y se SUMAN a
    videos.canales; el que ya esté en config.yaml (el de ejemplo) nunca se pisa."""
    carpeta = carpeta or ROOT / "canales"
    if not carpeta.is_dir():
        return
    canales = data.setdefault("videos", {}).setdefault("canales", {})
    for f in sorted(carpeta.glob("*.yaml")):
        try:
            info = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(info, dict) and f.stem not in canales:
            info["_propio"] = True
            canales[f.stem] = info


def canales_visibles(cfg) -> dict:
    """Los canales que se enseñan y se eligen: los tuyos. El de EJEMPLO (con datos inventados, para que el programa funcione desde el primer minuto) solo sale mientras no has creado ninguno."""
    todos = dict(cfg.get_path("videos.canales") or {}) if cfg is not None else {}
    reales = {k: v for k, v in todos.items() if not (isinstance(v, dict) and v.get("ejemplo"))}
    return reales or todos


def canal_defecto(cfg) -> str:
    """El canal que se usa cuando nadie dice cuál: `ideas.canal` de config.yaml si está, y si no el primero de los tuyos («» si no hay ninguno)."""
    c = cfg.get_path("ideas.canal") if cfg is not None else None
    if c:
        return str(c)
    return next(iter(canales_visibles(cfg)), "")


def _fusiona(base: dict, extra: dict) -> dict:
    """Mezcla `extra` ENCIMA de `base` (los diccionarios se mezclan por dentro; lo demás se sustituye)."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _fusiona(base[k], v)
        else:
            base[k] = v
    return base


def load(path: str | Path | None = None) -> Config:
    principal = path is None
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if principal:
        propio = ROOT / "config.local.yaml"                   # TUS ajustes (no se sube a Git ni se pisa al actualizar): solo escribe lo que quieras cambiar de config.yaml
        if propio.is_file():
            try:
                _fusiona(data, yaml.safe_load(propio.read_text(encoding="utf-8")) or {})
            except (OSError, yaml.YAMLError):
                pass
        _mezcla_canales_usuario(data)
    return Config(data)


_RUTA_MOTOR = ROOT / "config.yaml"
_RE_MOTOR = re.compile(r"(?m)^(  motor:)\s*\w+(\s*(?:#.*)?)$")


def guarda_motor(valor: str, ruta: Path | None = None) -> bool:
    """Cambia `output.motor` (auto|gpu|cpu — Opciones del panel, pedido del autor 2026-09-22) EN
    config.yaml, tocando solo esa línea (igual que `clave.escribe_env` con el .env): así
    `config.load()` la recoge sola en el siguiente vídeo, sin reiniciar el panel, y sin perder
    ni un comentario del archivo. Devuelve False si el valor no es válido o la línea no existe."""
    if valor not in ("auto", "gpu", "cpu"):
        return False
    ruta = ruta or _RUTA_MOTOR
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        return False
    nuevo, n = _RE_MOTOR.subn(lambda m: f"{m.group(1)} {valor}{m.group(2)}", texto, count=1)
    if n == 0:
        return False
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(nuevo, encoding="utf-8")
    tmp.replace(ruta)
    return True


_RE_VISION = re.compile(r"(?m)^(  ia_ve_video:)[ \t]*\w+([ \t]*(?:#.*)?)$")


def guarda_vision(activo: bool, ruta: Path | None = None) -> bool:
    """Cambia `videos.ia_ve_video` («La IA ve el vídeo», ⚙️ Opciones del panel, el autor 2026-09-24) en config.yaml tocando
    solo esa línea, como `guarda_motor`: se aplica desde el siguiente vídeo, sin reiniciar el panel."""
    ruta = ruta or _RUTA_MOTOR
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        return False
    valor = "true" if activo else "false"
    nuevo, n = _RE_VISION.subn(lambda m: f"{m.group(1)} {valor}{m.group(2)}", texto, count=1)
    if n == 0:
        return False
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(nuevo, encoding="utf-8")
    tmp.replace(ruta)
    return True


def paths() -> dict[str, Path]:
    # Carpeta de salida configurable en config.yaml -> output.folder
    try:
        out_name = (load().get_path("output.folder") or "shorts").strip("/\\")
    except Exception:
        out_name = "shorts"

    p = {
        "root": ROOT,
        "inbox": ROOT / "inbox",
        "outbox": ROOT / out_name,
        "work": ROOT / "work",
        "fonts": ROOT / "fonts",
        "state": ROOT / "work" / "state.json",
    }
    for key in ("inbox", "outbox", "work", "fonts"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY")


def groq_key() -> str | None:
    return os.environ.get("GROQ_API_KEY")
