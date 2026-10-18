"""Existing owner-private device configuration; read only after explicit agent approval."""
import hashlib
import hmac
import os
from pathlib import Path
import shlex
import stat
from network import SetupError
DOMAIN = "centrabus-remote-wifi-v1"

def derive_key(secret):
    return hmac.digest(hashlib.sha256(secret.encode("utf-8")).digest(), DOMAIN.encode("utf-8"), "sha256")

def default_config_files(device_id, *, home=None):
    """Locate the documented existing paths by metadata only, never their contents."""
    folder = (Path.home() if home is None else Path(home)) / "centrabus-device"
    return tuple(str(path) for path in (folder / "runtime.env", folder / (device_id + ".env"))
                 if not path.is_symlink() and path.is_file())

def read_key(filenames, device_id, *, home=None):
    """Read only existing owner-private files after explicit check-in approval."""
    if os.name != "posix" or not 1 <= len(filenames) <= 3:
        raise SetupError("Select one to three existing private presence configuration files at launch.")
    home = Path.home() if home is None else Path(home)
    values = {}
    try:
        for filename in filenames:
            path = Path(filename).expanduser()
            if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
                    raise ValueError()
                text = source.read(65537)
            if len(text) > 65536:
                raise ValueError()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                name, separator, raw = line.partition("=")
                if separator and name in ("CENTRABUS_DEVICE_ID", "CENTRABUS_DEVICE_SECRET"):
                    parts = shlex.split(raw, comments=False, posix=True)
                    if len(parts) != 1 or not parts[0] or any(char in parts[0] for char in ("\x00", "\n", "\r")):
                        raise ValueError()
                    values[name] = parts[0]
        if values.get("CENTRABUS_DEVICE_ID") != device_id or not values.get("CENTRABUS_DEVICE_SECRET"):
            raise ValueError()
        return derive_key(values["CENTRABUS_DEVICE_SECRET"])
    except (OSError, ValueError, UnicodeError):
        raise SetupError("Existing presence configuration is unavailable, unsafe or belongs to another device. "
                         "Use owner-private files inside this Pi account's home. Contents withheld.") from None
