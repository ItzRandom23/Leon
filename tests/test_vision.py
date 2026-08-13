"""Tests for provider-neutral semantic screen understanding."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import jarvis.vision.providers as vision_providers
from jarvis.vision.analyzer import VisionAnalyzer
from jarvis.vision.models import BoundingBox, VisionAnalysis, VisionTarget
from jarvis.vision.providers import (
    OpenAICompatibleVisionProvider,
    OpenAIResponsesVisionProvider,
    VisionProvider,
    VisionProviderError,
    parse_grounded_analysis,
)


class FakeCapture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.exited = False

    @contextmanager
    def temporary_screen(self):  # type: ignore[no-untyped-def]
        try:
            yield Captured(self.path)
        finally:
            self.exited = True


@dataclass(frozen=True)
class Captured:
    path: Path


class FakeVisionProvider(VisionProvider):
    async def analyze_image(self, image_path: Path, prompt: str) -> VisionAnalysis:
        return VisionAnalysis(f"{prompt}: {image_path.name}")


class StubHttpResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.read_limit: int | None = None

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.data[:limit]

    def __enter__(self) -> StubHttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class StubOpener:
    def __init__(self, response: StubHttpResponse) -> None:
        self.response = response

    def open(self, *_args: object, **_kwargs: object) -> StubHttpResponse:
        return self.response


def test_bounding_box_and_unique_grounded_target() -> None:
    box = BoundingBox(10, 20, 30, 50)
    analysis = VisionAnalysis("A save button.", targets=(VisionTarget("Save", 0.9, box),))

    assert box.center == (20, 35)
    assert analysis.find_grounded_target(" save ") == VisionTarget("Save", 0.9, box)


def test_grounded_target_is_not_guessed_when_ambiguous_or_missing_bounds() -> None:
    box = BoundingBox(0, 0, 10, 10)
    analysis = VisionAnalysis(
        "Two save labels.",
        targets=(VisionTarget("save", bounds=box), VisionTarget("Save", bounds=box)),
    )
    assert analysis.find_grounded_target("save") is None
    assert (
        VisionAnalysis("Text", targets=(VisionTarget("save"),)).find_grounded_target("save") is None
    )


def test_vision_models_validate_geometry_and_confidence() -> None:
    with pytest.raises(ValueError):
        BoundingBox(5, 5, 4, 6)
    with pytest.raises(ValueError):
        VisionTarget("target", confidence=1.1)


def test_analyzer_cleans_up_temporary_capture(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    capture = FakeCapture(image)

    result = asyncio.run(VisionAnalyzer(capture, FakeVisionProvider()).analyze_screen("Explain"))

    assert result.description == "Explain: screen.png"
    assert capture.exited is True


def test_openai_compatible_vision_request_is_mocked_and_semantic_only(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"small-image")
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict[str, Any]:
        captured.update(url=url, payload=json.loads(body))
        return {
            "model": "vision-model",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"description": "A text editor is open.", "visible_text": ["Hello"]}
                        )
                    }
                }
            ],
        }

    provider = OpenAICompatibleVisionProvider(model="vision-model", transport=transport)
    result = asyncio.run(provider.analyze_image(image, "What's on screen?"))

    assert result.description == "A text editor is open."
    assert result.visible_text == ("Hello",)
    assert provider.supports_grounding is False
    image_url = captured["payload"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_openai_responses_vision_request_uses_official_image_input(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"small-image")
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict[str, Any]:
        captured.update(url=url, headers=headers, payload=json.loads(body))
        return {
            "model": "vision-model",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "A calculator is open."}],
                }
            ],
        }

    provider = OpenAIResponsesVisionProvider(
        model="vision-model",
        api_key="secret",
        transport=transport,
    )
    result = asyncio.run(provider.analyze_image(image, "What app is open?"))

    assert result.description == "A calculator is open."
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["store"] is False
    image_input = captured["payload"]["input"][0]["content"][1]
    assert image_input["type"] == "input_image"
    assert image_input["image_url"].startswith("data:image/png;base64,")


def test_vision_provider_rejects_missing_large_and_malformed_images(tmp_path: Path) -> None:
    provider = OpenAICompatibleVisionProvider(
        model="m",
        max_image_bytes=2,
        transport=lambda *_args: {"choices": []},
    )
    with pytest.raises(VisionProviderError, match="does not exist"):
        asyncio.run(provider.analyze_image(tmp_path / "missing.png", "describe"))
    image = tmp_path / "large.png"
    image.write_bytes(b"123")
    with pytest.raises(VisionProviderError, match="exceeds"):
        asyncio.run(provider.analyze_image(image, "describe"))


def test_grounded_payload_parser_validates_provider_coordinates() -> None:
    analysis = parse_grounded_analysis(
        {
            "description": "One button",
            "targets": [
                {
                    "label": "Search",
                    "confidence": 0.8,
                    "bounds": {"left": 1, "top": 2, "right": 11, "bottom": 12},
                }
            ],
        },
        model="grounded-model",
    )
    assert analysis.targets[0].bounds == BoundingBox(1, 2, 11, 12)
    with pytest.raises(VisionProviderError):
        parse_grounded_analysis({"description": "bad", "targets": [{"label": "x", "bounds": {}}]})


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda base_url: OpenAICompatibleVisionProvider(
            model="m", base_url=base_url, api_key="secret"
        ),
        lambda base_url: OpenAIResponsesVisionProvider(
            model="m", base_url=base_url, api_key="secret"
        ),
    ],
)
def test_keyed_vision_providers_reject_insecure_or_credential_bearing_urls(
    provider_factory: Any,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        provider_factory("http://provider.example/v1")
    with pytest.raises(ValueError, match="cannot contain credentials"):
        provider_factory("https://user:password@provider.example/v1")
    with pytest.raises(ValueError, match="query or fragment"):
        provider_factory("https://provider.example/v1#secret")


def test_unkeyed_vision_provider_can_use_an_explicit_local_http_endpoint() -> None:
    provider = OpenAICompatibleVisionProvider(
        model="local",
        base_url="http://127.0.0.1:11434/v1",
    )

    assert provider._url == "http://127.0.0.1:11434/v1/chat/completions"


@pytest.mark.parametrize(
    "target",
    ["https://attacker.example/collect", "http://provider.example/downgrade"],
)
def test_vision_transport_blocks_cross_origin_or_downgrade_redirects(target: str) -> None:
    request = urllib.request.Request(
        "https://provider.example/v1/responses",
        data=b"{}",
        headers={"Authorization": "Bearer secret"},
        method="POST",
    )
    handler = vision_providers._SameOriginRedirectHandler()

    with pytest.raises(urllib.error.HTTPError, match="Cross-origin redirect blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, target)

    redirected = handler.redirect_request(request, None, 302, "Found", {}, "/v2")
    assert redirected is not None
    assert redirected.full_url == "https://provider.example/v2"
    assert redirected.get_header("Authorization") == "Bearer secret"


@pytest.mark.parametrize("response_data", [b"123456789", b"\xff"])
def test_vision_transport_bounds_and_strictly_decodes_responses(
    monkeypatch: pytest.MonkeyPatch,
    response_data: bytes,
) -> None:
    response = StubHttpResponse(response_data)
    monkeypatch.setattr(vision_providers, "_MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr(
        vision_providers.urllib.request,
        "build_opener",
        lambda *_handlers: StubOpener(response),
    )

    with pytest.raises(VisionProviderError, match="invalid response"):
        vision_providers._default_transport(
            "https://provider.example/v1",
            {},
            b"{}",
            1.0,
        )

    assert response.read_limit == 9


def test_vision_providers_contain_malformed_container_shapes(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"small-image")
    chat_provider = OpenAICompatibleVisionProvider(
        model="m",
        transport=lambda *_args: {"choices": [{"message": []}]},
    )
    responses_provider = OpenAIResponsesVisionProvider(
        model="m",
        api_key="secret",
        transport=lambda *_args: {"output": [{"type": "message", "content": "not-a-list"}]},
    )

    with pytest.raises(VisionProviderError):
        asyncio.run(chat_provider.analyze_image(image, "describe"))
    with pytest.raises(VisionProviderError):
        asyncio.run(responses_provider.analyze_image(image, "describe"))


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda transport: OpenAICompatibleVisionProvider(model="m", transport=transport),
        lambda transport: OpenAIResponsesVisionProvider(
            model="m", api_key="secret", transport=transport
        ),
    ],
)
def test_vision_transport_exceptions_are_sanitized(
    tmp_path: Path,
    provider_factory: Any,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"small-image")

    def exploding_transport(*_args: object) -> dict[str, Any]:
        raise RuntimeError("Bearer secret leaked through https://provider.example/private")

    provider = provider_factory(exploding_transport)

    with pytest.raises(VisionProviderError) as captured:
        asyncio.run(provider.analyze_image(image, "describe"))

    assert str(captured.value) == "The vision request failed"
    assert captured.value.__cause__ is None
    assert "secret" not in str(captured.value)
