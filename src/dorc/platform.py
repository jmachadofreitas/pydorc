import os
import platform as _platform
from dataclasses import dataclass


@dataclass
class Host:
    """Concrete platform facts detected for the current machine.

    ``distro=None`` means the host has no Linux distribution, such as macOS.
    """

    family: str
    distro: str | None = None


@dataclass
class HostSelector:
    """Partial platform requirements used to select tasks for a host.

    ``None`` is a wildcard: ``HostSelector(family="linux")`` matches every Linux
    distribution. This differs from :class:`Host`, where ``distro=None`` is a
    concrete fact about a non-Linux host.
    """

    family: str | None = None
    distro: str | None = None

    def matches(self, host: Host) -> bool:
        return (self.family is None or self.family == host.family) and (
            self.distro is None or self.distro == host.distro
        )


linux = HostSelector(family="linux")
ubuntu = HostSelector(family="linux", distro="ubuntu")
darwin = HostSelector(family="darwin")


def _read_os_release() -> dict[str, str]:
    try:
        return _platform.freedesktop_os_release()
    except OSError:
        return {}


def detect_desktop() -> bool:
    return detect_host().family == "darwin" or any(
        os.environ.get(name) for name in ("DISPLAY", "WAYLAND_DISPLAY")
    )


def detect_host() -> Host:
    family = _platform.system().lower()
    if family not in {"linux", "darwin"}:
        raise RuntimeError(f"unsupported host: {family}")
    return Host(
        family=family,
        distro=_read_os_release().get("ID") if family == "linux" else None,
    )


def resolve_host(family: str = "auto", distro: str = "auto") -> Host:
    detected = detect_host()
    return Host(
        family=detected.family if family == "auto" else family,
        distro=detected.distro
        if distro == "auto" and family in {"auto", detected.family}
        else None
        if distro == "auto"
        else distro,
    )
