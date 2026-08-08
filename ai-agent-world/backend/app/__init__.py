"""AI Agent World — backend package.

A modular, local-first 2D AI-agent simulation server. Subsystems are kept
deliberately separate (see the ``app`` sub-packages): AI providers, memory,
tools, world, workflows, events, recovery and persistence never reach across
each other except through well-defined interfaces.
"""

__version__ = "0.1.0"
