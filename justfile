# List available recipes.
default:
    @just --list

# Install the project and its dev dependency group into .venv.
install-dev:
    uv sync

# Byte-compile every tracked source file, including modules no test imports.
check:
    uv run python -m compileall -q -x '\.sandbox' src tests examples

# Run the test suite. Extra arguments are passed through to pytest.
test *args:
    uv run pytest {{args}}

# Static check plus the full suite: the gate to run before pushing.
ci: check test

# Run the CLI from the checkout, e.g. `just cli --list examples/build.py`.
cli +args:
    uv run dorc {{args}}

# List the flows declared by the bundled example build.
example-list:
    uv run dorc --list examples/build.py --platform linux

# Show read-only status for one example flow. `--status` exits 1 to report
# drift rather than failure, so the exit code is ignored here.
example-status flow="install":
    -uv run dorc {{flow}} examples/build.py --status --platform linux
