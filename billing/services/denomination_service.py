"""Change-making for the shop's cash denominations.

The shop's note supply is *limited*, so a one-pass greedy algorithm (always
take as many of the largest note as fit, with no backtracking) is not
provably correct: it can commit to a high denomination that strands the
remainder with no valid completion, even though skipping that note in favor
of smaller ones reaches the exact target. For example, with available notes
{50: 1, 20: 3} and a target of 60, greedy takes the 50 first (it fits and is
largest), leaving a remainder of 10 that nothing else can cover -- greedy
reports failure. Yet 60 is achievable using three 20s instead. Supply limits
break the "greedy is optimal for canonical coin systems" guarantee that only
holds for *unlimited* supply, so this uses a bounded dynamic-programming
search instead: it explores every achievable sum rather than committing to
one path, so it's correct by construction, not by observation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChangeResult:
    breakdown: dict[int, int]  # denomination value -> count dispensed
    notes_used: int


def compute_change(amount: int, available: dict[int, int]) -> ChangeResult | None:
    """Find a combination of denominations summing exactly to ``amount``,
    respecting the ``available`` count for each denomination value, using the
    fewest total notes. Returns ``None`` if no exact combination exists.

    ``amount`` must be a non-negative whole number (the shop only deals in
    whole-rupee notes/coins, so change is always requested in whole rupees).
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if amount == 0:
        return ChangeResult(breakdown={}, notes_used=0)

    # dp[x] = (min notes to make x, combination used) or None if unreachable.
    dp: list[dict[int, int] | None] = [None] * (amount + 1)
    dp[0] = {}
    notes_count = [0] * (amount + 1)

    for value, count in available.items():
        if value <= 0 or count <= 0:
            continue
        new_dp = list(dp)
        new_notes_count = list(notes_count)
        for reachable_amount in range(amount + 1):
            if dp[reachable_amount] is None:
                continue
            base_notes = notes_count[reachable_amount]
            for k in range(1, count + 1):
                target = reachable_amount + k * value
                if target > amount:
                    break
                candidate_notes = base_notes + k
                if new_dp[target] is None or candidate_notes < new_notes_count[target]:
                    combo = dict(dp[reachable_amount])
                    combo[value] = combo.get(value, 0) + k
                    new_dp[target] = combo
                    new_notes_count[target] = candidate_notes
        dp = new_dp
        notes_count = new_notes_count

    result = dp[amount]
    if result is None:
        return None
    return ChangeResult(breakdown=result, notes_used=notes_count[amount])
