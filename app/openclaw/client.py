"""OpenClaw 本地客户端、缓存和锚点图生成封装。"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import socket
import time
import uuid
from pathlib import Path
from urllib import error, parse, request

from pydantic import BaseModel

from app.asset_catalog import load_asset_catalog
from app.openclaw.models import (
    AssetPlannerRequest,
    AssetPlannerResponse,
    CatalogAssetSummary,
    PromptComposerRequest,
    PromptComposerResponse,
    SceneAnchorImageRequest,
    SceneAnchorImageResponse,
    SceneAnchorReviewRequest,
    SceneAnchorReviewResponse,
)
from app.openclaw.skills import run_asset_planner_skill, run_prompt_composer_skill


class SceneAnchorImageError(RuntimeError):
    """场景锚点图生成失败。"""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.response_body = response_body


class SceneAnchorReviewError(RuntimeError):
    """场景锚点图审查失败。"""


def _first_non_empty_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def get_scene_anchor_image_api_config() -> tuple[str, str]:
    """Read config for scene anchor image generation.

    Prefer dedicated image-generation vars, then fall back to legacy GEMINI_* vars.
    """

    api_key = _first_non_empty_env("SCENE_ANCHOR_IMAGE_API_KEY", "GEMINI_API_KEY")
    base_url = _first_non_empty_env("SCENE_ANCHOR_IMAGE_BASE_URL", "GEMINI_BASE_URL")
    return api_key, base_url


def get_scene_anchor_review_api_config() -> tuple[str, str, str]:
    """Read config for scene anchor review.

    Prefer dedicated review vars, then fall back to legacy GEMINI_* vars.
    """

    api_key = _first_non_empty_env("SCENE_ANCHOR_REVIEW_API_KEY", "GEMINI_API_KEY")
    base_url = _first_non_empty_env("SCENE_ANCHOR_REVIEW_BASE_URL", "GEMINI_BASE_URL")
    model_name = _first_non_empty_env("SCENE_ANCHOR_REVIEW_MODEL", "GEMINI_MODEL", default="gemini-2.5-flash")
    return api_key, base_url, model_name


def derive_image_edits_endpoint(base_url: str) -> str:
    """从第三方兼容地址归一化出 images/edits 端点。"""

    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        raise SceneAnchorImageError(
            "缺少第三方图片接口地址，请先配置 SCENE_ANCHOR_IMAGE_BASE_URL，或继续使用兼容的 GEMINI_BASE_URL。"
        )

    parsed = parse.urlsplit(cleaned)
    host = parsed.netloc.casefold()
    if "generativelanguage.googleapis.com" in host:
        raise SceneAnchorImageError(
            "当前 scene_anchor_image 只支持第三方兼容的 /v1/images/edits 接口，请不要传官方 Gemini 原生地址。"
        )

    path = parsed.path or ""
    if "/v1/" in path:
        base_path = path.split("/v1/", 1)[0]
    elif path.endswith("/v1"):
        base_path = path[: -len("/v1")]
    else:
        base_path = path
    normalized_path = f"{base_path}/v1/images/edits".replace("//", "/")
    return parse.urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


class OpenClawMockRunner:
    """本地 mock runner。"""

    def asset_planner(self, request: AssetPlannerRequest) -> AssetPlannerResponse:
        return run_asset_planner_skill(request)

    def prompt_composer(self, request: PromptComposerRequest) -> PromptComposerResponse:
        return run_prompt_composer_skill(request)


class OpenClawClient:
    """统一封装技能调用、缓存和场景锚点图生成。"""

    def __init__(self, runner: OpenClawMockRunner | None = None) -> None:
        self.runner = runner or OpenClawMockRunner()
        self._cache: dict[str, BaseModel] = {}

    def run_asset_planner(self, request_model: AssetPlannerRequest) -> AssetPlannerResponse:
        cache_key = self._build_cache_key("asset_planner", request_model)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return AssetPlannerResponse.model_validate(cached.model_dump())

        response = self.runner.asset_planner(request_model)
        self._cache[cache_key] = response
        return response

    def run_prompt_composer(self, request_model: PromptComposerRequest) -> PromptComposerResponse:
        cache_key = self._build_cache_key("prompt_composer", request_model)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return PromptComposerResponse.model_validate(cached.model_dump())

        response = self.runner.prompt_composer(request_model)
        self._cache[cache_key] = response
        return response

    def build_asset_planner_request_from_catalog(
        self,
        *,
        storyboard_id: str,
        storyboard_text: str,
        catalog_path: Path,
        style_summary: str = "",
    ) -> AssetPlannerRequest:
        """从 catalog.json 直接构建 AssetPlannerRequest。"""

        catalog = load_asset_catalog(catalog_path)
        catalog_assets = [
            CatalogAssetSummary(
                asset_id=asset.asset_id,
                type=asset.type,
                display_name=asset.display_name,
                jimeng_ref_name=asset.jimeng_ref_name,
                tags=asset.tags,
            )
            for asset in catalog.assets
        ]
        return AssetPlannerRequest(
            storyboard_id=storyboard_id,
            storyboard_text=storyboard_text,
            style_summary=style_summary,
            catalog_assets=catalog_assets,
        )

    def generate_scene_anchor_image(self, request_model: SceneAnchorImageRequest) -> SceneAnchorImageResponse:
        """调用第三方多图编辑接口，输出换场景首帧锚点图。"""

        api_key, base_url = get_scene_anchor_image_api_config()
        if not api_key:
            raise SceneAnchorImageError(
                "缺少图片生成接口密钥，请先配置 SCENE_ANCHOR_IMAGE_API_KEY，或继续使用兼容的 GEMINI_API_KEY。"
            )

        endpoint = derive_image_edits_endpoint(base_url)
        output_path = Path(request_model.output_path or f"{request_model.shot_id}_scene_anchor.png")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        source_images = [
            Path(path)
            for path in [
                *request_model.scene_reference_paths,
                *request_model.character_reference_paths,
                *request_model.extra_reference_paths,
            ]
            if path
        ]
        if not source_images:
            raise SceneAnchorImageError("至少需要一张人物图或场景图，才能生成 scene_anchor_image。")

        request_source_images = source_images
        reference_board_path = self._build_reference_board(
            character_images=[Path(path) for path in request_model.character_reference_paths if path],
            scene_images=[Path(path) for path in request_model.scene_reference_paths if path],
            extra_images=[Path(path) for path in request_model.extra_reference_paths if path],
            output_path=output_path,
        )
        if reference_board_path is not None:
            request_source_images = [reference_board_path]

        response_payload = self._post_image_edit_request(
            endpoint=endpoint,
            api_key=api_key,
            request_model=request_model,
            source_images=request_source_images,
        )
        image_url = self._write_image_output(response_payload, output_path)

        response_source_images = [str(path) for path in source_images]
        if reference_board_path is not None:
            response_source_images.append(str(reference_board_path))

        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(output_path),
            source_images=response_source_images,
            image_url=image_url,
        )

    def _build_reference_board(
        self,
        *,
        character_images: list[Path],
        scene_images: list[Path],
        extra_images: list[Path],
        output_path: Path,
    ) -> Path | None:
        source_images = [*character_images, *scene_images, *extra_images]
        if len(source_images) <= 1:
            return None

        try:
            from PIL import Image, ImageOps
        except Exception:
            return None

        board_path = output_path.with_name(f"{output_path.stem}_reference_board.png")
        board_path.parent.mkdir(parents=True, exist_ok=True)

        canvas_width = 1600
        canvas_height = 900
        margin = 24
        left_width = 420
        gap = 16
        right_width = canvas_width - left_width - margin * 2 - gap
        card_height = (canvas_height - margin * 2 - gap * (len(source_images) - 1)) // len(source_images)

        canvas = Image.new("RGB", (canvas_width, canvas_height), (247, 244, 236))

        scene_path = scene_images[0] if scene_images else source_images[-1]
        if scene_path:
            with Image.open(scene_path) as scene_image:
                scene_rgb = ImageOps.exif_transpose(scene_image).convert("RGB")
                scene_fill = ImageOps.fit(scene_rgb, (right_width, canvas_height - margin * 2), method=Image.Resampling.LANCZOS)
                canvas.paste(scene_fill, (margin + left_width + gap, margin))

        side_images = [*character_images, *extra_images]
        if not side_images:
            side_images = source_images
        card_height = (canvas_height - margin * 2 - gap * (len(side_images) - 1)) // len(side_images)
        for index, image_path in enumerate(side_images):
            top = margin + index * (card_height + gap)
            with Image.open(image_path) as image:
                rgb = ImageOps.exif_transpose(image).convert("RGB")
                fitted = ImageOps.fit(rgb, (left_width, card_height), method=Image.Resampling.LANCZOS)
                canvas.paste(fitted, (margin, top))

        canvas.save(board_path, format="PNG")
        return board_path

    def review_scene_anchor_image(self, request_model: SceneAnchorReviewRequest) -> SceneAnchorReviewResponse:
        """调用 Gemini 多模态接口审查首帧锚点图。"""

        api_key, base_url, model_name = get_scene_anchor_review_api_config()
        if not api_key:
            raise SceneAnchorReviewError(
                "缺少首帧图审查密钥，请先配置 SCENE_ANCHOR_REVIEW_API_KEY，或继续使用兼容的 GEMINI_API_KEY。"
            )

        image_path = Path(request_model.image_path)
        if not image_path.exists():
            raise SceneAnchorReviewError(f"找不到待审查图片: {image_path}")

        if self._is_openai_compatible_base_url(base_url):
            response_text = self._review_scene_anchor_openai_compatible(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                request_model=request_model,
                image_path=image_path,
            )
        else:
            response_text = self._review_scene_anchor_gemini_native(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                request_model=request_model,
                image_path=image_path,
            )

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise SceneAnchorReviewError(f"场景锚点图审查返回的 JSON 无法解析: {exc}") from exc

        action = str(payload.get("action", "reject")).strip().lower()
        if action not in {"approve", "revise", "reject"}:
            action = "reject"
        selected_issue_ids = [
            str(item).strip()
            for item in payload.get("selected_issue_ids", [])
            if isinstance(item, str) and str(item).strip()
        ]
        review_summary = str(payload.get("review_summary", "")).strip()
        prompt_patch = str(payload.get("prompt_patch", "")).strip()
        revised_prompt = request_model.prompt
        if prompt_patch and prompt_patch not in revised_prompt:
            revised_prompt = f"{request_model.prompt}；补充约束：{prompt_patch}"

        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action=action,
            review_summary=review_summary,
            selected_issue_ids=selected_issue_ids,
            prompt_patch=prompt_patch,
            revised_prompt=revised_prompt if prompt_patch else "",
            model_name=model_name,
        )

    def _post_image_edit_request(
        self,
        *,
        endpoint: str,
        api_key: str,
        request_model: SceneAnchorImageRequest,
        source_images: list[Path],
    ) -> dict[str, object]:
        body, content_type = self._build_multipart_body(request_model, source_images)
        http_request = request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(http_request, timeout=180) as response:
                payload = response.read().decode("utf-8")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise SceneAnchorImageError(
                "第三方图片接口返回错误。",
                url=endpoint,
                status_code=exc.code,
                response_body=response_body,
            ) from exc
        except error.URLError as exc:
            raise SceneAnchorImageError(f"第三方图片接口请求失败: {exc.reason}", url=endpoint) from exc

        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SceneAnchorImageError("第三方图片接口返回了无法解析的 JSON。", url=endpoint, response_body=payload) from exc

        if not isinstance(loaded, dict):
            raise SceneAnchorImageError("第三方图片接口返回结构异常。", url=endpoint, response_body=payload)
        return loaded

    def _review_scene_anchor_gemini_native(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        request_model: SceneAnchorReviewRequest,
        image_path: Path,
    ) -> str:
        prompt_text = self._build_scene_anchor_review_prompt(request_model)
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": encoded,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["approve", "revise", "reject"],
                        },
                        "selected_issue_ids": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                        "review_summary": {"type": "STRING"},
                        "prompt_patch": {"type": "STRING"},
                    },
                    "required": ["action", "selected_issue_ids", "review_summary", "prompt_patch"],
                },
            },
        }
        url = f"{base_url.rstrip('/')}/models/{model_name}:generateContent?key={api_key}"
        http_request = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise SceneAnchorReviewError(f"场景锚点图审查失败: HTTP {exc.code} {response_body}") from exc
        except error.URLError as exc:
            raise SceneAnchorReviewError(f"场景锚点图审查失败: {exc.reason}") from exc

        try:
            payload = json.loads(body)
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise SceneAnchorReviewError(f"场景锚点图审查返回结构异常: {body}") from exc

    def _review_scene_anchor_openai_compatible(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        request_model: SceneAnchorReviewRequest,
        image_path: Path,
    ) -> str:
        prompt_text = self._build_scene_anchor_review_prompt(request_model)
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "scene_anchor_review",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["approve", "revise", "reject"]},
                            "selected_issue_ids": {"type": "array", "items": {"type": "string"}},
                            "review_summary": {"type": "string"},
                            "prompt_patch": {"type": "string"},
                        },
                        "required": ["action", "selected_issue_ids", "review_summary", "prompt_patch"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        url = self._resolve_openai_compatible_url(base_url)
        http_request = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise SceneAnchorReviewError(f"场景锚点图审查失败: HTTP {exc.code} {response_body}") from exc
        except error.URLError as exc:
            raise SceneAnchorReviewError(f"场景锚点图审查失败: {exc.reason}") from exc

        try:
            payload = json.loads(body)
            content = payload["choices"][0]["message"]["content"]
            return self._normalize_openai_compatible_content(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise SceneAnchorReviewError(f"场景锚点图审查返回结构异常: {body}") from exc

    def _build_multipart_body(
        self,
        request_model: SceneAnchorImageRequest,
        source_images: list[Path],
    ) -> tuple[bytes, str]:
        boundary = f"----OpenClawBoundary{uuid.uuid4().hex}"
        body = bytearray()

        def append_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        def append_file(name: str, file_path: Path) -> None:
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{file_path.name}"\r\n'.encode("utf-8")
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            body.extend(file_path.read_bytes())
            body.extend(b"\r\n")

        append_field("model", request_model.model_name)
        append_field("prompt", request_model.prompt)
        append_field("aspect_ratio", request_model.aspect_ratio)
        for file_path in source_images:
            append_file("image[]", file_path)
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        return bytes(body), f"multipart/form-data; boundary={boundary}"

    def _write_image_output(self, response_payload: dict[str, object], output_path: Path) -> str:
        data = response_payload.get("data")
        if not isinstance(data, list) or not data:
            raise SceneAnchorImageError(
                "第三方图片接口未返回图片数据。",
                response_body=json.dumps(response_payload, ensure_ascii=False),
            )

        first_item = data[0]
        if not isinstance(first_item, dict):
            raise SceneAnchorImageError(
                "第三方图片接口返回的图片项结构异常。",
                response_body=json.dumps(response_payload, ensure_ascii=False),
            )

        image_url = str(first_item.get("url") or "").strip()
        if image_url:
            self._download_image_url(image_url, output_path)
            return image_url

        b64_json = str(first_item.get("b64_json") or "").strip()
        if b64_json:
            output_path.write_bytes(base64.b64decode(b64_json))
            return ""

        raise SceneAnchorImageError(
            "第三方图片接口没有返回 url 或 b64_json。",
            response_body=json.dumps(response_payload, ensure_ascii=False),
        )

    def _download_image_url(self, image_url: str, output_path: Path) -> None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        parsed = parse.urlsplit(image_url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        referer = f"{origin}/" if origin else image_url
        common_headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        header_candidates: list[dict[str, str]] = [
            {
                **common_headers,
                "Authorization": f"Bearer {api_key}",
                "Referer": referer,
            },
            {
                **common_headers,
                "Referer": referer,
            },
            {
                **common_headers,
                "Authorization": f"Bearer {api_key}",
            },
        ]
        if not api_key:
            for headers in header_candidates:
                headers.pop("Authorization", None)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        last_status_code: int | None = None
        last_response_body = ""

        for attempt in range(3):
            for headers in header_candidates:
                image_request = request.Request(image_url, headers=headers)
                try:
                    with request.urlopen(image_request, timeout=300) as response:
                        with output_path.open("wb") as output_file:
                            while True:
                                chunk = response.read(64 * 1024)
                                if not chunk:
                                    break
                                output_file.write(chunk)
                    if output_path.stat().st_size == 0:
                        raise SceneAnchorImageError("场景锚点图下载完成但文件为空。", url=image_url)
                    return
                except error.HTTPError as exc:
                    last_error = exc
                    last_status_code = exc.code
                    last_response_body = exc.read().decode("utf-8", errors="replace")
                except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                    last_error = exc
                    last_status_code = None
                    last_response_body = str(exc)
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))

        if isinstance(last_error, error.HTTPError):
            raise SceneAnchorImageError(
                "场景锚点图 URL 下载失败。",
                url=image_url,
                status_code=last_status_code,
                response_body=last_response_body,
            ) from last_error
        raise SceneAnchorImageError(
            f"场景锚点图 URL 下载失败: {last_response_body or last_error}",
            url=image_url,
        ) from last_error

    def _is_openai_compatible_base_url(self, base_url: str) -> bool:
        normalized = base_url.rstrip("/").lower()
        return "/chat/completions" in normalized or normalized.endswith("/v1")

    def _resolve_openai_compatible_url(self, base_url: str) -> str:
        cleaned = base_url.rstrip("/")
        if cleaned.lower().endswith("/chat/completions"):
            return cleaned
        if cleaned.lower().endswith("/v1"):
            return f"{cleaned}/chat/completions"

        parsed = parse.urlsplit(cleaned)
        if parsed.path.lower().endswith("/chat/completions"):
            return cleaned
        return f"{cleaned}/chat/completions"

    def _normalize_openai_compatible_content(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            normalized = "".join(parts).strip()
            if normalized:
                return normalized
        raise SceneAnchorReviewError(f"场景锚点图审查未返回可解析文本: {content!r}")

    def _build_scene_anchor_review_prompt(self, request_model: SceneAnchorReviewRequest) -> str:
        source_image_text = ", ".join(request_model.source_images) if request_model.source_images else "无"
        return f"""你是一名首帧锚点图自动审查员。请审查当前图片是否适合作为短视频的首帧锚点图。

