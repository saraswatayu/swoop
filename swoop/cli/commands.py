"""CLI commands for swoop: search and price."""

from contextlib import nullcontext
from typing import Any

import click
from rich.console import Console

from .utils import (
    CABIN_CHOICES,
    DATE,
    IATA_CODE,
    SORT_MAP,
    check_past_date,
    configure_verbose_logging,
    resolve_quiet,
)


def _err_console(no_color: bool = False) -> Console:
    return Console(stderr=True, no_color=no_color)


def _run_search(
    origin, destination, date, *,
    return_date, cabin, passengers, children, infants_in_seat, infants_on_lap,
    sort, nonstop, max_stops,
    airline, flight_number, include_basic,
    depart_after, depart_before, arrive_after, arrive_before,
    return_depart_after, return_depart_before,
    timeout, retries,
    country, proxy,
    max_results, beam_width, time_budget,
):
    """Run swoop.search() with the given parameters. Returns the result."""
    import swoop

    stops = max_stops
    if nonstop:
        stops = 0

    sort_val = SORT_MAP.get(sort, swoop.SORT_DEPARTURE_TIME)
    airlines = list(airline) if airline else None

    pax = swoop.Passengers(
        adults=passengers,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
    )

    transport = swoop.TransportConfig(
        timeout=timeout,
        retries=retries,
        country=country,
        proxy=proxy,
    )

    return swoop.search(
        origin,
        destination,
        date,
        return_date=return_date,
        cabin=cabin,
        passengers=pax,
        sort=sort_val,
        max_stops=stops,
        airlines=airlines,
        flight_number=flight_number,
        include_basic_economy=include_basic,
        earliest_departure=depart_after,
        latest_departure=depart_before,
        earliest_arrival=arrive_after,
        latest_arrival=arrive_before,
        return_earliest_departure=return_depart_after,
        return_latest_departure=return_depart_before,
        transport=transport,
        max_results=max_results,
        beam_width=beam_width,
        time_budget=time_budget,
    )


def _run_search_legs(
    legs,
    *,
    cabin,
    passengers,
    children,
    infants_in_seat,
    infants_on_lap,
    sort,
    nonstop,
    max_stops,
    airline,
    include_basic,
    timeout,
    retries,
    country,
    proxy,
    max_results,
    beam_width,
    time_budget,
):
    """Run swoop.search_legs() with global CLI filters applied to each leg."""
    import swoop

    stops = max_stops
    if nonstop:
        stops = 0

    sort_val = SORT_MAP.get(sort, swoop.SORT_DEPARTURE_TIME)
    airlines = list(airline) if airline else None
    search_legs = [
        swoop.SearchLeg(
            date=leg_date,
            from_airport=leg_origin,
            to_airport=leg_destination,
            max_stops=stops,
            airlines=airlines,
        )
        for leg_origin, leg_destination, leg_date in legs
    ]

    pax = swoop.Passengers(
        adults=passengers,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
    )

    transport = swoop.TransportConfig(
        timeout=timeout,
        retries=retries,
        country=country,
        proxy=proxy,
    )

    return swoop.search_legs(
        search_legs,
        cabin=cabin,
        passengers=pax,
        sort=sort_val,
        include_basic_economy=include_basic,
        transport=transport,
        max_results=max_results,
        beam_width=beam_width,
        time_budget=time_budget,
    )


def _shell_quote_force(value):
    """Return a POSIX-safe single-quoted shell literal."""
    text = str(value)
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _build_price_selector_command(selector: str) -> str:
    """Build a copy/paste selector pricing command."""
    return f"swoop price --selector {_shell_quote_force(selector)}"


def _query_legs_from_price_result(result):
    """Build formatter query legs from a PriceResult."""
    return [
        {
            "flight_number": leg.flight_summary,
            "origin": leg.origin,
            "destination": leg.destination,
            "date": leg.date,
            "selection": leg.selection,
        }
        for leg in result.resolved_legs
    ]


