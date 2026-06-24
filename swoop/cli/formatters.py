"""Output formatters for the CLI — table, JSON, CSV, brief."""

import csv
import functools
import json
import sys
from typing import Optional

from babel.numbers import get_currency_symbol
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..decoder import _flight_summary_repr
from .utils import format_date_display, format_duration, format_time


@functools.lru_cache(maxsize=16)
def _currency_symbol(currency: str) -> str:
    """Cached currency symbol lookup."""
    try:
        return get_currency_symbol(currency)
    except Exception:
        return currency + " "


def _format_price(price: Optional[int], currency: Optional[str] = None) -> str:
    """Format a price with the correct currency symbol.

    Uses the system's default locale for symbol lookup.
    """
    if price is None:
        return "\u2014"  # em-dash
    if not currency:
        return str(price)
    return f"{_currency_symbol(currency)}{price:,}"


def _stdout_console(**kwargs) -> Console:
    """Console that writes to stdout."""
    return Console(**kwargs)


# CSV-injection guard: when Excel/Sheets/LibreOffice open a CSV and see a cell
# beginning with `=`, `+`, `-`, `@`, tab, or CR, they treat it as a formula.
# Google's RPC returns seller/brand/place strings that swoop passes through
# opaquely; if any ever start with one of those characters, opening the CSV
# could execute attacker-controlled content. Prefix with a single quote to
# neuter the cell while keeping it human-readable after manual unquoting.
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    """Neuter CSV formula-injection on a cell value; "" for None/empty.

    Shared by the price/deals/explore CSV formatters.
    """
    if value is None or value == "":
        return ""
    s = str(value)
    if s.startswith(_DANGEROUS_PREFIXES):
        return "'" + s
    return s


# ---------------------------------------------------------------------------
# Search formatters
# ---------------------------------------------------------------------------


def _stops_text(itin) -> Text:
    """Colored stop count with layover details."""
    n = itin.stop_count if itin.stop_count is not None else len(itin.layovers)
    if n == 0:
        text = Text("Nonstop", style="green")
        # Show legroom for nonstop single-segment flights
        if itin.segments and len(itin.segments) == 1:
            lr = itin.segments[0].legroom
            if lr:
                text.append(f"\n{lr}", style="dim")
        return text
    label = "1 stop" if n == 1 else f"{n} stops"
    style = "yellow" if n == 1 else "red"
    text = Text(label, style=style)
    for lay in itin.layovers:
        airport = lay.departure_airport_code or lay.arrival_airport_code
        overnight = " (overnight)" if getattr(lay, "is_overnight", False) else ""
        text.append(f"\n{format_duration(lay.minutes)} {airport}{overnight}", style="dim")
    return text


def _airline_names(itin) -> str:
    """Comma-separated airline names, truncated."""
    names = itin.airline_names or []
    if not names and itin.segments:
        names = list(dict.fromkeys(f.airline_name for f in itin.segments if f.airline_name))
    return ", ".join(names) if names else itin.airline_code or ""


def _flight_summary(itin) -> str:
    """Compact flight number summary for an itinerary."""
    return _flight_summary_repr(itin.segments or [])


def _price_text(price: Optional[int], cheapest: Optional[int], currency: Optional[str] = None) -> Text:
    """Formatted price, cheapest highlighted green."""
    if price is None:
        return Text("—")
    text = Text(_format_price(price, currency), style="bold")
    if cheapest is not None and price == cheapest:
        text.stylize("green")
    return text


def _format_clock(value) -> Optional[str]:
    if not isinstance(value, (list, tuple)) or len(value) < 1:
        return None
    hour = value[0] if value[0] is not None else 0  # Google uses None for midnight
    minute = value[1] if len(value) >= 2 and value[1] is not None else 0
    return f"{int(hour):02d}:{int(minute):02d}"


def _format_date_tuple(value) -> Optional[str]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    year, month, day = value[0], value[1], value[2]
    if not year or not month or not day:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _trip_header(
    *,
    origin: Optional[str],
    destination: Optional[str],
    date: Optional[str],
    return_date: Optional[str],
    legs,
) -> str:
    if legs:
        parts = [f"{leg_origin} -> {leg_destination} ({format_date_display(leg_date)})" for leg_origin, leg_destination, leg_date in legs]
        return " / ".join(parts)
    trip = f"{origin} -> {destination}"
    if return_date:
        trip += f" (return {format_date_display(return_date)})"
    if date:
        trip += f" · {format_date_display(date)}"
    return trip


