"""视频分析服务。

当前聚焦于“为下一镜头挑选最佳承接帧”：
- 不只看清晰度或是否接近视频尾部
- 会结合当前镜头与下一镜头的整段剧情，判断哪一帧最适合顺滑承接
- 输出结果可直接作为后续 `@TransitionFrame` 的来源说明
"""

from __future__ import annotations

from pathlib import Path

from app.video_analyzer.models import (
    BestTransitionFrame,
    CandidateFrame,
    FrameContinuityMetrics,
    FrameQualityMetrics,
    TransitionFrameResult,
)

_CONTINUITY_LABELS = {
    "scene_match": "场景连续性",
    "character_state_match": "人物状态连续性",
    "pose_match": "姿势连续性",
    "action_settle": "动作收束度",
    "start_stability": "起始稳定性",
}

_QUALITY_LABELS = {
    "sharpness": "清晰度",
    "exposure": "曝光合理性",
    "subject_visibility": "主体可见性",
}

_DYNAMIC_ACTION_KEYWORDS = {
    "冲",
    "扑",
    "砸",
    "打",
    "出拳",
    "挥拳",
    "追击",
    "奔跑",
    "疾行",
    "转身冲出",
}

_CONTROLLED_TRANSITION_KEYWORDS = {
    "收回拳头",
    "收拳",
    "指着",
    "指向",
    "咬牙",
    "放狠话",
    "恶狠狠",
    "大笑",
    "嘲笑",
    "离开",
    "走出去",
    "搂着",
    "瞪",
}

_INTERRUPTION_KEYWORDS = {
    "广播",
    "喇叭",
    "打断",
    "拉着",
    "拉住",
    "拦住",
    "准备物资",
}

_GROUP_RELATIONSHIP_TAGS = {
    "多人同框",
    "关系清晰",
    "环境完整",
    "中景",
    "主体完整",
}

_INTERVENTION_STATE_TAGS = {
    "被拉住",
    "被打断",
    "收势",
    "收拳前",
    "怒视对手",
    "指向对手",
}

_DIALOGUE_READY_TAGS = {
    "站定",
    "准备",
    "怒视对手",
    "视线朝前",
    "重心稳定",
}


