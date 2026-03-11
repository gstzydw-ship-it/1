"""飞书开放平台客户端。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class FeishuApiError(RuntimeError):
    """飞书 API 调用异常，保留调试上下文。"""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        method: str,
        query_params: dict[str, Any] | None = None,
        response_body: str = "",
        status_code: int | None = None,
        api_code: int | None = None,
        api_msg: str | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.method = method
        self.query_params = query_params or {}
        self.response_body = response_body
        self.status_code = status_code
        self.api_code = api_code
        self.api_msg = api_msg


class FeishuClient:
    """封装飞书最小 HTTP 调用能力。"""

    def __init__(self, app_id: str, app_secret: str, base_url: str = "https://open.feishu.cn") -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = self._normalize_base_url(base_url)
        self._tenant_access_token: str | None = None

    def get_tenant_access_token(self) -> str:
        """获取并缓存 tenant_access_token。"""

        if self._tenant_access_token:
            return self._tenant_access_token

        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8")
        response = self._request_json(
            path="/auth/v3/tenant_access_token/internal",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        token = response.get("tenant_access_token")
        if not token:
            raise RuntimeError("飞书 tenant_access_token 响应缺少 tenant_access_token 字段。")
        self._tenant_access_token = token
        return token

    def read_multiple_ranges(self, spreadsheet_token: str, ranges: list[str]) -> dict[str, Any]:
        """批量读取电子表格多个区间。"""

        return self._request_json(
            path=f"/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get",
            headers=self._auth_headers(),
            query_params={"ranges": ranges},
        )

    def read_bitable_records(
        self,
        app_token: str,
        table_id: str,
        view_id: str = "",
        page_size: int = 500,
    ) -> dict[str, Any]:
        """读取多维表格记录。"""

        query_params: dict[str, Any] = {"page_size": page_size}
        if view_id:
            query_params["view_id"] = view_id
        return self._request_json(
            path=f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            headers=self._auth_headers(),
            query_params=query_params,
        )

    def download_media(self, file_token: str, out_path: Path) -> Path:
        """下载飞书媒体文件到本地路径。"""

        out_path.parent.mkdir(parents=True, exist_ok=True)
        req = request.Request(
            self._build_url(f"/drive/v1/medias/{file_token}/download"),
            headers=self._auth_headers(),
            method="GET",
        )
        with request.urlopen(req) as response:
            content = response.read()
        out_path.write_bytes(content)
        return out_path

    def fetch_public_page_html(self, url: str) -> str:
        """抓取公开页面 HTML，用于调试 wiki/base 页面中的数据源线索。"""

        req = request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
            method="GET",
        )
        try:
            with request.urlopen(req) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise FeishuApiError(
                f"飞书页面抓取失败: status={exc.code}",
                url=url,
                method="GET",
                response_body=response_body,
                status_code=exc.code,
            ) from exc

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_tenant_access_token()}"}

    def _request_json(
        self,
        path: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._build_url(path, query_params=query_params)
        req = request.Request(url, headers=headers or {}, data=data, method=method)
        try:
            with request.urlopen(req) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            parsed = self._safe_load_json(response_body)
            raise FeishuApiError(
                f"飞书 API HTTP 请求失败: status={exc.code}",
                url=url,
                method=method,
                query_params=query_params,
                response_body=response_body,
                status_code=exc.code,
                api_code=parsed.get("code") if isinstance(parsed, dict) else None,
                api_msg=parsed.get("msg") if isinstance(parsed, dict) else None,
            ) from exc

        code = payload.get("code", 0)
        if code not in (0, None):
            raise FeishuApiError(
                f"飞书 API 调用失败: code={code}, msg={payload.get('msg')}",
                url=url,
                method=method,
                query_params=query_params,
                response_body=json.dumps(payload, ensure_ascii=False),
                api_code=code if isinstance(code, int) else None,
                api_msg=payload.get("msg"),
            )
        return payload

    def _build_url(self, path: str, query_params: dict[str, Any] | None = None) -> str:
        normalized_path = "/" + path.lstrip("/")
        url = f"{self.base_url}/open-apis{normalized_path}"
        if not query_params:
            return url

        pairs: list[tuple[str, str]] = []
        for key, value in query_params.items():
            if isinstance(value, list):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        return f"{url}?{parse.urlencode(pairs)}"

    def _normalize_base_url(self, base_url: str) -> str:
        sanitized = (base_url or "https://open.feishu.cn").strip().rstrip("/")
        if sanitized.endswith("/open-apis"):
            sanitized = sanitized[: -len("/open-apis")]
        return sanitized

    def _safe_load_json(self, raw_text: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