def _trip_summary(option) -> str:
    return " | ".join(
        _flight_summary(leg.itinerary) if leg.itinerary is not None else f"{leg.origin}->{leg.destination}"
        for leg in option.legs
    )


def _co2_text(option) -> Text:
    """Compact CO2 emissions indicator from the first leg's itinerary."""
    for leg in option.legs:
        itin = leg.itinerary
        if itin and itin.carbon_emissions and itin.carbon_emissions.difference_percent is not None:
            pct = itin.carbon_emissions.difference_percent
            if pct < 0:
                return Text(f"{pct:+d}%", style="green")
            elif pct == 0:
                return Text("avg", style="dim")
            else:
                return Text(f"+{pct}%", style="yellow" if pct <= 20 else "red")
    return Text("\u2014", style="dim")


def _render_search_price_hint(console, price_commands: Optional[list[str]]) -> None:
    """Render the human pricing guidance for search output."""
    console.print(" [dim]Prices shown are shopping totals.[/dim]")
    if price_commands:
        console.print(" [dim]Bookable fare commands for shown rows:[/dim]")
        for index, command in enumerate(price_commands, 1):
            console.print(f" [bold cyan]{index}. {command}[/bold cyan]")
    else:
        console.print(" [dim]Use --show-price-commands for copy/paste `swoop price --selector ...` commands, or -o json to access selectors directly.[/dim]")


def format_search_table(
    result,
    *,
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    adults: int = 1,
    return_date: Optional[str] = None,
    legs=None,
    limit: Optional[int] = None,
    price_commands: Optional[list[str]] = None,
    no_color: bool = False,
) -> None:
    """Render search results as a Rich table to stdout."""
    console = _stdout_console(no_color=no_color)
    all_options = list(result.results)

    if not all_options:
        console.print("[yellow]No flights found.[/yellow]")
        return

    if limit:
        display_options = all_options[:limit]
    else:
        display_options = all_options

    cabin_display = cabin.replace("-", " ").title()
    pax = f"{adults} adult" if adults == 1 else f"{adults} adults"

    console.print()
    console.print(
        f" [bold]Flights: {_trip_header(origin=origin, destination=destination, date=date, return_date=return_date, legs=legs)} · {cabin_display} · {pax}[/bold]"
    )
    console.print()

    prices = [option.price for option in display_options if option.price is not None]
    cheapest = min(prices) if prices else None

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Airline", no_wrap=True)
    table.add_column("Dep", width=5)
    table.add_column("Arr", width=5)
    table.add_column("Dur", width=7)
    table.add_column("Stops", min_width=7)
    table.add_column("Price", justify="right", width=8)
    table.add_column("CO2", justify="right", width=5)

    currency = result.currency
    for i, option in enumerate(display_options, 1):
        is_multi = len(option.legs) > 1
        for leg_idx, leg in enumerate(option.legs):
            is_first = leg_idx == 0
            itin = leg.itinerary
            prefix = f"Leg {leg_idx + 1}: " if is_multi else ""
            if itin is None:
                table.add_row(
                    str(i) if is_first else "",
                    f"{prefix}{leg.origin}->{leg.destination}",
                    "?", "?", "", Text("—"),
                    _price_text(option.price, cheapest, currency) if is_first else Text(""),
                    _co2_text(option) if is_first else Text(""),
                )
                continue
            dep = _format_clock(itin.departure_time)
            if dep is None and itin.segments:
                dep = _format_clock(itin.segments[0].departure_time)
            dep = dep or "?"
            arr = _format_clock(itin.arrival_time)
            if arr is None and itin.segments:
                arr = _format_clock(itin.segments[-1].arrival_time)
            arr = arr or "?"
            has_overnight = any(getattr(seg, "overnight", False) for seg in itin.segments)
            if has_overnight:
                arr += "+1"
            airline_text = Text(f"{prefix}{_airline_names(itin)}")
            airline_text.append(f"\n{_flight_summary(itin)}", style="dim")
            table.add_row(
                str(i) if is_first else "",
                airline_text,
                dep,
                arr,
                format_duration(itin.travel_time) if itin.travel_time else "",
                _stops_text(itin),
                _price_text(option.price, cheapest, currency) if is_first else Text(""),
                _co2_text(option) if is_first else Text(""),
            )

    console.print(table)
    console.print()

    total = len(all_options)
    shown = len(display_options)
    if result.price_range and result.price_range.low is not None:
        pr = result.price_range
        low_fmt = _format_price(pr.low, currency)
        high_fmt = _format_price(pr.high, currency)
        range_str = f"{low_fmt}-{high_fmt}" if pr.high else f"from {low_fmt}"
        console.print(f" [dim]Price range: {range_str} · {shown} of {total} results shown[/dim]")
    else:
        if prices:
            low_fmt = _format_price(min(prices), currency)
            high_fmt = _format_price(max(prices), currency)
            console.print(f" [dim]Price range: {low_fmt}-{high_fmt} · {shown} of {total} results shown[/dim]")
        else:
            console.print(f" [dim]{shown} of {total} results shown[/dim]")

    if not result.is_complete:
        console.print(" [dim]Results truncated. Use --max-results or --time-budget to expand (multi-city only).[/dim]")

    _render_search_price_hint(console, price_commands)
    console.print()


