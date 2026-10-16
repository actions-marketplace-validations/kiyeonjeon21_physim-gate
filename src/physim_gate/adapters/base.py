"""The adapter plugin contract and discovery.

An adapter turns a simulator's native output into a :class:`~physim_gate.schema.RunResult`.
Third-party packages register adapters under the ``physim_gate.adapters`` entry-point group::

    [project.entry-points."physim_gate.adapters"]
    my-sim = "my_package.adapter:MySimAdapter"

The entry point must resolve to a zero-argument callable (usually a class) that returns an
object satisfying :class:`Adapter`. No change to PhySim Gate itself is needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from physim_gate.schema import RunResult

ENTRY_POINT_GROUP = "physim_gate.adapters"

#: Built-in adapters, available even when package metadata is missing (e.g. a source checkout).
BUILTIN_ADAPTERS: dict[str, str] = {
    "json": "physim_gate.adapters.json_adapter:JsonAdapter",
    "synthetic": "physim_gate.adapters.synthetic:SyntheticAdapter",
    "gymnasium": "physim_gate.adapters.gymnasium:GymnasiumAdapter",
}


class AdapterError(Exception):
    """An adapter could not be found, loaded, or could not convert its input."""


@runtime_checkable
class Adapter(Protocol):
    """What every adapter provides.

    ``load`` receives the path given on the command line and any ``--adapter-option key=value``
    pairs. It must return a validated :class:`RunResult` or raise :class:`AdapterError` (or
    :class:`~physim_gate.schema.SchemaError`) with a message that tells the user what to fix.
    """

    name: str
    description: str

    def load(self, source: Path, options: Mapping[str, str]) -> RunResult: ...


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    target: str
    origin: str
    error: str | None = None


def _entry_points() -> list[EntryPoint]:
    return list(entry_points(group=ENTRY_POINT_GROUP))


def discover() -> dict[str, AdapterInfo]:
    """All known adapters by name. Conflicting registrations are reported as errors."""
    found = {
        name: AdapterInfo(name=name, target=target, origin="built-in")
        for name, target in BUILTIN_ADAPTERS.items()
    }
    for ep in _entry_points():
        origin = ep.dist.name if ep.dist is not None else "unknown distribution"
        existing = found.get(ep.name)
        if existing is None:
            found[ep.name] = AdapterInfo(name=ep.name, target=ep.value, origin=origin)
        elif existing.target != ep.value:
            found[f"{ep.name} ({origin})"] = AdapterInfo(
                name=ep.name,
                target=ep.value,
                origin=origin,
                error=f"name conflicts with the adapter from {existing.origin}; ignored",
            )
    return dict(sorted(found.items()))


def _resolve(target: str) -> object:
    module_name, _, attr = target.partition(":")
    obj: object = import_module(module_name)
    for part in attr.split(".") if attr else []:
        obj = getattr(obj, part)
    return obj


def get_adapter(name: str) -> Adapter:
    """Instantiate the adapter registered as ``name``."""
    info = discover().get(name)
    if info is None or info.error is not None:
        available = ", ".join(n for n, i in discover().items() if i.error is None)
        raise AdapterError(f"unknown adapter {name!r}; available: {available}")
    try:
        factory = _resolve(info.target)
    except Exception as exc:
        raise AdapterError(
            f"adapter {name!r} ({info.target}, from {info.origin}) failed to import: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not callable(factory):
        raise AdapterError(f"adapter {name!r} ({info.target}) is not callable")
    try:
        adapter = factory()
    except Exception as exc:
        raise AdapterError(
            f"adapter {name!r} ({info.target}) failed to initialize: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(adapter, Adapter):
        raise AdapterError(
            f"adapter {name!r} ({info.target}) does not implement the Adapter protocol "
            "(needs 'name', 'description' and 'load(source, options)')"
        )
    return adapter


def parse_options(pairs: list[str]) -> dict[str, str]:
    """Parse repeated ``key=value`` strings."""
    options: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise AdapterError(f"adapter option {pair!r} must look like key=value")
        options[key.strip()] = value.strip()
    return options


def reject_unknown_options(adapter: str, options: Mapping[str, str], known: set[str]) -> None:
    unknown = sorted(set(options) - known)
    if unknown:
        accepted = ", ".join(sorted(known)) or "none"
        raise AdapterError(
            f"adapter {adapter!r} does not accept option(s) {', '.join(unknown)}; "
            f"accepted: {accepted}"
        )
