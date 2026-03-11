from pathlib import Path

from app.jimeng_operator.models import JimengDryRunRequest, JimengOneShotRequest, JimengOperatorConfig
from app.jimeng_operator.web_operator import JimengWebOperator


class FakeBrowserSession:
    def __init__(self, _config: JimengOperatorConfig) -> None:
        self.calls: list[str] = []
        self.selected: list[str] = []

    def goto(self, url: str) -> None:
        self.calls.append(f"goto:{url}")

    def close_blocking_dialogs(self, selectors) -> None:
        self.calls.append("close_blocking_dialogs")

    def enter_video_reference_mode(self, selectors) -> bool:
        self.calls.append("enter_video_reference_mode")
        return True

    def fill_prompt(self, selectors, prompt_main: str) -> bool:
        self.calls.append(f"fill_prompt:{prompt_main}")
        return True

    def fill_negative_prompt(self, prompt_negative: str) -> bool:
        self.calls.append(f"fill_negative_prompt:{prompt_negative}")
        return True

    def upload_reference_files(self, selectors, file_paths: list[Path]) -> list[str]:
        self.calls.append(f"upload_reference_files:{len(file_paths)}")
        return [f"图片{i}" for i in range(1, len(file_paths) + 1)]

    def select_reference_asset(self, selectors, asset_name: str) -> bool:
        self.calls.append(f"select_reference_asset:{asset_name}")
        self.selected.append(asset_name)
        return True

    def get_selected_reference_names(self, selectors) -> list[str]:
        self.calls.append("get_selected_reference_names")
        return list(self.selected)

    def submit_generation(self) -> bool:
        self.calls.append("submit_generation")
        return True

    def wait_for_generation_result(self, timeout_seconds: int, poll_interval_seconds: int) -> tuple[bool, str]:
        self.calls.append(f"wait_for_generation_result:{timeout_seconds}:{poll_interval_seconds}")
        return True, "ready_marker_increased"

    def download_latest_result(self, output_path: Path) -> bool:
        self.calls.append(f"download_latest_result:{output_path.name}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video")
        return True

    def close(self) -> None:
        self.calls.append("close")


def test_jimeng_web_operator_runs_dry_run_in_order(tmp_path: Path) -> None:
    created_sessions: list[FakeBrowserSession] = []

    def factory(config: JimengOperatorConfig) -> FakeBrowserSession:
        session = FakeBrowserSession(config)
        created_sessions.append(session)
        return session

    operator = JimengWebOperator(
        JimengOperatorConfig(user_data_dir=tmp_path / "browser"),
        session_factory=factory,
    )

    result = operator.run_dry_run(
        JimengDryRunRequest(
            prompt_main="主体：林白；动作：准备迎战。",
            ref_assets_in_order=["CHAR_林白__v1", "@TransitionFrame", "SCENE_古城门__v1"],
            reference_file_paths=[tmp_path / "char.png", tmp_path / "scene.png"],
        )
    )

    session = created_sessions[0]
    assert result.page_opened is True
    assert result.reference_mode_ready is True
    assert result.prompt_filled is True
    assert result.uploaded_reference_names == ["图片1", "图片2"]
    assert result.references_selected is True
    assert result.validation_passed is True
    assert result.selected_reference_names == ["图片1", "图片2"]
    assert session.calls == [
        "goto:https://jimeng.jianying.com/ai-tool/home",
        "close_blocking_dialogs",
        "enter_video_reference_mode",
        "upload_reference_files:2",
        "fill_prompt:主体：林白；动作：准备迎战。",
        "select_reference_asset:图片1",
        "select_reference_asset:图片2",
        "get_selected_reference_names",
        "close",
    ]


