"""Substrate CLI subcommands. Wired into Hermes's argparse tree by
``hermes_cli/main.py`` via :func:`substrate.cli.inspect.add_subparser`.

Phase A surface is a single debug command::

    hermes substrate inspect
    hermes substrate inspect streams
    hermes substrate inspect slices --stream NAME --limit 20
    hermes substrate inspect pending
    hermes substrate inspect profiles
"""
