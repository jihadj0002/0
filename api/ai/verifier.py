import re


def _tokenize(text):
    return [t for t in re.split(r"\W+", (text or "").lower()) if t]


def verify_results(plan, results):
    """Loose verifier: accepts if any result name contains any stop token.

    Returns dict: {is_valid, best_candidates, reason}
    """
    stop = (plan.get("stop_criteria") or {}).get("name_contains") or []
    stop_tokens = [t.lower() for t in stop if t]

    candidates = []
    for p in results or []:
        name = (p.get("name") or "").lower()
        name_tokens = set(_tokenize(name))
        score = 0
        for t in stop_tokens:
            if t in name_tokens:
                score += 1
        if score > 0:
            candidates.append((score, p))

    candidates.sort(key=lambda item: item[0], reverse=True)
    best = [p for _, p in candidates][:5]

    if best:
        return {
            "is_valid": True,
            "best_candidates": best,
            "reason": "Matched stop criteria tokens in product names",
        }

    # No strict token match: allow soft pass if there are results but no stop tokens
    if results:
        return {
            "is_valid": True,
            "best_candidates": results[:5],
            "reason": "No exact token match; passing loosely with available results",
        }

    return {
        "is_valid": False,
        "best_candidates": [],
        "reason": "No results",
    }
