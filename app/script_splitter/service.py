"""Heuristic script splitter tuned for short image-to-video shots."""

from __future__ import annotations

import re
from dataclasses import asdict

from app.script_splitter.models import ScriptSplitRequest, ScriptSplitResult, SplitShot

_SCENE_LINE_RE = re.compile(
    r"^(第[一二三四五六七八九十百0-9]+[场幕镜集]|INT\.|EXT\.|内景|外景|日[内外]?|夜[内外]?|场景[:：]).*"
)
_SOFT_BREAK_TOKENS = ("然后", "随后", "接着", "此时", "这时", "突然", "并且", "同时", "紧接着")
_DIALOGUE_RE = re.compile(r"^[^。！？!?：:\n]{1,30}[：:].+")
_INLINE_ACTION_SPLIT_RE = re.compile(
    r"^(?P<speech>.+?(?:。|！|？|!|\?|……|……”|！”|？”|!\"|\?\"|…))(?:\s+)(?P<action>(?:[一-龥]{1,8}|他|她|它|两人|小黄).+)$"
)


class ScriptSplitterService:
    """Split dense script text into smaller shot-sized beats for Manju."""

    def split_script(self, request: ScriptSplitRequest) -> ScriptSplitResult:
        normalized_text = self._normalize_text(request.script_text)
        if not normalized_text:
            raise ValueError("script_text is empty")

        units = self._merge_orphan_units(self._extract_units(normalized_text))
        shots = self._apply_cinematic_shot_design(self._build_shots(units, request))
        notes = [
            "切分策略偏保守：优先把对白、动作转折、突发事件拆开，降低单镜头信息密度。",
            "输出镜头默认适配 manju_scene_batch，可直接补充项目 URL 后进入批量生成。",
        ]
        return ScriptSplitResult(
            workflow_mode=request.workflow_mode,
            character_ref=request.character_ref,
            scene_ref=request.scene_ref,
            shots=shots,
            splitting_notes=notes,
            aspect_ratio=request.aspect_ratio,
            model_name=request.model_name,
            manju_mode=request.manju_mode,
            manju_resolution=request.manju_resolution,
            manju_model_name=request.manju_model_name,
            manju_headless=request.manju_headless,
        )

    def to_payload(self, result: ScriptSplitResult) -> dict[str, object]:
        return {
            "workflow_mode": result.workflow_mode,
            "character_ref": result.character_ref,
            "scene_ref": result.scene_ref,
            "aspect_ratio": result.aspect_ratio,
            "model_name": result.model_name,
            "manju_mode": result.manju_mode,
            "manju_resolution": result.manju_resolution,
            "manju_model_name": result.manju_model_name,
            "manju_headless": result.manju_headless,
            "splitting_notes": result.splitting_notes,
            "shots": [asdict(shot) for shot in result.shots],
        }

    def _normalize_text(self, script_text: str) -> str:
        text = script_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(line for line in lines if line)

    def _extract_units(self, script_text: str) -> list[dict[str, str]]:
        units: list[dict[str, str]] = []
        current_scene = ""

        for raw_line in script_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if self._is_scene_line(line):
                current_scene, remainder = self._split_scene_line(line)
                if not remainder:
                    continue
                line = remainder

            line = self._clean_line(line)
            if not line:
                continue

            for fragment in self._split_line_fragments(line):
                for fragment in self._split_dialogue_sentence(fragment):
                    cleaned_fragment = fragment.strip()
                    if not cleaned_fragment:
                        continue
                    if self._is_dialogue_stub(cleaned_fragment):
                        continue
                    if self._is_dialogue_line(cleaned_fragment) or self._is_metadata_line(cleaned_fragment):
                        units.append(
                            {
                                "scene": current_scene,
                                "text": cleaned_fragment,
                                "kind": self._classify_unit(cleaned_fragment),
                            }
                        )
                        continue

                    for beat in self._split_sentence_into_beats(cleaned_fragment):
                        cleaned = beat.strip(" ，、；;")
                        if not cleaned or self._is_noise_fragment(cleaned):
                            continue
                        units.append(
                            {
                                "scene": current_scene,
                                "text": cleaned,
                                "kind": self._classify_unit(cleaned),
                            }
                        )
        return units

    def _split_line_fragments(self, line: str) -> list[str]:
        sentences = self._split_line_into_sentences(line)
        if not sentences:
            return [line]
        if not self._is_dialogue_line(sentences[0]):
            return sentences

        speaker, marker, _ = self._split_dialogue_prefix(sentences[0])
        if not marker:
            return sentences

        fragments: list[str] = [sentences[0]]
        for sentence in sentences[1:]:
            stripped = sentence.strip()
            if not stripped:
                continue
            if self._is_metadata_line(stripped):
                fragments.append(stripped)
                continue
            if self._looks_like_action_sentence(stripped):
                fragments.append(stripped)
                continue
            fragments.append(f"{speaker}{marker}{stripped}")
        return fragments

    def _build_shots(self, units: list[dict[str, str]], request: ScriptSplitRequest) -> list[SplitShot]:
        shots: list[SplitShot] = []
        buffer: list[dict[str, str]] = []

        def flush_buffer() -> None:
            if not buffer:
                return
            shot_index = len(shots) + 1
            scene_prefix = buffer[0]["scene"]
            body = " ".join(item["text"] for item in buffer).strip()
            storyboard_text = f"{scene_prefix} {body}".strip() if scene_prefix else body
            shots.append(
                SplitShot(
                    storyboard_id=f"{request.shot_prefix}_{shot_index:03d}",
                    storyboard_text=storyboard_text,
                    character_ref=request.character_ref,
                    scene_ref=request.scene_ref,
                    style_summary="以图生视频细分镜头，优先稳住人物与场景，不追求复杂运镜。",
                    current_shot_summary=body,
                    continuity_requirements=self._build_continuity_requirements(request, shot_index),
                    source_text=body,
                )
            )
            buffer.clear()

        for unit in units:
            if not buffer:
                buffer.append(unit)
                continue

            previous = buffer[-1]
            same_scene = previous["scene"] == unit["scene"]
            merged_text = " ".join(item["text"] for item in buffer + [unit])
            would_exceed_chars = len(merged_text) > request.max_chars_per_shot
            would_exceed_units = len(buffer) >= request.max_units_per_shot
            current_is_dialogue = unit["kind"] == "dialogue"
            previous_is_dialogue = previous["kind"] == "dialogue"
            action_turn = unit["kind"] == "turn"
            metadata_unit = unit["kind"] == "metadata"

            if metadata_unit and previous_is_dialogue:
                buffer.append(unit)
                continue

            if (
                (not same_scene)
                or would_exceed_chars
                or would_exceed_units
                or current_is_dialogue
                or previous_is_dialogue
                or action_turn
            ):
                flush_buffer()
            buffer.append(unit)

        flush_buffer()
        return shots

    def _apply_cinematic_shot_design(self, shots: list[SplitShot]) -> list[SplitShot]:
        designed: list[SplitShot] = []
        movement_cycle = [
            ("中景", "三分之二前侧", "人物与空间关系同时清楚", "建立人物在空间中的行进关系"),
            ("侧面中景", "正侧面", "人物的行走方向和步态连续", "动作延续时换角度，避免同景别跳切"),
            ("中近景", "三分之二侧前方", "主角神态与动作收势同步可读", "从位移切到情绪与状态"),
            ("背后三分之二中景", "背后偏侧", "人物去向与前方空间目标明确", "保持方向连续，补足空间指向"),
        ]

        for index, shot in enumerate(shots, start=1):
            kind = self._classify_shot_kind(shot.storyboard_text, shot.current_shot_summary, index=index)
            shot_size = "中近景"
            camera_angle = "三分之二前侧"
            camera_focus = "主体清楚、构图稳定"
            cut_reason = "保持镜头信息集中"

            if kind == "establishing":
                shot_size = "中景"
                camera_angle = "三分之二前侧"
                camera_focus = "人物与场景入口关系同时可读"
                cut_reason = "先交代人物落点、空间朝向和场面关系"
            elif kind == "dialogue":
                shot_size = "中近景"
                camera_angle = "三分之二侧面"
                camera_focus = "脸部、口型和视线方向清楚"
                cut_reason = "用于对话反打或对白前后的情绪承接"
            elif kind == "reaction":
                shot_size = "近景"
                camera_angle = "正面或三分之二前侧"
                camera_focus = "表情、眼神和轻微动作清楚"
                cut_reason = "放大情绪反应，给前后动作留节奏落点"
            elif kind == "action_peak":
                shot_size = "中景"
                camera_angle = "斜侧面"
                camera_focus = "关键动作与受影响对象同时可见"
                cut_reason = "突出动作爆点，同时保持空间可读性"
            elif kind == "movement":
                shot_size, camera_angle, camera_focus, cut_reason = movement_cycle[(index - 1) % len(movement_cycle)]

            shot.shot_kind = kind
            shot.shot_size = shot_size
            shot.camera_angle = camera_angle
            shot.camera_focus = camera_focus
            shot.cut_reason = cut_reason
            shot.anchor_strategy = "auto" if index == 1 else "generate_from_continuity_ref"
            designed.append(shot)

        return designed

    def _split_line_into_sentences(self, line: str) -> list[str]:
        parts = self._split_outside_brackets(line, separators=("。", "！", "？", "!", "?", "；", ";"))
        return parts or [line]

    def _split_dialogue_sentence(self, sentence: str) -> list[str]:
        if not self._is_dialogue_line(sentence):
            return [sentence]
        speaker, marker, body = self._split_dialogue_prefix(sentence)
        if not marker:
            return [sentence]

        body = body.strip()
        match = _INLINE_ACTION_SPLIT_RE.match(body)
        if not match:
            return [sentence]

        speech = match.group("speech").strip()
        action = match.group("action").strip()
        dialogue = f"{speaker}{marker}{speech}".strip()
        return [dialogue, action]

    def _split_dialogue_prefix(self, sentence: str) -> tuple[str, str, str]:
        speaker, marker, body = sentence.partition("：")
        if not marker:
            speaker, marker, body = sentence.partition(":")
        return speaker, marker, body

    def _split_sentence_into_beats(self, sentence: str) -> list[str]:
        if self._is_dialogue_line(sentence) or self._is_metadata_line(sentence):
            return [sentence]
        if len(sentence) <= 36:
            return [sentence]

        pieces = [sentence]
        for token in _SOFT_BREAK_TOKENS:
            next_pieces: list[str] = []
            for piece in pieces:
                next_pieces.extend(self._split_piece_on_token(piece, token))
            pieces = next_pieces

        final_pieces: list[str] = []
        for piece in pieces:
            if len(piece) <= 40:
                final_pieces.append(piece)
                continue
            comma_parts = self._split_outside_brackets(piece, separators=("，", "、"))
            if len(comma_parts) > 1:
                final_pieces.extend(comma_parts)
            else:
                final_pieces.append(piece)
        return final_pieces

    def _split_piece_on_token(self, piece: str, token: str) -> list[str]:
        if token not in piece or len(piece) <= 30:
            return [piece]
        index = piece.find(token)
        if index <= 0:
            return [piece]
        left = piece[:index].strip(" ，、；;")
        right = piece[index:].strip(" ，、；;")
        if not left or not right:
            return [piece]
        return [left, right]

    def _merge_orphan_units(self, units: list[dict[str, str]]) -> list[dict[str, str]]:
        if not units:
            return units

        merged: list[dict[str, str]] = []
        for unit in units:
            text = unit["text"].strip()
            if not merged:
                merged.append(unit)
                continue

            previous = merged[-1]
            if text.startswith(("”", "』", "」", "】", "）", ")", "、", "，", "。")):
                previous["text"] = f"{previous['text']} {text}".strip()
                previous["kind"] = self._classify_unit(previous["text"])
                continue

            if unit["kind"] == "action" and previous["kind"] == "action" and len(text) <= 10:
                previous["text"] = f"{previous['text']} {text}".strip()
                previous["kind"] = self._classify_unit(previous["text"])
                continue

            merged.append(unit)
        return merged

    def _looks_like_action_sentence(self, sentence: str) -> bool:
        if self._is_dialogue_line(sentence) or self._is_metadata_line(sentence):
            return False
        return bool(
            re.match(
                r"^(他|她|它|两人|小黄|林白|林可儿|少女|少年|女人|男人|众人|周围路人|司机|车内|眼前|不远处|随后|然后|突然|这时|此时)",
                sentence,
            )
        )

    def _is_noise_fragment(self, text: str) -> bool:
        return not re.sub(r"[。！？!?…·\s]+", "", text)

    def _is_dialogue_stub(self, text: str) -> bool:
        if not self._is_dialogue_line(text):
            return False
        _, _, body = self._split_dialogue_prefix(text)
        return self._is_noise_fragment(body)

    def _is_scene_line(self, line: str) -> bool:
        return bool(_SCENE_LINE_RE.match(line)) or ("场景" in line and len(line) <= 30)

    def _split_scene_line(self, line: str) -> tuple[str, str]:
        for separator in ("。", "：", ":", ".", "!", "?"):
            if separator in line:
                scene_text, remainder = line.split(separator, 1)
                return scene_text.strip(), remainder.strip()
        return line.strip(), ""

    def _clean_line(self, line: str) -> str:
        cleaned = re.sub(r"^[△▲]\s*", "", line.strip())
        return re.sub(r"\s+", " ", cleaned).strip()

    def _is_dialogue_line(self, line: str) -> bool:
        if self._is_metadata_line(line):
            return False
        return bool(_DIALOGUE_RE.match(line))

    def _is_metadata_line(self, line: str) -> bool:
        return line.startswith("【字幕")

    def _classify_unit(self, text: str) -> str:
        if self._is_metadata_line(text):
            return "metadata"
        if self._is_dialogue_line(text):
            return "dialogue"
        if any(token in text for token in ("突然", "这时", "此时", "随后", "然后", "紧接着")):
            return "turn"
        return "action"

    def _split_outside_brackets(self, text: str, *, separators: tuple[str, ...]) -> list[str]:
        parts: list[str] = []
        buffer: list[str] = []
        depth = 0
        bracket_pairs = {"（": "）", "(": ")", "【": "】", "[": "]", "“": "”", '"': '"'}
        closing = set(bracket_pairs.values())

        for char in text:
            buffer.append(char)
            if char in bracket_pairs:
                if char == '"' and depth > 0:
                    depth -= 1
                else:
                    depth += 1
                continue
            if char in closing and depth > 0:
                depth -= 1
                continue
            if char in separators and depth == 0:
                piece = "".join(buffer).strip()
                if piece:
                    parts.append(piece)
                buffer = []

        tail = "".join(buffer).strip()
        if tail:
            parts.append(tail)
        return parts

    def _build_continuity_requirements(self, request: ScriptSplitRequest, shot_index: int) -> str:
        parts = ["保持人物外观、服装、发型和站位稳定", "保持场景空间朝向和镜头方位稳定"]
        if request.character_ref:
            parts.append(f"角色参考继续使用 {request.character_ref}")
        if request.scene_ref:
            parts.append(f"场景参考继续使用 {request.scene_ref}")
        if shot_index > 1:
            parts.append("动作从上一镜头收尾处继续，不要跳轴或突然换景")
        return "；".join(parts)

    def _classify_shot_kind(self, storyboard_text: str, current_shot_summary: str, *, index: int) -> str:
        text = f"{storyboard_text} {current_shot_summary}".strip()
        if self._is_dialogue_line(storyboard_text) or self._is_dialogue_line(current_shot_summary):
            return "dialogue"

        reaction_tokens = ("表情", "眼神", "惊讶", "沉默", "犹豫", "皱眉", "眼睛一亮", "笑了笑", "看着", "盯着")
        action_peak_tokens = ("突然", "爆炸", "浓烟", "大火", "砸", "挥拳", "冲上去", "拉出来", "救人", "碎裂")
        movement_tokens = ("走", "跑", "奔", "来到", "走在", "进", "出", "转身", "继续", "牵着", "起身", "快步")
        establishing_tokens = ("路上", "街道", "门口", "宿舍外", "教室", "走廊", "广场", "场景", "空间", "眼前")

        if any(token in text for token in action_peak_tokens):
            return "action_peak"
        if any(token in text for token in reaction_tokens):
            return "reaction"
        if index == 1 and any(token in text for token in establishing_tokens + movement_tokens):
            return "establishing"
        if any(token in text for token in movement_tokens):
            return "movement"
        if any(token in text for token in establishing_tokens):
            return "establishing"
        return "reaction" if index > 1 else "establishing"
