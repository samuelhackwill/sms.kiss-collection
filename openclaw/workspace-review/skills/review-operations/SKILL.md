# Review Operations

Use this skill when a Telegram message is meant to operate the Internet Archive kissing pipeline review flow.

## Intent

Translate short operator messages into one deterministic local command.

Supported messages:

- `scan`
- `review`
- `status`
- `resend`
- `1 2 6`
- `reject 4`
- `more 5`
- `clip 3`

## Required behavior

1. Do not improvise.
2. Do not explain internal tools.
3. Execute exactly one dispatcher command for the operator message.
4. Reply concisely with the result.

## Command

From this workspace, run:

```bash
bash scripts/review_dispatch.sh "<operator_message>"
```

Examples:

```bash
bash scripts/review_dispatch.sh "scan"
bash scripts/review_dispatch.sh "1 2"
bash scripts/review_dispatch.sh "clip 1"
```

## Output policy

- If the dispatcher succeeded, summarize the action in one short reply.
- If the dispatcher already sent media or review items to Telegram, do not repeat them in prose.
- If the dispatcher failed, report one short reason and the safest next action.

