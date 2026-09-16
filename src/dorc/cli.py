import argparse
import sys
from pathlib import Path

from .platform import detect_desktop, resolve_host
from .runtime import Planner, Runner, load_build


def _parser() -> argparse.ArgumentParser:
    """Create the parser for flow commands and their inspection flags."""

    parser = argparse.ArgumentParser(prog="dorc")
    parser.add_argument("flow", nargs="?", default="default")
    parser.add_argument("build_file", nargs="?", default="build.py")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--list", action="store_true", help="list flows")
    view.add_argument("--status", action="store_true", help="show flow status")
    view.add_argument(
        "--dry-run",
        action="store_true",
        help="preview without changing files; recipes see DORC_DRY_RUN=1",
    )
    parser.add_argument(
        "--platform", choices=("auto", "linux", "darwin"), default="auto"
    )
    parser.add_argument("--distro", default="auto")
    desktop = parser.add_mutually_exclusive_group()
    desktop.add_argument("--desktop", action="store_true")
    desktop.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--deps", action="store_true")
    parser.add_argument("--no-deps", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a flow or inspect its status from command-line arguments."""

    try:
        arguments = _parser().parse_args(argv)
        # `--list` has no flow argument, so its one optional positional is the
        # build file. This keeps `dorc --list other-build.py` natural.

        if (
            arguments.list
            and arguments.flow != "default"
            and arguments.build_file == "build.py"
        ):
            arguments.build_file, arguments.flow = arguments.flow, "default"

        loaded_build = load_build(Path(arguments.build_file))
        host = resolve_host(arguments.platform, arguments.distro)
        if arguments.no_desktop:
            desktop = False
        elif arguments.desktop:
            desktop = True
        else:
            desktop = detect_desktop()

        if arguments.list:
            for flow in [*loaded_build.build.flows, loaded_build.build.all_flow]:
                if flow.description:
                    print(f"{flow.name}  {flow.description}")
                else:
                    print(flow.name)
            return 0

        selected_flow = loaded_build.build.resolve(arguments.flow)

        dependency_override = (
            "run" if arguments.deps else "skip" if arguments.no_deps else None
        )

        planner = Planner()
        plan = planner.plan(selected_flow, override=dependency_override)

        if arguments.status and plan.prompted:
            print(
                f"{selected_flow.name} depends on: "
                f"{', '.join(item.name for item in selected_flow.after)} "
                "(run uses prompt policy)",
                file=sys.stderr,
            )

            plan = planner.plan(selected_flow, override="skip")

        elif plan.prompted:
            if not sys.stdin.isatty():
                print(
                    f"{selected_flow.name} depends on: "
                    f"{', '.join(item.name for item in selected_flow.after)}; "
                    "use --deps or --no-deps",
                    file=sys.stderr,
                )
                return 2

            answer = input(
                f"{selected_flow.name} depends on "
                f"{', '.join(item.name for item in selected_flow.after)}. "
                "Run dependencies first? [y/N] "
            )

            plan = planner.plan(
                selected_flow,
                override=("run" if answer.lower() in {"y", "yes"} else "skip"),
            )

        runner = Runner(
            source_root=loaded_build.source_root,
            home=Path.home(),
            host=host,
            desktop=desktop,
            dry_run=arguments.dry_run,
        )

        asset_results = runner.status(plan) if arguments.status else runner.run(plan)
        for asset_result in asset_results:
            print(asset_result.asset.name, asset_result.state.message)
            if not arguments.status:
                for action in asset_result.actions:
                    print(f"  {action}")

        return 1 if any(not result.state.ok for result in asset_results) else 0

    except (OSError, KeyError, RuntimeError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
