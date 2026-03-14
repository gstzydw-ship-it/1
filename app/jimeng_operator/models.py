"""即梦网页自动化数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class JimengOperatorConfig:
    """即梦网页自动化配置。"""

    base_url: str = "https://jimeng.jianying.com/ai-tool/home"
    user_data_dir: Path = Path(".runtime/jimeng-browser")
    headless: bool = False
    dry_run: bool = True
    timeout_ms: int = 5000


@dataclass(slots=True)
class JimengDryRunRequest:
    """即梦 dry run 输入。"""

    prompt_main: str
    duration_seconds: int = 0
    ref_assets_in_order: list[str] = field(default_factory=list)
    reference_file_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class JimengDryRunResult:
    """即梦 dry run 结果。"""

    page_opened: bool = False
    reference_mode_ready: bool = False
    prompt_filled: bool = False
    references_selected: bool = False
    validation_passed: bool = False
    uploaded_reference_names: list[str] = field(default_factory=list)
    selected_reference_names: list[str] = field(default_factory=list)
    failed_stage: str = ""
    messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JimengOneShotRequest:
    """单镜头最小生成闭环输入。"""

    shot_id: str
    prompt_main: str
    model_name: str = ""
    duration_seconds: int = 0
    prompt_negative: str = ""
    ref_assets_in_order: list[str] = field(default_factory=list)
    reference_file_paths: list[Path] = field(default_factory=list)
    transition_reference: str = "@TransitionFrame"
    storyboard_text: str = ""
    hold_for_audit: bool = False
    output_path: Path = Path("outputs/videos/demo_shot_001.mp4")
    poll_timeout_seconds: int = 180
    poll_interval_seconds: int = 5


@dataclass(slots=True)
class JimengOneShotResult:
    """单镜头最小生成闭环结果。"""

    shot_id: str
    prompt_main: str
    ref_assets_in_order: list[str] = field(default_factory=list)
    transition_reference: str = "@TransitionFrame"
    page_opened: bool = False
    reference_mode_ready: bool = False
    prompt_filled: bool = False
    negative_prompt_filled: bool = False
    references_selected: bool = False
    validation_passed: bool = False
    submitted: bool = False
    generation_completed: bool = False
    ready_for_download: bool = False
    download_succeeded: bool = False
    failed_stage: str = ""
    download_path: str = ""
    audit_report_path: str = ""
    audit_action: str = ""
    audit_summary: str = ""
    prompt_patch: str = ""
    revised_prompt_main: str = ""
    uploaded_reference_names: list[str] = field(default_factory=list)
    selected_reference_names: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AuditIssueOption:
    """下载前审计可勾选的问题项。"""

    issue_id: str
    label: str
    patch_hint: str


@dataclass(slots=True)
class PromptAuditDecision:
    """人工审计后的处理结果。"""

    action: str
    selected_issue_ids: list[str] = field(default_factory=list)
    extra_notes: str = ""
    review_summary: str = ""
    prompt_patch: str = ""
    revised_prompt_main: str = ""
    report_path: str = ""


@dataclass(slots=True)
class GeminiAuditConfig:
    """Gemini 自动审查配置。"""

    api_key: str
    model_name: str = "gemini-2.5-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    frame_count: int = 3


@dataclass(slots=True)
class GeminiAuditResult:
    """Gemini 自动审查结果。"""

    action: str
    selected_issue_ids: list[str] = field(default_factory=list)
    review_summary: str = ""
    prompt_patch: str = ""
    raw_response_text: str = ""
    model_name: str = ""
    frame_paths: list[str] = field(default_factory=list)
    temp_video_path: str = ""


@dataclass(slots=True)
class JimengWatchResult:
    """即梦任务监视与下载结果。"""

    page_opened: bool = False
    reference_mode_ready: bool = False
    generation_completed: bool = False
    download_succeeded: bool = False
    poll_status: str = ""
    failed_stage: str = ""
    download_path: str = ""
    messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JimengJobResult:
    """表示一次即梦视频生成任务的最小结果。"""

    job_id: str = ""
    status: str = ""
    video_path: str = ""
