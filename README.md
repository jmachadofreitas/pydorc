# dorc

Python-first **Dotfile ORChestration**. A `build.py` names flows of directories, symlinks, and bash recipes. dorc plans and runs that graph on Linux and macOS.

## Layout

```
build.py      # flows and assets (the registry)
dots/         # files to link into $HOME
recipes/      # bash scripts, named explicitly in build.py
```

## Commands

```
dorc                         # default flow (usually install)
dorc all                     # every ordinary flow, in dependency order
dorc setup
dorc --list                  # flow names and descriptions
dorc --status                # current state of selected assets
dorc --dry-run               # preview; recipes see DORC_DRY_RUN=1
dorc --deps / --no-deps      # always run, or skip, predecessor flows
dorc --desktop / --no-desktop
```

Pass a flow name and optional build file: `dorc install path/to/build.py`.

## Recipes

A recipe is a bash file under `recipes/`. Declare it in `build.py`; undeclared scripts are ignored. It should exit 0, be idempotent, and stay runnable without dorc:

```
bash recipes/setup/ubuntu/headless
```

Platform filters belong in `build.py` (`@flow.task(ubuntu)`), not inside every script. dorc sets `HOME` and `DORC_DRY_RUN` (`1` under `--dry-run`, otherwise `0`). Recipe helpers can no-op when `DORC_DRY_RUN=1`.
