from importlib.metadata import PackageNotFoundError, version as _version

from .api import Build, create_dir, link, link_glob, links, recipe, retired, same, shell
from .platform import Host, HostSelector, darwin, linux, ubuntu

try:
    # The distribution is `pydorc`; the import package and CLI are `dorc`.
    __version__ = _version("pydorc")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "Build",
    "Host",
    "HostSelector",
    "__version__",
    "create_dir",
    "darwin",
    "link",
    "link_glob",
    "links",
    "linux",
    "recipe",
    "retired",
    "same",
    "shell",
    "ubuntu",
]