def _itin_to_dict(itin, currency: Optional[str] = None) -> dict:
    """Convert an itinerary to a JSON-serializable dict."""
    segments = []
    for f in itin.segments:
        segment_dict = {
            "airline": f.airline,
            "airline_name": f.airline_name,
            "flight_number": f.flight_number,
            "aircraft": f.aircraft,
            "departure_airport_code": f.departure_airport_code,
            "arrival_airport_code": f.arrival_airport_code,
            "departure_time": _format_clock(f.departure_time),
            "arrival_time": _format_clock(f.arrival_time),
            "departure_date": _format_date_tuple(f.departure_date),
            "arrival_date": _format_date_tuple(f.arrival_date),
            "duration_minutes": f.travel_time,
            "legroom": f.legroom or None,
            "co2_grams": f.co2_grams,
        }
        segments.append(segment_dict)

    layovers = []
    for lay in itin.layovers:
        layovers.append({
            "minutes": lay.minutes,
            "airport": lay.departure_airport_code or lay.arrival_airport_code,
            "is_overnight": lay.is_overnight,
        })

    emissions = None
    if itin.carbon_emissions:
        ce = itin.carbon_emissions
        emissions = {
            "this_flight_grams": ce.this_flight_grams,
            "typical_grams": ce.typical_for_route_grams,
            "difference_percent": ce.difference_percent,
        }

    return {
        "flight_summary": _flight_summary(itin),
        "price": itin.price,
        "currency": currency or itin.currency,
        "airlines": _airline_names(itin),
        "departure_airport_code": itin.departure_airport_code,
        "arrival_airport_code": itin.arrival_airport_code,
        "departure_time": _format_clock(itin.departure_time),
        "arrival_time": _format_clock(itin.arrival_time),
        "duration_minutes": itin.travel_time,
        "stops": itin.stop_count if itin.stop_count is not None else len(itin.layovers),
        "is_budget_carrier": itin.is_budget_carrier,
        "segments": segments,
        "layovers": layovers,
        "carbon_emissions": emissions,
    }


