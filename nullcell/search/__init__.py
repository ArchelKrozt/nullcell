"""El buscador — §8, three mechanisms and one ranking.

§8 lists them in priority order, corrected in M2 after measuring:

1. **DHT keyword index.** Works from day one, no crawler. Publishing a portal
   files it under each of its own words.
2. **Gossip of indices.** The one that scales: a new cell inherits what the
   network knows in minutes instead of recrawling it.
3. **Local FTS5.** Depth of text, built by a polite crawler over circuits —
   which is slow by construction, so it is the complement, not the engine.
"""
