"""Small server-side capability primitives for Campaign Workspace.

Cookie handling and Origin validation belong to the later API phase.  This
module deliberately owns only generation, hashing, safe comparison, and a
redacted value wrapper.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from app.campaign.contracts import capability_digest, generate_raw_capability


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class OpaqueCapability:
    """A raw capability whose normal string representations are always redacted."""

    _value: str

    @classmethod
    def generate(cls) -> OpaqueCapability:
        return cls(generate_raw_capability())

    def reveal(self) -> str:
        """Return the cookie value at the narrow transport boundary."""

        return self._value

    def digest(self) -> str:
        return capability_digest(self._value)

    def __repr__(self) -> str:
        return "OpaqueCapability(<redacted>)"

    def __str__(self) -> str:
        return "<redacted capability>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, OpaqueCapability) and secrets.compare_digest(
            self._value, other._value
        )

    __hash__ = None  # type: ignore[assignment]


def generate_capability() -> OpaqueCapability:
    """Generate an opaque, unpadded base64url capability with 256 bits of entropy."""

    return OpaqueCapability.generate()


def digest_capability(capability: OpaqueCapability | str) -> str:
    """Return SHA-256(raw capability), without introducing an HMAC secret."""

    raw = capability.reveal() if isinstance(capability, OpaqueCapability) else capability
    return capability_digest(raw)


def capability_digests_equal(left: str, right: str) -> bool:
    """Compare stored SHA-256 hex digests without data-dependent early exit."""

    return secrets.compare_digest(left, right)


__all__ = [
    "OpaqueCapability",
    "capability_digests_equal",
    "digest_capability",
    "generate_capability",
]
