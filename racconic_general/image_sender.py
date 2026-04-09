from __future__ import annotations

import logging
import time
from html import escape

from mautrix.types import (
    Format,
    ImageInfo,
    MediaMessageEventContent,
    MessageType,
)

from .types import GenerationResult

log = logging.getLogger("racconic.sender")


async def send_images(client, evt, result: GenerationResult) -> None:
    """Upload images to Matrix and send them as m.image messages with an inline caption."""
    contents: list[tuple[str, MediaMessageEventContent]] = []
    failed = 0

    for i, image_data in enumerate(result.images):
        filename = f"racconic_{int(time.time() * 1000)}_{i}.png"
        try:
            uri = await client.upload_media(image_data, mime_type="image/png", filename=filename)
            content = MediaMessageEventContent(
                msgtype=MessageType.IMAGE,
                body=filename,
                url=uri,
                info=ImageInfo(
                    mimetype="image/png",
                    size=len(image_data),
                ),
            )
            contents.append((filename, content))
        except Exception:
            log.exception("Failed to upload image %d", i)
            failed += 1

    sent = len(contents)

    if sent == 0:
        await evt.respond(f"~Failed to upload all {failed} image(s). Check logs for details.")
        return

    # Build plain-text caption and attach it to the last image.
    # Matrix clients show body as the caption when a separate filename field is present.
    prompt_display = result.prompt_used
    if len(prompt_display) > 4000:
        prompt_display = prompt_display[:3990] + "... [truncated]"

    plain_parts = [f"~Backend: {result.backend}"]
    html_parts = [f"~<strong>Backend:</strong> {escape(result.backend)}"]
    if failed > 0:
        note = f"({failed} of {sent + failed} image(s) failed to upload)"
        plain_parts.append(note)
        html_parts.append(note)
    plain_parts.append(f"Prompt: {prompt_display}")
    html_parts.append(
        f"<details><summary>Prompt</summary>"
        f"<pre><code>{escape(prompt_display)}</code></pre></details>"
    )

    last_filename, last_content = contents[-1]
    last_content["filename"] = last_filename
    last_content.body = "\n".join(plain_parts)
    last_content["format"] = Format.HTML
    last_content["formatted_body"] = "<br>".join(html_parts)

    for _, content in contents:
        await evt.respond(content)
