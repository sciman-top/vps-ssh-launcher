"""vps-ssh-launcher package."""

from .contracts import __version__


def main() -> int:
    """Load CLI dispatch only when invoking the package entrypoint."""
    from .cli import main as cli_main

    return cli_main()


__all__ = ["__version__", "main"]
