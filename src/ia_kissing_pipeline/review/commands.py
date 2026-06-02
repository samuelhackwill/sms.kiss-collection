from __future__ import annotations


def parse_review_command(text: str) -> dict:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        raise ValueError("Empty review command")

    prefix = "ia-kissing-scraper "
    lowered = cleaned.lower()
    if lowered.startswith(prefix):
        cleaned = cleaned[len(prefix):].strip()
        if not cleaned:
            raise ValueError("ia-kissing-scraper requires a subcommand")

    tokens = cleaned.split()
    if all(token.isdigit() for token in tokens):
        return {"action": "approve", "indices": [int(token) for token in tokens]}

    action = tokens[0].lower()
    if action in {"approve", "reject", "clip", "more"}:
        indices = [int(token) for token in tokens[1:] if token.isdigit()]
        if not indices:
            raise ValueError(f"{action} requires at least one numeric index")
        return {"action": action, "indices": indices}

    if action in {"review", "status", "resend"}:
        return {"action": action}

    raise ValueError(f"Unknown review command: {text}")
