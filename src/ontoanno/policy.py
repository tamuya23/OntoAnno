from __future__ import annotations

import copy
from typing import Any


DEFAULT_POLICY = {
    "ontology": True,
    "granularity": "balanced",
    "fallback": "up",
    "review_tie": True,
    "review_nomatch": True,
}

_ALLOWED_GRANULARITY = {"coarse", "balanced", "fine"}
_ALLOWED_FALLBACK = {"up"}


def normalize_policy(raw_policy: Any) -> dict[str, Any] | Any:
    if raw_policy is None:
        return copy.deepcopy(DEFAULT_POLICY)
    if not isinstance(raw_policy, dict):
        return raw_policy

    policy = copy.deepcopy(DEFAULT_POLICY)
    for key in DEFAULT_POLICY:
        if key in raw_policy:
            policy[key] = raw_policy[key]

    if isinstance(policy.get("granularity"), str):
        policy["granularity"] = policy["granularity"].strip().lower()
    if isinstance(policy.get("fallback"), str):
        policy["fallback"] = policy["fallback"].strip().lower()
    return policy


def validate_policy(policy: Any) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(policy, dict):
        return {"errors": ["policy must be a mapping"], "warnings": warnings}

    for key in ("ontology", "review_tie", "review_nomatch"):
        if not isinstance(policy.get(key), bool):
            errors.append(f"policy.{key} must be true or false")

    granularity = policy.get("granularity")
    if granularity not in _ALLOWED_GRANULARITY:
        errors.append(
            "policy.granularity must be one of: "
            + ", ".join(sorted(_ALLOWED_GRANULARITY))
        )

    fallback = policy.get("fallback")
    if fallback not in _ALLOWED_FALLBACK:
        errors.append(
            "policy.fallback must be one of: "
            + ", ".join(sorted(_ALLOWED_FALLBACK))
        )

    return {"errors": errors, "warnings": warnings}
