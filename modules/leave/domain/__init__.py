"""The leave rules, as pure functions.

Nothing in this package does I/O, reads the clock or knows about the web, a
database or the invoice app. Every date a rule depends on is a parameter, so
the same inputs give the same answer next year, which is the whole point of
a calculation somebody will check months later.
"""