# Shared search options decorator
def _search_options(f):
    """Apply common search filter options to a command."""
    options = [
        # Trip basics
        click.option("-r", "--return", "return_date", type=DATE, default=None, help="Return date (roundtrip)."),
        click.option("-c", "--cabin", type=click.Choice(CABIN_CHOICES, case_sensitive=False), default="economy", show_default=True, help="Cabin class."),
        click.option("-s", "--sort", type=click.Choice(list(SORT_MAP), case_sensitive=False), default="departure", show_default=True, help="Sort order."),
        click.option("--country", type=str, default=None, help="Point-of-sale country code (e.g. GB, DE). Affects currency and fares."),
        # Passengers
        click.option("-p", "--passengers", type=int, default=1, show_default=True, help="Number of adults."),
        click.option("--children", type=int, default=0, show_default=True, help="Number of children (2-11)."),
        click.option("--infants-in-seat", type=int, default=0, show_default=True, help="Number of infants in seat."),
        click.option("--infants-on-lap", type=int, default=0, show_default=True, help="Number of infants on lap."),
        # Filters
        click.option("-n", "--nonstop", is_flag=True, default=False, help="Nonstop flights only."),
        click.option("--max-stops", type=click.IntRange(0, 2), default=None, help="Max stops (0, 1, or 2)."),
        click.option("-a", "--airline", type=str, multiple=True, help="Filter by airline IATA code (repeatable)."),
        click.option("--flight", "flight_number", type=str, default=None, help="Filter to specific flight number."),
        click.option("--include-basic", is_flag=True, default=False, help="Include basic economy fares."),
        # Time windows
        click.option("--depart-after", type=click.IntRange(0, 23), default=None, help="Earliest departure hour (0-23)."),
        click.option("--depart-before", type=click.IntRange(1, 24), default=None, help="Latest departure hour (1-24)."),
        click.option("--arrive-after", type=click.IntRange(0, 23), default=None, help="Earliest arrival hour (0-23)."),
        click.option("--arrive-before", type=click.IntRange(1, 24), default=None, help="Latest arrival hour (1-24)."),
        click.option("--return-depart-after", type=click.IntRange(0, 23), default=None, help="Return departure window start."),
        click.option("--return-depart-before", type=click.IntRange(1, 24), default=None, help="Return departure window end."),
        # Advanced
        click.option("--max-results", type=int, default=None, help="Max trip combinations for beam search (multi-city)."),
        click.option("--beam-width", type=int, default=None, help="Beam search width (multi-city)."),
        click.option("--time-budget", type=int, default=None, help="Beam search time budget in seconds (multi-city)."),
        click.option("--timeout", type=int, default=90, show_default=True, help="HTTP timeout in seconds."),
        click.option("--retries", type=int, default=2, show_default=True, help="Retries on rate limit."),
        click.option("--proxy", type=str, default=None, help="HTTP/SOCKS5 proxy URL."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _output_options(formats: list[str]):
    """Apply output format options to a command."""
    def decorator(f):
        options = [
            click.option("-o", "--output", "output_format", type=click.Choice(formats, case_sensitive=False), default=formats[0], show_default=True, help="Output format."),
            click.option("--no-color", is_flag=True, default=False, help="Disable color output."),
            click.option("-q", "--quiet", is_flag=True, default=False,
                         help="Suppress spinners/headers. Auto-on when stdout is not a TTY."),
            click.option("-v", "--verbose", is_flag=True, default=False,
                         help="Show debug logging (RPC URLs, response sizes, retries) on stderr."),
        ]
        for option in reversed(options):
            f = option(f)
        return f
    return decorator


@click.command("search")
@click.argument("origin", type=IATA_CODE, required=False, default=None)
@click.argument("destination", type=IATA_CODE, required=False, default=None)
@click.argument("date", type=DATE, required=False, default=None)
@_search_options
@click.option("--leg", multiple=True, type=(IATA_CODE, IATA_CODE, DATE),
              help="Explicit leg: ORIGIN DEST DATE (repeatable).")
@click.option("-l", "--limit", type=int, default=None, help="Max results to display.")
@click.option("--show-price-commands", is_flag=True, default=False,
              help="Show copy/paste `swoop price --selector ...` commands for displayed rows.")
@_output_options(["table", "json", "csv", "brief"])
@click.pass_context
def search_cmd(
    ctx, origin, destination, date,
    leg,
    return_date, cabin, passengers, children, infants_in_seat, infants_on_lap,
    sort, nonstop, max_stops,
    airline, flight_number, include_basic,
    depart_after, depart_before, arrive_after, arrive_before,
    return_depart_after, return_depart_before,
    country, proxy,
    timeout, retries, max_results, beam_width, time_budget,
    limit, show_price_commands,
    output_format, no_color, quiet, verbose,
):
    """Search for flights.

    \b
    Examples:
      swoop search JFK LAX 2026-06-15
      swoop search JFK LAX 2026-06-15 --nonstop --sort cheapest
      swoop search JFK LAX 2026-06-15 -r 2026-06-22 --cabin business
      swoop search --leg JFK LAX 2026-06-15 --leg LAX SFO 2026-06-18
      swoop search JFK LAX 2026-06-15 --show-price-commands
      swoop search JFK LAX 2026-06-15 -o json -q | jq '.results[0]'
    """
    from swoop.exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError

    from .formatters import (
        format_search_brief,
        format_search_csv,
        format_search_json,
        format_search_table,
    )

    configure_verbose_logging(ctx, verbose)
    quiet = resolve_quiet(quiet)
    err = _err_console(no_color)
    has_positional = any(value is not None for value in (origin, destination, date))
    has_full_positional = all(value is not None for value in (origin, destination, date))
    has_leg = len(leg) > 0

    if show_price_commands and output_format in {"json", "csv"}:
        err.print("[red]Error: --show-price-commands is only supported with table or brief output.[/red]")
        ctx.exit(2)

    if has_leg and has_positional:
        err.print("[red]Error: positional args and --leg cannot be used together.[/red]")
        ctx.exit(2)
    if has_leg:
        if return_date is not None:
            err.print("[red]Error: --leg cannot be combined with --return.[/red]")
            ctx.exit(2)
        if flight_number is not None:
            err.print("[red]Error: --leg cannot be combined with --flight.[/red]")
            ctx.exit(2)
        if any(value is not None for value in (
            depart_after, depart_before, arrive_after, arrive_before,
            return_depart_after, return_depart_before,
        )):
            err.print("[red]Error: time-window filters are not supported with --leg searches.[/red]")
            ctx.exit(2)
    elif has_positional and not has_full_positional:
        err.print("[red]Error: ORIGIN DESTINATION DATE are all required.[/red]")
        ctx.exit(2)
    elif not has_full_positional:
        err.print("[red]Error: provide ORIGIN DESTINATION DATE or use --leg.[/red]")
        ctx.exit(2)

    if has_leg:
        for leg_origin, leg_destination, leg_date in leg:
            warning = check_past_date(leg_date)
            if warning:
                err.print(f"[yellow]{warning}[/yellow]")
                break
    else:
        warning = check_past_date(date)
        if warning:
            err.print(f"[yellow]{warning}[/yellow]")

    spinner = err.status("[bold]Searching flights...[/bold]") if (not quiet and output_format == "table") else nullcontext()
    with spinner:
        try:
            if has_leg:
                result = _run_search_legs(
                    leg,
                    cabin=cabin, passengers=passengers,
                    children=children, infants_in_seat=infants_in_seat,
                    infants_on_lap=infants_on_lap,
                    sort=sort, nonstop=nonstop, max_stops=max_stops,
                    airline=airline, include_basic=include_basic,
                    timeout=timeout, retries=retries,
                    country=country, proxy=proxy,
                    max_results=max_results, beam_width=beam_width,
                    time_budget=time_budget,
                )
            else:
                result = _run_search(
                    origin, destination, date,
                    return_date=return_date, cabin=cabin, passengers=passengers,
                    children=children, infants_in_seat=infants_in_seat,
                    infants_on_lap=infants_on_lap,
                    sort=sort, nonstop=nonstop, max_stops=max_stops,
                    airline=airline, flight_number=flight_number,
                    include_basic=include_basic,
                    depart_after=depart_after, depart_before=depart_before,
                    arrive_after=arrive_after, arrive_before=arrive_before,
                    return_depart_after=return_depart_after,
                    return_depart_before=return_depart_before,
                    timeout=timeout, retries=retries,
                    country=country, proxy=proxy,
                    max_results=max_results, beam_width=beam_width,
                    time_budget=time_budget,
                )
        except ValueError as e:
            err.print(f"[red]Error: {e}[/red]")
            ctx.exit(2)
        except SwoopRateLimitError:
            err.print("[red]Rate limited. Wait a few minutes. Tip: use --retries 3[/red]")
            ctx.exit(3)
        except SwoopHTTPError as e:
            err.print(f"[red]Google Flights returned HTTP {e.status_code}[/red]")
            ctx.exit(3)
        except SwoopParseError:
            err.print("[red]Could not parse Google Flights response[/red]")
            ctx.exit(4)

    if result is None or not result.results:
        if has_leg:
            err.print("[yellow]No flights found for the requested trip.[/yellow]")
        else:
            err.print(
                f"[yellow]No flights found for {origin} -> {destination} "
                f"on {date}.[/yellow]"
            )
        ctx.exit(1)

    assert result is not None  # narrowed by the ctx.exit(1) above
    display_origin = leg[0][0] if has_leg else origin
    display_destination = leg[-1][1] if has_leg else destination
    display_date = leg[0][2] if has_leg else date
    display_return_date = None if has_leg else return_date
    display_options = list(result.results[:limit]) if limit else list(result.results)
    price_commands = None
    if show_price_commands:
        price_commands = [
            _build_price_selector_command(option.selector)
            for option in display_options
        ]
    # dict[str, Any] so pyright doesn't widen each value to the union of all values
    # and reject every str-typed parameter on the formatter calls below.
    fmt_kwargs: dict[str, Any] = dict(
        origin=display_origin, destination=display_destination, date=display_date,
        cabin=cabin, adults=passengers, return_date=display_return_date,
        legs=leg if has_leg else None,
        limit=limit,
        price_commands=price_commands,
    )

    if output_format == "table":
        format_search_table(result, no_color=no_color, **fmt_kwargs)
    elif output_format == "json":
        format_search_json(result, **fmt_kwargs)
    elif output_format == "csv":
        format_search_csv(result, limit=limit)
    elif output_format == "brief":
        format_search_brief(
            result,
            limit=limit,
            price_commands=fmt_kwargs["price_commands"],
        )


@click.command("price")
@click.argument("origin", type=IATA_CODE, required=False, default=None)
@click.argument("destination", type=IATA_CODE, required=False, default=None)
@click.option("--selector", type=str, default=None, help="Opaque itinerary selector from search JSON.")
@click.option("-d", "--depart", type=(DATE, str), default=None, help="Departure leg: DATE FLIGHT.")
@click.option("-r", "--return", "return_leg", type=(DATE, str), default=None, help="Return leg: DATE FLIGHT.")
@click.option("--leg", multiple=True, type=(IATA_CODE, IATA_CODE, DATE, str),
              help="Explicit leg: ORIGIN DEST DATE FLIGHT (repeatable).")
@click.option("-c", "--cabin", type=click.Choice(CABIN_CHOICES, case_sensitive=False), default="economy", show_default=True)
@click.option("-p", "--passengers", type=int, default=1, show_default=True)
@click.option("--children", type=int, default=0, show_default=True, help="Number of children (2-11).")
@click.option("--infants-in-seat", type=int, default=0, show_default=True, help="Number of infants in seat.")
@click.option("--infants-on-lap", type=int, default=0, show_default=True, help="Number of infants on lap.")
@click.option("--max-stops", type=click.IntRange(0, 2), default=None)
@click.option("--include-basic", is_flag=True, default=False, help="Include basic economy fares.")
@click.option("--country", type=str, default=None, help="Point-of-sale country code (e.g. GB, DE). Affects currency and fares.")
@click.option("--proxy", type=str, default=None, help="HTTP/SOCKS5 proxy URL.")
@click.option("--timeout", type=int, default=90, show_default=True)
@click.option("--retries", type=int, default=2, show_default=True)
@_output_options(["table", "json", "csv", "brief"])
@click.pass_context
def price_cmd(
    ctx, origin, destination,
    selector,
    depart, return_leg, leg, cabin, passengers,
    children, infants_in_seat, infants_on_lap,
    max_stops, include_basic,
    country, proxy,
    timeout, retries,
    output_format, no_color, quiet, verbose,
):
    """Check the current bookable fare for a chosen itinerary.

    \b
    Shorthand syntax:
      swoop price JFK LAX --depart 2026-06-15 DL2300
      swoop price JFK LAX --depart 2026-06-15 DL2300 --return 2026-06-22 DL2301

    \b
    Explicit leg syntax (--leg):
      swoop price --leg JFK LAX 2026-06-15 DL2300 --leg LAX JFK 2026-06-22 DL2301

    \b
    Selector syntax (from search --show-price-commands or -o json):
      swoop price --selector 'swoop:sel:1:...'
    """
    import swoop
    from swoop.exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError

    from .formatters import (
        format_price_brief,
        format_price_csv,
        format_price_json,
        format_price_table,
    )

    configure_verbose_logging(ctx, verbose)
    quiet = resolve_quiet(quiet)
    err = _err_console(no_color)

    has_route_args = any(value is not None for value in (origin, destination))
    has_full_route = all(value is not None for value in (origin, destination))
    has_depart = depart is not None
    has_return = return_leg is not None
    has_shorthand = has_route_args or has_depart or has_return
    has_leg = len(leg) > 0
    has_selector = selector is not None

    if has_selector and (has_shorthand or has_leg):
        err.print("[red]Error: --selector, shorthand args, and --leg are mutually exclusive.[/red]")
        ctx.exit(2)
        return
    if has_leg and has_shorthand:
        err.print("[red]Error: shorthand args and --leg are mutually exclusive.[/red]")
        ctx.exit(2)
        return

    if has_selector:
        if max_stops is not None or cabin != "economy" or passengers != 1 or include_basic:
            err.print("[red]Error: --selector is self-contained and cannot be combined with pricing overrides.[/red]")
            ctx.exit(2)
            return
    elif has_leg and max_stops is not None:
        err.print("[red]Error: --max-stops is not supported with explicit --leg pricing.[/red]")
        ctx.exit(2)
        return

    if has_leg:
        query_legs = [
            {
                "flight_number": leg_flight,
                "origin": leg_origin,
                "destination": leg_dest,
                "date": leg_date,
                "selection": "explicit",
            }
            for leg_origin, leg_dest, leg_date, leg_flight in leg
        ]
    elif not has_selector and not has_shorthand:
        err.print("[red]Error: provide ORIGIN DEST with --depart, or use --leg/--selector.[/red]")
        ctx.exit(2)
        return
    elif has_route_args and not has_full_route:
        err.print("[red]Error: ORIGIN DESTINATION are both required for shorthand pricing.[/red]")
        ctx.exit(2)
        return
    elif has_return and not has_depart:
        err.print("[red]Error: --return requires --depart.[/red]")
        ctx.exit(2)
        return
    elif has_shorthand and not has_full_route:
        err.print("[red]Error: ORIGIN DESTINATION are required for shorthand pricing.[/red]")
        ctx.exit(2)
        return
    elif has_shorthand and not has_depart:
        err.print("[red]Error: --depart is required for shorthand pricing.[/red]")
        ctx.exit(2)
        return

    if has_selector:
        query_legs = None
    elif has_leg:
        for _leg_origin, _leg_dest, leg_date, _leg_flight in leg:
            warning = check_past_date(leg_date)
            if warning:
                err.print(f"[yellow]{warning}[/yellow]")
                break
    else:
        assert depart is not None  # not has_selector and not has_leg => has_depart
        warning = check_past_date(depart[0])
        if warning:
            err.print(f"[yellow]{warning}[/yellow]")
        elif has_return:
            assert return_leg is not None  # has_return narrows return_leg
            warning = check_past_date(return_leg[0])
            if warning:
                err.print(f"[yellow]{warning}[/yellow]")

    spinner = err.status("[bold]Checking price...[/bold]") if (not quiet and output_format == "table") else nullcontext()
    try:
        with spinner:
            if has_selector:
                transport = swoop.TransportConfig(timeout=timeout, retries=retries, country=country, proxy=proxy)
                result = swoop.price_selector(selector, transport=transport)
            elif has_leg:
                pax = swoop.Passengers(
                    adults=passengers,
                    children=children,
                    infants_in_seat=infants_in_seat,
                    infants_on_lap=infants_on_lap,
                )
                transport = swoop.TransportConfig(timeout=timeout, retries=retries, country=country, proxy=proxy)
                result = swoop.price_legs(
                    [
                        swoop.SelectedLeg(
                            flight_number=leg_flight,
                            origin=leg_origin,
                            destination=leg_dest,
                            date=leg_date,
                        )
                        for leg_origin, leg_dest, leg_date, leg_flight in leg
                    ],
                    cabin=cabin,
                    passengers=pax,
                    include_basic_economy=include_basic,
                    transport=transport,
                )
            else:
                assert depart is not None  # has_depart guards this branch
                assert origin is not None and destination is not None
                pax = swoop.Passengers(
                    adults=passengers,
                    children=children,
                    infants_in_seat=infants_in_seat,
                    infants_on_lap=infants_on_lap,
                )
                transport = swoop.TransportConfig(timeout=timeout, retries=retries, country=country, proxy=proxy)
                result = swoop.check_price(
                    depart[1],
                    origin=origin,
                    destination=destination,
                    date=depart[0],
                    return_flight_number=return_leg[1] if return_leg is not None else None,
                    return_date=return_leg[0] if return_leg is not None else None,
                    cabin=cabin,
                    passengers=pax,
                    max_stops=max_stops,
                    include_basic_economy=include_basic,
                    transport=transport,
                )
    except ValueError as e:
        err.print(f"[red]Error: {e}[/red]")
        ctx.exit(2)
    except SwoopRateLimitError:
        err.print("[red]Rate limited. Wait a few minutes. Tip: use --retries 3[/red]")
        ctx.exit(3)
    except SwoopHTTPError as e:
        err.print(f"[red]Google Flights returned HTTP {e.status_code}[/red]")
        ctx.exit(3)
    except SwoopParseError:
        err.print("[red]Could not parse Google Flights response[/red]")
        ctx.exit(4)

    if result is None:
        if has_selector:
            err.print("[yellow]Selected itinerary no longer exists.[/yellow]")
        elif has_leg:
            err.print("[yellow]Selected itinerary was not found for the requested trip.[/yellow]")
        else:
            assert depart is not None  # not has_selector and not has_leg => has_depart
            trip = f"{origin} -> {destination} on {depart[0]}"
            if has_return:
                assert return_leg is not None
                trip += f" / {destination} -> {origin} on {return_leg[0]}"
            err.print(
                f"[yellow]Requested itinerary was not found for {trip}.[/yellow]"
            )
        ctx.exit(1)

    if not has_selector and not has_leg:
        assert depart is not None  # has_depart guards this branch
        query_legs = [
            {
                "flight_number": depart[1],
                "origin": origin,
                "destination": destination,
                "date": depart[0],
                "selection": "explicit",
            }
        ]
        if has_return:
            assert return_leg is not None
            query_legs.append(
                {
                    "flight_number": return_leg[1],
                    "origin": destination,
                    "destination": origin,
                    "date": return_leg[0],
                    "selection": "explicit",
                }
            )
    if query_legs is None:
        query_legs = _query_legs_from_price_result(result)

    if output_format == "json":
        format_price_json(result, query_legs=query_legs)
    elif output_format == "csv":
        format_price_csv(result, query_legs=query_legs)
    elif output_format == "brief":
        format_price_brief(result, query_legs=query_legs)
    else:
        format_price_table(result, query_legs=query_legs, no_color=no_color)
