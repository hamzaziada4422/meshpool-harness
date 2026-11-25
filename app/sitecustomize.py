"""Runs at every interpreter start when this folder is on sys.path (PYTHONPATH=<app>, as the command, the launchers
and restart() set it): first put back a copy whose update was killed half way (meshpool_recover), then run any other
sitecustomize that this one shadows. Must never raise, whatever state the app folder is in.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import meshpool_recover
    meshpool_recover.recover(_HERE)
except Exception:  # noqa: BLE001
    pass

try:
    import importlib.machinery
    import importlib.util

    def _norm(p):
        return os.path.normcase(os.path.abspath(p or os.getcwd()))

    _others = [p for p in sys.path if isinstance(p, str) and _norm(p) != _norm(_HERE)]
    _spec = importlib.machinery.PathFinder.find_spec("sitecustomize", _others)
    if _spec is not None and _spec.loader is not None and _spec.origin and \
            _norm(os.path.dirname(_spec.origin)) != _norm(_HERE):
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
except Exception:  # noqa: BLE001
    pass
