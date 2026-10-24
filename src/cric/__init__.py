"""CRIC - Cosmic Response & Intelligence Console.

Layers (see ARCHITECTURE.md):
  ingest    real data: CelesTrak orbits and SATCAT, Tavily web pages
  physics   deterministic orbit maths: screening, burns, collision probability, burn options
  research  satellite dossiers: who can manoeuvre, every fact cited
  agent     Nemotron decisions over verified options
  safety    rules enforced in code on every decision
"""

__version__ = "0.1.0"
