"""CLI: `fetch` caches feedstock recipes to disk, `generate` builds artifacts from that cache."""

from __future__ import annotations

import rich_click as click

# Imported as modules, not `from .fetch import fetch`, so that
# `feedstock_maintainers.cli.fetch`/`.generate` keep referring to the submodules (which
# tests/test_cli.py monkeypatches network-fetch functions on) rather than being shadowed by the
# `fetch`/`generate` click.Group objects defined inside them.
from . import fetch as _fetch
from . import generate as _generate


@click.group()
def main() -> None:
    """Track conda-forge feedstock maintainers: fetch recipes, then generate artifacts from them."""


main.add_command(_fetch.fetch)
main.add_command(_generate.generate)


if __name__ == "__main__":
    main()
