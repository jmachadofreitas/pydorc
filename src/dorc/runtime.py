import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .api import Build, DependencyMode, Flow
from .assets import Asset, AssetState, Context
from .platform import Host


@dataclass(frozen=True)
class LoadedBuild:
    build: Build
    source_root: Path


@dataclass(frozen=True)
class ExecutionPlan:
    flows: tuple[Flow, ...]
    prompted: bool = False


@dataclass(frozen=True)
class AssetResult:
    asset: Asset
    state: AssetState
    actions: tuple[str, ...] = ()


class Planner:
    """Turn a requested flow and dependency policy into flows to process."""

    def plan(
        self,
        requested_flow: Flow,
        *,
        override: DependencyMode | None = None,
    ) -> ExecutionPlan:
        """Return the flows to run using the flow's policy or one override."""

        dependency_mode = override or requested_flow.dependencies

        # `all` is the built-in composed command: it always expands every
        # predecessor instead of relying on the selected flow's policy.
        if requested_flow.name == "all" or dependency_mode == "run":
            return ExecutionPlan(tuple(self._dependency_order(requested_flow)))

        if (
            dependency_mode == "prompt"
            and requested_flow.after
            and override is None
        ):
            return ExecutionPlan((requested_flow,), prompted=True)

        return ExecutionPlan((requested_flow,))

    def _dependency_order(self, requested_flow: Flow) -> list[Flow]:
        """Return a flow's unique prerequisites followed by the requested flow."""

        ordered_flows: list[Flow] = []
        visited: set[int] = set()

        def visit(current_flow: Flow) -> None:
            if id(current_flow) in visited:
                return
            visited.add(id(current_flow))

            # Post-order traversal puts every prerequisite before the flow that
            # needs it and de-duplicates shared prerequisites.
            for predecessor in current_flow.after:
                visit(predecessor)

            # `all` is only a virtual grouping flow; it has no assets itself.
            if current_flow.name != "all":
                ordered_flows.append(current_flow)

        visit(requested_flow)
        return ordered_flows


class Runner:
    """Check and apply the assets selected by an execution plan."""

    def __init__(
        self,
        *,
        source_root: Path,
        home: Path,
        host: Host,
        desktop: bool,
        dry_run: bool = False,
    ) -> None:
        self.host = host
        self.context = Context(
            source_root=source_root, home=home, desktop=desktop, dry_run=dry_run
        )

    def status(self, plan: ExecutionPlan) -> list[AssetResult]:
        """Report each selected asset's current state without changing it."""

        return [
            AssetResult(asset, asset.status(self.context))
            for flow in plan.flows
            for asset in flow.assets(self.host, self.context.desktop)
        ]

    def run(self, plan: ExecutionPlan) -> list[AssetResult]:
        """Apply selected assets that need work and report their final states."""

        results: list[AssetResult] = []
        for flow in plan.flows:
            for asset in flow.assets(self.host, self.context.desktop):
                state_before_apply = asset.status(self.context)
                needs_apply = asset.always_apply or not state_before_apply.ok
                actions = tuple(asset.apply(self.context)) if needs_apply else ()
                # Report the state after applying so output describes the final
                # filesystem rather than the condition that triggered a change.
                # Skip the redundant re-check when nothing was applied.
                # Dry-run does not mutate dirs/links, so keep the pre-apply
                # message and treat the preview as successful unless apply raised.
                if needs_apply and not self.context.dry_run:
                    state_after_apply = asset.status(self.context)
                elif self.context.dry_run:
                    state_after_apply = AssetState(True, state_before_apply.message)
                else:
                    state_after_apply = state_before_apply
                results.append(AssetResult(asset, state_after_apply, actions))
        return results


def load_build(build_file: Path) -> LoadedBuild:
    """Load a build file and use its containing directory as the source root."""

    build_file = build_file.resolve()
    if not build_file.is_file():
        raise FileNotFoundError(f"missing build file: {build_file}")

    module_spec = importlib.util.spec_from_file_location("dorc_build", build_file)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load {build_file}")

    build_module = importlib.util.module_from_spec(module_spec)
    sys.modules["dorc_build"] = build_module
    caller_directory = Path.cwd()

    try:
        # Build files commonly use paths relative to their own directory.
        # Execute from that directory, then always restore dorc's caller cwd.
        os.chdir(build_file.parent)
        module_spec.loader.exec_module(build_module)
    finally:
        os.chdir(caller_directory)

    build = next(
        (value for value in vars(build_module).values() if isinstance(value, Build)),
        None,
    )

    if build is None:
        raise ValueError(f"no Build in {build_file}")

    return LoadedBuild(build, build_file.parent)