def format_search_json(
    result,
    *,
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    adults: int = 1,
    return_date: Optional[str] = None,
    legs=None,
    limit: Optional[int] = None,
    price_commands: Optional[list[str]] = None,
) -> None:
    """Render search results as JSON to stdout."""
    all_options = list(result.results)
    if limit:
        all_options = all_options[:limit]

    price_range = None
    if result.price_range and result.price_range.low is not None:
        price_range = {
            "low": result.price_range.low,
            "high": result.price_range.high,
        }

    currency = result.currency

    output = {
        "query": {
            "origin": origin,
            "destination": destination,
            "date": date,
            "return_date": return_date,
            "legs": [
                {"origin": leg_origin, "destination": leg_destination, "date": leg_date}
                for leg_origin, leg_destination, leg_date in (legs or [])
            ],
            "cabin": cabin,
            "adults": adults,
        },
        "currency": currency,
        "price_source": "shopping",
        "price_range": price_range,
        "total_results": len(result.results),
        "is_complete": result.is_complete,
        "results": [
            {
                "index": index + 1,
                "selector": option.selector,
                "price": option.price,
                "currency": option.currency or currency,
                "legs": [
                    {
                        "origin": leg.origin,
                        "destination": leg.destination,
                        "date": leg.date,
                        "itinerary": _itin_to_dict(leg.itinerary, currency=option.currency or currency) if leg.itinerary else None,
                    }
                    for leg in option.legs
                ],
            }
            for index, option in enumerate(all_options)
        ],
    }
    print(json.dumps(output, indent=2))


def format_search_csv(
    result,
    *,
    limit: Optional[int] = None,
) -> None:
    """Render search results as CSV to stdout."""
    all_options = list(result.results)
    if limit:
        all_options = all_options[:limit]

    currency = result.currency

    writer = csv.writer(sys.stdout)
    writer.writerow([
        "index", "selector", "price", "currency", "leg_count",
        "duration_minutes", "stops", "departure_time", "arrival_time",
        "airlines", "summary",
    ])
    for i, option in enumerate(all_options, 1):
        first_itin = option.legs[0].itinerary if option.legs else None
        dur_min = first_itin.travel_time if first_itin and first_itin.travel_time else ""
        stops = ""
        dep_time = ""
        arr_time = ""
        airlines = ""
        if first_itin:
            stops = first_itin.stop_count if first_itin.stop_count is not None else len(first_itin.layovers)
            dep_time = _format_clock(first_itin.departure_time) or ""
            arr_time = _format_clock(first_itin.arrival_time) or ""
            airlines = _airline_names(first_itin)
        writer.writerow([
            i,
            option.selector,
            option.price if option.price is not None else "",
            option.currency or currency or "",
            len(option.legs),
            dur_min,
            stops,
            dep_time,
            arr_time,
            airlines,
            _trip_summary(option),
        ])


def format_search_brief(
    result,
    *,
    limit: Optional[int] = None,
    price_commands: Optional[list[str]] = None,
) -> None:
    """Render search results in compact single-line format to stdout."""
    all_options = list(result.results)
    if limit:
        all_options = all_options[:limit]

    currency = result.currency
    for i, option in enumerate(all_options, 1):
        price = _format_price(option.price, option.currency or currency)
        # Pull duration and stops from first leg's itinerary
        dur_str = ""
        stop_str = ""
        first_itin = option.legs[0].itinerary if option.legs else None
        if first_itin:
            dur_str = format_duration(first_itin.travel_time) if first_itin.travel_time else ""
            stops = first_itin.stop_count if first_itin.stop_count is not None else len(first_itin.layovers)
            stop_str = "Nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
        print(f"{i:<3} {price:<12} {dur_str:<8} {stop_str:<10} {_trip_summary(option)}")

    if all_options:
        console = _stdout_console()
        _render_search_price_hint(console, price_commands)


# ---------------------------------------------------------------------------
# Price check formatters
# ---------------------------------------------------------------------------


def _render_resolved_legs(console, resolved_legs) -> None:
    """Render resolved flight details for each leg."""
    leg_labels = ["Outbound", "Return"]
    for idx, leg in enumerate(resolved_legs):
        label = leg_labels[idx] if idx < len(leg_labels) else f"Leg {idx + 1}"
        console.print(f"  [bold]{label}[/bold] · {leg.selection}")

        itin = leg.itinerary
        if itin and itin.segments:
            for fi, flight in enumerate(itin.segments):
                fid = f"{flight.airline} {flight.flight_number}" if flight.airline else str(flight.flight_number or "")
                dep = format_time(flight.departure_time[0], flight.departure_time[1])
                arr = format_time(flight.arrival_time[0], flight.arrival_time[1])
                dur = format_duration(flight.travel_time) if flight.travel_time else ""
                route = f"{flight.departure_airport_code} -> {flight.arrival_airport_code}"
                console.print(f"  {fid}  {route}")
                console.print(f"  Depart: {dep}   Arrive: {arr}   Duration: {dur}")
                details = []
                if flight.aircraft:
                    details.append(f"Aircraft: {flight.aircraft}")
                if flight.legroom:
                    details.append(f"Legroom: {flight.legroom}")
                if details:
                    console.print(f"  [dim]{' · '.join(details)}[/dim]")

                # Show layover after this segment (if not last segment)
                if fi < len(itin.layovers):
                    lay = itin.layovers[fi]
                    airport = lay.departure_airport_code or lay.arrival_airport_code or ""
                    console.print(f"  [dim]  Layover: {format_duration(lay.minutes)} {airport}[/dim]")
        else:
            console.print(f"  {leg.flight_summary}  {leg.origin} -> {leg.destination}")
        console.print()


