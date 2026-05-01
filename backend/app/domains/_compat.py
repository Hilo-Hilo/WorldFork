from __future__ import annotations

import importlib
import inspect
import sys
import types


def alias_module(public_name: str, target_name: str) -> None:
    """Make an old import path resolve to a moved domain module."""

    module = importlib.import_module(target_name)
    sys.modules[public_name] = module
    if "." not in public_name:
        frame = inspect.currentframe()
        caller_globals = frame.f_back.f_globals if frame and frame.f_back else None
        if caller_globals is not None:
            _copy_module_bindings(module, caller_globals, public_name)
        return
    parent = sys.modules.get(public_name.rsplit(".", 1)[0])
    if parent is not None:
        setattr(parent, public_name.rsplit(".", 1)[1], module)


def _copy_module_bindings(module, target_globals: dict, public_name: str) -> None:
    for name, value in module.__dict__.items():
        if name in {"__name__", "__package__", "__loader__", "__spec__", "__builtins__"}:
            continue
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
            rebound = types.FunctionType(
                value.__code__,
                target_globals,
                name=value.__name__,
                argdefs=value.__defaults__,
                closure=value.__closure__,
            )
            rebound.__kwdefaults__ = value.__kwdefaults__
            rebound.__annotations__ = dict(getattr(value, "__annotations__", {}))
            rebound.__doc__ = value.__doc__
            rebound.__module__ = public_name
            target_globals[name] = rebound
        else:
            target_globals[name] = value
