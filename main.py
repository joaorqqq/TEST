"""
╔═══════════════════════════════════════════════════════════════════╗
║                    ANDRUX TERMINAL v2.0                           ║
║         Mobile-First Python Terminal Ecosystem                    ║
║         ARQUIVO ÚNICO — GUI + Kernel + Motor Nativo               ║
╠═══════════════════════════════════════════════════════════════════╣
║  MÓDULOS EMBUTIDOS:                                               ║
║  - AndruxKernel     : Tradução de sintaxe de comandos             ║
║  - AndruxShell      : Executor com streaming de output            ║
║  - AndruxHistory    : Histórico persistente                       ║
║  - AndruxAliasDB    : Aliases customizados                        ║
║  - AndruxPermission : Permissões Android                          ║
║  - NativeEngine     : Comandos Python puro (sem binários)         ║
║  - AndruxApp        : Interface Flet retro/hacker                 ║
╠═══════════════════════════════════════════════════════════════════╣
║  INSTALL:  pip install flet                                       ║
║  RUN PC:   python andrux.py                                       ║
║  RUN APK:  flet build apk                                         ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import flet as ft
import subprocess
import threading
import os
import sys
import json
import re
import shlex
import time
import platform
import shutil
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

# Motor nativo: embutido abaixo (NativeEngine)
_NATIVE_AVAILABLE = True


# ─────────────────────────────────────────────
#  CONSTANTS & CONFIG
# ─────────────────────────────────────────────

ANDRUX_VERSION = "2.0.0"
ANDRUX_CODENAME = "PHANTOM"

IS_ANDROID = hasattr(sys, "getandroidapilevel") or os.path.exists("/system/app")
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

# ── BUG FIX #1: Path seguro para Android ──────────────────────────
# Path.home() no Android aponta para um local sem permissão de escrita,
# causando OSError e fechamento imediato do app.
# Solução: detectar a plataforma e usar o diretório gravável correto.
def _get_safe_base_dir() -> Path:
    """Retorna diretório base com garantia de escrita em qualquer plataforma."""
    candidates = []
    if IS_ANDROID:
        # Android: tempfile.gettempdir() retorna /data/local/tmp — sempre gravável.
        candidates = [
            Path(tempfile.gettempdir()) / "andrux",
            Path("/data/local/tmp/andrux"),
        ]
    else:
        candidates = [
            Path.home() / ".andrux",
            Path(tempfile.gettempdir()) / "andrux",
        ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            return candidate
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "andrux_fallback"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

ANDRUX_DIR   = _get_safe_base_dir()
HISTORY_FILE = ANDRUX_DIR / "history.json"
ALIAS_FILE   = ANDRUX_DIR / "aliases.json"
CONFIG_FILE  = ANDRUX_DIR / "config.json"
LOG_FILE     = ANDRUX_DIR / "andrux.log"
SCRIPTS_DIR  = ANDRUX_DIR / "scripts"

# Retro terminal color palette (ANSI-style mapped to Flet colors)
COLORS = {
    "bg":           "#0a0d0f",
    "bg_secondary": "#0f1418",
    "panel":        "#111820",
    "border":       "#1a2a1a",
    "green":        "#00ff41",
    "green_dim":    "#00aa2a",
    "green_dark":   "#003a0a",
    "cyan":         "#00e5ff",
    "yellow":       "#ffd600",
    "red":          "#ff1744",
    "orange":       "#ff6d00",
    "white":        "#e0ffe0",
    "grey":         "#546e5a",
    "grey_dim":     "#2a3a2a",
    "purple":       "#b388ff",
    "pink":         "#f48fb1",
}

DEFAULT_CONFIG = {
    "theme": "matrix",
    "font_size": 13,
    "max_history": 500,
    "show_timestamp": True,
    "show_banner": True,
    "prompt_style": "full",   # full | minimal | custom
    "custom_prompt": "andrux> ",
    "auto_complete": True,
    "animation_speed": 30,    # ms per character for typewriter effect
    "max_output_lines": 2000,
}


# ── Imports adicionais do motor nativo ──────────────────────────────
from __future__ import annotations
import stat
import gzip
import socket
import struct
import tarfile
import hashlib
import fnmatch
import zipfile
import urllib.request
import urllib.error
import urllib.parse
import importlib.util
from io import StringIO, BytesIO

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    try:
        import importlib_metadata  # backport
    except ImportError:
        importlib_metadata = None

try:
    import subprocess as subprocess
    _HAS_SUBPROCESS = True
except ImportError:
    _HAS_SUBPROCESS = False


# Tipo de output line: (texto, categoria)
# categoria: "out" | "err" | "info" | "warn"
Line = tuple[str, str]
CmdGenerator = Generator[Line, None, int]  # yields Lines, returns exit code


# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}P"

def _fmt_perms(mode: int) -> str:
    kinds = {
        stat.S_IFDIR: "d", stat.S_IFLNK: "l", stat.S_IFREG: "-",
        stat.S_IFBLK: "b", stat.S_IFCHR: "c", stat.S_IFIFO: "p",
    }
    kind = kinds.get(stat.S_IFMT(mode), "?")
    bits = ""
    for who in ("USR", "GRP", "OTH"):
        bits += "r" if mode & getattr(stat, f"S_IR{who}") else "-"
        bits += "w" if mode & getattr(stat, f"S_IW{who}") else "-"
        bits += "x" if mode & getattr(stat, f"S_IX{who}") else "-"
    return kind + bits

def _resolve(path: str, cwd: str) -> Path:
    p = Path(os.path.expanduser(os.path.expandvars(path)))
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()

def _out(text: str) -> Line:
    return (text, "out")

def _err(text: str) -> Line:
    return (text, "err")

def _info(text: str) -> Line:
    return (text, "info")

def _warn(text: str) -> Line:
    return (text, "warn")


# ─────────────────────────────────────────────────────────────────
#  ARQUIVO — ls, cat, mkdir, rm, cp, mv, find, du, df, which, touch, wc
# ─────────────────────────────────────────────────────────────────

def native_ls(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """ls -la nativo: lista arquivos com permissões, tamanho e data."""
    # Parse simples de flags e paths
    flags = set()
    paths = []
    for a in args:
        if a.startswith("-"):
            flags.update(a[1:])
        else:
            paths.append(a)

    show_hidden = "a" in flags
    long_format = "l" in flags or True  # sempre long no Andrux

    targets = [_resolve(p, cwd) for p in paths] if paths else [Path(cwd)]

    for target in targets:
        if len(targets) > 1:
            yield _out(f"\n{target}:")

        if target.is_dir():
            try:
                entries = sorted(
                    target.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower())
                )
            except PermissionError:
                yield _err(f"ls: '{target}': Permissão negada")
                continue

            if not show_hidden:
                entries = [e for e in entries if not e.name.startswith(".")]

            total_blocks = sum(
                (e.stat().st_size // 512) for e in entries
                if not e.is_symlink() and e.exists()
            )
            yield _out(f"total {total_blocks}")

            for entry in entries:
                try:
                    s = entry.lstat()
                    perms = _fmt_perms(s.st_mode)
                    nlinks = s.st_nlink
                    size = _fmt_size(s.st_size) if "h" in flags else str(s.st_size)
                    mtime = datetime.fromtimestamp(s.st_mtime).strftime("%b %d %H:%M")
                    name = entry.name
                    if entry.is_symlink():
                        try:
                            name += f" -> {os.readlink(entry)}"
                        except OSError:
                            pass
                    yield _out(f"{perms}  {nlinks:3}  {size:>8}  {mtime}  {name}")
                except OSError as exc:
                    yield _err(f"ls: '{entry.name}': {exc.strerror}")
        elif target.exists():
            try:
                s = target.lstat()
                yield _out(
                    f"{_fmt_perms(s.st_mode)}  1  {s.st_size:>8}  "
                    f"{datetime.fromtimestamp(s.st_mtime).strftime('%b %d %H:%M')}  {target.name}"
                )
            except OSError as exc:
                yield _err(str(exc))
        else:
            yield _err(f"ls: '{target}': Arquivo ou diretório não encontrado")
    return 0


def native_cat(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """cat nativo: exibe conteúdo de arquivos."""
    if not args:
        yield _err("cat: nenhum arquivo especificado")
        return 1

    show_lines = "-n" in args
    files = [a for a in args if not a.startswith("-")]

    for fname in files:
        path = _resolve(fname, cwd)
        if not path.exists():
            yield _err(f"cat: '{fname}': Arquivo não encontrado")
            continue
        if path.is_dir():
            yield _err(f"cat: '{fname}': É um diretório")
            continue
        try:
            # Tenta decodificar como texto; se falhar, mostra hex
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except UnicodeDecodeError:
                content = path.read_text(encoding="latin-1")
            for i, line in enumerate(content.splitlines(), 1):
                prefix = f"{i:6}\t" if show_lines else ""
                yield _out(f"{prefix}{line}")
        except PermissionError:
            yield _err(f"cat: '{fname}': Permissão negada")
        except OSError as exc:
            yield _err(f"cat: '{fname}': {exc.strerror}")
    return 0


def native_mkdir(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """mkdir -p nativo."""
    if not args:
        yield _err("mkdir: nenhum caminho especificado")
        return 1
    parents = "-p" in args
    dirs = [a for a in args if not a.startswith("-")]
    for d in dirs:
        path = _resolve(d, cwd)
        try:
            path.mkdir(parents=parents, exist_ok=parents)
            yield _info(f"✓ Diretório criado: {path}")
        except FileExistsError:
            yield _err(f"mkdir: '{d}': Já existe")
        except PermissionError:
            yield _err(f"mkdir: '{d}': Permissão negada")
        except OSError as exc:
            yield _err(f"mkdir: '{d}': {exc.strerror}")
    return 0


def native_rm(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """rm -rf nativo."""
    if not args:
        yield _err("rm: nenhum caminho especificado")
        return 1
    flags = set()
    paths_raw = []
    for a in args:
        if a.startswith("-"):
            flags.update(a[1:])
        else:
            paths_raw.append(a)
    recursive = "r" in flags or "R" in flags
    force = "f" in flags

    for raw in paths_raw:
        path = _resolve(raw, cwd)
        try:
            if not path.exists() and not path.is_symlink():
                if not force:
                    yield _err(f"rm: '{raw}': Arquivo não encontrado")
                continue
            if path.is_dir() and not path.is_symlink():
                if recursive:
                    shutil.rmtree(path)
                    yield _info(f"✓ Removido: {path}")
                else:
                    yield _err(f"rm: '{raw}': É um diretório (use -r)")
            else:
                path.unlink()
                yield _info(f"✓ Removido: {path}")
        except PermissionError:
            yield _err(f"rm: '{raw}': Permissão negada")
        except OSError as exc:
            yield _err(f"rm: '{raw}': {exc.strerror}")
    return 0


def native_cp(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """cp -r nativo."""
    flags = set()
    paths_raw = [a for a in args if not a.startswith("-")]
    for a in args:
        if a.startswith("-"):
            flags.update(a[1:])
    if len(paths_raw) < 2:
        yield _err("cp: uso: cp [-r] <origem> <destino>")
        return 1
    src = _resolve(paths_raw[0], cwd)
    dst = _resolve(paths_raw[1], cwd)
    recursive = "r" in flags or "R" in flags
    try:
        if src.is_dir():
            if not recursive:
                yield _err(f"cp: '{src}': É um diretório (use -r)")
                return 1
            dest_path = dst / src.name if dst.is_dir() else dst
            shutil.copytree(src, dest_path)
        else:
            dst_path = dst / src.name if dst.is_dir() else dst
            shutil.copy2(src, dst_path)
        yield _info(f"✓ Copiado: {src} → {dst}")
        return 0
    except PermissionError:
        yield _err(f"cp: Permissão negada")
        return 1
    except OSError as exc:
        yield _err(f"cp: {exc.strerror}")
        return 1


def native_mv(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """mv nativo."""
    paths_raw = [a for a in args if not a.startswith("-")]
    if len(paths_raw) < 2:
        yield _err("mv: uso: mv <origem> <destino>")
        return 1
    src = _resolve(paths_raw[0], cwd)
    dst = _resolve(paths_raw[1], cwd)
    try:
        target = dst / src.name if dst.is_dir() else dst
        shutil.move(str(src), str(target))
        yield _info(f"✓ Movido: {src} → {target}")
        return 0
    except PermissionError:
        yield _err("mv: Permissão negada")
        return 1
    except OSError as exc:
        yield _err(f"mv: {exc.strerror}")
        return 1


def native_find(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """find nativo com suporte a -name, -type, -size."""
    # Parsing: find [path] [-name pattern] [-type f|d] [-maxdepth N]
    search_dir = cwd
    name_pattern = None
    type_filter = None
    max_depth = 999
    i = 0
    positional = []
    while i < len(args):
        a = args[i]
        if a == "-name" and i + 1 < len(args):
            name_pattern = args[i + 1]; i += 2
        elif a == "-type" and i + 1 < len(args):
            type_filter = args[i + 1]; i += 2
        elif a == "-maxdepth" and i + 1 < len(args):
            max_depth = int(args[i + 1]); i += 2
        elif not a.startswith("-"):
            positional.append(a); i += 1
        else:
            i += 1

    if positional:
        search_dir = str(_resolve(positional[0], cwd))

    count = 0
    base_depth = search_dir.count(os.sep)
    for root, dirs, files in os.walk(search_dir):
        depth = root.count(os.sep) - base_depth
        if depth > max_depth:
            dirs.clear()
            continue
        entries = []
        if type_filter != "f":
            entries += [(d, True) for d in dirs]
        if type_filter != "d":
            entries += [(f, False) for f in files]
        for name, is_dir in entries:
            if name_pattern and not fnmatch.fnmatch(name, name_pattern):
                continue
            full = os.path.join(root, name)
            yield _out(full)
            count += 1
    if count == 0:
        yield _info("(nenhum resultado)")
    return 0


def native_du(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """du -sh nativo."""
    paths_raw = [a for a in args if not a.startswith("-")]
    target = _resolve(paths_raw[0], cwd) if paths_raw else Path(cwd)
    total = 0
    try:
        if target.is_dir():
            for root, dirs, files in os.walk(target):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        else:
            total = target.stat().st_size
        yield _out(f"{_fmt_size(total)}\t{target}")
        return 0
    except PermissionError:
        yield _err(f"du: '{target}': Permissão negada")
        return 1


def native_df(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """df -h nativo usando shutil.disk_usage."""
    yield _out(f"{'Filesystem':<25} {'Size':>8} {'Used':>8} {'Avail':>8} {'Use%':>5}  Mounted on")
    check_paths = ["/", cwd]
    if os.path.exists("/storage/emulated/0"):
        check_paths.append("/storage/emulated/0")
    seen = set()
    for p in check_paths:
        try:
            usage = shutil.disk_usage(p)
            if usage.total in seen:
                continue
            seen.add(usage.total)
            pct = int(usage.used / usage.total * 100) if usage.total else 0
            yield _out(
                f"{p:<25} {_fmt_size(usage.total):>8} "
                f"{_fmt_size(usage.used):>8} {_fmt_size(usage.free):>8} "
                f"{pct:>4}%  {p}"
            )
        except OSError:
            pass
    return 0


def native_which(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """which nativo."""
    if not args:
        yield _err("which: nenhum comando especificado")
        return 1
    for cmd in args:
        found = shutil.which(cmd)
        if found:
            yield _out(found)
        else:
            yield _err(f"{cmd}: não encontrado")
    return 0 if all(shutil.which(a) for a in args) else 1


def native_touch(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """touch nativo."""
    for fname in args:
        path = _resolve(fname, cwd)
        try:
            path.touch(exist_ok=True)
            yield _info(f"✓ {path}")
        except PermissionError:
            yield _err(f"touch: '{fname}': Permissão negada")
    return 0


def native_wc(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """wc -l nativo."""
    flags = set()
    files = []
    for a in args:
        if a.startswith("-"):
            flags.update(a[1:])
        else:
            files.append(a)
    count_lines = "l" in flags or not flags
    count_words = "w" in flags
    count_bytes = "c" in flags

    for fname in files:
        path = _resolve(fname, cwd)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.count("\n")
            words = len(text.split())
            bts = path.stat().st_size
            parts = []
            if count_lines:
                parts.append(f"{lines:8}")
            if count_words:
                parts.append(f"{words:8}")
            if count_bytes:
                parts.append(f"{bts:8}")
            yield _out("  ".join(parts) + f"  {fname}")
        except OSError as exc:
            yield _err(f"wc: '{fname}': {exc.strerror}")
    return 0


def native_grep(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """grep -rn nativo."""
    flags = set()
    positional = []
    i = 0
    while i < len(args):
        if args[i].startswith("-"):
            flags.update(args[i][1:])
            i += 1
        else:
            positional.append(args[i])
            i += 1

    if not positional:
        yield _err("grep: padrão não especificado")
        return 1

    pattern = positional[0]
    search_paths = [_resolve(p, cwd) for p in positional[1:]] if len(positional) > 1 else [Path(cwd)]
    recursive = "r" in flags or "R" in flags
    show_lines = "n" in flags
    ignore_case = "i" in flags
    count_only = "c" in flags

    re_flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as exc:
        yield _err(f"grep: padrão inválido: {exc}")
        return 1

    match_count = 0

    def search_file(fpath: Path):
        nonlocal match_count
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    match_count += 1
                    if not count_only:
                        prefix = f"{fpath}:" if len(search_paths) > 1 or recursive else ""
                        lnum = f"{lineno}:" if show_lines else ""
                        highlighted = compiled.sub(
                            lambda m: f"[{m.group()}]", line
                        )
                        yield _out(f"{prefix}{lnum}{highlighted}")
        except (PermissionError, OSError):
            pass

    for sp in search_paths:
        if sp.is_dir() and recursive:
            for root, _, files in os.walk(sp):
                for f in files:
                    yield from search_file(Path(root) / f)
        elif sp.is_file():
            yield from search_file(sp)

    if count_only:
        yield _out(str(match_count))
    elif match_count == 0:
        yield _info("(nenhum resultado)")
    return 0 if match_count > 0 else 1


# ─────────────────────────────────────────────────────────────────
#  COMPRESSÃO — tar, gzip
# ─────────────────────────────────────────────────────────────────

def native_tar_create(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """tar -czf <output.tar.gz> <path> nativo."""
    # args já chegam como: -czf arquivo.tar.gz fonte
    paths_raw = [a for a in args if not a.startswith("-")]
    if len(paths_raw) < 2:
        yield _err("tar: uso: compress <arquivo.tar.gz> <origem>")
        return 1
    out_path = _resolve(paths_raw[0], cwd)
    src_path = _resolve(paths_raw[1], cwd)
    try:
        mode = "w:gz" if str(out_path).endswith((".tar.gz", ".tgz")) else "w"
        with tarfile.open(out_path, mode) as tf:
            tf.add(src_path, arcname=src_path.name)
        size = out_path.stat().st_size
        yield _info(f"✓ Comprimido: {out_path} ({_fmt_size(size)})")
        return 0
    except PermissionError:
        yield _err("tar: Permissão negada")
        return 1
    except OSError as exc:
        yield _err(f"tar: {exc.strerror}")
        return 1


def native_tar_extract(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """tar -xzf <arquivo> nativo."""
    paths_raw = [a for a in args if not a.startswith("-")]
    if not paths_raw:
        yield _err("tar: arquivo não especificado")
        return 1
    src = _resolve(paths_raw[0], cwd)
    dest = _resolve(paths_raw[1], cwd) if len(paths_raw) > 1 else Path(cwd)
    try:
        with tarfile.open(src, "r:*") as tf:
            members = tf.getmembers()
            tf.extractall(dest)
        yield _info(f"✓ Extraído: {len(members)} arquivo(s) em {dest}")
        return 0
    except tarfile.TarError as exc:
        yield _err(f"tar: {exc}")
        return 1
    except OSError as exc:
        yield _err(f"tar: {exc.strerror}")
        return 1


# ─────────────────────────────────────────────────────────────────
#  REDE — ping, wget, curl, ifconfig, nslookup
# ─────────────────────────────────────────────────────────────────

def native_ping(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """
    Ping nativo usando socket TCP na porta 80.
    ICMP real exige root no Android; TCP é uma boa aproximação.
    """
    count = 4
    positional = []
    i = 0
    while i < len(args):
        if args[i] in ("-c", "-n") and i + 1 < len(args):
            try:
                count = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif not args[i].startswith("-"):
            positional.append(args[i])
            i += 1
        else:
            i += 1

    if not positional:
        yield _err("ping: host não especificado")
        return 1

    host = positional[0]
    yield _info(f"PING {host} (TCP/80) — {count} tentativas")

    try:
        resolved_ip = socket.gethostbyname(host)
        yield _out(f"  → Resolvido: {resolved_ip}")
    except socket.gaierror as exc:
        yield _err(f"ping: {host}: {exc.strerror or str(exc)}")
        return 1

    ok = 0
    for seq in range(1, count + 1):
        t0 = time.monotonic()
        try:
            with socket.create_connection((resolved_ip, 80), timeout=3):
                rtt = (time.monotonic() - t0) * 1000
                yield _out(f"  seq={seq} ip={resolved_ip} time={rtt:.1f}ms")
                ok += 1
        except OSError:
            # Porta 80 fechada não significa host morto; tenta porta 443
            try:
                with socket.create_connection((resolved_ip, 443), timeout=3):
                    rtt = (time.monotonic() - t0) * 1000
                    yield _out(f"  seq={seq} ip={resolved_ip} port=443 time={rtt:.1f}ms")
                    ok += 1
            except OSError:
                yield _out(f"  seq={seq} ip={resolved_ip} unreachable")
        time.sleep(0.5)

    loss = int((count - ok) / count * 100)
    yield _out(f"\n--- {host} ping stats ---")
    yield _out(f"  {count} enviados, {ok} OK, {loss}% perda")
    return 0 if ok > 0 else 1


def native_wget(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """wget nativo via urllib com barra de progresso."""
    positional = [a for a in args if not a.startswith("-")]
    output_file = None
    for i, a in enumerate(args):
        if a in ("-O", "--output-document") and i + 1 < len(args):
            output_file = args[i + 1]

    if not positional:
        yield _err("wget: URL não especificada")
        return 1

    url = positional[0]
    filename = output_file or Path(urllib.parse.urlparse(url).path).name or "index.html"
    dest = _resolve(filename, cwd)

    yield _info(f"↓ {url}")
    yield _out(f"  → {dest}")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Andrux/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                        yield _out(f"\r  [{bar}] {pct:5.1f}% {_fmt_size(downloaded)}/{_fmt_size(total)}")
                    else:
                        yield _out(f"\r  {_fmt_size(downloaded)} baixados...")
        yield _info(f"\n✓ Salvo: {dest} ({_fmt_size(dest.stat().st_size)})")
        return 0
    except urllib.error.HTTPError as exc:
        yield _err(f"wget: HTTP {exc.code}: {exc.reason}")
        return 1
    except urllib.error.URLError as exc:
        yield _err(f"wget: {exc.reason}")
        return 1
    except OSError as exc:
        yield _err(f"wget: {exc.strerror}")
        return 1


def native_curl(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """curl -L nativo via urllib com suporte a headers e output."""
    url = None
    output_file = None
    headers_extra = {}
    show_headers = False
    silent = "-s" in args or "--silent" in args

    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--output") and i + 1 < len(args):
            output_file = args[i + 1]; i += 2
        elif a in ("-H", "--header") and i + 1 < len(args):
            k, _, v = args[i + 1].partition(":")
            headers_extra[k.strip()] = v.strip(); i += 2
        elif a in ("-I", "--head"):
            show_headers = True; i += 1
        elif not a.startswith("-"):
            url = a; i += 1
        else:
            i += 1

    if not url:
        yield _err("curl: URL não especificada")
        return 1

    try:
        hdrs = {"User-Agent": "Andrux/2.0 (curl-native)", **headers_extra}
        req = urllib.request.Request(url, headers=hdrs)

        if show_headers:
            with urllib.request.urlopen(req, timeout=15) as resp:
                yield _out(f"HTTP/1.1 {resp.status} {resp.reason}")
                for k, v in resp.headers.items():
                    yield _out(f"{k}: {v}")
            return 0

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()

        if output_file:
            dest = _resolve(output_file, cwd)
            dest.write_bytes(data)
            if not silent:
                yield _info(f"✓ Salvo: {dest} ({_fmt_size(len(data))})")
        else:
            try:
                text = data.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    yield _out(line)
            except Exception:
                yield _out(f"<dados binários: {_fmt_size(len(data))}>")
        return 0

    except urllib.error.HTTPError as exc:
        yield _err(f"curl: HTTP {exc.code}: {exc.reason}")
        return 1
    except urllib.error.URLError as exc:
        yield _err(f"curl: {exc.reason}")
        return 1


def native_myip(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """Retorna IP externo via API pública."""
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    for svc in services:
        try:
            req = urllib.request.Request(svc, headers={"User-Agent": "Andrux/2.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ip = resp.read().decode().strip()
            yield _out(ip)
            return 0
        except Exception:
            continue
    yield _err("myip: não foi possível obter o IP externo")
    return 1


def native_dns(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """nslookup nativo via socket.getaddrinfo."""
    if not args:
        yield _err("dns: host não especificado")
        return 1
    host = args[0]
    try:
        results = socket.getaddrinfo(host, None)
        seen = set()
        yield _out(f"Server:\t\t(sistema)")
        yield _out(f"Name:\t\t{host}")
        for family, _, _, _, addr in results:
            ip = addr[0]
            if ip in seen:
                continue
            seen.add(ip)
            fam_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
            yield _out(f"Address:\t{ip} ({fam_name})")
        return 0
    except socket.gaierror as exc:
        yield _err(f"dns: '{host}': {exc.strerror or str(exc)}")
        return 1


# ─────────────────────────────────────────────────────────────────
#  SISTEMA — ps, free, uname, env, uptime
# ─────────────────────────────────────────────────────────────────

def native_ps(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """ps nativo via /proc (Android/Linux) ou fallback."""
    yield _out(f"{'PID':>7}  {'STAT':<5}  {'CMD'}")
    yield _out("-" * 50)

    # Tenta /proc (Android e Linux)
    proc_dir = Path("/proc")
    if proc_dir.exists():
        count = 0
        for p in sorted(proc_dir.iterdir()):
            if not p.name.isdigit():
                continue
            pid = p.name
            try:
                status_file = p / "status"
                cmdline_file = p / "cmdline"
                state = "?"
                name = "?"
                if status_file.exists():
                    for line in status_file.read_text().splitlines():
                        if line.startswith("Name:"):
                            name = line.split(":", 1)[1].strip()
                        if line.startswith("State:"):
                            state = line.split(":", 1)[1].strip()[:1]
                if cmdline_file.exists():
                    cmd = cmdline_file.read_bytes().replace(b"\x00", b" ").decode(errors="replace").strip()
                    if cmd:
                        name = cmd[:60]
                yield _out(f"{pid:>7}  {state:<5}  {name}")
                count += 1
            except (PermissionError, OSError):
                pass
        yield _info(f"\n{count} processos listados")
    else:
        yield _warn("ps: /proc não disponível nesta plataforma")
        # Fallback: apenas o processo atual
        yield _out(f"{os.getpid():>7}  R      python3 (processo atual)")
    return 0


def native_free(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """free -h nativo via /proc/meminfo."""
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        data = {}
        for line in meminfo.read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                nums = re.findall(r"\d+", v)
                if nums:
                    data[k.strip()] = int(nums[0]) * 1024  # kB → bytes

        total = data.get("MemTotal", 0)
        free_ = data.get("MemFree", 0)
        avail = data.get("MemAvailable", free_)
        bufs  = data.get("Buffers", 0)
        cache = data.get("Cached", 0)
        used  = total - avail
        swap_total = data.get("SwapTotal", 0)
        swap_free  = data.get("SwapFree", 0)
        swap_used  = swap_total - swap_free

        yield _out(f"{'':16} {'total':>9} {'used':>9} {'free':>9} {'available':>9}")
        yield _out(
            f"{'Mem:':16} {_fmt_size(total):>9} {_fmt_size(used):>9} "
            f"{_fmt_size(free_):>9} {_fmt_size(avail):>9}"
        )
        if swap_total:
            yield _out(
                f"{'Swap:':16} {_fmt_size(swap_total):>9} {_fmt_size(swap_used):>9} "
                f"{_fmt_size(swap_free):>9}"
            )
    else:
        # macOS / Windows fallback
        try:
            import ctypes
            if platform.system() == "Windows":
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                m = MEMORYSTATUSEX()
                m.dwLength = ctypes.sizeof(m)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
                yield _out(f"Total: {_fmt_size(m.ullTotalPhys)}")
                yield _out(f"Livre: {_fmt_size(m.ullAvailPhys)}")
                yield _out(f"Uso:   {m.dwMemoryLoad}%")
        except Exception:
            yield _warn("free: informações de memória não disponíveis")
    return 0


def native_uname(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """uname -a nativo."""
    show_all = "-a" in args
    p = platform.uname()
    if show_all:
        yield _out(f"{p.system} {p.node} {p.release} {p.version} {p.machine} {p.processor or p.machine}")
    else:
        parts = []
        if "-s" in args or not args:
            parts.append(p.system)
        if "-n" in args:
            parts.append(p.node)
        if "-r" in args:
            parts.append(p.release)
        if "-m" in args:
            parts.append(p.machine)
        yield _out(" ".join(parts) if parts else p.system)
    return 0


def native_env_list(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """env: lista variáveis de ambiente."""
    for k, v in sorted(env.items()):
        yield _out(f"{k}={v}")
    return 0


def native_uptime(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """uptime nativo."""
    uptime_file = Path("/proc/uptime")
    if uptime_file.exists():
        secs = float(uptime_file.read_text().split()[0])
        d, rem = divmod(int(secs), 86400)
        h, rem = divmod(rem, 3600)
        m, s   = divmod(rem, 60)
        yield _out(f"up {d}d {h:02}:{m:02}:{s:02}")
    else:
        yield _out(f"uptime: plataforma não suporta /proc/uptime")
    return 0


# ─────────────────────────────────────────────────────────────────
#  PYTHON / PIP — nativo via importlib + pip module
# ─────────────────────────────────────────────────────────────────

def native_pip(subcmd: str, args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """
    pip nativo: usa o próprio módulo pip do Python.
    No Android (APK), o pip está embutido no Python empacotado.
    """
    import io

    # Tenta importar pip como módulo
    try:
        import pip._internal.cli.main as pip_main
        _has_pip_module = True
    except ImportError:
        _has_pip_module = False

    full_args = [subcmd] + args

    if _has_pip_module:
        # Captura stdout/stderr do pip
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = buf_out = io.StringIO()
        sys.stderr = buf_err = io.StringIO()
        try:
            rc = pip_main.main(full_args)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 0
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        for line in buf_out.getvalue().splitlines():
            if line.strip():
                yield _out(line)
        for line in buf_err.getvalue().splitlines():
            if line.strip():
                yield _err(line)
        return rc if isinstance(rc, int) else 0

    else:
        # Fallback: subprocess python -m pip (funciona se Python estiver no PATH)
        yield _warn("pip: módulo nativo não encontrado, tentando python -m pip...")
        if _HAS_SUBPROCESS:
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip"] + full_args,
                    capture_output=True, text=True, cwd=cwd
                )
                for line in proc.stdout.splitlines():
                    yield _out(line)
                for line in proc.stderr.splitlines():
                    yield _err(line)
                return proc.returncode
            except Exception as exc:
                yield _err(f"pip: {exc}")
                return 1
        else:
            yield _err("pip: não disponível neste ambiente")
            return 1


def native_pip_list(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """pip list nativo via importlib.metadata."""
    pkgs = []
    if importlib_metadata:
        try:
            pkgs = sorted(importlib_metadata.packages_distributions().keys())
        except Exception:
            pass
        if not pkgs:
            try:
                pkgs = sorted(d.metadata["Name"] for d in importlib_metadata.distributions())
            except Exception:
                pass

    if pkgs:
        yield _out(f"{'Package':<30} {'Version'}")
        yield _out("-" * 45)
        for pkg in pkgs:
            try:
                ver = importlib_metadata.version(pkg)
            except Exception:
                ver = "?"
            yield _out(f"{pkg:<30} {ver}")
        yield _info(f"\n{len(pkgs)} pacotes instalados")
    else:
        yield _warn("pip list: não foi possível listar pacotes")
    return 0


def native_pip_show(pkg: str, args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """pip show nativo via importlib.metadata."""
    if not importlib_metadata:
        yield _err("pip show: importlib.metadata não disponível")
        return 1
    try:
        meta = importlib_metadata.metadata(pkg)
        for key in ("Name", "Version", "Summary", "Author", "License", "Home-page", "Location"):
            val = meta.get(key)
            if val:
                yield _out(f"{key}: {val}")
        return 0
    except importlib_metadata.PackageNotFoundError:
        yield _err(f"pip show: '{pkg}' não encontrado")
        return 1


# ─────────────────────────────────────────────────────────────────
#  GIT — clone, status, add, commit, push, pull, log, branch, diff
# ─────────────────────────────────────────────────────────────────

def _find_git_root(cwd: str) -> Optional[Path]:
    """Sobe no diretório até encontrar .git"""
    p = Path(cwd)
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


def _git_via_subprocess(git_args: list[str], cwd: str) -> CmdGenerator:
    """Tenta executar git real via subprocess como fallback."""
    git_bin = shutil.which("git")
    if not git_bin:
        yield _err("git: binário não encontrado no PATH")
        yield _warn("  Instale git ou use os comandos nativos do Andrux.")
        return 127

    if not _HAS_SUBPROCESS:
        yield _err("git: subprocess não disponível neste ambiente")
        return 1

    try:
        proc = subprocess.Popen(
            [git_bin] + git_args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True
        )
        for line in proc.stdout:
            yield _out(line.rstrip())
        for line in proc.stderr:
            yield _err(line.rstrip())
        return proc.wait()
    except Exception as exc:
        yield _err(f"git: {exc}")
        return 1


def native_git_status(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git status nativo: lê .git/index e HEAD."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git (ou nenhum diretório pai)")
        return 128

    git_dir = root / ".git"

    # HEAD
    head_file = git_dir / "HEAD"
    branch = "unknown"
    if head_file.exists():
        head = head_file.read_text().strip()
        if head.startswith("ref: refs/heads/"):
            branch = head[len("ref: refs/heads/"):]
        else:
            branch = head[:8] + "... (HEAD detached)"

    yield _out(f"On branch {branch}")

    # Arquivos não rastreados
    try:
        gitignore_patterns = []
        gi = root / ".gitignore"
        if gi.exists():
            gitignore_patterns = [
                line.strip() for line in gi.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]

        untracked = []
        for p in sorted(root.rglob("*")):
            if ".git" in p.parts:
                continue
            if p.is_file():
                rel = str(p.relative_to(root))
                ignored = any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat)
                              for pat in gitignore_patterns)
                if not ignored:
                    untracked.append(rel)

        if untracked:
            yield _out("\nUntracked files:")
            yield _out("  (use \"git add <file>...\")")
            for f in untracked[:20]:
                yield _out(f"\t{f}")
            if len(untracked) > 20:
                yield _out(f"\t... e mais {len(untracked) - 20} arquivo(s)")
        else:
            yield _out("\nnothing to commit, working tree clean")
    except Exception:
        pass

    return 0


def native_git_log(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git log nativo: lê objetos do .git."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128

    git_dir = root / ".git"
    head_file = git_dir / "HEAD"
    if not head_file.exists():
        yield _warn("Repositório vazio (sem commits)")
        return 0

    head = head_file.read_text().strip()
    if head.startswith("ref:"):
        ref_path = git_dir / head[5:]
        if not ref_path.exists():
            yield _warn("Repositório vazio (sem commits)")
            return 0
        commit_hash = ref_path.read_text().strip()
    else:
        commit_hash = head

    count = 0
    limit = 20
    for a in args:
        if a.startswith("-") and a[1:].isdigit():
            limit = int(a[1:])

    while commit_hash and count < limit:
        obj_path = git_dir / "objects" / commit_hash[:2] / commit_hash[2:]
        if not obj_path.exists():
            break
        try:
            import zlib
            raw = zlib.decompress(obj_path.read_bytes())
            header, _, body = raw.partition(b"\x00")
            fields = {}
            msg_lines = []
            in_msg = False
            for line in body.decode(errors="replace").splitlines():
                if in_msg:
                    msg_lines.append(line)
                elif line == "":
                    in_msg = True
                else:
                    k, _, v = line.partition(" ")
                    fields[k] = v
            author = fields.get("author", "unknown")
            date_ts = int(author.split()[-2]) if author.split() else 0
            author_name = " ".join(author.split()[:-2]) if author else "?"
            date_str = datetime.fromtimestamp(date_ts).strftime("%Y-%m-%d %H:%M") if date_ts else "?"
            msg = " ".join(msg_lines).strip()[:72]
            yield _out(f"\033[33m{commit_hash[:8]}\033[0m {msg}")
            yield _out(f"  Author: {author_name}")
            yield _out(f"  Date:   {date_str}")
            yield _out("")
            commit_hash = fields.get("parent", "")
            count += 1
        except Exception:
            # Git pack files ou zlib falhou — usa git real
            yield from _git_via_subprocess(["log", "--oneline", f"-{limit}"], cwd)
            return 0

    if count == 0:
        yield _warn("git log: sem commits ou formato de objeto não suportado")
        yield from _git_via_subprocess(["log", "--oneline", f"-{limit}"], cwd)
    return 0


def native_git_branch(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git branch nativo."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128

    git_dir = root / ".git"
    heads_dir = git_dir / "refs" / "heads"

    # Branch atual
    current = ""
    head_file = git_dir / "HEAD"
    if head_file.exists():
        head = head_file.read_text().strip()
        if head.startswith("ref: refs/heads/"):
            current = head[len("ref: refs/heads/"):]

    if heads_dir.exists():
        branches = sorted(p.name for p in heads_dir.iterdir() if p.is_file())
        for b in branches:
            prefix = "* " if b == current else "  "
            yield _out(f"{prefix}{b}")
    else:
        yield _out(f"* {current or 'main'} (branch inicial)")

    # Branches remotas
    remotes_dir = git_dir / "refs" / "remotes"
    if remotes_dir.exists() and ("-a" in args or "--all" in args):
        for remote in sorted(remotes_dir.iterdir()):
            for branch_file in sorted(remote.iterdir()):
                yield _out(f"  remotes/{remote.name}/{branch_file.name}")
    return 0


def native_git_diff(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git diff nativo: compara arquivos com index."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128
    # diff real requer parsing do index — delega para git real se disponível
    yield from _git_via_subprocess(["diff"] + args, cwd)
    return 0


def native_git_add(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git add via git real ou confirmação visual."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128
    result = list(_git_via_subprocess(["add"] + (args or ["."]), cwd))
    if result:
        yield from result
    else:
        target = " ".join(args) if args else "."
        yield _info(f"✓ git add {target}")
    return 0


def native_git_commit(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git commit via git real."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128
    yield from _git_via_subprocess(["commit"] + args, cwd)
    return 0


def native_git_push(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git push via git real."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128
    yield from _git_via_subprocess(["push"] + args, cwd)
    return 0


def native_git_pull(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git pull via git real."""
    root = _find_git_root(cwd)
    if not root:
        yield _err("fatal: não é um repositório git")
        return 128
    yield from _git_via_subprocess(["pull"] + args, cwd)
    return 0