def _query_trip_title(query_legs) -> str:
    if not query_legs:
        return "Trip"
    return " / ".join(
        f"{leg['origin']} -> {leg['destination']} ({format_date_display(leg['date'])})"
        for leg in query_legs
    )


def format_price_table(
    result,
    *,
    query_legs=None,
    no_color: bool = False,
) -> None:
    """Render a price check result as a Rich table to stdout."""
    console = _stdout_console(no_color=no_color)
    console.print()

    trip_type = f"{len(query_legs or result.resolved_legs or [])}-leg" if (query_legs or result.resolved_legs) else "Trip"
    console.print(f" [bold]{_query_trip_title(query_legs or [])} · {trip_type}[/bold]")
    console.print()

    # Resolved flight details
    if result.resolved_legs:
        _render_resolved_legs(console, result.resolved_legs)

    currency = result.currency
    estimate_tag = " [yellow](estimate)[/yellow]" if result.is_estimate else ""
    console.print(f" [bold green]Price: {_format_price(result.price, currency)}[/bold green]{estimate_tag}")

    if result.fare_brand:
        console.print(f" [dim]Fare: {result.fare_brand}[/dim]")
    if result.is_basic_economy:
        console.print(" [yellow]Basic Economy[/yellow]")

    if result.booking_options:
        console.print()
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("Fare", min_width=16)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Basic", width=6)

        for i, opt in enumerate(result.booking_options, 1):
            price = _format_price(opt.price, currency) if opt.price else "\u2014"
            table.add_row(
                str(i),
                opt.brand_label or opt.brand_code or "\u2014",
                price,
                "Yes" if opt.is_basic else "No",
            )
        console.print(table)

    console.print()


def format_price_json(
    result,
    *,
    query_legs=None,
) -> None:
    """Render a price check result as JSON to stdout."""
    currency = result.currency
    output = {
        "query": {
            "legs": list(query_legs or []),
        },
        "price": result.price,
        "currency": currency,
        "fare_brand": result.fare_brand,
        "is_basic_economy": result.is_basic_economy,
        "is_estimate": result.is_estimate,
        "booking_options": [
            {
                "brand_label": opt.brand_label,
                "brand_code": opt.brand_code,
                "price": opt.price,
                "is_basic": opt.is_basic,
                "fare_family": opt.fare_family,
                "rebookability_signal": opt.rebookability_signal,
                "seller_name": opt.seller_name,
                "seller_code": opt.seller_code,
                "booking_url": opt.booking_url,
                "logo_url": opt.logo_url,
                "is_airline_direct": opt.is_airline_direct,
            }
            for opt in result.booking_options
        ] if result.booking_options else [],
    }
    if result.itinerary:
        output["itinerary"] = _itin_to_dict(result.itinerary, currency=currency)
    if result.resolved_legs:
        output["resolved_legs"] = [
            {
                "flight_summary": leg.flight_summary,
                "origin": leg.origin,
                "destination": leg.destination,
                "date": leg.date,
                "selection": leg.selection,
                "itinerary": _itin_to_dict(leg.itinerary, currency=currency) if leg.itinerary else None,
            }
            for leg in result.resolved_legs
        ]
    print(json.dumps(output, indent=2))


def format_price_brief(
    result,
    *,
    query_legs=None,
) -> None:
    """Render a price check result in compact format to stdout."""
    trip_type = f"{len(query_legs or result.resolved_legs or [])}-leg"
    brand = f" ({result.fare_brand})" if result.fare_brand else ""
    print(f"{_format_price(result.price, result.currency)}{brand} {trip_type}")


