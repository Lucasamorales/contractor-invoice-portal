"""Pure business rules.

Nothing in this package may import from ``app.db``, ``app.services`` or any
I/O library. Everything here must be testable without a database
(CLAUDE.md §5). If a rule needs data, the caller passes it in.
"""
