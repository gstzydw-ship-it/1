from __future__ import annotations

import argparse
import base64
import concurrent.futures
import gzip
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.asset_catalog.catalog import build_asset_catalog
from app.feishu_sync.service import parse_feishu_link


DEFAULT_PROFILE_DIR = PROJECT_ROOT / ".runtime" / "feishu-browser"
DEFAULT_ASSETS_DIR = PROJECT_ROOT / "assets"

PRIMARY_TYPE_DIRS = {
    "人物": "characters",
    "场景": "scenes",
    "妖兽": "monsters",
}

EXTRA_TYPE_DIRS = {
    "宠物": Path("extras") / "pets",
    "宝物": Path("extras") / "treasures",
    "道具": Path("extras") / "props",
}

KNOWN_TYPES = set(PRIMARY_TYPE_DIRS) | set(EXTRA_TYPE_DIRS)
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _decode_payload(blob: str) -> Any:
    return json.loads(gzip.decompress(base64.b64decode(blob)).decode("utf-8"))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _sanitize_filename(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", _normalize_text(name))
    cleaned = cleaned.rstrip(". ")
    return cleaned or "UNKNOWN"


def _cell_text(cell: dict[str, Any] | None) -> str:
    if not cell:
        return ""
    value = cell.get("value")
    if isinstance(value, list) and value and isinstance(value[0], dict) and "text" in value[0]:
        return _normalize_text("".join(part.get("text", "") for part in value if isinstance(part, dict)))
    if isinstance(value, str):
        return _normalize_text(value)
    return ""


def _infer_field_ids(record_map: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    text_values: dict[str, list[str]] = defaultdict(list)
    attachment_counts: Counter[str] = Counter()

    for record in record_map.values():
        for field_id, cell in record.items():
            value = cell.get("value")
            if value in (None, [], ""):
                continue
            if isinstance(value, list) and value and isinstance(value[0], dict) and "attachmentToken" in value[0]:
                attachment_counts[field_id] += 1
                continue
            text = _cell_text(cell)
            if text:
                text_values[field_id].append(text)

    attachment_field_id = attachment_counts.most_common(1)[0][0]

    type_field_id = ""
    for field_id, values in text_values.items():
        unique_values = set(values)
        if unique_values & KNOWN_TYPES:
            type_field_id = field_id
            break
    if not type_field_id:
        raise RuntimeError("Could not infer the Feishu type field.")

    name_candidates: list[tuple[int, int, int, str]] = []
    for field_id, values in text_values.items():
        if field_id == type_field_id:
            continue
        filtered = [value for value in values if value and len(value) <= 80]
        if not filtered:
            continue
        name_candidates.append((len(filtered), len(set(filtered)), -max(len(v) for v in filtered), field_id))
    if not name_candidates:
        raise RuntimeError("Could not infer the Feishu name field.")

    name_field_id = sorted(name_candidates, reverse=True)[0][3]
    return name_field_id, type_field_id, attachment_field_id


def _request_json(context: BrowserContext, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request_ctx = context.request
    if method == "GET":
        response = request_ctx.get(url)
    elif method == "POST":
        response = request_ctx.post(url, data=json.dumps(payload or {}, ensure_ascii=False), headers={"content-type": "application/json"})
    else:
        raise ValueError(f"Unsupported method: {method}")

    if not response.ok:
        raise RuntimeError(f"Request failed: {response.status} {url}")
    return response.json()


def _load_base_data(context: BrowserContext, *, app_token: str, table_id: str, view_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cache_info_url = f"https://qv88q3mutni.feishu.cn/space/api/bitable/{app_token}/ssr_cache_info?table={table_id}"
    clientvars_url = (
        f"https://qv88q3mutni.feishu.cn/space/api/v1/bitable/{app_token}/clientvars"
        f"?tableID={table_id}&viewID={view_id}&recordLimit=200&ondemandLimit=200"
        "&needBase=true&viewLazyLoad=true&ondemandVer=2&openType=0&noMissCS=true"
        "&optimizationFlag=1&removeFmlExtra=true"
    )
    cache_info = _request_json(context, "GET", cache_info_url)
    table_rev = int(cache_info["data"]["SSRCacheInfo"]["tableRev"])
    records_url = (
        f"https://qv88q3mutni.feishu.cn/space/api/v1/bitable/{app_token}/records"
        f"?tableId={table_id}&viewId={view_id}&tableRev={table_rev}&depRev=%7B%7D&viewLazyLoad=true"
        f"&offset=0&limit=3000&tableID={table_id}&viewID={view_id}&removeFmlExtra=true"
    )

    clientvars_raw = _request_json(context, "GET", clientvars_url)
    records_raw = _request_json(context, "GET", records_url)
    return _decode_payload(clientvars_raw["data"]["base"]), _decode_payload(records_raw["data"]["records"])


def _resolve_output_dir(asset_type: str, assets_dir: Path) -> Path | None:
    if asset_type in PRIMARY_TYPE_DIRS:
        return assets_dir / PRIMARY_TYPE_DIRS[asset_type]
    if asset_type in EXTRA_TYPE_DIRS:
        return assets_dir / EXTRA_TYPE_DIRS[asset_type]
    return None


def _download_file(download_url: str, target_path: Path, cookie_header: str) -> None:
    request = urllib.request.Request(
        download_url,
        headers={
            "Cookie": cookie_header,
            "Referer": "https://qv88q3mutni.feishu.cn/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        target_path.write_bytes(response.read())


def restore_assets(
    *,
    url: str,
    profile_dir: Path,
    assets_dir: Path,
    headless: bool,
) -> dict[str, Any]:
    link_info = parse_feishu_link(url)
    app_token = link_info.get("app_token", "")
    table_id = link_info.get("table_id", "")
    view_id = link_info.get("view_id", "")
    if not app_token or not table_id:
        raise ValueError("The Feishu link is missing app_token or table_id.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=headless,
            viewport={"width": 1440, "height": 960},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(8_000)
            _, records_data = _load_base_data(context, app_token=app_token, table_id=table_id, view_id=view_id)

            record_map: dict[str, dict[str, Any]] = records_data["recordMap"]
            name_field_id, type_field_id, attachment_field_id = _infer_field_ids(record_map)
            restore_items: list[dict[str, Any]] = []
            skipped_records = 0
            skipped_by_type: Counter[str] = Counter()

            for record_id, record in record_map.items():
                asset_type = _cell_text(record.get(type_field_id))
                attachments = (record.get(attachment_field_id) or {}).get("value") or []
                if not attachments:
                    skipped_records += 1
                    skipped_by_type[asset_type or "<empty>"] += 1
                    continue

                output_dir = _resolve_output_dir(asset_type, assets_dir)
                if output_dir is None:
                    skipped_records += 1
                    skipped_by_type[asset_type or "<empty>"] += 1
                    continue

                display_name = _cell_text(record.get(name_field_id)) or record_id
                for index, attachment in enumerate(attachments, start=1):
                    restore_items.append(
                        {
                            "record_id": record_id,
                            "asset_type": asset_type,
                            "display_name": display_name,
                            "output_dir": output_dir,
                            "token": attachment["attachmentToken"],
                            "width": int(attachment.get("width") or 0),
                            "height": int(attachment.get("height") or 0),
                            "mime_type": attachment.get("mimeType", ""),
                            "source_name": attachment.get("name", ""),
                            "index": index,
                        }
                    )

            downloaded = 0
            skipped_existing = 0
            failed_downloads = 0
            restored_by_type: Counter[str] = Counter()
            pending_items: list[dict[str, Any]] = []
            for item in restore_items:
                suffix = Path(item["source_name"]).suffix or _suffix_from_mime_type(item["mime_type"])
                filename = f"{_sanitize_filename(item['display_name'])}_{item['index']}{suffix}"
                target_path = item["output_dir"] / filename
                item["target_path"] = target_path
                if target_path.exists() and target_path.stat().st_size > 0:
                    skipped_existing += 1
                    restored_by_type[item["asset_type"]] += 1
                    continue
                pending_items.append(item)

            cookies = context.cookies()
            cookie_header = "; ".join(
                f"{cookie['name']}={cookie['value']}"
                for cookie in cookies
                if "feishu.cn" in cookie["domain"]
            )

            for item in pending_items:
                item["output_dir"].mkdir(parents=True, exist_ok=True)

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                future_to_item = {
                    executor.submit(
                        _download_file,
                        f"https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/{item['token']}",
                        item["target_path"],
                        cookie_header,
                    ): item
                    for item in pending_items
                }
                for future in concurrent.futures.as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        future.result()
                        downloaded += 1
                        restored_by_type[item["asset_type"]] += 1
                    except Exception:
                        failed_downloads += 1
        finally:
            context.close()

    build_result = build_asset_catalog(assets_dir)
    return {
        "records_total": len(record_map),
        "attachments_total": len(restore_items),
        "downloaded": downloaded,
        "skipped_existing": skipped_existing,
        "failed_downloads": failed_downloads,
        "skipped_records": skipped_records,
        "restored_by_type": dict(restored_by_type),
        "skipped_by_type": dict(skipped_by_type),
        "catalog_total_assets": build_result.total_assets,
        "catalog_type_counts": build_result.type_counts,
        "catalog_path": build_result.catalog_path,
        "field_ids": {
            "name": name_field_id,
            "type": type_field_id,
            "attachment": attachment_field_id,
        },
    }


def _suffix_from_mime_type(mime_type: str) -> str:
    if mime_type == "image/png":
        return ".png"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "image/jpeg":
        return ".jpg"
    return ".bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore Feishu Base reference images into local assets.")
    parser.add_argument("--url", required=True, help="Feishu Base URL.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="Logged-in Chrome profile directory.")
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS_DIR, help="Assets output directory.")
    parser.add_argument("--headed", action="store_true", help="Run with a visible browser window.")
    args = parser.parse_args()

    result = restore_assets(
        url=args.url,
        profile_dir=args.profile_dir,
        assets_dir=args.assets_dir,
        headless=not args.headed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
