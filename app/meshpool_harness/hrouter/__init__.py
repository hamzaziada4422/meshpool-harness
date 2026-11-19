"""Harness router v2: one decision per task, made with the cheapest signal that is sure.

    signals  (free, deterministic)  ->  language, pasted errors, attachments, repo state, sub-task, size
    specs    (the harness family)   ->  chat, code.{debug,build,refactor,review,test,terminal,explain},
                                        research, data, docs, browser, automation
    route()                          ->  Route: family, sub, language, lane (chat|task|deep), difficulty, the backend
                                        agent (from the benchmark table in harness_router), prompt add-on, extra tools

A small model call is used only when the rules are unsure and a client is given (route(..., client=)). The decision
is logged (outcomes.py) so the routing table can be refit from what actually passed.
"""
from .route import Route, route  # noqa: F401
