"""Validation shared by RPC, TFS and staged pricing constraints."""

from typing import Optional


def _nonnegative(value: Optional[int], name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_pricing(
    carry_on_bags: int, checked_bags: int, max_price: Optional[int]
) -> None:
    if type(carry_on_bags) is not int or type(checked_bags) is not int:
        raise ValueError("bag counts must be integers")
    _nonnegative(carry_on_bags, "carry_on_bags")
    _nonnegative(checked_bags, "checked_bags")
    _nonnegative(max_price, "max_price")
    if carry_on_bags > 1 or checked_bags > 2:
        raise ValueError(
            "Google supports 0–1 carry-on bags and 0–2 checked bags per passenger"
        )


def _validate_routing(
    max_duration: Optional[int],
    connecting_airports: Optional[list[str]],
    exclude_connecting_airports: Optional[list[str]],
    min_layover_duration: Optional[int],
    max_layover_duration: Optional[int],
) -> None:
    for name, value in [
        ("max_duration", max_duration),
        ("min_layover_duration", min_layover_duration),
        ("max_layover_duration", max_layover_duration),
    ]:
        _nonnegative(value, name)
    if (
        min_layover_duration is not None
        and max_layover_duration is not None
        and min_layover_duration > max_layover_duration
    ):
        raise ValueError("min_layover_duration must not exceed max_layover_duration")
    if connecting_airports and exclude_connecting_airports:
        raise ValueError("choose connecting_airports or exclude_connecting_airports")
    for codes in (connecting_airports, exclude_connecting_airports):
        if codes is not None:
            if not isinstance(codes, list) or any(
                not isinstance(c, str)
                or len(c) != 3
                or not c.isascii()
                or not c.isalpha()
                for c in codes
            ):
                raise ValueError(
                    "connecting airports must be a list of three-letter IATA codes"
                )
