"""Swoop CLI — search Google Flights from the terminal."""

import click

from .. import __version__
from .commands import deals_cmd, price_cmd, search_cmd


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="swoop")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Swoop — search Google Flights from the terminal."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


main.add_command(search_cmd)
main.add_command(price_cmd)
main.add_command(deals_cmd)
