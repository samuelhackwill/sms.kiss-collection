---
name: manual-image-send
description: Send local JPG, JPEG, or PNG files from the filesystem into the current Telegram chat when the operator asks to see frames, stills, keyframes, screenshots, or images manually. Use for ad hoc manual inspection requests, not the automated review dispatcher.
---

# Manual Image Send

Use this skill when the operator asks in natural language to send one or more local images into Telegram for manual inspection.

Examples:

- "send me the keyframes for film 3"
- "show me these jpgs"
- "post the frames from this folder"
- "can you send the still images from `/home/bot/ia-kissing-pipeline/data/frames/...`"

## Required behavior

1. Do not use the review dispatcher for this.
2. Do not reply with only text if the request is clearly asking for images.
3. Use the `message` tool with `action=send`.
4. Send the local image file via `path` or `filePath`.
5. Prefer replying in the current Telegram session. Do not invent another target unless asked.
6. Keep any caption short and operational.

## File handling

- Accept `.jpg`, `.jpeg`, and `.png`.
- Verify the file exists before sending.
- If the operator names a directory, list matching image files and send a small, relevant subset first.
- If the request is ambiguous, ask one short clarifying question about which path or which images.
- If more than 4 images match, send the best 4 first and mention that more are available.

## Message tool pattern

Use one `message` tool call per image when sending local files.

Preferred shape:

```json
{
  "action": "send",
  "path": "/absolute/path/to/frame.jpg",
  "message": "Frame 1"
}
```

Alternative accepted shape:

```json
{
  "action": "send",
  "filePath": "/absolute/path/to/frame.jpg",
  "message": "Frame 1"
}
```

## Safety

- Only send files that already exist locally.
- Do not convert, rewrite, or inline image bytes unless explicitly needed.
- If the file is missing or unreadable, reply with one short error and the path that failed.
