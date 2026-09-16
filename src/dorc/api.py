from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, overload

from .assets import (
    Asset,
    CreateDir,
    Link,
    Recipe,
    Shell,
    Unlink,
    _Retired,
    _SameName,
)
from .platform import Host, HostSelector

TaskAssetsFn = Callable[[], Iterable[Asset | Iterable[Asset]]]
DependencyMode = Literal["skip", "prompt", "run"]

same = _SameName()


class _RetiredMarker:
    def __call__(self, source: str | _SameName | None = None) -> _Retired:
        return _Retired(source)


retired = _RetiredMarker()


def shell(command: str, *, name: str) -> Shell:
    """Create a shell-command asset."""
    return Shell(command=command, name=name)


def create_dir(
    *paths: str, desktop: bool = False, mode: int = 0o750
) -> list[CreateDir]:
    """Create directory assets for every supplied path."""

    return [
        CreateDir(name=path, path=path, desktop=desktop, mode=mode) for path in paths
    ]


def _link_source(
    value: str | _SameName | _Retired | _RetiredMarker,
    target: str,
) -> tuple[str, bool]:
    if value is same:
        return target, False
    if isinstance(value, _Retired):
        source = value.source
        if source is None or source is same:
            return target, True
        if isinstance(source, str):
            return source, True
        raise TypeError(f"invalid retired link source: {source!r}")
    if value is retired:
        return target, True
    if isinstance(value, str):
        return value, False
    raise TypeError(f"invalid link source: {value!r}")


def link(
    source: str | _SameName | _Retired | _RetiredMarker,
    target: str,
    *,
    source_dir: str | None = None,
    target_dir: str | None = None,
    desktop: bool = False,
) -> Link:
    """Create one symbolic-link asset, optionally rooted at two directories."""

    source_path, is_retired = _link_source(source, target)
    if source_dir:
        source_path = str(Path(source_dir) / source_path)
    if target_dir:
        target = str(Path(target_dir) / target)
    return Link(
        name=target,
        source=source_path,
        target=target,
        retired=is_retired,
        desktop=desktop,
    )


def links(
    source_dir: str,
    target_dir: str,
    mapping: dict[str, str | _SameName | _Retired | _RetiredMarker],
    *,
    desktop: bool = False,
) -> list[Link]:
    """Create link assets from target names to source names."""

    return [
        link(
            source,
            target,
            source_dir=source_dir,
            target_dir=target_dir,
            desktop=desktop,
        )
        for target, source in mapping.items()
    ]


def link_glob(
    target_dir: str, pattern: str, *, source_root: str = "dots", desktop: bool = False
) -> list[Link]:
    """Create link assets for files matching a source-directory pattern."""

    return [
        link(str(path), str(Path(target_dir) / path.name), desktop=desktop)
        for path in sorted(Path(source_root).glob(pattern))
    ]


def recipe(*names: str, dir: str | None = None, desktop: bool = False) -> list[Recipe]:
    """Create recipe assets, optionally under a recipe subdirectory."""

    return [
        Recipe(name=name, path=str(Path(dir) / name) if dir else name, desktop=desktop)
        for name in names
    ]


@dataclass
class Task:
    """A named asset function, optionally selected for a host."""

    name: str
    task_assets_fn: TaskAssetsFn
    host_selector: HostSelector | None = None
    after: tuple["Task", ...] = ()

    def matches(self, host: Host) -> bool:
        return self.host_selector is None or self.host_selector.matches(host)


