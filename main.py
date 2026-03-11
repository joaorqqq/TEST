"""
╔═══════════════════════════════════════════════════════════════╗
║                    ANDRUX TERMINAL v2.0                       ║
║         Mobile-First Python Terminal Ecosystem                ║
║         GUI: Flet | Kernel: Custom Syntax Translator          ║
╚═══════════════════════════════════════════════════════════════╝

ARCHITECTURE:
  - AndruxKernel     : Intercepta e traduz sintaxe de comandos
  - AndruxShell      : Executa comandos traduzidos com output em tempo real
  - AndruxHistory    : Histórico persistente de comandos
  - AndruxAliasDB    : Banco de aliases customizados
  - AndruxPermission : Gerencia permissões Android
  - AndruxApp        : Interface Flet (GUI retro/hacker)

INSTALL DEPS:
  pip install flet

RUN (Desktop preview):
  python andrux.py

RUN (Android via BeeWare/Briefcase or Flet mobile):
  flet run andrux.py --android
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
        """Executa comando em thread separada com streaming de output."""

        def run():
            self._running = True
            # Trata cd separadamente
            cd_match = re.match(r"^\s*cd\s+(.*)", command)
            if cd_match:
                target = cd_match.group(1).strip() or str(Path.home())
                ok, msg = self.set_cwd(target)
                if ok:
                    done_cb(0)
                else:
                    stderr_cb(msg)
                    done_cb(1)
                self._running = False
                return

            # Trata export separadamente
            export_match = re.match(r"^\s*export\s+(\w+)=(.*)", command)
            if export_match:
                self.env[export_match.group(1)] = export_match.group(2)
                os.environ[export_match.group(1)] = export_match.group(2)
                done_cb(0)
                self._running = False
                return

            # ── BUG FIX #2: Shell correto por plataforma ──────────────
            # Android não tem 'bash' nativamente fora do Termux.
            # O shell universal do Android é /system/bin/sh.
            # Fazemos detecção em ordem de preferência.
            if IS_WINDOWS:
                shell_exec = ["cmd", "/c", command]
            elif IS_ANDROID:
                # Tenta sh do Android, com fallback para qualquer sh disponível
                for sh in ["/system/bin/sh", "/bin/sh", "sh"]:
                    if IS_WINDOWS:
                        break
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
                # Linux/macOS: prefere bash, cai para sh
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

                # Stream stdout
                def read_stdout():
                    for line in iter(self._process.stdout.readline, ""):
                        if line:
                            stdout_cb(line.rstrip("\n"))
                    self._process.stdout.close()

                # Stream stderr
                def read_stderr():
                    for line in iter(self._process.stderr.readline, ""):
                        if line:
                            stderr_cb(line.rstrip("\n"))
                    self._process.stderr.close()

                t1 = threading.Thread(target=read_stdout, daemon=True)
                t2 = threading.Thread(target=read_stderr, daemon=True)
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                rc = self._process.wait()
                done_cb(rc)

            except FileNotFoundError:
                stderr_cb(f"andrux: comando não encontrado: {command.split()[0]}")
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
