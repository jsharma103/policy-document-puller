"""Carrier registry — name → adapter instance."""
from .base import CarrierAdapter
from .goodcover import GoodcoverAdapter
from .lemonade import LemonadeAdapter
from .statefarm import StateFarmAdapter

_ADAPTERS: dict[str, CarrierAdapter] = {
    a.name: a for a in (LemonadeAdapter(), StateFarmAdapter(), GoodcoverAdapter())
}


def get_adapter(name: str) -> CarrierAdapter | None:
    return _ADAPTERS.get(name)


def carrier_names() -> list[str]:
    return list(_ADAPTERS)
