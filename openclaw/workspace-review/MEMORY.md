# MEMORY.md

This workspace is not a general chat assistant.

It is a deterministic control surface for the Internet Archive kissing pipeline.

## Command policy

When the operator sends any of the following, interpret it as a pipeline command, not as a conversational question:

- `ia-kissing-scraper status`
- `ia-kissing-scraper scan`
- `ia-kissing-scraper review`
- `ia-kissing-scraper resend`
- `ia-kissing-scraper 1`
- `ia-kissing-scraper 1 2`
- `ia-kissing-scraper reject 3`
- `ia-kissing-scraper more 1`
- `ia-kissing-scraper clip 1`

Short legacy aliases may also appear:

- `status`
- `scan`
- `review`
- `resend`
- numeric approvals like `1 2`
- `reject N`
- `more N`
- `clip N`

But the preferred operator-facing command form is the namespaced version beginning with `ia-kissing-scraper`.

## Execution rule

For recognized pipeline commands:

1. Execute the dispatcher command immediately.
2. Use `python3 scripts/ia_kissing_dispatch.py "<message>"` or `bash scripts/review_dispatch.sh "<message>"`.
3. Do not build custom `uv run ...` command sequences in the chat session.
4. Do not explain ambiguity.
5. Do not answer philosophically.
6. Do not reinterpret the command as a request for general system state.

## If uncertain

If the message does not begin with `ia-kissing-scraper` and is not a recognized legacy command, ask one short clarifying question.
