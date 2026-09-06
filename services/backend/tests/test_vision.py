"""POST /vision -- and the ways it must refuse rather than guess."""
import pytest

from app.api.vision import detect_media_type
from app.llm.base import ImagePart, LLMMessage, LLMProviderError
from app.llm.groq import GroqProvider

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_media_type_is_detected_from_the_bytes():
    """The classic failure here is a declared type that does not match what
    was actually encoded -- it looks like the whole feature is broken when it
    is one wrong string.
    """
    assert detect_media_type(_PNG) == "image/png"
    assert detect_media_type(_JPEG) == "image/jpeg"
    assert detect_media_type(b"GIF89a....") == "image/gif"
    assert detect_media_type(b"RIFF____WEBP") == "image/webp"


def test_a_non_image_is_not_given_a_media_type():
    assert detect_media_type(b"%PDF-1.7") is None
    assert detect_media_type(b"") is None


@pytest.mark.asyncio
async def test_a_text_only_model_refuses_images_rather_than_ignoring_them():
    """Dropping the image and answering from the question alone would produce
    a confident description of something the model never saw.
    """
    provider = GroqProvider(api_key="unused")

    with pytest.raises(LLMProviderError, match="cannot accept images"):
        await provider.agenerate(
            [LLMMessage(role="user", content="what is this", images=(ImagePart(data=_PNG),))]
        )


async def test_a_non_image_upload_is_refused(client):
    response = await client.post(
        "/vision",
        data={"question": "what is this", "persona": "jarvis"},
        files={"image": ("doc.pdf", b"%PDF-1.7 not an image", "application/pdf")},
    )

    assert response.status_code == 415


async def test_an_empty_upload_is_refused(client):
    response = await client.post(
        "/vision",
        data={"question": "what is this"},
        files={"image": ("empty.png", b"", "image/png")},
    )

    assert response.status_code == 400
