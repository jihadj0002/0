import re


def _tokenize(text):
    return [t for t in re.split(r"\W+", (text or "").lower()) if t]


def verify_results(plan, results):
    """Loose verifier: rank by token overlap, allow soft matches.

    Returns dict: {is_valid, best_candidates, reason}
    """
    stop = (plan.get("stop_criteria") or {}).get("name_contains") or []
    stop_tokens = [t.lower() for t in stop if t]
    result_limit = int(plan.get("result_limit", 3) or 3)

    scored = []
    for p in results or []:
        name = (p.get("name") or "").lower()
        name_tokens = set(_tokenize(name))
        score = 0
        for t in stop_tokens:
            if t in name_tokens:
                score += 1
        scored.append((score, p))

    scored.sort(key=lambda item: item[0], reverse=True)
    best = [p for _, p in scored if p][:result_limit]

    if best and (scored[0][0] > 0):
        return {
            "is_valid": True,
            "best_candidates": best,
            "reason": "Matched stop tokens in product names",
        }

    if results:
        return {
            "is_valid": True,
            "best_candidates": results[:result_limit],
            "reason": "Loose match: no token overlap but results available",
        }

    return {
        "is_valid": False,
        "best_candidates": [],
        "reason": "No results",
    }
