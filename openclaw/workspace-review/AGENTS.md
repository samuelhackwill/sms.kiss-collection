# Operating Rules

- Use deterministic local CLI commands in `/home/bot/ia-kissing-pipeline`
- Prefer one final reply per operator request
- Keep Telegram replies concise
- Use the latest pending review batch unless the operator specifies otherwise
- If a command is ambiguous, ask one short clarifying question
- If a live Telegram or OpenClaw binding is unavailable, do not fake success
- Do not expose raw stack traces to Telegram
- For review operations, prefer `bash scripts/review_dispatch.sh "<message>"` from this workspace
- For supported operator commands, do not assemble custom pipeline command chains in chat
- For supported operator commands, use only `python3 scripts/ia_kissing_dispatch.py "<message>"` or `bash scripts/review_dispatch.sh "<message>"`
- When the operator asks in natural language to see local frames, keyframes, JPGs, JPEGs, PNGs, or still images in Telegram, use the `manual-image-send` skill
- Treat `scan`, `review`, `status`, `resend`, numeric approvals, `reject N`, `more N`, and `clip N` as dispatcher commands
- Treat `ia-kissing-scraper <subcommand>` as the canonical command form
- On these recognized commands, execute first and reply second
- Never ask onboarding questions in this workspace
- Never ask for a name or identity in this workspace

Useful commands:

- `uv run python -m ia_kissing_pipeline.main status`
- `uv run python -m ia_kissing_pipeline.main review`
- `uv run python -m ia_kissing_pipeline.main send-review-batch --channel telegram --target 1155836070`
- `uv run python -m ia_kissing_pipeline.main review-dispatch "scan" --channel telegram --target 1155836070`
- `uv run python -m ia_kissing_pipeline.main review-dispatch "1 2" --channel telegram --target 1155836070`
- `bash scripts/review_dispatch.sh "scan"`
- `bash scripts/review_dispatch.sh "clip 1"`
