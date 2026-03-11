"""Gemini 自动审查客户端。

当前策略：
- 不直接把整段视频上传给 Gemini
- 先在本地抽取少量关键帧
- 再结合剧本、提示词和参考图顺序做结构化审查
"""

from __future__ import annotations

import base64
import json
import mimetypes
import socket
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request
from pathlib import Path

from app.jimeng_operator.models import GeminiAuditConfig, GeminiAuditResult


class GeminiAuditError(RuntimeError):
    """Gemini 自动审查失败。"""


class GeminiVideoAuditClient:
    """调用 Gemini 多模态接口进行下载前自动审查。"""

    def __init__(self, config: GeminiAuditConfig) -> None:
        self.config = config

    def audit_frames(
        self,
        *,
        shot_id: str,
        storyboard_text: str,
        prompt_main: str,
        prompt_negative: str,
        ref_assets_in_order: list[str],
        frame_paths: list[Path],
        temp_video_path: Path,
        issue_options: list[object],
    ) -> GeminiAuditResult:
        response_text = self._generate_content(
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            frame_paths=frame_paths,
            issue_options=issue_options,
        )
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiAuditError(f"Gemini 返回的 JSON 无法解析: {exc}") from exc

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

        return GeminiAuditResult(
            action=action,
            selected_issue_ids=selected_issue_ids,
            review_summary=review_summary,
            prompt_patch=prompt_patch,
            raw_response_text=response_text,
            model_name=self.config.model_name,
            frame_paths=[str(path) for path in frame_paths],
            temp_video_path=str(temp_video_path),
        )

    def _read_json_response(self, request: urllib.request.Request, *, error_prefix: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=240) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise GeminiAuditError(f"{error_prefix}: HTTP {exc.code} {error_body}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                reason = getattr(exc, "reason", exc)
                raise GeminiAuditError(f"{error_prefix}: {reason}") from exc
        raise GeminiAuditError(f"{error_prefix}: {last_error}")

    def _generate_content(
        self,
        *,
        shot_id: str,
        storyboard_text: str,
        prompt_main: str,
        prompt_negative: str,
        ref_assets_in_order: list[str],
        frame_paths: list[Path],
        issue_options: list[object],
    ) -> str:
        if self._is_openai_compatible_base_url():
            return self._generate_content_openai_compatible(
                shot_id=shot_id,
                storyboard_text=storyboard_text,
                prompt_main=prompt_main,
                prompt_negative=prompt_negative,
                ref_assets_in_order=ref_assets_in_order,
                frame_paths=frame_paths,
                issue_options=issue_options,
            )

        return self._generate_content_gemini_native(
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            frame_paths=frame_paths,
            issue_options=issue_options,
        )

    def _generate_content_gemini_native(
        self,
        *,
        shot_id: str,
        storyboard_text: str,
        prompt_main: str,
        prompt_negative: str,
        ref_assets_in_order: list[str],
        frame_paths: list[Path],
        issue_options: list[object],
    ) -> str:
        prompt_text = self._build_prompt(
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            issue_options=issue_options,
        )
        parts = [{"text": prompt_text}]
        for frame_path in frame_paths:
            mime_type = mimetypes.guess_type(frame_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": encoded,
                    }
                }
            )

        payload = {
            "contents": [{"parts": parts}],
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
        url = (
            f"{self.config.base_url.rstrip('/')}/models/"
            f"{self.config.model_name}:generateContent?key={self.config.api_key}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        body = self._read_json_response(request, error_prefix="Gemini 请求失败")

        try:
            payload = json.loads(body)
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiAuditError(f"Gemini 响应结构异常: {body}") from exc

    def _generate_content_openai_compatible(
        self,
        *,
        shot_id: str,
        storyboard_text: str,
        prompt_main: str,
        prompt_negative: str,
        ref_assets_in_order: list[str],
        frame_paths: list[Path],
        issue_options: list[object],
    ) -> str:
        prompt_text = self._build_prompt(
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            issue_options=issue_options,
        )
        content_parts: list[dict[str, object]] = [{"type": "text", "text": prompt_text}]
        for frame_path in frame_paths:
            mime_type = mimetypes.guess_type(frame_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                    },
                }
            )

        payload = {
            "model": self.config.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ],
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "video_audit_result",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["approve", "revise", "reject"],
                            },
                            "selected_issue_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "review_summary": {"type": "string"},
                            "prompt_patch": {"type": "string"},
                        },
                        "required": ["action", "selected_issue_ids", "review_summary", "prompt_patch"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        url = self._resolve_openai_compatible_url()
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )

        body = self._read_json_response(request, error_prefix="Gemini 兼容接口请求失败")

        try:
            payload = json.loads(body)
            content = payload["choices"][0]["message"]["content"]
            return self._normalize_openai_compatible_content(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiAuditError(f"Gemini 兼容接口响应结构异常: {body}") from exc

    def _is_openai_compatible_base_url(self) -> bool:
        normalized = self.config.base_url.rstrip("/").lower()
        return "/chat/completions" in normalized or normalized.endswith("/v1")

    def _resolve_openai_compatible_url(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        if base_url.lower().endswith("/chat/completions"):
            return base_url
        if base_url.lower().endswith("/v1"):
            return f"{base_url}/chat/completions"

        parsed = urlparse(base_url)
        if parsed.path.lower().endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

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
        raise GeminiAuditError(f"Gemini 兼容接口未返回可解析文本: {content!r}")

    def _build_prompt(
        self,
        *,
        shot_id: str,
        storyboard_text: str,
        prompt_main: str,
        prompt_negative: str,
        ref_assets_in_order: list[str],
        issue_options: list[object],
    ) -> str:
        issue_lines = []
        for issue in issue_options:
            issue_id = getattr(issue, "issue_id", "")
            label = getattr(issue, "label", "")
            patch_hint = getattr(issue, "patch_hint", "")
            issue_lines.append(f"- {issue_id}: {label}；建议补丁：{patch_hint}")

        ref_assets_text = ", ".join(ref_assets_in_order) if ref_assets_in_order else "无"
        issue_text = "\n".join(issue_lines)
        return f"""你是一名视频镜头自动审查员。下面会给你当前镜头的剧本、即梦提示词、参考图顺序，以及 3 张从生成视频中抽出的关键帧。

你的任务是判断：这条视频是否可以作为当前镜头成片下载。

审查原则：
1. 优先检查是否符合当前镜头剧本。
2. 检查角色身份、服装、发型、表情、动作阶段、场景空间是否稳定。
3. 如果问题可以通过“小幅补强约束”修复，请返回 action=revise。
4. 小幅补丁只能补强，不允许重写整段提示词，不允许改变核心剧情、主要角色、主场景和镜头意图。
5. 如果基本符合要求，返回 action=approve。
6. 如果问题明显且不适合只靠小补丁修复，返回 action=reject。
7. 只输出 JSON，不要输出额外解释。

审查重点优先级：
1. 人物一致性
   - 脸部漂移
   - 服装变化
   - 发型变化
2. 场景一致性
   - 背景跳变
   - 光线突变
   - 场景不连续
3. 动作可用性
   - 过度模糊
   - 动作中间态太乱
   - 主体姿态不可读
4. 画面稳定性
   - 背景闪烁
   - 镜头异常抖动
   - 主体不稳定
5. 承接可用性
   - 是否能从当前视频中选出适合下一镜头的 @TransitionFrame

判定要求：
- 如果人物、场景和动作大体正确，但存在轻微漂移、轻微模糊或稳定性问题，优先返回 action=revise。
- 只有当画面整体可用，且后续能够从中抽出适合下一镜头的承接帧时，才返回 action=approve。
- 如果当前视频虽然勉强可看，但明显不利于抽出可用的 @TransitionFrame，也应返回 action=revise 或 reject。

当前镜头：
- shot_id: {shot_id}
- 剧本摘要: {storyboard_text}
- prompt_main: {prompt_main}
- prompt_negative: {prompt_negative or "无"}
- @参考图顺序: {ref_assets_text}

可选问题项如下：
{issue_text}

JSON 字段要求：
- action: approve / revise / reject
- selected_issue_ids: 从上面的问题项中选择，没问题可返回空数组
- review_summary: 用 1 到 3 句中文总结审查结果
- prompt_patch: 只有在 revise 时填写；必须是很短的补充约束；approve / reject 时返回空字符串
"""
