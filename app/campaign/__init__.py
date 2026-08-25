"""Frozen Phase 0 contracts for the future Campaign Workspace.

This package intentionally contains no persistence, reducer, planner, API, or
provider integration. Runtime implementation starts in later phases.
"""

from app.campaign.contracts import CAMPAIGN_RUNTIME_ENABLED

__all__ = ["CAMPAIGN_RUNTIME_ENABLED"]
