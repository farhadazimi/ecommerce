"""Shared building blocks for the Plan B e-commerce services.

Everything that talks to an external managed service (RDS, DCS, OBS, SMN) is hidden
behind a small adapter so that business logic stays cloud-provider independent.
"""

__version__ = "1.0.0"