当前信息：
- shot_id: {request_model.shot_id}
- 角色目标: {request_model.character_name}
- 场景目标: {request_model.scene_name}
- 分镜摘要: {request_model.storyboard_text or "无"}
- 出图提示词: {request_model.prompt}
- 参考图来源: {source_image_text}

审查重点：
1. 人物一致性：脸部、发型、服装是否稳定，是否有串脸或明显错乱
2. 场景一致性：背景是否符合目标场景，是否混入错误环境元素
3. 构图可用性：主体是否清楚、构图是否稳定、是否适合作为固定镜头首帧
4. 画面质量：是否有畸形、多手多肢、明显模糊或错乱
5. 首帧适配性：是否可以直接作为后续视频生成的首帧

返回规则：
- approve：图可直接进入视频生成
- revise：图大体可用，但需要补充很短的约束提示词后重出
- reject：图明显错误，不适合只靠小补丁修复

只输出 JSON，不要输出额外解释。
JSON 字段要求：
- action: approve / revise / reject
- selected_issue_ids: 从以下问题项中选择，可为空数组
- review_summary: 1 到 3 句中文总结
- prompt_patch: 仅在 revise 时填写很短的补充约束；approve / reject 时返回空字符串

可选问题项：
- character_drift
- clothing_drift
- hairstyle_drift
- scene_mismatch
- composition_unstable
- deform_glitch
- blur_or_low_readability
- first_frame_unusable
"""

    def _build_cache_key(self, skill_name: str, request_model: BaseModel) -> str:
        payload = {
            "skill_name": skill_name,
            "input": request_model.model_dump(mode="json"),
        }
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return f"{skill_name}:{digest}"

    @property
    def cache_size(self) -> int:
        return len(self._cache)
