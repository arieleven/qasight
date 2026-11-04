"""Shared resource pools for parallel test runs: keys, leases and a fair queue.

- :mod:`~qasight.resources.pools`: the ``resources:`` config section.
- :mod:`~qasight.resources.store`: leases in the run-history database.
- :mod:`~qasight.resources.client`: take and hold a key, here or on a team server.
"""
