"""Substrate CLI subcommands. Wired into Hermes's argparse tree by
``hermes_cli/main.py`` via :func:`substrate.cli.inspect.add_subparser`.

Phase A surface is a single debug command::

    hermes substrate
    hermes substrate streams
    hermes substrate slices --stream NAME --limit 20
    hermes substrate pending
    hermes substrate profiles
"""
