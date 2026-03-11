"""即梦服务占位导出。"""

from __future__ import annotations

from dataclasses import asdict

from app.jimeng_operator.models import JimengJobResult


class JimengOperator:
    """为旧骨架保留的最小服务封装。"""

    def submit_storyboard_job(self, payload: object) -> dict[str, object]:
        return asdict(
            JimengJobResult(
                job_id="jimeng-placeholder-job",
                status="submitted",
                video_path="outputs/placeholder.mp4",
            )
        )