def native_git_clone(args: list[str], cwd: str, env: dict) -> CmdGenerator:
    """git clone via urllib (download do zip do GitHub/GitLab) ou git real."""
    # Primeiro tenta git real
    git_bin = shutil.which("git")
    if git_bin and _HAS_SUBPROCESS:
        yield from _git_via_subprocess(["clone"] + args, cwd)
        return 0

    # Fallback nativo: GitHub/GitLab suportam download de zip
    if not args:
        yield _err("git clone: URL não especificada")
        return 1

    url = args[0]
    dest_name = Path(urllib.parse.urlparse(url).path).stem
    if dest_name.endswith(".git"):
        dest_name = dest_name[:-4]
    dest = Path(cwd) / (args[1] if len(args) > 1 else dest_name)

    # Tenta converter URL git para URL de arquivo zip
    zip_url = None
    gh_match = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    gl_match = re.match(r"https?://gitlab\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)

    if gh_match:
        user, repo = gh_match.groups()
        zip_url = f"https://github.com/{user}/{repo}/archive/refs/heads/main.zip"
    elif gl_match:
        user, repo = gl_match.groups()
        zip_url = f"https://gitlab.com/{user}/{repo}/-/archive/main/{repo}-main.zip"

    if not zip_url:
        yield _err(f"git clone: URL não suportada sem git instalado: {url}")
        yield _warn("  Suportado: github.com e gitlab.com")
        return 1

    yield _info(f"Clonando {url} via download ZIP...")
    try:
        req = urllib.request.Request(zip_url, headers={"User-Agent": "Andrux/2.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            zip_data = resp.read()

        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            members = zf.namelist()
            prefix = members[0] if members else ""
            dest.mkdir(parents=True, exist_ok=True)
            for member in members:
                target = dest / member[len(prefix):]
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))

        yield _info(f"✓ Clonado em: {dest}")
        yield _warn("  Nota: clone via ZIP não inclui histórico git completo")
        return 0
    except Exception as exc:
        yield _err(f"git clone: {exc}")
        return 1


# ─────────────────────────────────────────────────────────────────
#  DISPATCHER PRINCIPAL
# ─────────────────────────────────────────────────────────────────

class NativeEngine:
    """
    Dispatcher central do motor nativo.
    
    Recebe um comando traduzido (ex: "ls -la /tmp") e decide:
    1. Se existe implementação nativa Python → executa ela
    2. Se o binário existe no PATH → usa subprocess (fallback)
    3. Se não há nada → retorna erro claro com sugestão
    """

    # Mapeamento: primeiro token do comando → handler nativo
    NATIVE_MAP: dict[str, Callable] = {
        "ls":       native_ls,
        "cat":      native_cat,
        "mkdir":    native_mkdir,
        "rm":       native_rm,
        "cp":       native_cp,
        "mv":       native_mv,
        "find":     native_find,
        "du":       native_du,
        "df":       native_df,
        "which":    native_which,
        "touch":    native_touch,
        "wc":       native_wc,
        "grep":     native_grep,
        "ping":     native_ping,
        "wget":     native_wget,
        "curl":     native_curl,
        "ps":       native_ps,
        "free":     native_free,
        "uname":    native_uname,
        "env":      native_env_list,
        "uptime":   native_uptime,
        "nslookup": native_dns,
    }

    # Comandos que têm lógica especial de dispatch
    SPECIAL = {"pip", "git", "python3", "python"}

    def __init__(self, shell_cwd_getter: Callable[[], str],
                 shell_env_getter: Callable[[], dict]):
        self._get_cwd = shell_cwd_getter
        self._get_env = shell_env_getter

    def can_handle(self, translated_command: str) -> bool:
        """Retorna True se o NativeEngine tem handler para este comando."""
        parts = translated_command.strip().split()
        if not parts:
            return False
        cmd = parts[0].lower()
        return cmd in self.NATIVE_MAP or cmd in self.SPECIAL

    def execute(
        self,
        translated_command: str,
        stdout_cb: Callable[[str], None],
        stderr_cb: Callable[[str], None],
        done_cb: Callable[[int], None],
    ):
        """Executa via motor nativo em thread separada."""
        def run():
            cwd = self._get_cwd()
            env = self._get_env()
            parts = translated_command.strip().split(None)
            if not parts:
                done_cb(0)
                return
            cmd = parts[0].lower()
            args = parts[1:]
            rc = 0
            try:
                gen = self._dispatch(cmd, args, cwd, env)
                for text, category in gen:
                    if category in ("out", "info"):
                        stdout_cb(text)
                    else:
                        stderr_cb(text)
            except StopIteration as e:
                rc = e.value if isinstance(e.value, int) else 0
            except Exception as exc:
                stderr_cb(f"native engine error: {exc}")
                rc = 1
            done_cb(rc)

        import threading
        threading.Thread(target=run, daemon=True).start()

    def _dispatch(self, cmd: str, args: list[str], cwd: str, env: dict) -> CmdGenerator:
        """Despacha para o handler correto, consumindo o generator inteiro."""

        # ── pip ────────────────────────────────────────────────────────
        if cmd == "pip":
            if not args:
                yield _err("pip: subcomando não especificado")
                return 1
            subcmd = args[0]
            sub_args = args[1:]
            if subcmd == "list":
                yield from native_pip_list(sub_args, cwd, env)
            elif subcmd == "show" and sub_args:
                yield from native_pip_show(sub_args[0], sub_args[1:], cwd, env)
            else:
                yield from native_pip(subcmd, sub_args, cwd, env)
            return

        # ── git ────────────────────────────────────────────────────────
        if cmd == "git":
            if not args:
                yield _err("git: subcomando não especificado")
                return 1
            sub = args[0]
            sub_args = args[1:]
            git_dispatch = {
                "status":  native_git_status,
                "log":     native_git_log,
                "branch":  native_git_branch,
                "diff":    native_git_diff,
                "add":     native_git_add,
                "commit":  native_git_commit,
                "push":    native_git_push,
                "pull":    native_git_pull,
                "clone":   native_git_clone,
            }
            handler = git_dispatch.get(sub)
            if handler:
                yield from handler(sub_args, cwd, env)
            else:
                yield from _git_via_subprocess([sub] + sub_args, cwd)
            return

        # ── python3 / python ──────────────────────────────────────────
        if cmd in ("python3", "python"):
            if not args:
                yield _err("python3: nenhum script ou código especificado")
                return 1
            if args[0] == "-c" and len(args) > 1:
                # Executa código inline capturando output
                code = " ".join(args[1:])
                import io, contextlib
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        exec(compile(code, "<andrux-inline>", "exec"), {})
                    for line in buf.getvalue().splitlines():
                        yield _out(line)
                except Exception as exc:
                    yield _err(f"python3: {type(exc).__name__}: {exc}")
                    return 1
            else:
                # Executa arquivo .py
                script = _resolve(args[0], cwd)
                if not script.exists():
                    yield _err(f"python3: '{args[0]}': arquivo não encontrado")
                    return 1
                import io, contextlib
                buf = io.StringIO()
                old_argv = sys.argv[:]
                sys.argv = [str(script)] + args[1:]
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        code_text = script.read_text(encoding="utf-8")
                        exec(compile(code_text, str(script), "exec"),
                             {"__file__": str(script), "__name__": "__main__"})
                    for line in buf.getvalue().splitlines():
                        yield _out(line)
                except SystemExit as e:
                    if e.code:
                        yield _err(f"[exit {e.code}]")
                except Exception as exc:
                    yield _err(f"python3: {type(exc).__name__}: {exc}")
                    return 1
                finally:
                    sys.argv = old_argv
            return

        # ── handlers diretos ──────────────────────────────────────────
        handler = self.NATIVE_MAP.get(cmd)
        if handler:
            yield from handler(args, cwd, env)
            return

        # ── fallback: binário não encontrado ──────────────────────────
        yield _err(f"andrux: '{cmd}': comando não encontrado")
        yield _warn(f"  Este comando requer binário externo não disponível no APK.")
        yield _warn(f"  Sugestões:")
        yield _warn(f"    • Verifique se o Andrux tem uma versão nativa (help)")
        yield _warn(f"    • Em Termux: get install {cmd}")
        return 1


# ─────────────────────────────────────────────
#  UTILS
# ─────────────────────────────────────────────

def ensure_andrux_dirs():
    """Garante que todos os diretórios do Andrux existam."""
    ANDRUX_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    """Carrega JSON com fallback seguro."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return default


def save_json(path: Path, data):
    """Salva JSON de forma segura (write-then-rename)."""
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except IOError as e:
        print(f"[ANDRUX] Falha ao salvar {path}: {e}")


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def datestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────
#  CONFIG MANAGER
# ─────────────────────────────────────────────

class AndruxConfig:
    def __init__(self):
        ensure_andrux_dirs()
        self._data = {**DEFAULT_CONFIG, **load_json(CONFIG_FILE, {})}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        save_json(CONFIG_FILE, self._data)

    def all(self):
        return dict(self._data)


# ─────────────────────────────────────────────
#  HISTORY MANAGER
# ─────────────────────────────────────────────

class AndruxHistory:
    def __init__(self, config: AndruxConfig):
        self.config = config
        self.max_size = config.get("max_history", 500)
        self._history: list[dict] = load_json(HISTORY_FILE, [])
        self._pointer = len(self._history)

    def add(self, command: str):
        if not command.strip():
            return
        # Evita duplicatas consecutivas
        if self._history and self._history[-1]["cmd"] == command:
            self._pointer = len(self._history)
            return
        entry = {"cmd": command, "ts": datestamp()}
        self._history.append(entry)
        if len(self._history) > self.max_size:
            self._history = self._history[-self.max_size:]
        self._pointer = len(self._history)
        save_json(HISTORY_FILE, self._history)

    def prev(self) -> Optional[str]:
        if not self._history:
            return None
        self._pointer = max(0, self._pointer - 1)
        return self._history[self._pointer]["cmd"]

    def next(self) -> Optional[str]:
        if self._pointer >= len(self._history) - 1:
            self._pointer = len(self._history)
            return ""
        self._pointer += 1
        return self._history[self._pointer]["cmd"]

    def search(self, query: str) -> list[str]:
        return [
            e["cmd"] for e in reversed(self._history)
            if query.lower() in e["cmd"].lower()
        ][:20]

    def all(self) -> list[dict]:
        return list(self._history)

    def clear(self):
        self._history = []
        self._pointer = 0
        save_json(HISTORY_FILE, [])


# ─────────────────────────────────────────────
#  ALIAS DATABASE
# ─────────────────────────────────────────────

class AndruxAliasDB:
    def __init__(self):
        self._aliases: dict[str, str] = load_json(ALIAS_FILE, {})

    def set(self, name: str, command: str):
        self._aliases[name] = command
        save_json(ALIAS_FILE, self._aliases)

    def get(self, name: str) -> Optional[str]:
        return self._aliases.get(name)

    def delete(self, name: str) -> bool:
        if name in self._aliases:
            del self._aliases[name]
            save_json(ALIAS_FILE, self._aliases)
            return True
        return False

    def all(self) -> dict:
        return dict(self._aliases)

    def resolve(self, command: str) -> str:
        """Resolve alias no início do comando."""
        parts = command.split(None, 1)
        if not parts:
            return command
        alias_val = self._aliases.get(parts[0])
        if alias_val:
            return f"{alias_val} {parts[1]}" if len(parts) > 1 else alias_val
        return command


# ─────────────────────────────────────────────
#  SYNTAX TRANSLATION KERNEL
# ─────────────────────────────────────────────

class AndruxKernel:
    """
    Motor de tradução de sintaxe do Andrux.
    
    Intercepta comandos especiais e os traduz para comandos reais
    antes da execução. Suporta regras fixas, regex e plugins.
    """

    # Regras de tradução: (pattern_regex, replacement_template, description)
    TRANSLATION_RULES = [
        # ── Gestão de Pacotes ──────────────────────────────────────────
        (r"^take\s+install\s+(.+)$",       r"pip install \1",              "pip install via 'take'"),
        (r"^take\s+remove\s+(.+)$",        r"pip uninstall -y \1",         "pip uninstall via 'take'"),
        (r"^take\s+update\s+(.+)$",        r"pip install --upgrade \1",    "pip upgrade via 'take'"),
        (r"^take\s+list$",                 r"pip list",                    "pip list via 'take'"),
        (r"^take\s+search\s+(.+)$",        r"pip search \1",               "pip search via 'take'"),
        (r"^take\s+info\s+(.+)$",          r"pip show \1",                 "pip show via 'take'"),
        (r"^take\s+freeze$",               r"pip freeze",                  "pip freeze via 'take'"),

        (r"^get\s+install\s+(.+)$",        r"pkg install \1",              "pkg install via 'get'"),
        (r"^get\s+remove\s+(.+)$",         r"pkg remove \1",               "pkg remove via 'get'"),
        (r"^get\s+update$",                r"pkg update",                  "pkg update via 'get'"),
        (r"^get\s+upgrade$",               r"pkg upgrade",                 "pkg upgrade via 'get'"),
        (r"^get\s+list$",                  r"pkg list-installed",          "pkg list via 'get'"),
        (r"^get\s+search\s+(.+)$",         r"pkg search \1",               "pkg search via 'get'"),
        (r"^get\s+info\s+(.+)$",           r"pkg show \1",                 "pkg show via 'get'"),

        # ── Navegação de Arquivos ──────────────────────────────────────
        (r"^fis\s+(.+)$",                  r"cd \1",                       "cd via 'fis'"),
        (r"^look$",                        r"ls -la",                      "ls -la via 'look'"),
        (r"^look\s+(.+)$",                 r"ls -la \1",                   "ls -la [path] via 'look'"),
        (r"^peek\s+(.+)$",                 r"cat \1",                      "cat via 'peek'"),
        (r"^mkplace\s+(.+)$",              r"mkdir -p \1",                 "mkdir -p via 'mkplace'"),
        (r"^rmplace\s+(.+)$",              r"rm -rf \1",                   "rm -rf via 'rmplace'"),
        (r"^copy\s+(.+)\s+to\s+(.+)$",     r"cp -r \1 \2",                 "cp via 'copy ... to ...'"),
        (r"^move\s+(.+)\s+to\s+(.+)$",     r"mv \1 \2",                    "mv via 'move ... to ...'"),
        (r"^touch\s+(.+)$",                r"touch \1",                    "touch (passthrough)"),
        (r"^find\s+(.+)\s+in\s+(.+)$",     r"find \2 -name '\1'",          "find via 'find X in Y'"),
        (r"^size\s+(.+)$",                 r"du -sh \1",                   "du -sh via 'size'"),
        (r"^disk$",                        r"df -h",                       "df -h via 'disk'"),
        (r"^where\s+(.+)$",                r"which \1",                    "which via 'where'"),

        # ── Rede ──────────────────────────────────────────────────────
        (r"^ping\s+(.+)$",                 r"ping -c 4 \1",                "ping -c 4 via 'ping'"),
        (r"^grab\s+(.+)$",                 r"wget \1",                     "wget via 'grab'"),
        (r"^fetch\s+(.+)$",                r"curl -L \1",                  "curl via 'fetch'"),
        (r"^myip$",                        r"curl -s ifconfig.me",         "external IP via 'myip'"),
        (r"^ports$",                       r"netstat -tulpn",              "netstat via 'ports'"),
        (r"^dns\s+(.+)$",                  r"nslookup \1",                 "nslookup via 'dns'"),

        # ── Processos ─────────────────────────────────────────────────
        (r"^procs$",                       r"ps aux",                      "ps aux via 'procs'"),
        (r"^kill\s+(\d+)$",                r"kill -9 \1",                  "kill -9 via 'kill'"),
        (r"^top$",                         r"top",                         "top (passthrough)"),
        (r"^mem$",                         r"free -h",                     "free -h via 'mem'"),
        (r"^cpu$",                         r"cat /proc/cpuinfo | head -30","cpu info via 'cpu'"),

        # ── Python ────────────────────────────────────────────────────
        (r"^py\s+(.+)$",                   r"python3 \1",                  "python3 via 'py'"),
        (r"^pyrun\s+(.+)$",                r"python3 -c \"\1\"",           "python3 -c via 'pyrun'"),
        (r"^venv\s+create\s+(.+)$",        r"python3 -m venv \1",          "venv create via 'venv create'"),
        (r"^venv\s+activate\s+(.+)$",      r"source \1/bin/activate",      "venv activate via 'venv activate'"),

        # ── Git ───────────────────────────────────────────────────────
        (r"^gclone\s+(.+)$",               r"git clone \1",                "git clone via 'gclone'"),
        (r"^gstatus$",                     r"git status",                  "git status via 'gstatus'"),
        (r"^gadd\s+(.+)$",                 r"git add \1",                  "git add via 'gadd'"),
        (r"^gadd$",                        r"git add .",                   "git add . via 'gadd'"),
        (r"^gcommit\s+(.+)$",              r"git commit -m '\1'",          "git commit via 'gcommit'"),
        (r"^gpush$",                       r"git push",                    "git push via 'gpush'"),
        (r"^gpull$",                       r"git pull",                    "git pull via 'gpull'"),
        (r"^glog$",                        r"git log --oneline --graph --all --decorate", "git log via 'glog'"),
        (r"^gbranch$",                     r"git branch -a",               "git branch via 'gbranch'"),
        (r"^gswitch\s+(.+)$",              r"git checkout \1",             "git checkout via 'gswitch'"),
        (r"^gdiff$",                       r"git diff",                    "git diff via 'gdiff'"),

        # ── Sistema ───────────────────────────────────────────────────
        (r"^sysinfo$",                     r"uname -a",                    "uname -a via 'sysinfo'"),
        (r"^uptime$",                      r"uptime",                      "uptime (passthrough)"),
        (r"^reboot$",                      r"reboot",                      "reboot (passthrough)"),
        (r"^env\s+set\s+(\w+)=(.+)$",      r"export \1=\2",                "export via 'env set'"),
        (r"^env\s+get\s+(\w+)$",           r"echo $\1",                    "echo $VAR via 'env get'"),
        (r"^env\s+list$",                  r"env",                         "env via 'env list'"),

        # ── Texto ─────────────────────────────────────────────────────
        (r"^grep\s+(.+)\s+in\s+(.+)$",     r"grep -rn '\1' \2",            "grep via 'grep X in Y'"),
        (r"^count\s+lines\s+(.+)$",        r"wc -l \1",                    "wc -l via 'count lines'"),
        (r"^compress\s+(.+)$",             r"tar -czf \1.tar.gz \1",       "tar -czf via 'compress'"),
        (r"^extract\s+(.+)$",              r"tar -xzf \1",                 "tar -xzf via 'extract'"),
    ]

    # Comandos internos (tratados diretamente pelo kernel, sem subprocess)
    INTERNAL_COMMANDS = {
        "help", "clear", "cls", "history", "alias", "unalias",
        "andrux", "version", "config", "banner", "about",
        "accept-setup-andrux", "scripts", "run", "reload",
        "matrix", "glitch", "neofetch-andrux", "motd",
    }

    def __init__(self, alias_db: AndruxAliasDB):
        self.alias_db = alias_db
        self._compiled_rules = [
            (re.compile(pattern, re.IGNORECASE), replacement, desc)
            for pattern, replacement, desc in self.TRANSLATION_RULES
        ]
        self._custom_rules: list[tuple] = []

    def add_rule(self, pattern: str, replacement: str, desc: str = "custom"):
        """Adiciona regra de tradução em tempo de execução."""
        self._custom_rules.append(
            (re.compile(pattern, re.IGNORECASE), replacement, desc)
        )

    def translate(self, raw_input: str) -> dict:
        """
        Traduz o input do usuário.
        
        Retorna dict com:
          - original  : comando original
          - translated: comando traduzido (pode ser igual ao original)
          - is_internal: True se for comando interno do Andrux
          - rule_used : descrição da regra aplicada (se houver)
          - parts     : lista com [comando, *args] splitados
        """
        raw = raw_input.strip()
        if not raw:
            return {"original": raw, "translated": raw,
                    "is_internal": False, "rule_used": None, "parts": []}

        # 1) Resolve alias primeiro
        resolved = self.alias_db.resolve(raw)

        # 2) Verifica se é comando interno
        first_word = resolved.split()[0].lower() if resolved.split() else ""
        if first_word in self.INTERNAL_COMMANDS:
            return {
                "original": raw,
                "translated": resolved,
                "is_internal": True,
                "rule_used": "internal",
                "parts": resolved.split(),
            }

        # 3) Aplica regras de tradução (custom primeiro, depois built-in)
        all_rules = self._custom_rules + self._compiled_rules
        for compiled_re, replacement, desc in all_rules:
            match = compiled_re.match(resolved)
            if match:
                translated = match.expand(replacement)
                return {
                    "original": raw,
                    "translated": translated,
                    "is_internal": False,
                    "rule_used": desc,
                    "parts": shlex.split(translated) if translated else [],
                }

        # 4) Sem tradução — passa direto
        return {
            "original": raw,
            "translated": resolved,
            "is_internal": False,
            "rule_used": None,
            "parts": shlex.split(resolved) if resolved else [],
        }

    def suggest(self, partial: str) -> list[str]:
        """Sugere completions para o input parcial."""
        suggestions = []
        partial_lower = partial.lower()
        andrux_cmds = [
            "take install", "take remove", "take update", "take list",
            "get install", "get remove", "get update", "get upgrade",
            "fis", "look", "peek", "mkplace", "rmplace",
            "copy", "move", "find", "size", "disk", "where",
            "ping", "grab", "fetch", "myip", "ports", "dns",
            "procs", "mem", "cpu", "py", "pyrun",
            "gclone", "gstatus", "gadd", "gcommit", "gpush", "gpull",
            "glog", "gbranch", "gswitch", "gdiff",
            "sysinfo", "uptime", "env set", "env get", "env list",
            "grep", "count lines", "compress", "extract",
            "help", "clear", "history", "alias", "version", "config",
            "accept-setup-andrux", "scripts", "run", "banner",
            "neofetch-andrux", "matrix",
        ]
        for cmd in andrux_cmds:
            if cmd.startswith(partial_lower):
                suggestions.append(cmd)
        return suggestions[:8]


# ─────────────────────────────────────────────
#  SHELL EXECUTOR
# ─────────────────────────────────────────────

class AndruxShell:
    """
    Executa comandos traduzidos pelo kernel.
    Gerencia CWD, ambiente e callbacks de output.
    """

    def __init__(self):
        # BUG FIX #1 (parte 2): cwd inicial também precisa ser gravável no Android
        if IS_ANDROID:
            self.cwd = tempfile.gettempdir()
        else:
            self.cwd = str(Path.home())
        self.env = os.environ.copy()
        self._process: Optional[subprocess.Popen] = None
        self._running = False

        # Motor nativo: executa comandos sem binários externos
        if self.native is not None:
            self.native = NativeEngine(
                shell_cwd_getter=lambda: self.cwd,
                shell_env_getter=lambda: self.env,
            )
        else:
            self.native = None

    def get_prompt_path(self) -> str:
        home = str(Path.home())
        if self.cwd.startswith(home):
            return "~" + self.cwd[len(home):]
        return self.cwd

    def set_cwd(self, path: str) -> tuple[bool, str]:
        """Muda diretório de trabalho atual."""
        expanded = os.path.expanduser(os.path.expandvars(path))
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.cwd, expanded)
        expanded = os.path.normpath(expanded)
        if os.path.isdir(expanded):
            self.cwd = expanded
            return True, expanded
        return False, f"andrux: fis: '{path}': diretório não encontrado"

    def execute(
        self,
        command: str,
        stdout_cb: Callable[[str], None],
        stderr_cb: Callable[[str], None],
        done_cb: Callable[[int], None],
    ):
        """
        Executa comando com prioridade:
        1. Comandos built-in (cd, export)
        2. Motor nativo Python (NativeEngine) — sem binários externos
        3. Subprocess + shell do sistema — fallback
        """

        def run():
            self._running = True

            # ── Built-in: cd ─────────────────────────────────────────
            cd_match = re.match(r"^\s*cd\s*(.*)", command)
            if cd_match:
                target = cd_match.group(1).strip() or (
                    tempfile.gettempdir() if IS_ANDROID else str(Path.home())
                )
                ok, msg = self.set_cwd(target)
                if ok:
                    done_cb(0)
                else:
                    stderr_cb(msg)
                    done_cb(1)
                self._running = False
                return

            # ── Built-in: export ──────────────────────────────────────
            export_match = re.match(r"^\s*export\s+(\w+)=(.*)", command)
            if export_match:
                self.env[export_match.group(1)] = export_match.group(2)
                os.environ[export_match.group(1)] = export_match.group(2)
                done_cb(0)
                self._running = False
                return

            # ── Motor nativo Python ───────────────────────────────────
            if self.native and self.native.can_handle(command):
                # NativeEngine gerencia sua própria thread e chama done_cb
                self.native.execute(
                    command,
                    stdout_cb,
                    stderr_cb,
                    lambda rc: (setattr(self, "_running", False), done_cb(rc)),
                )
                return  # não seta _running = False aqui; NativeEngine faz isso

            # ── Subprocess fallback ───────────────────────────────────
            # BUG FIX #2: Shell correto por plataforma
            if IS_WINDOWS:
                shell_exec = ["cmd", "/c", command]
            elif IS_ANDROID:
                for sh in ["/system/bin/sh", "/bin/sh", "sh"]:
                    try:
                        if sh.startswith("/"):
                            if os.path.isfile(sh):
                                shell_exec = [sh, "-c", command]
                                break
                        else:
                            if shutil.which(sh):
                                shell_exec = [sh, "-c", command]
                                break
                    except Exception:
                        continue
                else:
                    shell_exec = ["sh", "-c", command]
            else:
                shell_exec = [shutil.which("bash") or "sh", "-c", command]

            try:
                self._process = subprocess.Popen(
                    shell_exec,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.cwd,
                    env=self.env,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )

                def read_stdout():
                    for line in iter(self._process.stdout.readline, ""):
                        if line:
                            stdout_cb(line.rstrip("\n"))
                    self._process.stdout.close()

                def read_stderr():
                    for line in iter(self._process.stderr.readline, ""):
                        if line:
                            stderr_cb(line.rstrip("\n"))
                    self._process.stderr.close()

                t1 = threading.Thread(target=read_stdout, daemon=True)
                t2 = threading.Thread(target=read_stderr, daemon=True)
                t1.start(); t2.start()
                t1.join(); t2.join()
                rc = self._process.wait()
                done_cb(rc)

            except FileNotFoundError:
                # Binário não existe — sugere alternativa nativa
                first = command.split()[0]
                stderr_cb(f"andrux: '{first}': não encontrado no PATH do APK")
                stderr_cb(f"  Use o comando Andrux equivalente ou: get install {first}")
                done_cb(127)
            except Exception as e:
                stderr_cb(f"andrux: erro de execução: {e}")
                done_cb(1)
            finally:
                self._running = False
                self._process = None

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def interrupt(self):
        """Interrompe processo atual (CTRL+C)."""
        if self._process and self._running:
            try:
                self._process.terminate()
            except Exception:
                pass

    def is_running(self) -> bool:
        return self._running


# ─────────────────────────────────────────────
#  ANDROID PERMISSION HANDLER
# ─────────────────────────────────────────────

class AndruxPermission:
    """Gerencia permissões do Android via Termux ou Android API."""

    @staticmethod
    def request_storage(callback: Callable[[bool, str], None]):
        """Solicita permissão de storage do Android."""
        if IS_ANDROID:
            try:
                # Método 1: via termux-setup-storage
                result = subprocess.run(
                    ["termux-setup-storage"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    callback(True, "✓ Permissão de storage concedida via Termux")
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            try:
                # Método 2: via am (Activity Manager)
                pkg = "com.termux"
                result = subprocess.run([
                    "am", "start", "-a",
                    "android.intent.action.REQUEST_INSTALL_PACKAGES",
                    "-n", f"{pkg}/.app.TermuxActivity"
                ], capture_output=True, text=True, timeout=10)
                callback(True, "✓ Solicitação de permissão enviada ao Android")
            except Exception as e:
                callback(False, f"✗ Falha ao solicitar permissão: {e}")
        else:
            callback(True, "ℹ Desktop mode — permissões Android não aplicáveis")

    @staticmethod
    def check_storage() -> bool:
        if IS_ANDROID:
            return os.path.exists("/storage/emulated/0")
        return True

    @staticmethod
    def request_all(callback: Callable[[str], None]):
        """Solicita todas as permissões necessárias."""
        permissions = [
            ("STORAGE",      AndruxPermission.request_storage),
        ]
        results = []

        def run_next(idx):
            if idx >= len(permissions):
                summary = "\n".join(results)
                callback(f"Permissões configuradas:\n{summary}")
                return
            name, func = permissions[idx]
            callback(f"Solicitando permissão: {name}...")

            def perm_cb(ok, msg):
                results.append(msg)
                run_next(idx + 1)

            func(perm_cb)

        run_next(0)


# ─────────────────────────────────────────────
#  INTERNAL COMMAND HANDLERS
# ─────────────────────────────────────────────

class AndruxInternals:
    """Processa todos os comandos internos do Andrux."""

    BANNER = r"""
  ___  _   _ ____  ____  _   ___  __
 / _ \| \ | |  _ \|  _ \| | | \ \/ /
| | | |  \| | | | | |_) | | | |\  / 
| |_| | |\  | |_| |  _ <| |_| |/  \ 
 \__\_\_| \_|____/|_| \_\\___//_/\_\