def format_price_csv(
    result,
    *,
    query_legs=None,
) -> None:
    """Render a price check result as CSV to stdout.

    One row per booking option, so callers can sort/filter fares
    across sellers in a spreadsheet. The header row always exists
    even when the result has no booking_options (the chosen fare
    is still emitted as a single row).
    """
    # Lowercase so spreadsheet filters like `is_airline_direct == TRUE`
    # match — Python's str(True) is "True", which is parsed as text by
    # most consumers and trips up case-sensitive comparisons.
    def _b(v: bool) -> str:
        return "true" if v else "false"

    _s = _csv_safe
    currency = result.currency or ""
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "price", "currency", "fare_brand", "is_basic_economy", "is_estimate",
        "brand_label", "brand_code", "is_basic",
        "fare_family", "rebookability_signal",
        "seller_name", "seller_code", "is_airline_direct",
        "booking_url", "logo_url",
    ])
    if result.booking_options:
        for opt in result.booking_options:
            writer.writerow([
                opt.price,
                currency,
                _s(result.fare_brand),
                _b(result.is_basic_economy),
                _b(result.is_estimate),
                _s(opt.brand_label),
                _s(opt.brand_code),
                _b(opt.is_basic),
                _s(opt.fare_family),
                _s(opt.rebookability_signal),
                _s(opt.seller_name),
                _s(opt.seller_code),
                _b(opt.is_airline_direct),
                _s(opt.booking_url),
                _s(opt.logo_url),
            ])
    else:
        # No booking options surfaced — emit a single row with the chosen fare
        # so downstream tooling still has data to work with.
        writer.writerow([
            result.price,
            currency,
            _s(result.fare_brand),
            _b(result.is_basic_economy),
            _b(result.is_estimate),
            "", "", "", "", "", "", "", "", "", "",
        ])


# ---------------------------------------------------------------------------
# Deals formatters
# ---------------------------------------------------------------------------


def _deals_stops_text(stops: Optional[int]) -> Text:
    """Colored stop count for deals.

    ``None`` means upstream didn't report stops — render "?" in dim
    rather than collapsing to "Nonstop" (which would mislead the user
    into booking a multi-stop itinerary).
    """
    if stops is None:
        return Text("?", style="dim")
    if stops == 0:
        return Text("Nonstop", style="green")
    label = f"{stops} stop{'s' if stops != 1 else ''}"
    return Text(label, style="yellow" if stops == 1 else "red")


def _deals_date_range(deal) -> str:
    """Format departure-return date range compactly."""
    from datetime import date as _date

    def _fmt(value) -> str:
        # Portable "Mon D" (no leading zero on day). Avoid strftime("%-d")
        # which is POSIX-only and ValueErrors on Windows ucrt.
        return f"{value.strftime('%b')} {value.day}"

    dep = deal.departure_date
    ret = deal.return_date
    if not dep:
        return "\u2014"
    d: Optional[_date]
    try:
        d = _date.fromisoformat(dep)
        dep_str = _fmt(d)
    except (ValueError, TypeError):
        d = None
        dep_str = dep
    if not ret:
        return dep_str
    try:
        r = _date.fromisoformat(ret)
        if d is not None and d.month == r.month:
            ret_str = str(r.day)
        else:
            ret_str = _fmt(r)
    except (ValueError, TypeError):
        ret_str = ret
    return f"{dep_str}\u2013{ret_str}"


