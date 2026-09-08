"""Server-owned instance paths. Import before any provider/runtime initialization.

Legacy mode deliberately has no environment, filesystem or locking side effects.
Explicit mode is a local-process data boundary, NOT an OS sandbox or authentication.
"""
import atexit
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import sysconfig
import tempfile
from threading import Lock


class _InstanceLogStream:
    """Keep terminal output, and persist prints/uvicorn logs only in this instance."""
    def __init__(self, original, log, lock):
        self.original, self.log, self.lock = original, log, lock

    def write(self, value):
        with self.lock:
            self.log.write(value)
            self.log.flush()
            return self.original.write(value)

    def flush(self):
        with self.lock:
            self.log.flush()
            self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class InstanceBoundaryError(PermissionError):
    pass


def _inside(path, root):
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


class InstancePaths:
    def __init__(self):
        self.program_root = Path(__file__).resolve().parent
        keys = ("INSTANCE_ID", "INSTANCE_DATA_ROOT", "INSTANCE_HOST", "INSTANCE_PORT")
        self.explicit = any(key.startswith("INSTANCE_") for key in os.environ)
        self.instance_id = "owner"
        self.data_root = self.program_root
        self.host, self.port = "0.0.0.0", 3000
        self._lock = None
        configured_program = os.environ.get("PROGRAM_ROOT")
        if configured_program and Path(configured_program).resolve() != self.program_root:
            raise RuntimeError("PROGRAM_ROOT must match the installed source directory")
        if not self.explicit:
            return
        if not all(os.environ.get(key, "").strip() for key in keys):
            raise RuntimeError("Explicit instance requires INSTANCE_ID/DATA_ROOT/HOST/PORT")
        self.instance_id = os.environ["INSTANCE_ID"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.instance_id):
            raise RuntimeError("Invalid INSTANCE_ID")
        self.host = os.environ["INSTANCE_HOST"]
        if self.host != "127.0.0.1":
            raise RuntimeError("Phase 1 instances must bind 127.0.0.1")
        try:
            self.port = int(os.environ["INSTANCE_PORT"])
        except ValueError as exc:
            raise RuntimeError("Invalid INSTANCE_PORT") from exc
        if not 1024 <= self.port <= 65535:
            raise RuntimeError("INSTANCE_PORT must be 1024..65535")
        flag = os.environ.get("INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK", "0")
        if flag not in {"0", "1"}:
            raise RuntimeError("INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK must be 0 or 1")
        self.auth_http_loopback = flag == "1"
        try:
            self.session_ttl = int(os.environ.get("INSTANCE_SESSION_TTL_SECONDS", "28800"))
        except ValueError as exc:
            raise RuntimeError("Invalid session lifetime") from exc
        if not 1 <= self.session_ttl <= 86400:
            raise RuntimeError("Session lifetime must be 1..86400 seconds")
        # Phase 1 has no shared services. Only administrator-selected local mock endpoints.
        self.upstreams = set()
        for item in os.environ.get("INSTANCE_MOCK_UPSTREAMS", "").split(","):
            if not item.strip():
                continue
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]{4,5}", item.strip()):
                raise RuntimeError("INSTANCE_MOCK_UPSTREAMS accepts only loopback host:port")
            port = int(item.strip().split(":")[1])
            if not 1024 <= port <= 65535 or port == self.port:
                raise RuntimeError("Invalid mock upstream port")
            self.upstreams.add(("127.0.0.1", port))
        raw = Path(os.environ["INSTANCE_DATA_ROOT"])
        if not raw.is_absolute() or ".." in raw.parts:
            raise RuntimeError("INSTANCE_DATA_ROOT must be an absolute non-traversing path")
        # macOS /tmp is a system alias; canonicalize ancestors, not a selected symlink root.
        if raw.is_symlink():
            raise RuntimeError("INSTANCE_DATA_ROOT cannot be a symlink")
        self.data_root = raw.resolve()
        if (_inside(self.data_root, self.program_root)
                or _inside(self.program_root, self.data_root)
                or self.data_root == Path.home().resolve()):
            raise RuntimeError("Instance data root conflicts with source or home directory")
        for parent in self.data_root.parents:
            if (parent / ".instance.json").exists():
                raise RuntimeError("Nested instance data roots are forbidden")
            if (parent / "main.py").is_file() and (parent / "VERSION").is_file():
                raise RuntimeError("Instance data root cannot be inside another application/owner checkout")
        marker = self.data_root / ".instance.json"
        if self.data_root.exists():
            if not self.data_root.is_dir():
                raise RuntimeError("Instance data root is not a directory")
            for path in self.data_root.rglob("*"):
                if path.is_symlink() or (path.name == ".instance.json" and path != marker):
                    raise RuntimeError("Instance root contains a symlink or another instance")
            if not marker.exists() and any(p.name != ".instance.lock" for p in self.data_root.iterdir()):
                raise RuntimeError("Refusing existing unowned data; initialize an empty instance directory")
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._acquire_lock()
        try:
            if marker.exists():
                identity = json.loads(marker.read_text(encoding="utf-8"))
                if identity != {"instance_id": self.instance_id, "data_root": str(self.data_root)}:
                    raise RuntimeError("Instance identity/data root mismatch")
            else:
                marker.write_text(json.dumps({"instance_id": self.instance_id,
                                              "data_root": str(self.data_root)}), encoding="utf-8")
            for rel in (".runtime/tmp", ".runtime/logs", ".runtime/home", "workflows", "API", "data"):
                (self.data_root / rel).mkdir(parents=True, exist_ok=True, mode=0o700)
            self._isolate_environment()
            # Use Python's built-in MIME database, never machine/user web-server config.
            mimetypes.knownfiles = []
            mimetypes.init([])
            tempfile.tempdir = str(self.data_root / ".runtime/tmp")
            # python -c / uvicorn may otherwise resolve the empty sys.path entry in the new cwd.
            sys.path.insert(0, str(self.program_root))
            os.chdir(self.data_root)
            # This safety net covers Python file IO in main, PIL, StaticFiles and Kie.
            # Endpoint resolvers additionally check user paths before producing a response.
            self._install_io_boundary()
            log = open(self.data_root / ".runtime/logs/server.log", "a", encoding="utf-8")
            log_lock = Lock()
            sys.stdout = _InstanceLogStream(sys.stdout, log, log_lock)
            sys.stderr = _InstanceLogStream(sys.stderr, log, log_lock)
            (self.data_root / ".runtime/process.json").write_text(json.dumps({
                "instance_id": self.instance_id, "pid": os.getpid(),
                "host": self.host, "port": self.port,
            }), encoding="utf-8")
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def _acquire_lock(self):
        handle = open(self.data_root / ".instance.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Instance data root already in use by another process") from exc
        self._lock = handle  # Kernel releases ownership on exit/crash; never unlink the inode.

    def close(self):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _isolate_environment(self):
        # Allowlist, not a token-name blacklist: unknown credentials/proxies are dropped too.
        inherited = {key: os.environ[key] for key in ("LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
                     if key in os.environ}
        os.environ.clear()
        os.environ.update(inherited)
        os.environ.update({
            "PATH": os.pathsep.join([os.defpath, "/opt/homebrew/bin", "/usr/local/bin"]) if os.name != "nt" else os.defpath,
            "HOME": str(self.data_root / ".runtime/home"),
            "USERPROFILE": str(self.data_root / ".runtime/home"),
            "TMPDIR": str(self.data_root / ".runtime/tmp"),
            "TEMP": str(self.data_root / ".runtime/tmp"),
            "TMP": str(self.data_root / ".runtime/tmp"),
            "PROGRAM_ROOT": str(self.program_root),
            "INSTANCE_ID": self.instance_id,
            "INSTANCE_DATA_ROOT": str(self.data_root),
            "INSTANCE_HOST": self.host,
            "INSTANCE_PORT": str(self.port),
        })
        sys.dont_write_bytecode = True

    def allow_env_key(self, key):
        return not self.explicit or (not key.startswith(("INSTANCE_", "PYTHON", "LD_", "DYLD_"))
                                    and key not in {"PROGRAM_ROOT", "HOME", "USERPROFILE", "PATH",
                                                    "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR"})

    def user_path(self, path):
        """Validate existing components as well as a not-yet-created destination."""
        if not self.explicit:
            return os.fspath(path)
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.data_root / candidate
        resolved = candidate.resolve()
        if not _inside(resolved, self.data_root):
            raise InstanceBoundaryError("Path is outside this instance")
        rel = resolved.relative_to(self.data_root).as_posix().casefold()
        private = (".auth", "api", ".runtime", ".instance.json", ".instance.lock",
                   "global_config.json", "data/api_providers.json", "data/storage_settings.json",
                   "data/shared_folders.json")
        if any(rel == part or rel.startswith(part + "/") for part in private):
            raise InstanceBoundaryError("Private instance configuration is not a user file")
        # Reject even in-root symlinks to keep delete/rename semantics unambiguous.
        for part in (candidate, *candidate.parents):
            if part == self.data_root:
                break
            if part.is_symlink():
                raise InstanceBoundaryError("Instance paths cannot contain symlinks")
        return str(resolved)

    def _install_io_boundary(self):
        program = str(self.program_root)
        root = str(self.data_root)
        # Interpreter/dependency reads are necessary, but do not include the parent checkout.
        library_roots = {str(Path(sysconfig.get_path(key)).resolve())
                         for key in ("stdlib", "platstdlib", "purelib", "platlib")}
        zip_libraries = {str(Path(p).resolve()) for p in sys.path if p and p.endswith(".zip")
                         and _inside(str(Path(p).resolve()), str(Path(sys.base_prefix).resolve()))}
        # Do not keep arbitrary inherited PYTHONPATH/user-site search roots.
        sys.path[:] = [program] + [p for p in sys.path if p and
                                  (str(Path(p).resolve()) in zip_libraries or
                                   any(_inside(str(Path(p).resolve()), root) for root in library_roots))]
        system_files = {os.path.realpath(p) for p in (os.devnull, "/etc/mime.types", "/etc/localtime",
                                                    "/etc/ssl/openssl.cnf", "/etc/ssl/cert.pem")}
        protected = {str(self.data_root / name) for name in (".instance.lock", ".instance.json")}

        def check(path, write=False, directory=False):
            if isinstance(path, int) or path is None:
                return
            path = os.path.realpath(os.fsdecode(path))
            if _inside(path, root):
                if write and path in protected:
                    raise InstanceBoundaryError("Instance identity/lock cannot be replaced")
                return
            if not write:
                if path in system_files or path in zip_libraries or any(_inside(path, p) for p in library_roots):
                    return
                if _inside(path, program):
                    rel = os.path.relpath(path, program).replace("\\", "/")
                    if (rel in {".", "VERSION", "requirements.txt", "release-manifest.json"}
                            or rel in {"workflows", "providers", "static"}
                            or rel.startswith(("static/", "providers/"))
                            or ("/" not in rel and rel.endswith(".py"))
                            or (rel.startswith("workflows/") and rel.count("/") == 1)):
                        return
            raise InstanceBoundaryError("Filesystem access outside this instance is disabled")

        def audit(event, args):
            if event == "open":
                path, mode, flags = args
                write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                check(path, write)
            elif event in {"os.listdir", "os.scandir"}:
                check(args[0], directory=True)
            elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
                check(args[0], True)
            elif event in {"os.rename", "os.link", "os.symlink"}:
                check(args[0], True)
                check(args[1], True)
                if event in {"os.link", "os.symlink"}:
                    raise InstanceBoundaryError("Instance link creation is disabled")
            elif event == "subprocess.Popen":
                # Native children do not inherit Python's IO/network boundary. In particular,
                # ffmpeg can follow embedded file/HTTP references. Fail closed until a bounded
                # media worker exists; serving/downloading existing video bytes still works.
                raise InstanceBoundaryError("Native CLI/media workers are disabled in Phase 1 instances")
            elif event == "socket.bind":
                address = args[1]
                if isinstance(address, tuple) and (address[0] != self.host or address[1] != self.port):
                    raise InstanceBoundaryError("Instance listener must use configured loopback host/port")
            elif event == "socket.connect":
                address = args[1]
                if not isinstance(address, tuple) or tuple(address[:2]) not in self.upstreams:
                    raise InstanceBoundaryError("Only configured local mock upstreams are enabled in Phase 1")
            elif event in {"os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.exec"}:
                raise InstanceBoundaryError("Shell/fork execution is disabled in explicit instances")

        sys.addaudithook(audit)


PATHS = InstancePaths()
PROGRAM_ROOT = str(PATHS.program_root)
INSTANCE_ID = PATHS.instance_id
INSTANCE_DATA_ROOT = str(PATHS.data_root)
INSTANCE_HOST = PATHS.host
INSTANCE_PORT = PATHS.port
