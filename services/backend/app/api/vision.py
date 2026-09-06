"""POST /vision -- answering questions about an image (Phase 7).

The image arrives as an upload from the browser. Nothing captures the screen
server-side, and that is a design choice rather than a shortcut: a backend
that can screenshot the machine it runs on is a capability worth avoiding
when the browser can already ask the user to choose exactly what to share and
show them a recording indicator while it does.

Three rules the endpoint enforces, all of them about not answering for an
image the model did not actually see:

  - The frame is whatever was uploaded with this request. There is no cached
    last-frame, so a stale share cannot answer a fresh question.
  - The declared media type must match what the bytes actually are. A
    mismatch is the classic failure here: it looks like the whole feature is
    broken when it is one wrong string.
  - A provider that cannot accept images raises rather than quietly
    answering from the question text alone.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import get_current_user_id
from app.llm.base import ImagePart, LLMMessage, LLMProviderError
from app.llm.router import LLMRouter, get_llm_router
from app.models.schemas import VisionResponse
from app.personas import build_system_prompt, get_persona

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vision"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Magic bytes, checked against the declared type. Not paranoia: browsers and
#: canvas exports disagree about what they label a JPEG, and a PNG announced
#: as image/jpeg is rejected by the provider with an error that reads like a
#: server fault.
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
]


def detect_media_type(data: bytes) -> str | None:
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            if media_type == "image/webp" and data[8:12] != b"WEBP":
                continue
            return media_type
    return None


_VISION_INSTRUCTION = """\
Answer about what is actually visible in the image. Describe what is there, \
not what is usually there in images like it. If the image is too small, too \
blurry, or cropped such that you cannot tell, say exactly that rather than \
guessing -- a confident wrong reading of someone's screen is worse than \
admitting the image does not show it."""


@router.post("/vision", response_model=VisionResponse)
async def describe(
    question: str = Form(...),
    persona: str = Form("jarvis"),
    image: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user_id),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> VisionResponse:
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="That image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That image is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_IMAGE_BYTES // 1_048_576} MB.",
        )

    media_type = detect_media_type(data)
    if media_type is None:
        raise HTTPException(
            status_code=415,
            detail="That file is not a JPEG, PNG, GIF or WebP image.",
        )

    persona_config = get_persona(persona)
    system_prompt = build_system_prompt(persona_config) + "\n\n" + _VISION_INSTRUCTION

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(
            role="user",
            content=question.strip() or "What am I looking at?",
            images=(ImagePart(data=data, media_type=media_type),),
        ),
    ]

    try:
        response, _ = await llm_router.generate(messages)
    except LLMProviderError as exc:
        # Includes the "this model cannot see" case from the Groq provider,
        # which is a real answer rather than an outage -- so it is reported
        # as one instead of being swallowed into a generic 502 body.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return VisionResponse(answer=response.content, model_used=response.model)
