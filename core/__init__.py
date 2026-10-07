"""Shared infrastructure for the company hub: the only code modules may share.

Nothing in here imports a module. Pieces move here from ``modules/invoices``
one at a time, each only once it depends on nothing but standard library and
other ``core`` code. What is still waiting, and why, is in
docs/decisions/0001-modular-hub.md.
"""
