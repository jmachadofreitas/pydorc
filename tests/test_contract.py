from pathlib import Path

import pytest

from dorc import Build, create_dir, links, recipe, retired, shell
from dorc.assets import Asset, Context, Shell
from dorc.cli import main
from dorc.platform import Host, darwin, linux, ubuntu
from dorc.runtime import Planner, Runner, load_build

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "example_dotfiles" / "build.py"


def test_example_build():
    """The reference build exposes the intended flows and composed command."""
    build = load_build(FIXTURE).build

    assert [flow.name for flow in build.flows] == [
        "setup",
        "directories",
        "install",
        "unlink-install",
    ]
    assert [flow.name for flow in build.all_flow.after] == [
        "setup",
        "directories",
        "install",
    ]


def test_tasks():
    """Tasks select host assets and exclude desktop assets when headless."""
    build = Build("test")
    flow = build.flow("install", default=True)

    @flow.task
    def common():
        return [create_dir("~/.config"), create_dir("~/Applications", desktop=True)]

    @flow.task(ubuntu, after=common)
    def ubuntu_files():
        return [recipe("ubuntu")]

    host = Host("linux", "ubuntu")
    assert [task.name for task in flow.selected_tasks(host)] == [
        "common",
        "ubuntu_files",
    ]
    assert [asset.name for asset in flow.assets(host, desktop=False)] == [
        "~/.config",
        "ubuntu",
    ]
    assert [asset.name for asset in flow.assets(host, desktop=True)] == [
        "~/.config",
        "~/Applications",
        "ubuntu",
    ]


def test_task_order():
    """A task runs only after each task named in its ``after`` list."""
    build = Build("test")
    flow = build.flow("install", default=True)

    @flow.task
    def first() -> list[Asset]:
        return []

    @flow.task
    def second() -> list[Asset]:
        return []

    @flow.task(after=(first, second))
    def last() -> list[Asset]:
        return []

    assert [task.name for task in flow.selected_tasks(Host("linux", "ubuntu"))] == [
        "first",
        "second",
        "last",
    ]


def test_selected_task_dependencies():
    """A selected task cannot depend on a task excluded by host selection."""
    build = Build("test")
    flow = build.flow("install", default=True)

    @flow.task(darwin)
    def darwin_prerequisite() -> list[Asset]:
        return []

    @flow.task(ubuntu, after=darwin_prerequisite)
    def ubuntu_task() -> list[Asset]:
        return []

    with pytest.raises(
        ValueError, match="task cycle or unselected dependency in install"
    ):
        flow.selected_tasks(Host("linux", "ubuntu"))


def test_shell():
    """The public shell helper creates a shell-command asset."""
    asset = shell(name="hello", command="printf hello")

    assert isinstance(asset, Shell)
    assert asset.name == "hello"
    assert asset.command == "printf hello"


def test_shell_streams_output(tmp_path: Path, capfd):
    """Recipe and shell apply inherit stdout instead of capturing it."""
    asset = shell(name="hello", command="printf hello")
    asset.apply(Context(source_root=tmp_path, home=tmp_path, desktop=False))

    assert capfd.readouterr().out == "hello"


def test_recipe_failure_keeps_live_log(tmp_path: Path, capfd):
    """A failed recipe still prints its log, then raises a short error."""
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "fail").write_text("#!/usr/bin/env bash\nprintf boom >&2\nexit 1\n")
    asset = recipe("fail")[0]

    with pytest.raises(RuntimeError, match="recipe failed: fail"):
        asset.apply(Context(source_root=tmp_path, home=tmp_path, desktop=False))

    assert "boom" in capfd.readouterr().err


def test_flow_dependencies():
    """A flow can skip, prompt for, or run its predecessor flows."""
    build = Build("test")
    setup = build.flow("setup")
    tools = build.flow("tools", after=[setup], dependencies="prompt")
    clean = tools.infer_unlink(command="unlink-tools")
    planner = Planner()

    prompted_plan = planner.plan(tools)
    assert prompted_plan.prompted
    assert [flow.name for flow in prompted_plan.flows] == ["tools"]
    assert [flow.name for flow in planner.plan(tools, override="skip").flows] == [
        "tools"
    ]
    assert [flow.name for flow in planner.plan(tools, override="run").flows] == [
        "setup",
        "tools",
    ]
    assert [flow.name for flow in planner.plan(build.all_flow).flows] == [
        "setup",
        "tools",
    ]
    assert clean.name not in [flow.name for flow in build.all_flow.after]


def test_retired_links():
    """Retired links use their target name unless given an old source name."""
    removed = links("dots", "~", {"old": retired})[0]
    renamed = links("dots", "~", {"old": retired("new")})[0]

    assert (removed.source, removed.retired) == ("dots/old", True)
    assert (renamed.source, renamed.retired) == ("dots/new", True)


def test_install_and_unlink(tmp_path: Path):
    """An inferred unlink flow removes only links declared by its install flow."""
    loaded = load_build(FIXTURE)
    host = Host("linux", "ubuntu")
    runner = Runner(
        source_root=loaded.source_root, home=tmp_path, host=host, desktop=False
    )
    install = loaded.build.resolve("install")
    runner.run(Planner().plan(install, override="skip"))

    assert (tmp_path / ".bashrc").is_symlink()
    assert not (tmp_path / ".inputrc").exists()
    runner.run(Planner().plan(loaded.build.resolve("unlink-install")))
    assert not (tmp_path / ".bashrc").exists()


