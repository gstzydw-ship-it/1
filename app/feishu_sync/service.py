"""飞书素材同步服务。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib import parse

from app.feishu_sync.client import FeishuApiError, FeishuClient
from app.feishu_sync.models import AssetRaw, FeishuSyncConfig, SyncResult

logger = logging.getLogger(__name__)

TYPE_DIR_MAPPING = {
    "人物": "characters",
    "妖兽": "monsters",
    "场景": "scenes",
}

BITABLE_TYPE_FIELD_CANDIDATES = ["类型"]
BITABLE_NAME_FIELD_CANDIDATES = ["名称", "人物或场景名称"]
BITABLE_IMAGE_FIELD_CANDIDATES = ["图片", "附件"]


class FeishuSyncService:
    """飞书素材同步服务。"""

    def __init__(self, client: FeishuClient | None = None) -> None:
        self.client = client

    def sync_assets(self, config: FeishuSyncConfig | None = None) -> SyncResult:
        """执行素材同步。

        当未提供配置时，返回占位结果，以保持现有骨架兼容。
        """

        if config is None:
            return SyncResult(total_rows=0, success_count=0, failed_count=0, assets=[])
        return sync_assets(config=config, client=self.client)


def sync_assets(config: FeishuSyncConfig, client: FeishuClient | None = None) -> SyncResult:
    """从飞书同步素材到本地目录。"""

    resolved_client = client or FeishuClient(
        app_id=config.app_id,
        app_secret=config.app_secret,
        base_url=config.base_url,
    )
    _log_bitable_token_hint(config)
    rows = _load_source_rows(config=config, client=resolved_client)

    assets: list[AssetRaw] = []
    success_count = 0
    failed_count = 0

    for row in rows:
        asset_type, name, image_cell = _parse_row(row)
        if not asset_type or not name:
            continue

        attachments = _extract_attachments(image_cell)
        file_tokens = [item["file_token"] for item in attachments]
        asset = AssetRaw(
            asset_type=asset_type,
            name=name,
            feishu_file_tokens=file_tokens,
            local_files=[],
        )
        assets.append(asset)

        if not file_tokens:
            failed_count += 1
            logger.warning("素材行缺少 file_token，asset_type=%s, name=%s", asset_type, name)
            continue

        target_dir = config.output_dir / _resolve_type_dir(asset_type)
        sanitized_name = _sanitize_filename(name)
        logger.info(
            "开始处理素材，name=%s, type=%s, file_count=%s, target_dir=%s",
            name,
            asset_type,
            len(attachments),
            target_dir,
        )

        try:
            for index, attachment in enumerate(attachments, start=1):
                local_path = _build_local_path(
                    target_dir=target_dir,
                    asset_name=sanitized_name,
                    index=index,
                    attachment_name=attachment.get("name", ""),
                )
                existing_path = _find_existing_local_path(
                    target_dir=target_dir,
                    asset_name=sanitized_name,
                    index=index,
                    preferred_path=local_path,
                )
                if existing_path is not None:
                    logger.info("跳过已存在文件，path=%s", existing_path)
                    asset.local_files.append(str(existing_path))
                    continue

                logger.info(
                    "下载素材文件 %s/%s，name=%s, token=%s, out=%s",
                    index,
                    len(attachments),
                    name,
                    attachment["file_token"],
                    local_path,
                )
                downloaded = resolved_client.download_media(attachment["file_token"], local_path)
                asset.local_files.append(str(downloaded))
            success_count += 1
            logger.info("素材处理完成，name=%s, downloaded=%s", name, len(asset.local_files))
        except Exception as exc:
            failed_count += 1
            logger.exception("下载飞书素材失败，name=%s, error=%s", name, exc)

    return SyncResult(
        total_rows=len(rows),
        success_count=success_count,
        failed_count=failed_count,
        assets=assets,
        manifest_path=str(_write_manifest(config, assets)),
    )


def parse_feishu_link(url: str) -> dict[str, str]:
    """识别并解析飞书 Base/Wiki 链接。"""

    parsed = parse.urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    query = parse.parse_qs(parsed.query)

    result = {
        "link_type": "unknown",
        "app_token": "",
        "wiki_token": "",
        "table_id": query.get("table", [""])[0],
        "view_id": query.get("view", [""])[0],
        "warning": "",
    }

    if "base" in path_parts:
        base_index = path_parts.index("base")
        if base_index + 1 < len(path_parts):
            result["link_type"] = "base"
            result["app_token"] = path_parts[base_index + 1]
        return result

    if "wiki" in path_parts:
        wiki_index = path_parts.index("wiki")
        if wiki_index + 1 < len(path_parts):
            result["link_type"] = "wiki"
            result["wiki_token"] = path_parts[wiki_index + 1]
            result["warning"] = (
                "该链接是 wiki 页面链接，无法仅凭该链接确定 bitable app_token，"
                "不能直接用 wiki_token 调 bitable records API。"
            )
        return result

    result["warning"] = "未识别为 base 或 wiki 链接，无法自动推断 bitable 数据源。"
    return result


def inspect_feishu_link_source(url: str, client: FeishuClient | None = None) -> dict[str, Any]:
    """抓取链接页面源码，并尝试提取 bitable 线索。"""

    parsed = parse_feishu_link(url)
    resolved_client = client or FeishuClient(app_id="", app_secret="")
    html = resolved_client.fetch_public_page_html(url)

    base_tokens = _dedupe(re.findall(r"/base/([A-Za-z0-9]+)", html))
    app_tokens = _dedupe(re.findall(r'"app_token"\s*:\s*"([A-Za-z0-9]+)"', html))
    table_ids = _dedupe(re.findall(r"(tbl[a-zA-Z0-9]+)", html))
    view_ids = _dedupe(re.findall(r"(vew[a-zA-Z0-9]+)", html))

    return {
        "link_info": parsed,
        "html_length": len(html),
        "possible_base_tokens": base_tokens[:10],
        "possible_app_tokens": app_tokens[:10],
        "possible_table_ids": table_ids[:10],
        "possible_view_ids": view_ids[:10],
        "html_preview": _truncate_value(_collapse_whitespace(html), limit=400),
    }


def _load_source_rows(config: FeishuSyncConfig, client: FeishuClient) -> list[dict[str, Any]]:
    """根据配置自动选择 spreadsheet 或 bitable 读取模式。"""

    if config.use_bitable:
        payload = _read_bitable_rows_with_retry(config=config, client=client)
        rows = _extract_bitable_rows(payload)
        _log_bitable_sample(payload)
        return rows

    payload = client.read_multiple_ranges(config.spreadsheet_token, config.ranges)
    return _extract_spreadsheet_rows(payload)


def _read_bitable_rows_with_retry(config: FeishuSyncConfig, client: FeishuClient) -> dict[str, Any]:
    """读取 bitable 记录，并在 view_id 不可用时自动重试。"""

    logger.info("bitable 第一次请求是否带 view_id: %s", bool(config.view_id))
    try:
        return client.read_bitable_records(
            app_token=config.app_token,
            table_id=config.table_id,
            view_id=config.view_id,
        )
    except FeishuApiError as exc:
        _log_bitable_attempt("第一次", exc, used_view_id=bool(config.view_id))
        should_retry_without_view = bool(config.view_id) and exc.api_code == 91402 and exc.api_msg == "NOTEXIST"
        if not should_retry_without_view:
            raise

        logger.info("检测到 91402 NOTEXIST，重试时去掉 view_id: True")
        try:
            return client.read_bitable_records(
                app_token=config.app_token,
                table_id=config.table_id,
                view_id="",
            )
        except FeishuApiError as retry_exc:
            _log_bitable_attempt("重试", retry_exc, used_view_id=False)
            raise


def _log_bitable_attempt(label: str, exc: FeishuApiError, *, used_view_id: bool) -> None:
    logger.error("%s请求是否带 view_id: %s", label, used_view_id)
    logger.error("%s最终 URL: %s", label, exc.url)
    logger.error("%s响应 body: %s", label, exc.response_body or "<empty>")


def _log_bitable_sample(payload: dict[str, Any]) -> None:
    items = (payload.get("data") or {}).get("items") or []
    logger.info("bitable 成功读取记录条数: %s", len(items))
    if not items:
        return

    first_record = items[0]
    logger.info("第一条记录顶层 key 列表: %s", list(first_record.keys()))
    fields = first_record.get("fields")
    if isinstance(fields, dict):
        logger.info("第一条记录 fields 字段名: %s", list(fields.keys()))
        resolved_fields = {
            "type_field": _pick_field_name(fields, BITABLE_TYPE_FIELD_CANDIDATES),
            "name_field": _pick_field_name(fields, BITABLE_NAME_FIELD_CANDIDATES),
            "image_field": _pick_field_name(fields, BITABLE_IMAGE_FIELD_CANDIDATES),
        }
        logger.info("bitable 字段映射结果: %s", resolved_fields)
        sample_preview = {key: _truncate_value(fields.get(key)) for key in list(fields.keys())[:3]}
        logger.info("第一条记录 fields 样本预览: %s", sample_preview)


def _log_bitable_token_hint(config: FeishuSyncConfig) -> None:
    """对当前 bitable token 来源给出更明确的联调提示。"""

    if not config.use_bitable:
        return

    if config.app_token and config.app_token.startswith("wiki_"):
        logger.warning("当前 app_token 看起来像 wiki token，不能直接用于 bitable records API。")
        return

    logger.warning(
        "当前 bitable 模式使用的 app_token=%s。若它来自旧模板链接或 wiki 链接推断，"
        "它可能不对应现有数据源，91402 NOTEXIST 时请优先重新确认真实 bitable app_token。",
        config.app_token,
    )


def _extract_spreadsheet_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {})
    value_ranges = data.get("valueRanges") or data.get("value_ranges") or []
    rows: list[dict[str, Any]] = []
    for item in value_ranges:
        values = item.get("values") or []
        for value_row in values:
            rows.append({"source_type": "spreadsheet", "row": value_row})
    return rows


def _extract_bitable_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {})
    items = data.get("items") or []
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append({"source_type": "bitable", "record": item})
    return rows


def _parse_row(row: dict[str, Any]) -> tuple[str, str, Any]:
    source_type = row.get("source_type")
    if source_type == "bitable":
        fields = (row.get("record") or {}).get("fields") or {}
        type_field = _pick_field_name(fields, BITABLE_TYPE_FIELD_CANDIDATES)
        name_field = _pick_field_name(fields, BITABLE_NAME_FIELD_CANDIDATES)
        image_field = _pick_field_name(fields, BITABLE_IMAGE_FIELD_CANDIDATES)
        return (
            _to_text(fields.get(type_field)).strip(),
            _to_text(fields.get(name_field)).strip(),
            fields.get(image_field),
        )

    value_row = row.get("row") or []
    if len(value_row) < 3:
        return "", "", None
    return _to_text(value_row[0]).strip(), _to_text(value_row[1]).strip(), value_row[2]


def _extract_file_tokens(cell_value: Any) -> list[str]:
    """从图片单元格中提取 file_token。"""

    return [item["file_token"] for item in _extract_attachments(cell_value)]


def _extract_attachments(cell_value: Any) -> list[dict[str, str]]:
    """从附件单元格中提取 token 和文件名。

    当前优先识别飞书附件对象里常见的 `file_token` / `fileToken` 和 `name` 字段。
    """

    attachments: list[dict[str, str]] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            token = value.get("file_token") or value.get("fileToken")
            if isinstance(token, str) and token:
                attachments.append(
                    {
                        "file_token": token,
                        "name": _to_text(value.get("name")).strip(),
                    }
                )
            for nested in value.values():
                _walk(nested)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(cell_value)

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for attachment in attachments:
        token = attachment["file_token"]
        if token not in seen:
            deduped.append(attachment)
            seen.add(token)
    return deduped


def _resolve_type_dir(asset_type: str) -> str:
    return TYPE_DIR_MAPPING.get(asset_type, "others")


def _sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[\\\\/:*?\"<>|\\s]+", "_", name.strip())
    return sanitized.strip("_") or "asset"


def _build_local_path(target_dir: Path, asset_name: str, index: int, attachment_name: str) -> Path:
    if attachment_name:
        attachment_basename = Path(attachment_name).name
        attachment_stem = Path(attachment_basename).stem
        attachment_suffix = Path(attachment_basename).suffix or ".bin"
        final_name = f"{asset_name}_{index}_{_sanitize_filename(attachment_stem)}{attachment_suffix}"
    else:
        final_name = f"{asset_name}_{index}.bin"
    return target_dir / final_name


def _find_existing_local_path(target_dir: Path, asset_name: str, index: int, preferred_path: Path) -> Path | None:
    if preferred_path.exists():
        return preferred_path

    legacy_path = target_dir / f"{asset_name}_{index}.bin"
    if legacy_path.exists():
        return legacy_path

    prefix = f"{asset_name}_{index}_"
    for candidate in target_dir.glob(f"{prefix}*"):
        if candidate.is_file():
            return candidate
    return None


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _truncate_value(value: Any, limit: int = 120) -> str:
    text = _to_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _pick_field_name(fields: dict[str, Any], candidates: list[str]) -> str:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return candidates[0]


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _write_manifest(config: FeishuSyncConfig, assets: list[AssetRaw]) -> Path:
    """将本次同步结果写入本地 manifest。"""

    manifest_path = config.manifest_path
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_mode": "bitable" if config.use_bitable else "spreadsheet",
        "spreadsheet_token": config.spreadsheet_token,
        "app_token": config.app_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "asset_count": len(assets),
        "assets": [
            {
                "asset_type": asset.asset_type,
                "name": asset.name,
                "feishu_file_tokens": asset.feishu_file_tokens,
                "local_files": asset.local_files,
            }
            for asset in assets
        ],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已写入 feishu_sync manifest: %s", manifest_path)
    return manifest_path
