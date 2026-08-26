"""Campaign Workspace contracts and deterministic runtime implementation.

The public API feature flag remains off until the Phase 3 product boundary is
mounted, while the state, planning, orchestration, and scenario layers are
fully importable and independently testable.
"""

from app.campaign.contracts import CAMPAIGN_RUNTIME_ENABLED

__all__ = ["CAMPAIGN_RUNTIME_ENABLED"]
