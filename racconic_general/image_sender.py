from __future__ import annotations

import logging
import time

from mautrix.types import (
    ImageInfo,
    MediaMessageEventContent,
    MessageType,
)

from .types import GenerationResult

log = logging.getLogger("racconic.sender")


async def send_images(client, evt, result: GenerationResult) -> None:
    """Upload images to Matrix and send them as m.image messages."""
    for i, image_data in enumerate(result.images):
        filename = f"racconic_{int(time.time() * 1000)}_{i}.png"
        try:
            uri = await client.upload_media(image_data, mime_type="image/png", filename=filename)
        except Exception:
            log.exception("Failed to upload image %d", i)
            continue

        content = MediaMessageEventContent(
            msgtype=MessageType.IMAGE,
            body=filename,
            url=uri,
            info=ImageInfo(
                mimetype="image/png",
                size=len(image_data),
            ),
        )
        await evt.respond(content)

    # Send the prompt as a follow-up text message (spoilered)
    prompt_display = result.prompt_used
    if len(prompt_display) > 4000:
        prompt_display = prompt_display[:3990] + "... [truncated]"

    info_parts = [f"~**Backend:** {result.backend}"]
    info_parts.append(f"<details><summary>Prompt</summary>\n\n```\n{prompt_display}\n```\n</details>")
    await evt.respond("\n".join(info_parts), allow_html=True)