@dataclass
class Flow:
    """A command made of ordered tasks and optional predecessor flows."""

    name: str
    description: str = ""
    default: bool = False
    after: tuple["Flow", ...] = ()
    dependencies: DependencyMode = "skip"
    tasks: list[Task] = field(default_factory=list)
    unlink_from: "Flow | None" = None
    _build: "Build | None" = field(default=None, init=False, repr=False)

    @overload
    def task(
        self,
        selector_or_task_fn: TaskAssetsFn,
        *,
        after: Task | tuple[Task, ...] = (),
    ) -> Task: ...

    @overload
    def task(
        self,
        selector_or_task_fn: HostSelector | None = None,
        *,
        after: Task | tuple[Task, ...] = (),
    ) -> Callable[[TaskAssetsFn], Task]: ...

    def task(
        self,
        selector_or_task_fn: HostSelector | TaskAssetsFn | None = None,
        *,
        after: Task | tuple[Task, ...] = (),
    ) -> Callable[[TaskAssetsFn], Task] | Task:
        """Register an asset function, optionally limited by a host selector.

        Use ``@flow.task`` for every host or ``@flow.task(ubuntu)`` for a
        selected host. ``after`` orders tasks within this flow.
        """

        if callable(selector_or_task_fn):
            return self._register(selector_or_task_fn, None, after)

        host_selector = selector_or_task_fn

        def decorate(task_assets_fn: TaskAssetsFn) -> Task:
            return self._register(task_assets_fn, host_selector, after)

        return decorate

    def _register(
        self,
        task_assets_fn: TaskAssetsFn,
        host_selector: HostSelector | None,
        after: Task | tuple[Task, ...],
    ) -> Task:
        prerequisite_tasks = (after,) if isinstance(after, Task) else after
        task = Task(
            name=task_assets_fn.__name__,
            task_assets_fn=task_assets_fn,
            host_selector=host_selector,
            after=prerequisite_tasks,
        )
        self.tasks.append(task)
        return task

    def selected_tasks(self, host: Host) -> list[Task]:
        """
        Kahn's topological sort: completing one task may make a dependant
        ready once its last selected prerequisite is gone.

        Return this host's tasks with prerequisites before dependants."""

        selected_tasks = [task for task in self.tasks if task.matches(host)]
        selected_ids = {id(task) for task in selected_tasks}

        # A prerequisite excluded by this host selector can never complete,
        # so a task that depends on one is misconfigured for this host.
        for task in selected_tasks:
            if any(id(prerequisite) not in selected_ids for prerequisite in task.after):
                raise ValueError(f"task cycle or unselected dependency in {self.name}")

        # Kahn's algorithm represents incoming edges as the number of
        # prerequisite tasks still remaining for each task.
        remaining_prerequisites = {
            id(task): len(task.after) for task in selected_tasks
        }

        # S: all nodes with no incoming edge.
        ready_tasks = [
            task for task in selected_tasks if remaining_prerequisites[id(task)] == 0
        ]

        # L: the sorted tasks. Remove a task from S, add it to L, then remove
        # its outgoing edges by decrementing each dependant's prerequisite count.
        ordered_tasks: list[Task] = []
        while ready_tasks:
            completed_task = ready_tasks.pop(0)
            ordered_tasks.append(completed_task)
            for dependent_task in selected_tasks:
                if any(completed_task is p for p in dependent_task.after):
                    remaining_prerequisites[id(dependent_task)] -= 1
                    # A dependant with no remaining incoming edges enters S.
                    if remaining_prerequisites[id(dependent_task)] == 0:
                        ready_tasks.append(dependent_task)

        # Nodes left out of L still have incoming edges, so there is a cycle.
        if len(ordered_tasks) != len(selected_tasks):
            raise ValueError(f"task cycle or unselected dependency in {self.name}")

        return ordered_tasks

    def assets(self, host: Host, desktop: bool) -> list[Asset]:
        """Return this flow's selected, non-retired assets in task order."""

        if self.unlink_from is not None:
            # An inferred unlink flow reverses links only; copying or removing
            # any other asset would be unsafe.
            return [
                Unlink(
                    name=f"unlink {asset.target}",
                    source=asset.source,
                    target=asset.target,
                )
                for asset in self.unlink_from.assets(host, desktop)
                if isinstance(asset, Link)
            ]
        return [
            asset
            for task in self.selected_tasks(host)
            for asset in _flatten(task.task_assets_fn())
            if not getattr(asset, "retired", False) and (desktop or not asset.desktop)
        ]

    def infer_unlink(self, *, command: str, description: str = "") -> "Flow":
        """Create a flow that removes only the links declared by this flow."""

        if self._build is None:
            raise RuntimeError("flow is not attached to a build")
        return self._build.flow(command, description=description, unlink_from=self)


def _flatten(values: Iterable[Asset | Iterable[Asset]]) -> list[Asset]:
    result: list[Asset] = []
    for value in values:
        if isinstance(value, Asset):
            result.append(value)
        else:
            result.extend(_flatten(value))
    return result


@dataclass
class Build:
    """The flows defined by one build file."""

    name: str
    flows: list[Flow] = field(default_factory=list)

    def flow(
        self,
        name: str,
        *,
        description: str = "",
        default: bool = False,
        after: Iterable[Flow] = (),
        dependencies: DependencyMode = "skip",
        unlink_from: Flow | None = None,
    ) -> Flow:
        """Add one command flow to this build."""

        if any(flow.name == name for flow in self.flows):
            raise ValueError(f"duplicate flow: {name}")

        flow = Flow(
            name,
            description,
            default,
            tuple(after),
            dependencies,
            unlink_from=unlink_from,
        )
        flow._build = self
        self.flows.append(flow)
        return flow

    @property
    def default_flow(self) -> Flow:
        """Return the one flow used when the CLI receives no command."""

        defaults = [flow for flow in self.flows if flow.default]
        if len(defaults) != 1:
            raise ValueError("build must have exactly one default flow")
        return defaults[0]

    @property
    def all_flow(self) -> Flow:
        """Create the virtual command that runs every ordinary flow."""

        return Flow(
            "all",
            "Run all ordinary flows.",
            after=tuple(flow for flow in self.flows if flow.unlink_from is None),
            dependencies="run",
        )

    def resolve(self, name: str) -> Flow:
        """Resolve a named flow, ``default``, or the built-in ``all`` flow."""

        if name == "all":
            return self.all_flow
        if name == "default":
            return self.default_flow
        for flow in self.flows:
            if flow.name == name:
                return flow
        raise KeyError(f"unknown flow: {name}")
