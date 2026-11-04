"""Typer command implementations for the ``qasight`` CLI.

Each command module owns its own argument parsing and defers heavy imports
(engine, framework adapters, DB layer) until inside the command body so
``qasight --help`` is fast.
"""