def test_jimeng_web_operator_runs_one_shot_in_order(tmp_path: Path) -> None:
    created_sessions: list[FakeBrowserSession] = []

    def factory(config: JimengOperatorConfig) -> FakeBrowserSession:
        session = FakeBrowserSession(config)
        created_sessions.append(session)
        return session

    operator = JimengWebOperator(
        JimengOperatorConfig(user_data_dir=tmp_path / "browser", dry_run=False),
        session_factory=factory,
    )
    output_path = tmp_path / "outputs" / "videos" / "demo_shot_001.mp4"

    result = operator.run_one_shot(
        JimengOneShotRequest(
            shot_id="demo_shot_001",
            prompt_main="主体：林白；动作：准备迎战。",
            prompt_negative="避免主体模糊。",
            ref_assets_in_order=["CHAR_林白__v1", "@TransitionFrame", "SCENE_古城门__v1"],
            reference_file_paths=[tmp_path / "char.png", tmp_path / "scene.png"],
            output_path=output_path,
        )
    )

    session = created_sessions[0]
    assert result.page_opened is True
    assert result.reference_mode_ready is True
    assert result.prompt_filled is True
    assert result.negative_prompt_filled is True
    assert result.references_selected is True
    assert result.validation_passed is True
    assert result.submitted is True
    assert result.generation_completed is True
    assert result.download_succeeded is True
    assert output_path.exists()
    assert result.download_path.endswith("demo_shot_001.mp4")
    assert session.calls == [
        "goto:https://jimeng.jianying.com/ai-tool/home",
        "close_blocking_dialogs",
        "enter_video_reference_mode",
        "upload_reference_files:2",
        "fill_prompt:主体：林白；动作：准备迎战。",
        "select_reference_asset:图片1",
        "select_reference_asset:图片2",
        "get_selected_reference_names",
        "fill_negative_prompt:避免主体模糊。",
        "submit_generation",
        "wait_for_generation_result:180:5",
        "download_latest_result:demo_shot_001.mp4",
        "close",
    ]


def test_jimeng_web_operator_holds_for_audit_before_download(tmp_path: Path) -> None:
    created_sessions: list[FakeBrowserSession] = []

    def factory(config: JimengOperatorConfig) -> FakeBrowserSession:
        session = FakeBrowserSession(config)
        created_sessions.append(session)
        return session

    operator = JimengWebOperator(
        JimengOperatorConfig(user_data_dir=tmp_path / "browser", dry_run=False),
        session_factory=factory,
    )
    output_path = tmp_path / "outputs" / "videos" / "demo_shot_001.mp4"

    result = operator.run_one_shot(
        JimengOneShotRequest(
            shot_id="demo_shot_001",
            prompt_main="主体：林白；动作：准备迎战。",
            prompt_negative="避免主体模糊。",
            ref_assets_in_order=["CHAR_林白__v1", "@TransitionFrame", "SCENE_古城门__v1"],
            reference_file_paths=[tmp_path / "char.png", tmp_path / "scene.png"],
            hold_for_audit=True,
            output_path=output_path,
        )
    )

    session = created_sessions[0]
    assert result.submitted is True
    assert result.generation_completed is True
    assert result.ready_for_download is True
    assert result.download_succeeded is False
    assert result.download_path == ""
    assert "download_latest_result:demo_shot_001.mp4" not in session.calls
    assert "close" not in session.calls
    operator.close()
    assert session.calls[-1] == "close"


def test_jimeng_web_operator_watches_and_downloads_in_order(tmp_path: Path) -> None:
    created_sessions: list[FakeBrowserSession] = []

    def factory(config: JimengOperatorConfig) -> FakeBrowserSession:
        session = FakeBrowserSession(config)
        created_sessions.append(session)
        return session

    operator = JimengWebOperator(
        JimengOperatorConfig(user_data_dir=tmp_path / "browser", dry_run=False),
        session_factory=factory,
    )
    output_path = tmp_path / "outputs" / "videos" / "watched_latest.mp4"

    result = operator.watch_and_download(
        output_path=output_path,
        timeout_seconds=600,
        poll_interval_seconds=30,
    )

    session = created_sessions[0]
    assert result.page_opened is True
    assert result.reference_mode_ready is True
    assert result.generation_completed is True
    assert result.download_succeeded is True
    assert result.download_path.endswith("watched_latest.mp4")
    assert output_path.exists()
    assert session.calls == [
        "goto:https://jimeng.jianying.com/ai-tool/home",
        "close_blocking_dialogs",
        "enter_video_reference_mode",
        "wait_for_generation_result:600:30",
        "download_latest_result:watched_latest.mp4",
        "close",
    ]


def test_jimeng_web_operator_exposes_minimal_methods(tmp_path: Path) -> None:
    operator = JimengWebOperator(
        JimengOperatorConfig(user_data_dir=tmp_path / "browser"),
        session_factory=FakeBrowserSession,
    )

    assert hasattr(operator, "open_jimeng")
    assert hasattr(operator, "ensure_reference_mode")
    assert hasattr(operator, "fill_prompt")
    assert hasattr(operator, "fill_negative_prompt")
    assert hasattr(operator, "upload_reference_assets")
    assert hasattr(operator, "select_reference_assets")
    assert hasattr(operator, "validate_reference_selection")
    assert hasattr(operator, "submit_generation")
    assert hasattr(operator, "poll_generation_result")
    assert hasattr(operator, "download_latest_video")
    assert hasattr(operator, "run_dry_run")
    assert hasattr(operator, "run_one_shot")
    assert hasattr(operator, "watch_and_download")