def format_deals_table(
    result,
    *,
    cabin: str = "economy",
    no_color: bool = False,
    limit: Optional[int] = None,
) -> None:
    """Render deals as a Rich table to stdout."""
    console = _stdout_console(no_color=no_color)
    deals = list(result.deals[:limit]) if limit else list(result.deals)
    currency = result.currency

    table = Table(title=f"Deals from {result.origin} ({cabin})", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Destination", min_width=20)
    table.add_column("Dates", min_width=12)
    table.add_column("Price", justify="right")
    table.add_column("Typical", justify="right", style="dim")
    table.add_column("Savings", justify="right")
    table.add_column("Airline")
    table.add_column("Stops")
    table.add_column("Duration", justify="right")

    for i, deal in enumerate(deals, 1):
        city = deal.destination_city
        if deal.destination_country:
            city += f", {deal.destination_country}"
        dest_text = f"{city} ({deal.destination})"

        savings = Text("")
        if deal.discount_pct is not None:
            savings = Text(f"{deal.discount_pct}% off", style="bold green")

        dur = format_duration(deal.duration_minutes) if deal.duration_minutes else "\u2014"

        # Prefer human names; fall back to codes; "*" sentinel means multi-airline.
        airline_label = ", ".join(deal.airline_names) or ", ".join(deal.airlines)

        table.add_row(
            str(i),
            dest_text,
            _deals_date_range(deal),
            _format_price(deal.price, currency),
            _format_price(deal.typical_price, currency),
            savings,
            airline_label,
            _deals_stops_text(deal.stops),
            dur,
        )

    console.print(table)


def format_deals_json(
    result,
    *,
    cabin: str = "economy",
    limit: Optional[int] = None,
) -> None:
    """Render deals as JSON to stdout."""
    deals = list(result.deals[:limit]) if limit else list(result.deals)
    output = {
        "query": {"origin": result.origin, "cabin": cabin},
        "currency": result.currency,
        "total_deals": len(result.deals),
        # `partial` surfaces the per-origin-failure signal so CLI-orchestrated
        # cron pipelines (jq-style diffing) can detect partial responses and
        # refuse to overwrite their own baselines. Without this key, a script
        # comparing today's vs yesterday's JSON would treat a failed-origin's
        # deals as legitimately gone.
        "partial": result.partial,
        "deals": [
            {
                "index": i,
                "origin": d.origin,
                "destination": d.destination,
                "destination_city": d.destination_city,
                "destination_country": d.destination_country,
                "departure_date": d.departure_date,
                "return_date": d.return_date,
                "price": d.price,
                "typical_price": d.typical_price,
                "discount_pct": d.discount_pct,
                "airlines": d.airlines,
                "airline_names": d.airline_names,
                "duration_minutes": d.duration_minutes,
                "stops": d.stops,
                "trip_days": d.trip_days,
                "destination_region": d.destination_region.value if d.destination_region else None,
                "fingerprint": d.fingerprint,
                "booking_url": d.booking_url,
                # Query context: downstream consumers need these to
                # reproduce search_deal(d) faithfully (same cabin /
                # passenger count / basic-economy inclusion).
                "query_cabin": d.query_cabin,
                "query_adults": d.query_adults,
                "query_include_basic_economy": d.query_include_basic_economy,
            }
            for i, d in enumerate(deals, 1)
        ],
    }
    print(json.dumps(output, indent=2))


def format_deals_csv(
    result,
    *,
    limit: Optional[int] = None,
) -> None:
    """Render deals as CSV to stdout."""
    deals = list(result.deals[:limit]) if limit else list(result.deals)
    _s = _csv_safe
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "origin", "destination", "destination_city", "destination_country",
        "destination_region",
        "departure_date", "return_date", "price", "typical_price",
        "discount_pct", "airlines", "airline_names",
        "duration_minutes", "stops", "trip_days", "fingerprint",
    ])
    for d in deals:
        writer.writerow([
            _s(d.origin), _s(d.destination), _s(d.destination_city), _s(d.destination_country),
            d.destination_region.value if d.destination_region else "",
            _s(d.departure_date), _s(d.return_date or ""), d.price,
            d.typical_price or "", d.discount_pct or "",
            _s("|".join(d.airlines)), _s("|".join(d.airline_names)),
            d.duration_minutes or "", d.stops, d.trip_days or "", d.fingerprint,
        ])


def format_deals_brief(
    result,
    *,
    limit: Optional[int] = None,
) -> None:
    """Render deals in compact one-line-per-deal format to stdout."""
    deals = list(result.deals[:limit]) if limit else list(result.deals)
    currency = result.currency
    for i, d in enumerate(deals, 1):
        price_str = _format_price(d.price, currency)
        savings = f"  {d.discount_pct}% off" if d.discount_pct else ""
        if d.stops is None:
            stops = "?"
        elif d.stops == 0:
            stops = "Nonstop"
        else:
            stops = f"{d.stops} stop{'s' if d.stops != 1 else ''}"
        dates = _deals_date_range(d)
        airline_label = ", ".join(d.airline_names) or ", ".join(d.airlines)
        print(f"{i:3d}  {d.destination:4s}  {d.destination_city:<25s} {price_str:>10s}{savings:>10s}  {dates:>12s}  {stops:>8s}  {airline_label}")


