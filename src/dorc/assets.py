import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Context:
    """Filesystem locations and host mode used while inspecting an asset."""

    source_root: Path
    home: Path
    desktop: bool
    dry_run: bool = False

    def command_env(self) -> dict[str, str]:
        """Environment for recipe and shell processes.

        ``HOME`` is the configured home so tests can sandbox writes.
        ``DORC_DRY_RUN`` is ``1`` or ``0`` so recipe helpers can no-op.
        """

        return {
            **os.environ,
            "HOME": str(self.home),
            "DORC_DRY_RUN": "1" if self.dry_run else "0",
        }

    def _expand_home(self, path: str) -> Path:
        """Expand a leading ``~`` against the configured home.

        Unlike ``Path.expanduser()``, which always resolves against the
        real OS home, this expands against ``self.home`` so tests can
        sandbox filesystem effects with an injected home directory.
        """

        if path == "~":
            return self.home
        return self.home / path[2:] if path.startswith("~/") else Path(path)

    def target(self, path: str) -> Path:
        """Resolve a target path, expanding ``~`` against the configured home."""

        return self._expand_home(path)

    def source(self, path: str) -> Path:
        """Resolve a source path relative to the build file when necessary."""

        source_path = self._expand_home(path)
        return (
            source_path if source_path.is_absolute() else self.source_root / source_path
        )


@dataclass
class AssetState:
    """The result of checking whether an asset is already satisfied."""

    ok: bool
    message: str = ""


@dataclass(kw_only=True)
class Asset:
    """A single filesystem change or command in a flow."""

    name: str
    desktop: bool = False

    def status(self, context: Context) -> AssetState:
        """Describe whether this asset is satisfied without changing it."""

        raise NotImplementedError

    def apply(self, context: Context) -> list[str]:
        """Make this asset satisfied and return the actions performed."""

        raise NotImplementedError

    @property
    def always_apply(self) -> bool:
        """Whether the asset should apply even when its status is successful."""

        return False


@dataclass
class CreateDir(Asset):
    """Ensure a directory exists with the requested permissions."""

    path: str
    mode: int = 0o750

    def status(self, context: Context) -> AssetState:
        target = context.target(self.path)
        if not target.is_dir():
            return AssetState(
                False, "missing" if not target.exists() else "not a directory"
            )
        mode = target.stat().st_mode & 0o777
        return AssetState(
            mode == self.mode, "present" if mode == self.mode else f"mode {oct(mode)}"
        )

    def apply(self, context: Context) -> list[str]:
        target = context.target(self.path)
        if not context.dry_run:
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, self.mode)
        return [f"create {target}"]


@dataclass
class Link(Asset):
    """Ensure a target is a symbolic link to a source path."""

    source: str
    target: str
    retired: bool = False

    def status(self, context: Context) -> AssetState:
        source_path = context.source(self.source)
        target_path = context.target(self.target)
        if not source_path.exists():
            return AssetState(False, f"missing source {source_path}")
        if target_path.is_symlink() and target_path.resolve() == source_path.resolve():
            return AssetState(True, "linked")
        message = (
            "missing"
            if not target_path.exists() and not target_path.is_symlink()
            else "not linked"
        )
        return AssetState(False, message)

    def apply(self, context: Context) -> list[str]:
        source_path = context.source(self.source)
        target_path = context.target(self.target)
        if not source_path.exists():
            raise FileNotFoundError(f"missing source {source_path}")
        if not context.dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists() or target_path.is_symlink():
                if target_path.is_dir() and not target_path.is_symlink():
                    raise IsADirectoryError(
                        f"refusing to replace directory: {target_path}"
                    )
                target_path.unlink()
            target_path.symlink_to(
                source_path, target_is_directory=source_path.is_dir()
            )
        return [f"link {target_path} -> {source_path}"]


@dataclass
class Unlink(Asset):
    """Remove a target only when it is the link declared by this asset."""

    source: str
    target: str

    def status(self, context: Context) -> AssetState:
        source_path = context.source(self.source)
        target_path = context.target(self.target)
        is_declared_link = (
            target_path.is_symlink() and target_path.resolve() == source_path.resolve()
        )
        message = (
            "missing"
            if not target_path.exists() and not target_path.is_symlink()
            else "declared link"
        )
        return AssetState(not is_declared_link, message)

    def apply(self, context: Context) -> list[str]:
        target = context.target(self.target)
        if not self.status(context).ok:
            if not context.dry_run:
                target.unlink()
            return [f"unlink {target}"]
        return []


@dataclass
class Recipe(Asset):
    """Run a recipe script from the build file's ``recipes`` directory."""

    path: str

    def status(self, context: Context) -> AssetState:
        script = context.source(f"recipes/{self.path}")
        return AssetState(
            script.is_file(),
            "apply-only" if script.is_file() else f"missing recipe {script}",
        )

    def apply(self, context: Context) -> list[str]:
        script = context.source(f"recipes/{self.path}")
        completed_process = subprocess.run(
            ["bash", str(script)],
            cwd=context.source_root,
            env=context.command_env(),
            check=False,
        )
        if completed_process.returncode:
            raise RuntimeError(f"recipe failed: {self.path}")
        return [f"recipe {self.path}"]

    @property
    def always_apply(self) -> bool:
        return True


@dataclass
class Shell(Asset):
    """Run a shell command from the build file's source directory."""

    command: str

    def status(self, context: Context) -> AssetState:
        return AssetState(True, "apply-only")

    def apply(self, context: Context) -> list[str]:
        completed_process = subprocess.run(
            self.command,
            shell=True,
            cwd=context.source_root,
            env=context.command_env(),
            check=False,
        )
        if completed_process.returncode:
            raise RuntimeError(f"shell command failed: {self.name}")
        return [self.name]

    @property
    def always_apply(self) -> bool:
        return True


@dataclass(frozen=True)
class _SameName:
    pass


@dataclass(frozen=True)
class _Retired:
    source: str | _SameName | None = None
