"""Encrypted storage for the desktop data folder: atomic writes, shredding,
the vault (AES-256-GCM under one data key) and the password-protected export.

Part of onionmind_desktop_core, which re-exports every name defined here.
No Qt, and nothing from the other parts: they all build on this one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onionmind_desktop_core import SessionStore


PathInput = str | os.PathLike[str]


# The desktop app runs under pythonw.exe, which has no console; without this
# flag Windows gives every helper process (git, node, the engine) its own
# flashing cmd window.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _atomic_write_json(path: Path, value: Mapping[str, Any], vault: Any = None) -> None:
    """Write a JSON object next to its destination, then atomically replace it.

    With a vault the file is written encrypted. Without one, an encrypted file
    is never overwritten with plaintext: that would be a silent downgrade."""

    if vault is not None:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write_bytes(path, vault.encrypt(text.encode("utf-8")))
        return
    try:
        with path.open("rb") as existing:
            if is_encrypted(existing.read(len(_ENC_MAGIC))):
                raise VaultError(f"{path.name} is encrypted; it can only be written through the open vault")
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
        temporary = None
        # fsync the containing directory as well on POSIX so the rename itself
        # survives a sudden power loss. Windows does not expose directory
        # handles through os.open, so the file flush above is the strongest
        # portable guarantee available there.
        if os.name != "nt":
            directory_fd: int | None = None
            try:
                directory_fd = os.open(
                    path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                os.fsync(directory_fd)
            except OSError:
                # Some filesystems do not support directory fsync. The atomic
                # replacement has still completed successfully in that case.
                pass
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if handle is not None:
            handle.close()
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _quarantine_corrupt(path: Path) -> Path | None:
    """Move unreadable local state aside so callers can recover immediately."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid4().hex[:8]}")
    try:
        os.replace(path, destination)
    except OSError:
        return None
    return destination


def _read_json_object(path: Path, vault: Any = None) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None
    if is_encrypted(raw):
        # Without the right key the file is unreadable, not corrupt: leave it.
        if vault is None:
            return None
        try:
            raw = vault.decrypt(raw)
        except VaultError:
            return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _quarantine_corrupt(path)
        return None

    if not isinstance(value, dict):
        _quarantine_corrupt(path)
        return None
    return value


def _as_text_core(value: Any) -> str:
    return "" if value is None else str(value)


def shred_file(path: PathInput) -> bool:
    """Overwrite a file's bytes, then remove it. True when it is gone.

    A symlink is unlinked, never written through - the file it points at is
    not this program's to destroy.

    ponytail: overwrite-then-unlink is as far as user space reaches. On an SSD,
    a copy-on-write filesystem, or any volume with snapshots, the original
    blocks can survive untouched; full-disk encryption, or Matchstick's
    RAM-only image, is the real guarantee. TECHNICAL.md states the same limit.
    """
    target = Path(path)
    if target.is_symlink():
        try:
            target.unlink()
            return True
        except OSError:
            return False
    try:
        size = target.stat().st_size
        with open(target, "r+b", buffering=0) as handle:
            written = 0
            while written < size:
                chunk = min(1 << 20, size - written)
                handle.write(os.urandom(chunk))
                written += chunk
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass                      # unwritable is not a reason to keep the file
    for attempt in (0, 1):
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt:
                return False
            try:
                os.chmod(target, stat.S_IWRITE)    # Windows read-only flag
            except OSError:
                return False
        except OSError:
            return False
    return False


def shred_tree(root: PathInput) -> int:
    """Shred every file under root and remove the directories. Returns the
    number of files removed; a directory that will not go is left behind
    rather than failing the whole wipe."""
    base = Path(root)
    if not base.exists() and not base.is_symlink():
        return 0
    if base.is_symlink() or base.is_file():
        return 1 if shred_file(base) else 0
    removed = 0
    for parent, directories, files in os.walk(base, topdown=False):
        for name in files:
            if shred_file(Path(parent) / name):
                removed += 1
        for name in directories:
            entry = Path(parent) / name
            try:
                entry.unlink() if entry.is_symlink() else entry.rmdir()
            except OSError:
                pass
    try:
        base.rmdir()
    except OSError:
        pass
    return removed


