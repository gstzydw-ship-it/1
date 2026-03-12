---
name: video-shot-continuity-analysis
description: Use when planning or reviewing shot-to-shot continuity for AI video generation, especially when a next shot must grow from the previous shot's ending frame. Trigger this skill for requests about preserving any established visual element across cuts, turning a wide shot into a closer shot without breaking continuity, validating whether an angle change is real, or preventing false continuity between generated clips.
---

# Video Shot Continuity Analysis

## Overview

Use this skill before generating the next shot, not after. Its job is to decide whether a proposed next-shot image or prompt is actually continuous with the previous shot's ending frame.

In this workflow, "continuous" means more than "same character". It must preserve the correct subset of scene geometry, support cast, props, creatures, action phase, movement direction, camera grammar, and any other visual facts that the previous shot has already established.

## When To Use

Use this skill when any of these are true:

- The next shot is meant to continue directly from the previous clip's ending frame.
- The new shot changes size from wide or medium to close or half-body.
- The user asks for a "side angle", "another angle", "reverse", or "closer shot".
- One or more established visual elements must stay present or remain logically implied across the cut.
- A generated close-up looks plausible in isolation but may not actually match the prior shot.

## Core Rule

Do not approve a next shot just because the face matches. A cut is invalid if any mandatory continuity signal is broken.

Mandatory continuity signals:

- Subject identity
- Costume and silhouette
- Established carried, attached, accompanying, or scene-bound elements
- Action phase
- Screen direction and body orientation
- Camera axis and whether the angle change is real
- Scene geometry and landmark carry-over
- Any on-screen element that the prior frame made narratively or visually important

## Workflow

1. Read the previous shot's ending frame or strongest usable late-frame still.
2. Build a continuity inventory before writing prompts:
   - Must keep visible
   - May crop out but must remain logically implied
   - May fully exit
   - Must change
3. Decide the cut type:
   - Direct continuation
   - Push-in / closer coverage
   - Same-scene alternate angle
   - True relocation
4. Reject the shot design if it claims a new angle but does not move the background geometry accordingly.
5. Generate or request the next reference image only after the continuity inventory is explicit.

## Continuity Inventory

For every cut, explicitly label:

- `must_keep_subjects`
- `must_keep_visible_elements`
- `must_keep_implied_elements`
- `must_keep_background_landmarks`
- `can_crop_out`
- `can_exit_frame`
- `screen_direction`
- `camera_axis`
- `cut_type`
- `required_action_phase`

Think in categories, not hard-coded examples. The "elements" list may contain pets, props, crowds, vehicles, wounds, lighting effects, weather cues, magic effects, signage, debris, shadows, or anything else the previous shot makes meaningful.

If you cannot fill these confidently, do not greenlight the next image prompt yet.

## Close-Up Rule

When converting a wide or medium shot into a close shot:

- Use the previous shot's ending frame as the primary continuity reference, not only the character sheet.
- The character sheet protects identity.
- The ending frame protects composition inheritance, lighting, pose state, visible elements, and what was present in frame.
- If the prior frame visibly contains any meaningful companion, prop, crowd, effect, or landmark, decide explicitly whether the close-up may crop it out, must still show it, or must at least imply it.

Bad pattern:
- "Use character sheet to generate a close-up" when the close-up is supposed to grow out of a specific previous frame.

Better pattern:
- "Use previous ending frame for inherited framing state and scene retention, and use the character sheet only to stabilize identity."

## Angle Change Rule

A shot is not a real side angle just because the face turns.

Real angle change requires:

- Background perspective shift
- Landmark repositioning
- Character-body orientation consistent with the new camera placement
- Spatial relationship changes that match the claimed camera move

If the background still reads as the same frontal composition, the angle change is fake.

## Failure Patterns To Catch

Reject or revise when you see:

- Established elements disappear between shots without a motivated crop or exit.
- The close-up no longer carries the established scene logic.
- The next shot starts from a different action phase than the previous shot ended on.
- A "side view" still uses frontal background geometry.
- A generated close-up is compositionally nice but cannot plausibly be the next camera setup from the prior frame.
- The next shot invents a cleaner or emptier environment than the previous shot established.

## Output Format

Before generation, produce a short continuity brief:

```text
cut_type:
must_keep_subjects:
must_keep_visible_elements:
must_keep_implied_elements:
must_keep_background_landmarks:
can_crop_out:
can_exit_frame:
screen_direction:
camera_axis:
required_action_phase:
verdict:
why:
```

Use `verdict: block` when continuity is not yet strong enough to generate.

## References

Read [references/continuity-checklist.md](references/continuity-checklist.md) when you need the detailed checklist or examples of bad and good cuts.
