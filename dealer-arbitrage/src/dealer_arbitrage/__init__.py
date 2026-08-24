"""Dealer Arbitrage Agent.

An assisted-acquisition system for obtaining a manufacturer-, distributor-, or
dealer-sponsored golf cart evaluation unit at the lowest possible LANDED cost,
while establishing legitimate wholesale/dealer relationships.

Non-negotiable invariants, enforced in code rather than in prose:
  * No external message, application, offer acceptance, or purchase leaves this
    system without a recorded human approval (see approval.py, db schema triggers).
  * No claim about the business is emitted that is not present in the profile
    (see profile.py) and no claim about a counterparty is emitted that is not
    backed by a stored source row (see evidence.py).
  * Unknown costs are never treated as zero (see landed_cost.py).
"""

__version__ = "1.0.0"
__all__ = ["paths", "db", "profile", "evidence", "scoring", "strategy",
           "landed_cost", "outreach", "approval", "applications", "responses",
           "negotiation", "followups", "dashboard", "discovery", "security"]