# --- Encryption at rest --------------------------------------------------
#
# Everything under the desktop data folder (settings, sessions, the network
# log, revert backups) is AES-256-GCM encrypted with one random data key.
# vault.json beside it is the only plaintext file and holds no secret: which
# key store keeps the data key, or - once the person sets a password - the
# data key wrapped with a key derived from that password (scrypt). A password
# is optional; without one the operating system's key store releases the key
# to this user account, so a copied disk or another account reads nothing,
# but programs running as this same user could ask for it. With a password the
# key is nowhere on disk in usable form, and a forgotten password means the
# data is gone: there is no recovery, by design.

VAULT_FILE = "vault.json"
_ENC_MAGIC = b"OMENC1\n"
_ENC_LINE = "enc1:"
_ENC_AAD = b"onionmind/v1"
_NONCE_BYTES = 12
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class VaultError(RuntimeError):
    """The data folder's encryption could not be opened or changed."""


class WrongPassword(VaultError):
    pass


class KeystoreUnavailable(VaultError):
    pass


class VaultLocked(VaultError):
    """The vault was locked: its key is out of memory until the password is typed."""


def encryption_available() -> bool:
    """True when the `cryptography` package (AES-GCM) can be imported."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        return False
    return True


def _aesgcm(key: bytes) -> Any:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def _key_tag(root: Path) -> str:
    """A stable, non-identifying name for one data folder's key."""
    return hashlib.sha256(str(Path(root).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


class FileKeystore:
    """Fallback: a key file only this user can read, kept outside the data folder.

    It stops another account and a copied data folder, not a program running as
    this user - the settings say so and recommend a password."""

    name = "file"
    label = "a key file only your user account can read"
    strong = False

    def __init__(self, tag: str, directory: Optional[Path] = None) -> None:
        base = directory or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "onionmind" / "keys"
        self.path = base / f"{tag}.key"

    def get(self) -> Optional[bytes]:
        try:
            value = bytes.fromhex(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(key.hex())
        os.chmod(self.path, 0o600)

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class SecretServiceKeystore:
    """Linux desktops: GNOME Keyring / KWallet through `secret-tool` (libsecret).
    The key goes in on stdin, never on a command line."""

    name = "secret-service"
    label = "your desktop keyring (Secret Service)"
    strong = True

    def __init__(self, tag: str) -> None:
        self.attributes = ["service", "onionmind", "key", tag]

    def _run(self, *args: str, data: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["secret-tool", *args, *self.attributes], input=data, capture_output=True,
                                  text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeystoreUnavailable(f"secret-tool failed: {exc}") from exc

    def get(self) -> Optional[bytes]:
        result = self._run("lookup")
        try:
            value = bytes.fromhex(result.stdout.strip()) if result.returncode == 0 else b""
        except ValueError:
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        result = self._run("store", "--label=Onionmind data key", data=key.hex())
        if result.returncode != 0 or self.get() != key:
            raise KeystoreUnavailable(result.stderr.strip() or "the keyring did not keep the key")

    def delete(self) -> None:
        self._run("clear")


class MacKeychain:
    """macOS: the login keychain through `security`. Commands go in on stdin
    (`security -i`), so the key never appears in a process listing."""

    name = "keychain"
    label = "the macOS Keychain"
    strong = True

    def __init__(self, tag: str) -> None:
        self.service, self.account = "Onionmind", f"data-key-{tag}"

    def _run(self, args: list[str], data: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["security", *args], input=data, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeystoreUnavailable(f"security failed: {exc}") from exc

    def get(self) -> Optional[bytes]:
        result = self._run(["find-generic-password", "-s", self.service, "-a", self.account, "-w"])
        try:
            value = bytes.fromhex(result.stdout.strip()) if result.returncode == 0 else b""
        except ValueError:
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        command = (f"add-generic-password -U -s {self.service} -a {self.account} "
                   f"-l \"Onionmind data key\" -w {key.hex()}\n")
        result = self._run(["-i"], data=command)
        if result.returncode != 0 or self.get() != key:
            raise KeystoreUnavailable(result.stderr.strip() or "the Keychain did not keep the key")

    def delete(self) -> None:
        self._run(["delete-generic-password", "-s", self.service, "-a", self.account])


class DpapiKeystore:
    """Windows: the key sealed with DPAPI (CryptProtectData) for this user
    account, stored beside vault.json. The sealed blob is useless to another
    account or on another machine."""

    name = "dpapi"
    label = "Windows Data Protection (DPAPI) for your account"
    strong = True

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _call(function_name: str, data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(crypt32, function_name)
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        buffer = ctypes.create_string_buffer(data, len(data))
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        result = Blob()
        # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
        if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
            raise KeystoreUnavailable(f"{function_name} failed ({ctypes.get_last_error()})")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(result.pbData)

    def get(self) -> Optional[bytes]:
        try:
            value = self._call("CryptUnprotectData", self.path.read_bytes())
        except (OSError, KeystoreUnavailable):
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        sealed = self._call("CryptProtectData", key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_bytes(sealed)
        os.replace(temporary, self.path)
        if self.get() != key:
            raise KeystoreUnavailable("DPAPI did not return the key it sealed")

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def keystore_for(root: PathInput, name: Optional[str] = None) -> Any:
    """The key store a vault uses: `name` from its header, else this platform's best."""
    root = Path(root).expanduser()
    tag = _key_tag(root)
    choice = name or ("dpapi" if os.name == "nt" else
                      "keychain" if sys.platform == "darwin" else
                      "secret-service" if shutil.which("secret-tool") else "file")
    if choice == "dpapi":
        return DpapiKeystore(root / "device-key.dpapi")
    if choice == "keychain":
        return MacKeychain(tag)
    if choice == "secret-service":
        return SecretServiceKeystore(tag)
    return FileKeystore(tag)


def _wrap_key(key: bytes, password: str) -> dict[str, Any]:
    salt, nonce = os.urandom(16), os.urandom(_NONCE_BYTES)
    kek = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **_SCRYPT)
    wrapped = _aesgcm(kek).encrypt(nonce, key, b"onionmind/key")
    return {"kdf": "scrypt", **_SCRYPT, "salt": salt.hex(), "nonce": nonce.hex(), "wrapped": wrapped.hex()}


def _unwrap_key(record: Mapping[str, Any], password: str) -> bytes:
    from cryptography.exceptions import InvalidTag

    try:
        salt, nonce = bytes.fromhex(record["salt"]), bytes.fromhex(record["nonce"])
        wrapped = bytes.fromhex(record["wrapped"])
        params = {key: int(record[key]) for key in ("n", "r", "p")}
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultError("vault.json is damaged") from exc
    if record.get("kdf") != "scrypt":
        raise VaultError("vault.json names an unknown key derivation")
    kek = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=256 * 1024 * 1024, **params)
    try:
        return _aesgcm(kek).decrypt(nonce, wrapped, b"onionmind/key")
    except InvalidTag:
        raise WrongPassword("That password is not right.") from None


class Vault:
    """An open vault: the data key in memory, and the header describing it."""

    def __init__(self, root: PathInput, key: bytes, header: Mapping[str, Any], keystore: Any = None) -> None:
        self.root = Path(root).expanduser()
        self._key: Optional[bytes] = key
        self._cipher: Any = _aesgcm(key)
        self.header = dict(header)
        self.keystore = keystore

    # lock

    @property
    def locked(self) -> bool:
        return self._key is None

    def lock(self) -> bool:
        """Drop the data key from memory; only the password brings it back.

        Only with a password: without one the key store would hand the key
        straight back, so there is nothing to lock. Python cannot overwrite the
        old bytes, so this drops every reference and leaves them to the
        allocator. Returns True when the key was dropped."""
        if not self.has_password:
            return False
        self._key = None
        self._cipher = None
        return True

    def unlock(self, password: str) -> None:
        """Bring the key back from the password. Raises WrongPassword."""
        if not self.locked:
            return
        key = _unwrap_key(self.header["password"], password)
        self._key, self._cipher = key, _aesgcm(key)

    def _require_key(self) -> tuple[bytes, Any]:
        if self._key is None:
            raise VaultLocked("Onionmind is locked; unlock it to read or write its data.")
        return self._key, self._cipher

    # data

    def encrypt(self, data: bytes) -> bytes:
        _, cipher = self._require_key()
        nonce = os.urandom(_NONCE_BYTES)
        return _ENC_MAGIC + nonce + cipher.encrypt(nonce, data, _ENC_AAD)

    def decrypt(self, blob: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag

        if not is_encrypted(blob):
            raise VaultError("not an Onionmind encrypted file")
        _, cipher = self._require_key()
        start = len(_ENC_MAGIC)
        try:
            return cipher.decrypt(blob[start:start + _NONCE_BYTES], blob[start + _NONCE_BYTES:], _ENC_AAD)
        except InvalidTag:
            raise VaultError("the file was not encrypted with this data key, or it was changed") from None

    def encrypt_line(self, text: str) -> str:
        import base64

        return _ENC_LINE + base64.b64encode(self.encrypt(text.encode("utf-8"))).decode("ascii")

    def decrypt_line(self, line: str) -> str:
        import base64

        if not line.startswith(_ENC_LINE):
            return line
        return self.decrypt(base64.b64decode(line[len(_ENC_LINE):])).decode("utf-8")

    # password

    @property
    def mode(self) -> str:
        return "password" if isinstance(self.header.get("password"), dict) else "device"

    @property
    def has_password(self) -> bool:
        return self.mode == "password"

    def describe(self) -> str:
        if self.has_password:
            return "Encrypted (AES-256-GCM). The key is unlocked by your password, which is never stored."
        store = self.keystore or keystore_for(self.root, self.header.get("keystore"))
        text = f"Encrypted (AES-256-GCM). The key is kept in {store.label}."
        if not store.strong:
            text += " Set a password for full protection: without one, programs running as you could read that file."
        return text

    def check_password(self, password: str) -> bool:
        record = self.header.get("password")
        if not isinstance(record, dict):
            return False
        try:
            key = _unwrap_key(record, password)
        except VaultError:
            return False
        return self._key is None or key == self._key

    def set_password(self, password: str, current: Optional[str] = None) -> None:
        """Set or change the password. Changing needs the current one."""
        if not password:
            raise VaultError("The password cannot be empty.")
        if self.has_password and not self.check_password(current or ""):
            raise WrongPassword("The current password is not right.")
        key, _ = self._require_key()
        header = {**self.header, "mode": "password", "password": _wrap_key(key, password)}
        header.pop("keystore", None)
        _write_header(self.root, header)            # the wrapped key first...
        old = self.header.get("keystore")
        self.header = header
        if old:                                     # ...then the unwrapped copy goes
            try:
                keystore_for(self.root, old).delete()
            except KeystoreUnavailable:
                pass
        self.keystore = None

    def remove_password(self, current: str) -> None:
        """Back to the key store: the data stays encrypted, no login needed."""
        if not self.check_password(current):
            raise WrongPassword("That password is not right.")
        key, _ = self._require_key()
        store = keystore_for(self.root)
        store.put(key)                        # the key store first...
        header = {key: value for key, value in self.header.items() if key != "password"}
        header.update(mode="device", keystore=store.name)
        _write_header(self.root, header)            # ...then the header stops asking
        self.header, self.keystore = header, store


def is_encrypted(blob: bytes) -> bool:
    return blob[:len(_ENC_MAGIC)] == _ENC_MAGIC


def _write_header(root: Path, header: Mapping[str, Any]) -> None:
    _atomic_write_json(Path(root) / VAULT_FILE, header)


def vault_header(root: PathInput) -> Optional[dict[str, Any]]:
    """The plaintext header, or None when this data folder was never encrypted."""
    path = Path(root).expanduser() / VAULT_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise VaultError(f"{path} is unreadable") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise VaultError(f"{path} is not a vault header this version understands")
    return value


def vault_needs_password(root: PathInput) -> bool:
    header = vault_header(root)
    return bool(header and isinstance(header.get("password"), dict))


def open_vault(root: PathInput, password: Optional[str] = None, keystore: Any = None) -> Vault:
    """Open (or, the first time, create) the vault of a data folder.

    First run: a new random data key goes into the platform key store and every
    plaintext file already in the folder is encrypted in place. Afterwards the
    key comes from the key store, or from `password` once one is set. Raises
    WrongPassword, KeystoreUnavailable or VaultError - never falls back to
    plaintext on its own."""
    if not encryption_available():
        raise VaultError("The cryptography package is missing; run the updater to install it.")
    root = Path(root).expanduser()
    header = vault_header(root)
    if header is None:
        key = os.urandom(32)
        store = keystore or keystore_for(root)
        try:
            store.put(key)
        except KeystoreUnavailable:
            if store.name == "file":
                raise
            store = FileKeystore(_key_tag(root))
            store.put(key)
        header = {"version": 1, "cipher": "AES-256-GCM", "mode": "device", "keystore": store.name,
                  "created_at": _utc_now()}
        _write_header(root, header)
        vault = Vault(root, key, header, store)
        encrypt_data_folder(root, vault)
        return vault
    if isinstance(header.get("password"), dict):
        if password is None:
            raise WrongPassword("A password is needed to open Onionmind's data.")
        return Vault(root, _unwrap_key(header["password"], password), header)
    store = keystore or keystore_for(root, header.get("keystore"))
    key = store.get()
    if key is None:
        raise KeystoreUnavailable(
            f"The data key is missing from {store.label}. Without it the encrypted data cannot be read.")
    vault = Vault(root, key, header, store)
    encrypt_data_folder(root, vault)                # anything an older version left in plaintext
    return vault


_VAULT_OWN_FILES = {VAULT_FILE, "device-key.dpapi"}


def encrypt_data_folder(root: PathInput, vault: Vault) -> int:
    """Encrypt in place every plaintext file under the data folder. Returns the count."""
    root = Path(root).expanduser()
    done = 0
    if not root.is_dir():
        return done
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in _VAULT_OWN_FILES and path.parent == root:
            continue
        if path.name.endswith(".tmp"):
            continue
        try:
            with path.open("rb") as handle:
                if is_encrypted(handle.read(len(_ENC_MAGIC))) and path.suffix != ".jsonl":
                    continue
            raw = path.read_bytes()
        except OSError:
            continue
        if path.suffix == ".jsonl":
            lines = raw.decode("utf-8", "replace").splitlines()
            if all(line.startswith(_ENC_LINE) or not line.strip() for line in lines):
                continue
            body = "".join(vault.encrypt_line(line) + "\n" for line in lines if line.strip())
            _atomic_write_bytes(path, body.encode("ascii"))
        elif is_encrypted(raw):
            continue
        else:
            _atomic_write_bytes(path.with_name(f".{path.name}.migrating.tmp"), vault.encrypt(raw))
            # Overwrite the plaintext bytes before the encrypted copy takes the
            # name, so they do not simply linger in freed blocks (same limits as
            # shred_file: SSDs and copy-on-write filesystems may keep a copy).
            try:
                with open(path, "r+b", buffering=0) as handle:
                    handle.write(os.urandom(len(raw)))
                    os.fsync(handle.fileno())
            except OSError:
                pass
            os.replace(path.with_name(f".{path.name}.migrating.tmp"), path)
        done += 1
    return done


def erase_data_folder(root: PathInput) -> None:
    """Forgotten password: delete the encrypted data, its header and the key."""
    root = Path(root).expanduser()
    if not (root / VAULT_FILE).is_file():
        raise VaultError(f"{root} is not an Onionmind data folder")
    try:
        header = vault_header(root) or {}
    except VaultError:
        header = {}
    if header.get("keystore"):
        try:
            keystore_for(root, header["keystore"]).delete()
        except KeystoreUnavailable:
            pass
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_data_file(path: PathInput, vault: Optional[Vault]) -> bytes:
    """A file from the data folder, decrypted when it is encrypted."""
    raw = Path(path).read_bytes()
    if is_encrypted(raw):
        if vault is None:
            raise VaultError("this file is encrypted and the vault is not open")
        return vault.decrypt(raw)
    return raw


def write_data_file(path: PathInput, data: bytes, vault: Optional[Vault]) -> None:
    """Write a file into the data folder, encrypted when a vault is open."""
    _atomic_write_bytes(Path(path), vault.encrypt(data) if vault is not None else data)


# export: one password-protected file with the sessions and settings, to move
# them to another machine or keep a backup. It does not depend on the data
# key, so it opens anywhere with the password alone. PBKDF2-HMAC-SHA256
# rather than the vault's scrypt because the phone (javax.crypto) has it too,
# and the same file imports on either side. Layout:
#   OMEXPORT1\n  <header JSON, one line>\n  <12-byte nonce><AES-256-GCM ciphertext>
# The magic and header line are the associated data, so changing the KDF
# parameters breaks the tag instead of weakening the key.

EXPORT_MAGIC = b"OMEXPORT1\n"
EXPORT_SUFFIX = ".onionmind"
EXPORT_PBKDF2_ITERATIONS = 600_000


class ExportError(VaultError):
    """An export file that is not one, is damaged, or needs another password."""


def _export_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def export_bytes(password: str, payload: Mapping[str, Any]) -> bytes:
    import base64

    if not password:
        raise ExportError("The export password cannot be empty.")
    salt, nonce = os.urandom(16), os.urandom(_NONCE_BYTES)
    header = json.dumps({"format": "onionmind-export", "version": 1, "kdf": "PBKDF2WithHmacSHA256",
                         "iterations": EXPORT_PBKDF2_ITERATIONS,
                         "salt": base64.b64encode(salt).decode("ascii")}, separators=(",", ":"))
    preamble = EXPORT_MAGIC + header.encode("ascii") + b"\n"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    key = _export_key(password, salt, EXPORT_PBKDF2_ITERATIONS)
    return preamble + nonce + _aesgcm(key).encrypt(nonce, body, preamble)


def read_export_bytes(blob: bytes, password: str) -> dict[str, Any]:
    import base64
    from cryptography.exceptions import InvalidTag

    if not blob.startswith(EXPORT_MAGIC):
        raise ExportError("This is not an Onionmind export file.")
    end = blob.find(b"\n", len(EXPORT_MAGIC))
    try:
        header = json.loads(blob[len(EXPORT_MAGIC):end].decode("ascii"))
        salt = base64.b64decode(header["salt"], validate=True)
        iterations = int(header["iterations"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise ExportError("The export file is damaged.") from exc
    if end < 0 or header.get("format") != "onionmind-export" or header.get("version") != 1:
        raise ExportError("This export was made by a newer Onionmind; update to import it.")
    if header.get("kdf") != "PBKDF2WithHmacSHA256" or not 100_000 <= iterations <= 10_000_000:
        raise ExportError("The export file names a key derivation this version does not accept.")
    preamble, rest = blob[:end + 1], blob[end + 1:]
    if len(rest) < _NONCE_BYTES + 16:
        raise ExportError("The export file is damaged.")
    key = _export_key(password, salt, iterations)
    try:
        body = _aesgcm(key).decrypt(rest[:_NONCE_BYTES], rest[_NONCE_BYTES:], preamble)
    except InvalidTag:
        raise WrongPassword("That password does not open this export, or the file was changed.") from None
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ExportError("The export file is damaged.")
    return payload


def export_payload(sessions: Iterable[Any], settings: Optional[Mapping[str, Any]] = None,
                   source: str = "desktop") -> dict[str, Any]:
    """What an export carries: every session (archived ones too) and the
    settings, minus anything that only means something on this machine."""
    rows = [session.to_dict() if hasattr(session, "to_dict") else dict(session) for session in sessions]
    clean = copy.deepcopy(dict(settings or {}))
    lock = clean.get("lock")
    if isinstance(lock, dict):
        lock.pop("passphrase", None)                # an old hash; never travels
    return {"version": 1, "from": source, "exported_at": _utc_now(), "sessions": rows, "settings": clean}


def write_export(path: PathInput, password: str, store: SessionStore,
                 settings: Optional[Mapping[str, Any]] = None) -> int:
    """Write every session in `store` and `settings` to an export file. Returns the session count."""
    sessions = store.list() + store.list(archived=True)
    payload = export_payload(sessions, settings)
    _atomic_write_bytes(Path(path).expanduser(), export_bytes(password, payload))
    return len(sessions)


def read_export(path: PathInput, password: str) -> dict[str, Any]:
    try:
        blob = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise ExportError(f"The export file could not be read: {exc.strerror or exc}") from exc
    return read_export_bytes(blob, password)


def import_sessions(store: SessionStore, payload: Mapping[str, Any]) -> dict[str, int]:
    """Add the export's sessions to `store`. Ones already here are kept as they
    are; ones that do not validate are skipped. Counts: added, existing, invalid."""
    counts = {"added": 0, "existing": 0, "invalid": 0}
    rows = payload.get("sessions")
    for row in rows if isinstance(rows, list) else []:
        try:
            counts["added" if store.adopt(row) else "existing"] += 1
        except (TypeError, ValueError):
            counts["invalid"] += 1
    return counts


def imported_settings(current: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """`current` with the export's settings laid over it, for a desktop export only:
    the phone's settings are different knobs."""
    merged = copy.deepcopy(dict(current))
    settings = payload.get("settings")
    if payload.get("from") == "desktop" and isinstance(settings, dict):
        merged.update(copy.deepcopy(settings))
    return merged