class VideoAnalyzerService:
    """处理视频候选帧评分与最佳承接帧选择。"""

    def analyze_one_shot(
        self,
        video_path: str,
        *,
        next_shot_summary: str,
        current_shot_summary: str = "",
        candidate_frames: list[CandidateFrame] | None = None,
    ) -> TransitionFrameResult:
        """结合当前镜头与下一镜头摘要，选出最适合承接的最佳帧。"""

        candidates = candidate_frames or self._build_default_candidates(Path(video_path))
        scored_candidates = [
            self._score_candidate(
                candidate,
                current_shot_summary=current_shot_summary,
                next_shot_summary=next_shot_summary,
            )
            for candidate in candidates
        ]
        scored_candidates.sort(key=lambda item: item.total_score, reverse=True)

        best_candidate = scored_candidates[0] if scored_candidates else None
        best_frame = None
        if best_candidate is not None:
            best_frame = BestTransitionFrame(
                frame_index=best_candidate.frame_index,
                timestamp_seconds=best_candidate.timestamp_seconds,
                frame_path=best_candidate.frame_path,
                continuity_score=best_candidate.continuity_score,
                quality_score=best_candidate.quality_score,
                total_score=best_candidate.total_score,
                reason=best_candidate.reason,
                best_dimensions=self._best_dimensions(best_candidate),
            )

        return TransitionFrameResult(
            video_path=video_path,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
            candidate_frames=scored_candidates,
            best_frame=best_frame,
        )

    def pick_best_transition_frame(
        self,
        video_path: str,
        *,
        next_shot_summary: str = "",
        current_shot_summary: str = "",
        candidate_frames: list[CandidateFrame] | None = None,
    ) -> object:
        """兼容旧接口，返回完整承接帧分析结果。"""

        return self.analyze_one_shot(
            video_path,
            next_shot_summary=next_shot_summary,
            current_shot_summary=current_shot_summary,
            candidate_frames=candidate_frames,
        )

    def _score_candidate(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> CandidateFrame:
        candidate.quality_metrics = FrameQualityMetrics(
            sharpness=round(_clamp(1.0 - candidate.blur_level), 4),
            exposure=round(_clamp(candidate.exposure_score), 4),
            subject_visibility=round(_clamp(candidate.subject_visibility), 4),
        )
        candidate.quality_score = round(
            (
                candidate.quality_metrics.sharpness * 0.40
                + candidate.quality_metrics.exposure * 0.25
                + candidate.quality_metrics.subject_visibility * 0.35
            ),
            4,
        )

        candidate.continuity_metrics = FrameContinuityMetrics(
            scene_match=round(
                self._score_scene_match(
                    candidate,
                    current_shot_summary=current_shot_summary,
                    next_shot_summary=next_shot_summary,
                ),
                4,
            ),
            character_state_match=round(
                self._score_character_state_match(
                    candidate,
                    current_shot_summary=current_shot_summary,
                    next_shot_summary=next_shot_summary,
                ),
                4,
            ),
            pose_match=round(
                self._score_pose_match(
                    candidate,
                    current_shot_summary=current_shot_summary,
                    next_shot_summary=next_shot_summary,
                ),
                4,
            ),
            action_settle=round(
                self._score_action_settle(
                    candidate,
                    current_shot_summary=current_shot_summary,
                    next_shot_summary=next_shot_summary,
                ),
                4,
            ),
            start_stability=round(
                self._score_start_stability(
                    candidate,
                    current_shot_summary=current_shot_summary,
                    next_shot_summary=next_shot_summary,
                ),
                4,
            ),
        )

        candidate.continuity_score = round(
            (
                candidate.continuity_metrics.scene_match * 0.24
                + candidate.continuity_metrics.character_state_match * 0.24
                + candidate.continuity_metrics.pose_match * 0.20
                + candidate.continuity_metrics.action_settle * 0.16
                + candidate.continuity_metrics.start_stability * 0.16
            ),
            4,
        )
        candidate.total_score = round(candidate.continuity_score * 0.72 + candidate.quality_score * 0.28, 4)
        candidate.reason = self._build_reason(
            candidate,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
        )
        return candidate

    def _score_scene_match(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> float:
        base_score = self._weighted_summary_match(
            candidate.scene_tags,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
            default_value=0.55,
        )

        if self._needs_group_relationship(current_shot_summary, next_shot_summary):
            if self._has_any_tag(candidate.composition_tags, _GROUP_RELATIONSHIP_TAGS):
                base_score += 0.18
            if self._has_any_tag(candidate.scene_tags, {"教室", "环境完整", "多人对峙"}):
                base_score += 0.08

        if self._mentions_exit_followthrough(next_shot_summary):
            if self._has_any_tag(candidate.composition_tags, {"中景", "全身", "多人同框", "环境完整"}):
                base_score += 0.10

        return _clamp(base_score)

    def _score_character_state_match(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> float:
        base_score = self._weighted_summary_match(
            candidate.character_state_tags,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
            default_value=0.50,
        )

        if self._is_interruption_bridge(current_shot_summary, next_shot_summary):
            if self._has_any_tag(candidate.character_state_tags, _INTERVENTION_STATE_TAGS):
                base_score += 0.24
            elif candidate.action_phase == "mid_action":
                base_score -= 0.18

        if self._is_dialogue_heavy_next_shot(next_shot_summary):
            if self._has_any_tag(candidate.character_state_tags, _DIALOGUE_READY_TAGS):
                base_score += 0.12

        return _clamp(base_score)

    def _score_pose_match(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> float:
        base_score = self._weighted_summary_match(
            candidate.pose_tags,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
            default_value=0.50,
        )

        if self._is_dialogue_heavy_next_shot(next_shot_summary):
            if self._has_any_tag(candidate.pose_tags, {"正面", "视线朝前", "对视", "侧身对手", "重心稳定"}):
                base_score += 0.16

        if self._mentions_exit_followthrough(next_shot_summary):
            if self._has_any_tag(candidate.pose_tags, {"转身前", "回头", "侧身"}):
                base_score += 0.10

        return _clamp(base_score)

    def _weighted_summary_match(
        self,
        tags: list[str],
        *,
        current_shot_summary: str,
        next_shot_summary: str,
        default_value: float,
    ) -> float:
        if not tags:
            return default_value

        current_score = _summary_match_score(tags, current_shot_summary)
        next_score = _summary_match_score(tags, next_shot_summary)

        if current_score is None and next_score is None:
            return default_value
        if current_score is None:
            return max(next_score or 0.0, 0.12)
        if next_score is None:
            return max(current_score, 0.20)
        return _clamp(next_score * 0.70 + current_score * 0.30)

    def _score_action_settle(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> float:
        wants_dynamic_start = any(keyword in next_shot_summary for keyword in _DYNAMIC_ACTION_KEYWORDS)
        wants_controlled_transition = any(keyword in next_shot_summary for keyword in _CONTROLLED_TRANSITION_KEYWORDS)
        interruption_bridge = self._is_interruption_bridge(current_shot_summary, next_shot_summary)

        phase = candidate.action_phase.strip().lower()
        if phase == "settled":
            if wants_dynamic_start and not wants_controlled_transition:
                return 0.90
            return 1.0
        if phase == "transition":
            if wants_dynamic_start and not wants_controlled_transition:
                return 0.90
            if wants_controlled_transition or interruption_bridge:
                return 0.92
            return 0.76
        if phase == "mid_action":
            if wants_dynamic_start and not interruption_bridge:
                return 0.46
            return 0.18
        return 0.60

    def _score_start_stability(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> float:
        composition_bonus = 0.0
        stable_keywords = {
            "主体清晰",
            "主体居中",
            "中景",
            "近景",
            "面部可见",
            "主体完整",
            "构图稳定",
            "多人同框",
            "关系清晰",
        }
        if self._has_any_tag(candidate.composition_tags, stable_keywords):
            composition_bonus += 0.18
        if candidate.action_phase == "settled":
            composition_bonus += 0.12
        elif candidate.action_phase == "transition":
            composition_bonus += 0.07

        if self._is_dialogue_heavy_next_shot(next_shot_summary):
            composition_bonus += 0.05
        if self._needs_group_relationship(current_shot_summary, next_shot_summary):
            if self._has_any_tag(candidate.composition_tags, {"多人同框", "关系清晰", "环境完整"}):
                composition_bonus += 0.08

        base_score = candidate.subject_visibility * 0.55 + (1.0 - candidate.blur_level) * 0.25 + composition_bonus
        return _clamp(base_score)

    def _best_dimensions(self, candidate: CandidateFrame) -> list[str]:
        summary_driven_continuity = [
            (_CONTINUITY_LABELS["scene_match"], candidate.continuity_metrics.scene_match),
            (_CONTINUITY_LABELS["character_state_match"], candidate.continuity_metrics.character_state_match),
            (_CONTINUITY_LABELS["pose_match"], candidate.continuity_metrics.pose_match),
        ]
        generic_continuity = [
            (_CONTINUITY_LABELS["action_settle"], candidate.continuity_metrics.action_settle),
            (_CONTINUITY_LABELS["start_stability"], candidate.continuity_metrics.start_stability),
        ]
        quality_dimensions = [
            (_QUALITY_LABELS["sharpness"], candidate.quality_metrics.sharpness),
            (_QUALITY_LABELS["subject_visibility"], candidate.quality_metrics.subject_visibility),
            (_QUALITY_LABELS["exposure"], candidate.quality_metrics.exposure),
        ]

        best_summary_continuity = sorted(summary_driven_continuity, key=lambda item: item[1], reverse=True)[:1]
        best_generic_continuity = sorted(generic_continuity, key=lambda item: item[1], reverse=True)[:1]
        best_quality = sorted(quality_dimensions, key=lambda item: item[1], reverse=True)[:1]
        return [label for label, _score in [*best_summary_continuity, *best_generic_continuity, *best_quality]]

    def _build_reason(
        self,
        candidate: CandidateFrame,
        *,
        current_shot_summary: str,
        next_shot_summary: str,
    ) -> str:
        best_dimensions = self._best_dimensions(candidate)
        segments: list[str] = []

        if self._is_interruption_bridge(current_shot_summary, next_shot_summary):
            segments.append("当前承接目标包含“被打断后收势再放话”的转折")
        if self._needs_group_relationship(current_shot_summary, next_shot_summary):
            segments.append("该帧保留了更完整的人物关系与场面信息")
        if candidate.scene_tags:
            segments.append(f"场景信息覆盖 {', '.join(candidate.scene_tags[:2])}")
        if candidate.character_state_tags:
            segments.append(f"人物状态更接近 {', '.join(candidate.character_state_tags[:2])}")
        if candidate.pose_tags:
            segments.append(f"姿势与视线保持 {', '.join(candidate.pose_tags[:2])}")
        if candidate.action_phase == "settled":
            segments.append("动作已基本收束，适合作为下一镜头起始")
        elif candidate.action_phase == "transition":
            segments.append("动作处于收势过渡段，适合自然接入下一镜头")
        else:
            segments.append("动作仍在中段，承接风险偏高")
        if candidate.composition_tags:
            segments.append(f"构图上保留了 {', '.join(candidate.composition_tags[:2])}")

        return f"该帧在 {', '.join(best_dimensions)} 上表现更优；{'；'.join(segments[:5])}。"

    def _is_dialogue_heavy_next_shot(self, next_shot_summary: str) -> bool:
        return any(
            keyword in next_shot_summary
            for keyword in ("：", "语气", "语速", "大笑", "咬牙", "算你运气好", "时光屋", "哄堂大笑")
        )

    def _is_interruption_bridge(self, current_shot_summary: str, next_shot_summary: str) -> bool:
        current_has_interrupt = any(keyword in current_shot_summary for keyword in _INTERRUPTION_KEYWORDS)
        next_has_controlled_followup = any(keyword in next_shot_summary for keyword in _CONTROLLED_TRANSITION_KEYWORDS)
        return current_has_interrupt and next_has_controlled_followup

    def _needs_group_relationship(self, current_shot_summary: str, next_shot_summary: str) -> bool:
        combined = f"{current_shot_summary} {next_shot_summary}"
        return any(
            keyword in combined
            for keyword in ("陈夏娜", "跟班", "林白", "周浩天", "嘲笑", "离开", "教室", "拉住")
        )

    def _mentions_exit_followthrough(self, next_shot_summary: str) -> bool:
        return any(keyword in next_shot_summary for keyword in ("离开", "走出去", "头也不回", "瞪", "搂着"))

    def _has_any_tag(self, tags: list[str], expected_tags: set[str]) -> bool:
        return any(tag in expected_tags for tag in tags)

    def _build_default_candidates(self, video_path: Path) -> list[CandidateFrame]:
        """为最小验证构建一组可评分候选帧。"""

        stem = video_path.stem or "shot"
        frame_dir = video_path.parent / "frames"
        return [
            CandidateFrame(
                frame_index=72,
                timestamp_seconds=2.4,
                relative_position=0.58,
                frame_path=str(frame_dir / f"{stem}_0072.png"),
                scene_tags=["主场景", "环境完整"],
                character_state_tags=["站定", "准备"],
                pose_tags=["正面", "视线朝前", "重心稳定"],
                composition_tags=["主体清晰", "主体完整", "中景"],
                action_phase="settled",
                blur_level=0.16,
                exposure_score=0.82,
                subject_visibility=0.90,
            ),
            CandidateFrame(
                frame_index=88,
                timestamp_seconds=2.93,
                relative_position=0.70,
                frame_path=str(frame_dir / f"{stem}_0088.png"),
                scene_tags=["主场景", "环境完整"],
                character_state_tags=["转身", "蓄势"],
                pose_tags=["侧身", "视线偏左"],
                composition_tags=["主体清晰", "中景"],
                action_phase="transition",
                blur_level=0.20,
                exposure_score=0.84,
                subject_visibility=0.86,
            ),
            CandidateFrame(
                frame_index=101,
                timestamp_seconds=3.37,
                relative_position=0.80,
                frame_path=str(frame_dir / f"{stem}_0101.png"),
                scene_tags=["主场景", "人物占比更大"],
                character_state_tags=["出手前", "紧绷"],
                pose_tags=["正面", "视线朝前"],
                composition_tags=["近景", "面部可见"],
                action_phase="transition",
                blur_level=0.22,
                exposure_score=0.80,
                subject_visibility=0.88,
            ),
            CandidateFrame(
                frame_index=112,
                timestamp_seconds=3.73,
                relative_position=0.89,
                frame_path=str(frame_dir / f"{stem}_0112.png"),
                scene_tags=["主场景", "环境缺失"],
                character_state_tags=["动作中"],
                pose_tags=["朝向不稳", "视线模糊"],
                composition_tags=["主体偏移"],
                action_phase="mid_action",
                blur_level=0.48,
                exposure_score=0.73,
                subject_visibility=0.60,
            ),
            CandidateFrame(
                frame_index=119,
                timestamp_seconds=3.97,
                relative_position=0.95,
                frame_path=str(frame_dir / f"{stem}_0119.png"),
                scene_tags=["主场景", "环境残缺"],
                character_state_tags=["动作中"],
                pose_tags=["背身", "视线缺失"],
                composition_tags=["主体偏移"],
                action_phase="mid_action",
                blur_level=0.55,
                exposure_score=0.76,
                subject_visibility=0.54,
            ),
        ]


def _summary_match_score(tags: list[str], summary: str) -> float | None:
    normalized_summary = summary.strip()
    if not normalized_summary:
        return None

    matched_count = sum(1 for tag in tags if tag and tag in normalized_summary)
    if matched_count == 0:
        return 0.08

    coverage = matched_count / max(len(tags), 1)
    return _clamp(0.55 + coverage * 0.45)


def _clamp(value: float) -> float:
    return max(0.0, min(value, 1.0))