"""

    MOTD_LINES = [
        ("Welcome to Andrux",                                    COLORS["green"],     True),
        ("",                                                     None,                False),
        ("Working with packages:",                               COLORS["yellow"],    True),
        ("  - Search:  get search <query>",                      COLORS["white"],     False),
        ("  - Install: get install <package>",                   COLORS["white"],     False),
        ("  - Upgrade: get upgrade",                             COLORS["white"],     False),
        ("",                                                     None,                False),
        ("Subscribing to additional repositories:",              COLORS["yellow"],    True),
        ("  - Root:    get install root-repo",                   COLORS["white"],     False),
        ("  - X11:     get install x11-repo",                    COLORS["white"],     False),
        ("",                                                     None,                False),
        ("For fixing any repository issues, try:",               COLORS["cyan"],      False),
        ("  andrux-change-repo",                                 COLORS["green"],     True),
        ("",                                                     None,                False),
        ("BYEEER",                                               COLORS["green_dim"], False),
    ]

    def __init__(self, shell: AndruxShell, history: AndruxHistory,
                 alias_db: AndruxAliasDB, kernel: AndruxKernel,
                 config: AndruxConfig, output_cb: Callable):
        self.shell = shell
        self.history = history
        self.alias_db = alias_db
        self.kernel = kernel
        self.config = config
        self._out = output_cb  # (text, color, bold)

    def handle(self, parts: list[str], raw: str) -> bool:
        """
        Despacha para o handler correto.
        Retorna True se o comando foi tratado.
        """
        if not parts:
            return False
        cmd = parts[0].lower()
        args = parts[1:]

        dispatch = {
            "help":               self._help,
            "clear":              self._clear,
            "cls":                self._clear,
            "history":            self._history,
            "alias":              self._alias,
            "unalias":            self._unalias,
            "andrux":             self._andrux_meta,
            "version":            self._version,
            "config":             self._config_cmd,
            "banner":             self._banner,
            "about":              self._about,
            "accept-setup-andrux": self._accept_setup,
            "scripts":            self._scripts,
            "run":                self._run_script,
            "reload":             self._reload,
            "matrix":             self._matrix_effect,
            "glitch":             self._glitch_text,
            "neofetch-andrux":    self._neofetch,
            "motd":               self._motd,
        }
        handler = dispatch.get(cmd)
        if handler:
            handler(args)
            return True
        return False

    def _out_green(self, text):
        self._out(text, COLORS["green"], False)

    def _out_cyan(self, text):
        self._out(text, COLORS["cyan"], False)

    def _out_yellow(self, text):
        self._out(text, COLORS["yellow"], False)

    def _out_red(self, text):
        self._out(text, COLORS["red"], False)

    def _out_header(self, text):
        self._out(text, COLORS["green"], True)

    def _help(self, args):
        if args:
            topic = args[0].lower()
            self._help_topic(topic)
            return

        self._out_header("╔══════════════════════════════════════════════╗")
        self._out_header("║         ANDRUX COMMAND REFERENCE             ║")
        self._out_header("╚══════════════════════════════════════════════╝")
        self._out("")

        sections = [
            ("📦 PACOTES", [
                ("take install <pkg>",  "pip install"),
                ("take remove <pkg>",   "pip uninstall"),
                ("take update <pkg>",   "pip upgrade"),
                ("take list",           "pip list"),
                ("take info <pkg>",     "pip show"),
                ("take freeze",         "pip freeze"),
                ("get install <pkg>",   "pkg install"),
                ("get remove <pkg>",    "pkg remove"),
                ("get update",          "pkg update"),
                ("get upgrade",         "pkg upgrade"),
            ]),
            ("📂 ARQUIVOS", [
                ("fis <path>",          "cd (navegar)"),
                ("look [path]",         "ls -la"),
                ("peek <file>",         "cat (ver arquivo)"),
                ("mkplace <path>",      "mkdir -p"),
                ("rmplace <path>",      "rm -rf"),
                ("copy <src> to <dst>", "cp -r"),
                ("move <src> to <dst>", "mv"),
                ("find <name> in <dir>","find por nome"),
                ("size <path>",         "du -sh"),
                ("disk",                "df -h"),
                ("where <cmd>",         "which"),
            ]),
            ("🌐 REDE", [
                ("ping <host>",         "ping -c 4"),
                ("grab <url>",          "wget"),
                ("fetch <url>",         "curl -L"),
                ("myip",                "IP externo"),
                ("ports",               "netstat -tulpn"),
                ("dns <host>",          "nslookup"),
            ]),
            ("🐍 PYTHON", [
                ("py <script>",         "python3"),
                ("pyrun <code>",        "python3 -c"),
                ("venv create <name>",  "criar virtualenv"),
                ("venv activate <name>","ativar virtualenv"),
            ]),
            ("🔧 GIT", [
                ("gclone <url>",        "git clone"),
                ("gstatus",             "git status"),
                ("gadd [path]",         "git add"),
                ("gcommit <msg>",       "git commit -m"),
                ("gpush",               "git push"),
                ("gpull",               "git pull"),
                ("glog",                "git log visual"),
                ("gbranch",             "git branch -a"),
                ("gswitch <branch>",    "git checkout"),
                ("gdiff",               "git diff"),
            ]),
            ("⚙️  SISTEMA", [
                ("sysinfo",             "uname -a"),
                ("mem",                 "free -h"),
                ("cpu",                 "info do processador"),
                ("procs",               "ps aux"),
                ("kill <pid>",          "kill -9"),
                ("env set KEY=VAL",     "export variável"),
                ("env get KEY",         "echo $VAR"),
                ("env list",            "listar env vars"),
            ]),
            ("🔨 TEXTO/ARQUIVO", [
                ("grep <txt> in <dir>", "busca recursiva"),
                ("count lines <file>",  "wc -l"),
                ("compress <path>",     "tar -czf"),
                ("extract <file>",      "tar -xzf"),
            ]),
            ("🏠 ANDRUX", [
                ("accept-setup-andrux", "configurar permissões Android"),
                ("alias <n>=<cmd>",     "criar alias"),
                ("unalias <name>",      "remover alias"),
                ("history",             "ver histórico"),
                ("scripts",             "listar scripts"),
                ("run <script>",        "executar script"),
                ("config <key> <val>",  "alterar configuração"),
                ("banner",              "exibir banner"),
                ("neofetch-andrux",     "info do sistema"),
                ("matrix",              "efeito matrix"),
                ("version",             "versão do Andrux"),
                ("clear / cls",         "limpar tela"),
                ("help [topic]",        "esta ajuda"),
            ]),
        ]

        for title, commands in sections:
            self._out("")
            self._out(f"  {title}", COLORS["yellow"], True)
            for cmd_name, desc in commands:
                self._out(f"    {cmd_name:<28} {desc}", COLORS["white"], False)

        self._out("")
        self._out_cyan("  Dica: Use ↑/↓ para histórico | Tab para autocomplete")
        self._out_cyan("  Dica: 'help <tópico>' para detalhes (ex: help git)")

    def _help_topic(self, topic: str):
        topics = {
            "git": [
                ("gclone <url>",     "Clona repositório"),
                ("gstatus",          "Status do repo"),
                ("gadd [path]",      "Adiciona arquivos ao stage"),
                ("gcommit <msg>",    "Commit com mensagem"),
                ("gpush",            "Push para remote"),
                ("gpull",            "Pull do remote"),
                ("glog",             "Log visual com grafo"),
                ("gbranch",          "Lista branches"),
                ("gswitch <branch>", "Muda de branch"),
                ("gdiff",            "Mostra diff"),
            ],
            "python": [
                ("py <script>",         "Executa script python"),
                ("pyrun <code>",        "Executa código inline"),
                ("take install <pkg>",  "Instala pacote pip"),
                ("venv create <name>",  "Cria virtualenv"),
                ("venv activate <n>",   "Ativa virtualenv"),
            ],
            "files": [
                ("fis <path>",          "Navega para diretório"),
                ("look [path]",         "Lista arquivos (ls -la)"),
                ("peek <file>",         "Mostra conteúdo do arquivo"),
                ("mkplace <path>",      "Cria diretório"),
                ("rmplace <path>",      "Remove diretório/arquivo"),
                ("copy <src> to <dst>", "Copia"),
                ("move <src> to <dst>", "Move/renomeia"),
                ("find <n> in <dir>",   "Busca por nome"),
                ("size <path>",         "Tamanho em disco"),
                ("disk",                "Uso do disco"),
            ],
        }
        data = topics.get(topic)
        if not data:
            self._out_red(f"Tópico '{topic}' não encontrado. Tópicos: git, python, files")
            return
        self._out_header(f"  AJUDA: {topic.upper()}")
        for cmd_name, desc in data:
            self._out(f"    {cmd_name:<28} {desc}", COLORS["white"], False)

    def _clear(self, args):
        self._out("__CLEAR__", None, False)

    def _history(self, args):
        all_h = self.history.all()
        if not all_h:
            self._out_yellow("Histórico vazio.")
            return
        if args and args[0] == "clear":
            self.history.clear()
            self._out_green("✓ Histórico limpo.")
            return
        if args and args[0] == "search" and len(args) > 1:
            query = " ".join(args[1:])
            results = self.history.search(query)
            if results:
                self._out_cyan(f"Resultados para '{query}':")
                for r in results:
                    self._out(f"  {r}", COLORS["white"], False)
            else:
                self._out_yellow(f"Nenhum resultado para '{query}'")
            return
        self._out_header(f"  HISTÓRICO ({len(all_h)} entradas)")
        start = max(0, len(all_h) - 30) if not args else 0
        for i, entry in enumerate(all_h[start:], start=start + 1):
            ts = entry.get("ts", "")
            self._out(
                f"  {i:4}  [{ts[11:19]}]  {entry['cmd']}",
                COLORS["white"], False
            )

    def _alias(self, args):
        if not args:
            aliases = self.alias_db.all()
            if not aliases:
                self._out_yellow("Nenhum alias definido.")
                return
            self._out_header(f"  ALIASES ({len(aliases)})")
            for name, cmd in aliases.items():
                self._out(f"  {name:<20} = {cmd}", COLORS["white"], False)
            return
        raw = " ".join(args)
        if "=" in raw:
            name, _, cmd = raw.partition("=")
            name = name.strip()
            cmd = cmd.strip()
            if not name or not cmd:
                self._out_red("Uso: alias <nome>=<comando>")
                return
            self.alias_db.set(name, cmd)
            self._out_green(f"✓ Alias criado: {name} -> {cmd}")
        else:
            val = self.alias_db.get(args[0])
            if val:
                self._out(f"  {args[0]} = {val}", COLORS["white"], False)
            else:
                self._out_red(f"Alias '{args[0]}' não encontrado.")

    def _unalias(self, args):
        if not args:
            self._out_red("Uso: unalias <nome>")
            return
        if self.alias_db.delete(args[0]):
            self._out_green(f"✓ Alias '{args[0]}' removido.")
        else:
            self._out_red(f"Alias '{args[0]}' não encontrado.")

    def _version(self, args):
        self._out_header(f"  Andrux Terminal v{ANDRUX_VERSION} [{ANDRUX_CODENAME}]")
        self._out(f"  Python: {sys.version.split()[0]}", COLORS["white"], False)
        self._out(f"  Plataforma: {'Android' if IS_ANDROID else platform.system()}", COLORS["white"], False)
        self._out(f"  CWD: {self.shell.cwd}", COLORS["white"], False)
        self._out(f"  Config: {CONFIG_FILE}", COLORS["white"], False)
        self._out(f"  Histórico: {len(self.history.all())} entradas", COLORS["white"], False)
        self._out(f"  Aliases: {len(self.alias_db.all())}", COLORS["white"], False)

    def _config_cmd(self, args):
        if not args:
            self._out_header("  CONFIGURAÇÃO ATUAL")
            for k, v in self.config.all().items():
                self._out(f"  {k:<25} = {v}", COLORS["white"], False)
            return
        if len(args) == 1:
            val = self.config.get(args[0])
            if val is not None:
                self._out(f"  {args[0]} = {val}", COLORS["white"], False)
            else:
                self._out_red(f"Chave '{args[0]}' não encontrada.")
            return
        key, value = args[0], " ".join(args[1:])
        # Tenta converter para tipo correto
        if value.isdigit():
            value = int(value)
        elif value.lower() in ("true", "false"):
            value = value.lower() == "true"
        self.config.set(key, value)
        self._out_green(f"✓ config {key} = {value}")

    def _banner(self, args):
        for line in self.BANNER.strip().split("\n"):
            self._out(line, COLORS["green"], True)
        self._out(f"  v{ANDRUX_VERSION} | {ANDRUX_CODENAME}", COLORS["green_dim"], False)

    def _about(self, args):
        self._banner([])
        self._out("")
        self._out_cyan("  Andrux — Mobile-First Terminal Ecosystem")
        self._out("  Produtividade, automação e comandos customizados", COLORS["white"], False)
        self._out("  para Android via Python.", COLORS["white"], False)
        self._out("")
        self._out_yellow("  Diferenciais:")
        self._out("  • Sintaxe própria traduzida para comandos reais", COLORS["white"], False)
        self._out("  • Histórico persistente e aliases customizados", COLORS["white"], False)
        self._out("  • Interface retro otimizada para touch/mobile", COLORS["white"], False)
        self._out("  • Scripts automáticos e comandos encadeados", COLORS["white"], False)
        self._out("  • Kernel de tradução extensível em runtime", COLORS["white"], False)

    def _accept_setup(self, args):
        self._out_yellow("⚡ Iniciando setup de permissões Andrux...")
        self._out("")

        def perm_output(msg):
            if "✓" in msg:
                self._out(msg, COLORS["green"], True)
            elif "✗" in msg:
                self._out(msg, COLORS["red"], False)
            else:
                self._out(msg, COLORS["cyan"], False)

        AndruxPermission.request_all(perm_output)

        self._out("")
        self._out_green("✓ Setup Andrux concluído!")
        self._out_cyan("  Você pode usar /storage/emulated/0 para acessar arquivos.")
        self._out_cyan("  Use 'fis /storage/emulated/0' para navegar.")

    def _scripts(self, args):
        scripts = list(SCRIPTS_DIR.glob("*.sh")) + list(SCRIPTS_DIR.glob("*.py"))
        if not scripts:
            self._out_yellow(f"Nenhum script em {SCRIPTS_DIR}")
            self._out_cyan("  Crie scripts .sh ou .py nesse diretório.")
            return
        self._out_header(f"  SCRIPTS ({len(scripts)})")
        for s in scripts:
            size = s.stat().st_size
            self._out(
                f"  {s.name:<30} ({size} bytes)",
                COLORS["white"], False
            )

    def _run_script(self, args):
        if not args:
            self._out_red("Uso: run <nome_do_script>")
            return
        name = args[0]
        candidates = [
            SCRIPTS_DIR / name,
            SCRIPTS_DIR / f"{name}.sh",
            SCRIPTS_DIR / f"{name}.py",
        ]
        found = next((p for p in candidates if p.exists()), None)
        if not found:
            self._out_red(f"Script '{name}' não encontrado em {SCRIPTS_DIR}")
            return
        self._out_cyan(f"▶ Executando: {found.name}")
        if found.suffix == ".py":
            cmd = f"python3 {found}"
        else:
            cmd = f"bash {found}"
        # Delega para o executor normal
        self._out("__EXEC__" + cmd, None, False)

    def _reload(self, args):
        self._out_yellow("↺ Recarregando Andrux...")
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _matrix_effect(self, args):
        chars = "アイウエオカキクケコ0123456789ABCDEF<>{}[]|/\\"
        import random
        self._out("", None, False)
        for _ in range(8):
            line = "  " + " ".join(
                random.choice(chars) for _ in range(35)
            )
            self._out(line, COLORS["green"], False)
        self._out("", None, False)

    def _glitch_text(self, args):
        if not args:
            self._out_red("Uso: glitch <texto>")
            return
        text = " ".join(args)
        glitch_chars = "▓▒░█▄▀■□▪▫"
        import random
        glitched = ""
        for c in text:
            if random.random() < 0.15:
                glitched += random.choice(glitch_chars)
            else:
                glitched += c
        self._out(f"  {glitched}", COLORS["green"], True)

    def _neofetch(self, args):
        import platform as pf

        logo = [
            r"  ██████╗ ",
            r"  ██╔══██╗",
            r"  ███████║",
            r"  ██╔══██║",
            r"  ██║  ██║",
            r"  ╚═╝  ╚═╝",
        ]
        info = [
            f"user@andrux",
            f"──────────────────",
            f"OS: {'Android' if IS_ANDROID else pf.system()} {pf.release()}",
            f"Kernel: {pf.version()[:40]}",
            f"Shell: Andrux v{ANDRUX_VERSION}",
            f"Python: {pf.python_version()}",
            f"CWD: {self.shell.get_prompt_path()}",
            f"Aliases: {len(self.alias_db.all())}",
            f"History: {len(self.history.all())} cmds",
        ]
        max_lines = max(len(logo), len(info))
        for i in range(max_lines):
            l = logo[i] if i < len(logo) else " " * 10
            r = info[i] if i < len(info) else ""
            self._out(f"{l}  {r}", COLORS["green"], i == 0)

    def _motd(self, args):
        self._out("")
        for entry in self.MOTD_LINES:
            text, color, bold = entry
            if text == "":
                self._out("", None, False)
            else:
                self._out(f"  {text}", color or COLORS["white"], bold)
        self._out("")

    def _andrux_meta(self, args):
        if not args:
            self._about([])
            return
        sub = args[0].lower()
        if sub == "update":
            self._out_cyan("  Verificando atualizações...")
            self._out_yellow("  (recurso em desenvolvimento)")
        elif sub == "reset":
            self._out_yellow("  Resetando configurações para padrão...")
            for k, v in DEFAULT_CONFIG.items():
                self.config.set(k, v)
            self._out_green("✓ Configurações resetadas.")
        else:
            self._out_red(f"Subcomando desconhecido: {sub}")


# ─────────────────────────────────────────────
#  FLET GUI APPLICATION
# ─────────────────────────────────────────────

class AndruxApp:
    """Interface gráfica principal do Andrux usando Flet."""

    def __init__(self):
        self.config = AndruxConfig()
        self.history = AndruxHistory(self.config)
        self.alias_db = AndruxAliasDB()
        self.kernel = AndruxKernel(self.alias_db)
        self.shell = AndruxShell()
        self.page: Optional[ft.Page] = None
        self.output_list: Optional[ft.ListView] = None
        self.input_field: Optional[ft.TextField] = None
        self.status_bar: Optional[ft.Text] = None
        self.prompt_label: Optional[ft.Text] = None
        self._output_lines: list[ft.Control] = []
        self._suggest_row: Optional[ft.Row] = None
        self._is_executing = False
        self._clock_alive = True  # BUG FIX #3: flag para parar thread do relógio

        def output_bridge(text: str, color: str, bold: bool):
            self._append_output(text, color, bold)

        self.internals = AndruxInternals(
            self.shell, self.history, self.alias_db,
            self.kernel, self.config, output_bridge
        )

    # ── Flet entry point ─────────────────────────────────────────────

    def run(self):
        ft.app(target=self._main)

    def _main(self, page: ft.Page):
        self.page = page
        self._setup_page()
        self._build_ui()
        self._show_startup()

    def _setup_page(self):
        p = self.page
        p.title = f"Andrux Terminal v{ANDRUX_VERSION}"
        p.bgcolor = COLORS["bg"]
        p.padding = 0
        p.spacing = 0
        # ── BUG FIX #4: Fonte local, sem depender de internet ─────────
        # Google Fonts via URL falha se o device estiver offline ou a rede
        # demorar, travando a renderização da GUI.
        # Solução: registrar a fonte de um arquivo .ttf local (pasta assets/).
        # Se o arquivo não existir, o Flet usa a fonte monospace do sistema
        # como fallback seguro — nunca trava.
        font_path = Path(__file__).parent / "assets" / "ShareTechMono.ttf"
        if font_path.exists():
            p.fonts = {"mono": str(font_path)}
        else:
            # Fallback: usa monospace do sistema. Sem crash, sem freeze.
            p.fonts = {}
        p.theme = ft.Theme(font_family="mono")
        p.window_bgcolor = COLORS["bg"]
        p.window_title_bar_hidden = False
        if not IS_ANDROID:
            p.window_width = 420
            p.window_height = 820
            p.window_min_width = 320
            p.window_min_height = 500

    def _build_ui(self):
        p = self.page

        # ── Top bar ──────────────────────────────────────────────────
        topbar = ft.Container(
            content=ft.Row(
                [
                    ft.Text("◈", color=COLORS["green"], size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        f" ANDRUX v{ANDRUX_VERSION}",
                        color=COLORS["green"],
                        size=13,
                        weight=ft.FontWeight.BOLD,
                        font_family="mono",
                    ),
                    ft.Container(expand=True),
                    clock_text,
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=COLORS["panel"],
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            border=ft.border.only(bottom=ft.BorderSide(1, COLORS["border"])),
        )

        # ── BUG FIX #3: Relógio sem thread leak ───────────────────────
        # O update() num control que saiu de tela lança exceção e derruba
        # o app em background. Solução: flag de vida + try/except completo
        # + parar o loop quando a página for fechada.
        clock_text = ft.Text(
            timestamp(),
            color=COLORS["green_dim"],
            size=11,
            font_family="mono",
        )
        self._clock_alive = True

        def tick():
            while self._clock_alive:
                time.sleep(1)
                try:
                    if not self._clock_alive:
                        break
                    clock_text.value = timestamp()
                    clock_text.update()
                except Exception:
                    # Control foi destruído ou página fechou — para silenciosamente
                    self._clock_alive = False
                    break

        threading.Thread(target=tick, daemon=True).start()

        def _on_page_close(e):
            self._clock_alive = False

        self.page.on_close = _on_page_close

        # ── Output console ───────────────────────────────────────────
        self.output_list = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            auto_scroll=True,
        )

        output_container = ft.Container(
            content=self.output_list,
            expand=True,
            bgcolor=COLORS["bg"],
        )

        # ── Suggestion row ────────────────────────────────────────────
        self._suggest_row = ft.Row(
            controls=[],
            spacing=4,
            scroll=ft.ScrollMode.HIDDEN,
            visible=False,
        )
        suggest_container = ft.Container(
            content=self._suggest_row,
            bgcolor=COLORS["bg_secondary"],
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
            border=ft.border.only(
                top=ft.BorderSide(1, COLORS["border"]),
                bottom=ft.BorderSide(1, COLORS["border"])
            ),
            height=36,
            visible=False,
        )
        self._suggest_container = suggest_container

        # ── Prompt label ──────────────────────────────────────────────
        self.prompt_label = ft.Text(
            self._get_prompt(),
            color=COLORS["green"],
            size=13,
            font_family="mono",
            weight=ft.FontWeight.BOLD,
        )

        # ── Input field ───────────────────────────────────────────────
        self.input_field = ft.TextField(
            hint_text="digite um comando...",
            hint_style=ft.TextStyle(color=COLORS["grey"], size=12),
            text_style=ft.TextStyle(
                color=COLORS["white"],
                size=13,
                font_family="mono",
            ),
            border=ft.InputBorder.NONE,
            expand=True,
            cursor_color=COLORS["green"],
            bgcolor="transparent",
            content_padding=ft.padding.symmetric(horizontal=6, vertical=0),
            on_change=self._on_input_change,
            on_submit=self._on_submit,
        )

        input_row = ft.Row(
            [self.prompt_label, self.input_field],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # ── BUG FIX #5: Botões de toque para mobile ───────────────────
        # Celular não tem Arrow Up/Down nem Tab físico.
        # Adicionamos uma barra de ação acima do input com botões visíveis.
        def _btn(icon, tooltip, on_click, color=COLORS["green_dim"]):
            return ft.IconButton(
                icon=icon,
                tooltip=tooltip,
                icon_color=color,
                icon_size=20,
                on_click=on_click,
                style=ft.ButtonStyle(
                    padding=ft.padding.all(4),
                    overlay_color=COLORS["green_dark"],
                ),
            )

        def on_hist_prev(_):
            prev = self.history.prev()
            if prev is not None and self.input_field:
                self.input_field.value = prev
                try:
                    self.input_field.update()
                except Exception:
                    pass

        def on_hist_next(_):
            nxt = self.history.next()
            if nxt is not None and self.input_field:
                self.input_field.value = nxt
                try:
                    self.input_field.update()
                except Exception:
                    pass

        def on_tab(_):
            if self.input_field:
                partial = self.input_field.value or ""
                suggestions = self.kernel.suggest(partial)
                if suggestions:
                    self.input_field.value = suggestions[0] + " "
                    try:
                        self.input_field.update()
                    except Exception:
                        pass

        def on_interrupt(_):
            if self.shell.is_running():
                self.shell.interrupt()
                self._append_output("^C", COLORS["yellow"], False)
            else:
                if self.input_field:
                    self.input_field.value = ""
                    try:
                        self.input_field.update()
                    except Exception:
                        pass

        def on_clear_input(_):
            if self.input_field:
                self.input_field.value = ""
                try:
                    self.input_field.update()
                except Exception:
                    pass

        touch_toolbar = ft.Container(
            content=ft.Row(
                [
                    _btn(ft.icons.ARROW_UPWARD,   "Histórico anterior (↑)", on_hist_prev),
                    _btn(ft.icons.ARROW_DOWNWARD,  "Próximo histórico (↓)",  on_hist_next),
                    _btn(ft.icons.AUTO_AWESOME,    "Autocomplete (Tab)",     on_tab,
                         color=COLORS["cyan"]),
                    ft.Container(expand=True),
                    _btn(ft.icons.BACKSPACE_OUTLINED, "Limpar input",        on_clear_input,
                         color=COLORS["grey"]),
                    _btn(ft.icons.STOP_CIRCLE_OUTLINED, "Interromper (^C)",  on_interrupt,
                         color=COLORS["red"]),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=COLORS["bg_secondary"],
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            border=ft.border.only(top=ft.BorderSide(1, COLORS["border"])),
        )

        input_bar = ft.Container(
            content=input_row,
            bgcolor=COLORS["panel"],
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            border=ft.border.only(top=ft.BorderSide(1, COLORS["border"])),
        )

        # ── Status bar ────────────────────────────────────────────────
        self.status_bar = ft.Text(
            f"  {self.shell.get_prompt_path()}  |  ready",
            color=COLORS["green_dim"],
            size=10,
            font_family="mono",
        )
        status_container = ft.Container(
            content=self.status_bar,
            bgcolor=COLORS["green_dark"],
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
        )

        # ── Key handler (desktop) ─────────────────────────────────────
        self.page.on_keyboard_event = self._on_keyboard

        # ── Assemble ──────────────────────────────────────────────────
        self.page.add(
            ft.Column(
                [
                    topbar,
                    output_container,
                    suggest_container,
                    touch_toolbar,
                    input_bar,
                    status_container,
                ],
                expand=True,
                spacing=0,
            )
        )

    # ── Output management ────────────────────────────────────────────

    def _append_output(self, text: str, color: Optional[str], bold: bool):
        """Adiciona linha ao console de output (thread-safe via page.run_thread)."""
        if text == "__CLEAR__":
            self._do_clear()
            return
        if text and text.startswith("__EXEC__"):
            cmd = text[8:]
            threading.Thread(
                target=self._execute_translated,
                args=(cmd,), daemon=True
            ).start()
            return

        def _do():
            cfg_size = self.config.get("font_size", 13)
            color_actual = color or COLORS["white"]

            # Timestamp prefix?
            if self.config.get("show_timestamp") and color not in (None, COLORS["grey"]):
                ts = f"[{timestamp()}] "
            else:
                ts = ""

            line = ft.Text(
                f"{ts}{text}",
                color=color_actual,
                size=cfg_size,
                font_family="mono",
                weight=ft.FontWeight.BOLD if bold else ft.FontWeight.NORMAL,
                selectable=True,
                no_wrap=False,
            )
            self._output_lines.append(line)

            # Limite de linhas
            max_lines = self.config.get("max_output_lines", 2000)
            if len(self._output_lines) > max_lines:
                self._output_lines = self._output_lines[-max_lines:]
                self.output_list.controls = self._output_lines[:]
            else:
                self.output_list.controls.append(line)

            try:
                self.output_list.update()
            except Exception:
                pass

        self.page.run_thread(_do)

    def _do_clear(self):
        def _do():
            self._output_lines.clear()
            self.output_list.controls.clear()
            try:
                self.output_list.update()
            except Exception:
                pass
        self.page.run_thread(_do)

    # ── Input handling ───────────────────────────────────────────────

    def _on_input_change(self, e):
        partial = e.control.value or ""
        if self.config.get("auto_complete") and partial.strip():
            suggestions = self.kernel.suggest(partial)
            self._update_suggestions(suggestions)
        else:
            self._update_suggestions([])

    def _update_suggestions(self, suggestions: list[str]):
        def _do():
            self._suggest_row.controls.clear()
            if suggestions:
                for s in suggestions[:6]:
                    def make_click(sug):
                        def click(_):
                            self.input_field.value = sug + " "
                            self.input_field.focus()
                            try:
                                self.input_field.update()
                            except Exception:
                                pass
                            self._update_suggestions([])
                        return click

                    chip = ft.Container(
                        content=ft.Text(
                            s, color=COLORS["bg"], size=10,
                            font_family="mono",
                        ),
                        bgcolor=COLORS["green_dim"],
                        border_radius=3,
                        padding=ft.padding.symmetric(horizontal=6, vertical=2),
                        on_click=make_click(s),
                    )
                    self._suggest_row.controls.append(chip)
                self._suggest_container.visible = True
                self._suggest_row.visible = True
            else:
                self._suggest_container.visible = False
                self._suggest_row.visible = False
            try:
                self._suggest_container.update()
            except Exception:
                pass
        self.page.run_thread(_do)

    def _on_submit(self, e):
        cmd = (e.control.value or "").strip()
        if not cmd:
            return
        e.control.value = ""
        try:
            e.control.update()
        except Exception:
            pass
        self._update_suggestions([])
        self._process_command(cmd)

    def _on_keyboard(self, e: ft.KeyboardEvent):
        # CTRL+C — interrompe processo
        if e.ctrl and e.key == "C":
            if self.shell.is_running():
                self.shell.interrupt()
                self._append_output("^C", COLORS["yellow"], False)
            else:
                if self.input_field:
                    self.input_field.value = ""
                    try:
                        self.input_field.update()
                    except Exception:
                        pass
            return

        # CTRL+L — limpar tela
        if e.ctrl and e.key == "L":
            self._do_clear()
            return

        # ↑ — histórico anterior
        if e.key == "Arrow Up":
            prev = self.history.prev()
            if prev is not None and self.input_field:
                self.input_field.value = prev
                try:
                    self.input_field.update()
                except Exception:
                    pass
            return

        # ↓ — histórico próximo
        if e.key == "Arrow Down":
            nxt = self.history.next()
            if nxt is not None and self.input_field:
                self.input_field.value = nxt
                try:
                    self.input_field.update()
                except Exception:
                    pass
            return

        # Tab — primeiro sugerido
        if e.key == "Tab":
            if self.input_field:
                partial = self.input_field.value or ""
                suggestions = self.kernel.suggest(partial)
                if suggestions:
                    self.input_field.value = suggestions[0] + " "
                    try:
                        self.input_field.update()
                    except Exception:
                        pass
            return

    # ── Command processing ───────────────────────────────────────────

    def _process_command(self, raw: str):
        # Salva no histórico
        self.history.add(raw)

        # Exibe no console
        prompt = self._get_prompt()
        self._append_output(f"{prompt} {raw}", COLORS["green"], False)

        # Verifica se há processo rodando
        if self.shell.is_running():
            self._append_output(
                "⚠ Processo em andamento. Use Ctrl+C para interromper.",
                COLORS["yellow"], False
            )
            return

        # Traduz via kernel
        result = self.kernel.translate(raw)
        translated = result["translated"]
        rule = result["rule_used"]

        # Mostra tradução se houve
        if rule and rule != "internal" and translated != raw:
            self._append_output(
                f"  ↳ {translated}",
                COLORS["grey"], False
            )

        # Atualiza status bar
        self._update_status(f"exec: {translated[:40]}")

        # Processa comando interno ou externo
        if result["is_internal"]:
            parts = result["parts"]
            handled = self.internals.handle(parts, raw)
            if not handled:
                self._append_output(
                    f"andrux: comando interno não reconhecido: {parts[0] if parts else '?'}",
                    COLORS["red"], False
                )
            self._update_status("ready")
            self._update_prompt()
        else:
            self._execute_translated(translated)

    def _execute_translated(self, command: str):
        """Executa comando traduzido com callbacks de output."""
        self._is_executing = True
        self._update_status(f"▶ {command[:40]}")

        def on_stdout(line):
            self._append_output(line, COLORS["white"], False)

        def on_stderr(line):
            self._append_output(line, COLORS["orange"], False)

        def on_done(rc):
            self._is_executing = False
            if rc == 0:
                self._update_status("ready")
            else:
                self._update_status(f"exit: {rc}")
                if rc != 0:
                    self._append_output(
                        f"  [exit code {rc}]",
                        COLORS["red"], False
                    )
            self._update_prompt()

        self.shell.execute(command, on_stdout, on_stderr, on_done)

    # ── UI helpers ───────────────────────────────────────────────────

    def _get_prompt(self) -> str:
        style = self.config.get("prompt_style", "full")
        if style == "minimal":
            return ">"
        if style == "custom":
            return self.config.get("custom_prompt", "andrux> ")
        # full
        path = self.shell.get_prompt_path()
        return f"andrux:{path}$"

    def _update_prompt(self):
        if self.prompt_label:
            def _do():
                self.prompt_label.value = self._get_prompt()
                try:
                    self.prompt_label.update()
                except Exception:
                    pass
            self.page.run_thread(_do)

    def _update_status(self, msg: str):
        if self.status_bar:
            def _do():
                path = self.shell.get_prompt_path()
                self.status_bar.value = f"  {path}  |  {msg}"
                try:
                    self.status_bar.update()
                except Exception:
                    pass
            self.page.run_thread(_do)

    # ── Startup ──────────────────────────────────────────────────────

    def _show_startup(self):
        def _do():
            time.sleep(0.1)
            if self.config.get("show_banner"):
                self.internals._banner([])
                self._append_output("", None, False)
            self.internals._motd([])
            self._append_output("", None, False)
            self.input_field.focus()
            try:
                self.input_field.update()
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main():
    ensure_andrux_dirs()
    app = AndruxApp()
    app.run()


if __name__ == "__main__":
    main()
