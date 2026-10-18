"""Mandar archivos y carpetas a la PAPELERA DE RECICLAJE de Windows (nunca borrarlos para siempre).

el autor (2026-09-26): «siempre haya la opción de borrar… el simbolito de la basurita… y que se borre también en mi PC». El
🗑 de «Mis vídeos» lo manda todo aquí: se recupera desde la Papelera hasta que él la vacíe (eso lo decide él). Si Windows no
pudiera reciclar algo (p. ej. no cabe en la Papelera), Windows AVISA antes de borrarlo del todo (FOF_WANTNUKEWARNING): nada
desaparece sin que él lo acepte.
"""
from __future__ import annotations

import os
from pathlib import Path

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


def tamano(rutas) -> int:
    """Bytes que ocupan (carpetas enteras incluidas; un enlace duro cuenta una vez por nombre)."""
    total = 0
    for r in rutas:
        p = Path(r)
        try:
            if p.is_file():
                total += p.stat().st_size
            elif p.is_dir():
                for raiz, _dirs, archivos in os.walk(p):
                    for a in archivos:
                        try:
                            total += (Path(raiz) / a).stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
    return total


def _shfileop(rutas: list[str]) -> tuple[bool, str]:
    """SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO…): lo que hace el Explorador al pulsar «Suprimir» (va a la Papelera)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    buf = ctypes.create_unicode_buffer("\0".join(rutas) + "\0\0")      # lista terminada en doble nulo
    op = SHFILEOPSTRUCTW()
    op.hwnd, op.wFunc, op.pFrom, op.pTo = None, FO_DELETE, ctypes.cast(buf, ctypes.c_void_p), None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0:
        return False, f"Windows no pudo mandarlo a la Papelera (código {res})."
    if op.fAnyOperationsAborted:
        return False, "Se canceló (Windows avisó de que algo no cabía en la Papelera y no se aceptó)."
    return True, ""


def a_la_papelera(rutas, _op=None) -> tuple[bool, str, list[str]]:
    """Manda `rutas` (las que existan) a la Papelera de reciclaje en UNA operación. -> (ok, error, rutas mandadas).
    `_op` sustituye la llamada a Windows en las pruebas (así «Probar el sistema» no te llena la Papelera)."""
    existen = [str(Path(r).resolve()) for r in rutas if Path(r).exists()]
    if not existen:
        return True, "", []
    ok, error = (_op or _shfileop)(existen)
    return ok, error, existen


def prueba() -> list[dict]:
    """Sin tocar la Papelera de verdad (una operación falsa): solo manda lo que existe, en una sola llamada, con rutas
    absolutas; cuenta bien el tamaño; y un fallo de Windows vuelve como error claro."""
    import tempfile
    out: list[dict] = []

    def caso(n, ok, d=""):
        out.append({"caso": n, "ok": bool(ok), "detalle": str(d)})

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "a").mkdir()
        (t / "a" / "x.bin").write_bytes(b"1" * 1000)
        (t / "b.bin").write_bytes(b"2" * 500)
        caso("tamaño de carpetas y archivos", tamano([t / "a", t / "b.bin", t / "no-existe"]) == 1500,
             tamano([t / "a", t / "b.bin"]))
        llamadas: list = []
        ok, err, mandadas = a_la_papelera([t / "a", t / "no-existe", t / "b.bin"],
                                          _op=lambda r: (llamadas.append(list(r)), (True, ""))[1])
        caso("solo lo que existe, en UNA operación y con rutas absolutas",
             ok and len(llamadas) == 1 and len(mandadas) == 2 and all(Path(m).is_absolute() for m in mandadas), llamadas)
        ok2, err2, _m = a_la_papelera([t / "b.bin"], _op=lambda r: (False, "Windows no pudo (código 2)."))
        caso("si Windows falla, vuelve el error (y no se da por borrado)", not ok2 and "Windows" in err2, err2)
        caso("nada que mandar = ok sin llamar a Windows",
             a_la_papelera([t / "nada"], _op=lambda r: (_ for _ in ()).throw(AssertionError("no debía llamar")))[0])
    return out