# ---------------------------------------------------------------------------
# Explore formatters
# ---------------------------------------------------------------------------


def _explore_date_range(d) -> str:
    """Compact suggested-date range; just the departure for one-way."""
    if not d.departure_date:
        return "—"
    dep = format_date_display(d.departure_date)
    if d.return_date:
        return f"{dep} – {format_date_display(d.return_date)}"
    return dep


def format_explore_table(
    result,
    *,
    cabin: str = "economy",
    no_color: bool = False,
    limit: Optional[int] = None,
) -> None:
    """Render explore destinations as a Rich table to stdout."""
    console = _stdout_console(no_color=no_color)
    dests = list(result.destinations[:limit]) if limit else list(result.destinations)

    table = Table(title=f"Explore from {result.origin} ({cabin})", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Destination", min_width=20)
    table.add_column("Airport", width=7)
    table.add_column("Dates", min_width=12)
    table.add_column("Drive", justify="right")

    for i, d in enumerate(dests, 1):
        name = d.destination_name
        if d.destination_country:
            name += f", {d.destination_country}"
        drive = format_duration(d.drive_minutes) if d.drive_minutes is not None else "—"
        table.add_row(str(i), name, d.destination or "—", _explore_date_range(d), drive)

    console.print(table)


def format_explore_json(
    result,
    *,
    cabin: str = "economy",
    limit: Optional[int] = None,
) -> None:
    """Render explore destinations as JSON to stdout."""
    dests = list(result.destinations[:limit]) if limit else list(result.destinations)
    output = {
        "query": {"origin": result.origin, "cabin": cabin},
        "origin": {
            "code": result.origin,
            "name": result.origin_name,
            "place_id": result.origin_place_id,
            "latitude": result.origin_latitude,
            "longitude": result.origin_longitude,
        },
        "total_destinations": len(result.destinations),
        "destinations": [
            {
                "index": i,
                "destination": d.destination,
                "destination_name": d.destination_name,
                "destination_country": d.destination_country,
                "place_id": d.place_id,
                "latitude": d.latitude,
                "longitude": d.longitude,
                "departure_date": d.departure_date,
                "return_date": d.return_date,
                "drive_minutes": d.drive_minutes,
                "image_url": d.image_url,
                "secondary_image_url": d.secondary_image_url,
            }
            for i, d in enumerate(dests, 1)
        ],
    }
    print(json.dumps(output, indent=2))


def format_explore_csv(
    result,
    *,
    limit: Optional[int] = None,
) -> None:
    """Render explore destinations as CSV to stdout."""
    dests = list(result.destinations[:limit]) if limit else list(result.destinations)
    _s = _csv_safe
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "origin", "destination", "destination_name", "destination_country",
        "place_id", "latitude", "longitude",
        "departure_date", "return_date", "drive_minutes",
    ])
    for d in dests:
        writer.writerow([
            _s(d.origin), _s(d.destination or ""), _s(d.destination_name),
            _s(d.destination_country), _s(d.place_id),
            d.latitude if d.latitude is not None else "",
            d.longitude if d.longitude is not None else "",
            _s(d.departure_date or ""), _s(d.return_date or ""),
            d.drive_minutes if d.drive_minutes is not None else "",
        ])


def format_explore_brief(
    result,
    *,
    limit: Optional[int] = None,
) -> None:
    """Render explore destinations in compact one-line-per-destination format."""
    dests = list(result.destinations[:limit]) if limit else list(result.destinations)
    for i, d in enumerate(dests, 1):
        dur = format_duration(d.drive_minutes) if d.drive_minutes is not None else ""
        dates = _explore_date_range(d)
        code = d.destination or "???"
        print(f"{i:3d}  {code:4s}  {d.destination_name:<25s}  {dates:>16s}  {dur}")