def test_status_dependencies(capsys):
    """Status reports prompted predecessors without asking for confirmation."""
    main(
        [
            "install",
            str(FIXTURE),
            "--status",
            "--platform",
            "linux",
            "--distro",
            "ubuntu",
        ]
    )

    assert "install depends on: directories" in capsys.readouterr().err


def test_list(capsys):
    """List accepts a build file without requiring a flow name."""
    assert main(["--list", str(FIXTURE)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "setup",
        "directories",
        "install",
        "unlink-install",
        "all  Run all ordinary flows.",
    ]


def test_cli_prints_actions(tmp_path: Path, monkeypatch, capsys):
    """Apply output includes the actions collected for each asset."""
    monkeypatch.setattr("dorc.cli.Path.home", lambda: tmp_path)

    assert (
        main(
            [
                "directories",
                str(FIXTURE),
                "--no-deps",
                "--platform",
                "linux",
                "--distro",
                "ubuntu",
                "--no-desktop",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert f"  create {tmp_path / '.config'}" in output
    assert f"  create {tmp_path / '.local/bin'}" in output


def test_dry_run(tmp_path: Path, monkeypatch, capsys):
    """Dry-run skips mutating dirs/links and sets DORC_DRY_RUN for commands."""
    build = Build("test")
    flow = build.flow("install", default=True)
    (tmp_path / "dots").mkdir()
    (tmp_path / "dots" / "gitconfig").write_text("name = test\n")

    @flow.task
    def common():
        return [
            create_dir("~/.config"),
            links("dots", "~", {".gitconfig": "gitconfig"}),
            shell(
                name="record-dry-run",
                command='printf "$DORC_DRY_RUN" > "$HOME/.dry-run"',
            ),
        ]

    host = Host("linux", "ubuntu")
    dry_runner = Runner(
        source_root=tmp_path,
        home=tmp_path,
        host=host,
        desktop=False,
        dry_run=True,
    )
    results = dry_runner.run(Planner().plan(flow))

    assert not (tmp_path / ".config").exists()
    assert not (tmp_path / ".gitconfig").exists()
    assert (tmp_path / ".dry-run").read_text() == "1"
    assert [result.actions[0] for result in results] == [
        f"create {tmp_path / '.config'}",
        f"link {tmp_path / '.gitconfig'} -> {tmp_path / 'dots' / 'gitconfig'}",
        "record-dry-run",
    ]
    assert all(result.state.ok for result in results)

    real_runner = Runner(
        source_root=tmp_path, home=tmp_path, host=host, desktop=False
    )
    real_runner.run(Planner().plan(flow))

    assert (tmp_path / ".config").is_dir()
    assert (tmp_path / ".gitconfig").is_symlink()
    assert (tmp_path / ".dry-run").read_text() == "0"

    monkeypatch.setattr("dorc.cli.Path.home", lambda: tmp_path / "cli-home")
    (tmp_path / "cli-home").mkdir()
    assert (
        main(
            [
                "install",
                str(FIXTURE),
                "--dry-run",
                "--no-deps",
                "--platform",
                "linux",
                "--distro",
                "ubuntu",
                "--no-desktop",
            ]
        )
        == 0
    )
    assert not (tmp_path / "cli-home" / ".bashrc").exists()
    assert "  link " in capsys.readouterr().out


def test_no_desktop(tmp_path: Path, monkeypatch, capsys):
    """--no-desktop excludes GUI assets even when a desktop session is detected."""
    build_file = tmp_path / "build.py"
    build_file.write_text(
        "from dorc import Build, create_dir\n"
        "build = Build('test')\n"
        "flow = build.flow('install', default=True, description='Link configs.')\n"
        "\n"
        "@flow.task\n"
        "def common():\n"
        "    return [create_dir('~/.config'), create_dir('~/Applications', desktop=True)]\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("dorc.cli.detect_desktop", lambda: True)
    monkeypatch.setattr("dorc.cli.Path.home", lambda: home)

    assert main(["install", str(build_file), "--status"]) == 1
    assert "~/Applications" in capsys.readouterr().out

    assert main(["install", str(build_file), "--status", "--no-desktop"]) == 1
    output = capsys.readouterr().out
    assert "~/.config" in output
    assert "~/Applications" not in output


def test_version_flag(capsys):
    """`--version` reports the installed distribution version and exits 0."""

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0

    printed = capsys.readouterr().out.strip()
    assert printed.startswith("dorc ")
    assert printed.split()[1]


def test_platform_selectors_are_top_level():
    """Build files can take selectors from `dorc` without the submodule import."""

    import dorc

    assert (dorc.linux, dorc.darwin, dorc.ubuntu) == (linux, darwin, ubuntu)
    assert dorc.Host is Host

    for name in ("linux", "darwin", "ubuntu", "Host", "__version__"):
        assert name in dorc.__all__
