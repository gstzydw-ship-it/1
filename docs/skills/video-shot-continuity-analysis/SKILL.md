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
- Subject design lock across the shot family
- Costume and silhouette
- Established carried, attached, accompanying, or scene-bound elements
- Action phase
- Action carry-through across the cut
- Screen direction and body orientation
- Camera axis and whether the angle change is real
- Scene geometry and landmark carry-over
- Any on-screen element that the prior frame made narratively or visually important

## Narrative Priority Rule

Do not treat every script detail as equally mandatory.

Before rejecting or regenerating a shot, classify missing or altered details into one of three levels:

- `must_keep_story_detail`
- `compressible_story_detail`
- `optional_flavor_detail`

Use this test:

- If removing the detail breaks plot comprehension, character relationship, identity reveal, motivation, or the cause-and-effect chain, it is `must_keep_story_detail`.
- If removing the detail still preserves the plot beat but loses some richness, it is `compressible_story_detail`.
- If removing the detail only reduces texture, atmosphere, or descriptive flourish without harming the beat, it is `optional_flavor_detail`.

Examples of `must_keep_story_detail`:

- Who saves whom
- Whether the rescue succeeded
- Whether a key person is recognized
- Whether a promise, refusal, demand, accusation, or deal is clearly established
- Whether a later line depends on a prior reveal

Examples of `compressible_story_detail`:

- The exact micro-action used to transition into a conversation
- Whether a character adjusts clothing, stands fully upright, or takes a half-step before speaking
- A non-essential intermediate reaction if the before and after states are both clear

Examples of `optional_flavor_detail`:

- Wiping soot or tears before a reveal when the reveal still reads clearly without it
- Decorative gesture beats that do not change the meaning
- Extra environmental business that adds flavor but not narrative necessity

Do not regenerate a usable sequence just because an `optional_flavor_detail` is absent.
Do regenerate or patch when a `must_keep_story_detail` is missing, contradicted, or replaced by a wrong event.

## Workflow

1. Read the previous shot's ending frame or strongest usable late-frame still.
2. Build a continuity inventory before writing prompts:
   - Must keep visible
   - May crop out but must remain logically implied
   - May fully exit
   - Must change
   - Must-keep story details
   - Compressible story details
   - Optional flavor details
3. Decide the cut type:
   - Direct continuation
   - Insert / detail bridge shot
   - Push-in / closer coverage
   - Same-scene alternate angle
   - True relocation
4. Reject the shot design if it claims a new angle but does not move the background geometry accordingly.
5. Reject back-to-back cuts that keep nearly the same frontal coverage unless a bridge purpose is explicit.
6. Generate or request the next reference image only after the continuity inventory is explicit.

## Continuity Inventory

For every cut, explicitly label:

- `must_keep_subjects`
- `identity_lock_refs`
- `must_keep_visible_elements`
- `must_keep_implied_elements`
- `must_keep_background_landmarks`
- `must_keep_story_details`
- `compressible_story_details`
- `optional_flavor_details`
- `can_crop_out`
- `can_exit_frame`
- `screen_direction`
- `camera_axis`
- `cut_type`
- `bridge_action`
- `companion_visibility_mode`
- `angle_read_test`
- `coverage_change_reason`
- `required_action_phase`

Think in categories, not hard-coded examples. The "elements" list may contain pets, props, crowds, vehicles, wounds, lighting effects, weather cues, magic effects, signage, debris, shadows, or anything else the previous shot makes meaningful.

If you cannot fill these confidently, do not greenlight the next image prompt yet.

## Identity Lock Rule

Character continuity is not just "same hair color and clothes". For a shot family, the face structure and proportion read must stay locked.

Before greenlighting the next shot, identify which references are responsible for identity lock:

- Character sheet or approved design sheet
- The previous approved shot's ending frame
- The previous approved shot's approved anchor image

Use the previous frame to inherit scene state, but use the design sheet to keep the character from drifting into a look-alike variant.

If a shot is a close-up or reaction shot, identity tolerance must be stricter than in a wide shot.

## Companion Visibility Rule

If a companion subject such as a pet is unstable in a bridge or insert shot, do not let the model improvise a larger visible portion than it can hold consistently.

Choose one visibility mode explicitly:

- `full_visible`
- `partial_visible`
- `implied_only`
- `fully_cropped_but_logically_present`

Bad pattern:
- The companion was stable in a wide shot, then becomes a distorted partial face in an insert shot.

Better pattern:
- In the insert shot, show leash plus paws only, or crop the companion out entirely while preserving logical presence.

## Angle Read Rule

An angle claim must be validated by visual read, not by prompt wording.

If a shot is labeled side angle, three-quarter angle, or front-side angle, verify all of these:

- Nose direction and cheek contour are no longer front-on
- Shoulder line is rotated relative to camera
- Background perspective shifts compared with the frontal setup
- The frame would still read as the claimed angle even with the prompt removed

If the shot still reads as a frontal portrait, it is a frontal shot, not a side-derived shot.

## Coverage Progression Rule

Back-to-back shots with nearly the same viewing angle often create visible segmentation, even if the character and background match.

If shot A and shot B are both frontal or near-frontal coverage of the same beat, require at least one of these:

- A meaningful size change with a clear dramatic purpose
- A true angle change with matching background perspective shift
- An insert shot such as face close-up, eyes, hand, feet, leash, prop, or another detail that bridges the beat
- A body-part or motion-detail shot that carries the action forward before returning to the prior angle

Bad pattern:
- Front medium shot of walking
- Another front medium or front-close shot with only minor framing difference

Better pattern:
- Front walking shot
- Leg or foot insert continuing the walk
- Return to a closer frontal shot as the character slows and stops

Or:

- Front walking shot
- Side or three-quarter bridge shot with real perspective change
- Front closer shot after the action settles

Do not solve this by making a near-frontal shot and calling it coverage variation. If the second shot still reads as the same axis and same composition family, insert a real bridge shot or a true alternate angle.

## Shot Segmentation Rhythm Rule

Do not break a beat into a stack of nearly identical hero shots. Segment the beat by function.

Useful shot functions inside one short dramatic beat:

- Relationship or establishing shot
- Single-character reaction shot
- Detail or insert shot
- Cutaway to another person, object, or environment cue
- Reset shot that re-establishes shared space after several close reactions

Common strong pattern:

- Relationship shot
- Reaction shot
- Insert or cutaway
- Alternate reaction shot
- Reset shot

This rhythm prevents the sequence from feeling like repeated crops of the same frame.

If multiple close or medium shots of the same subject appear in one beat, separate them with a different function such as:

- A true angle change
- A detail insert
- A reaction from another subject
- An environment or doorway reset
- A short bridge shot that redirects attention

Bad pattern:

- Front walk
- Front half-body
- Front close-up

Better pattern:

- Walk and relationship setup
- Leg, leash, hand, or environment insert
- Off-axis reaction
- Return to closer coverage only after the action has progressed

Treat each shot as carrying one job, not every job.

Examples of one-shot jobs:

- Establish who is with whom
- Continue the motion beat
- Show where attention shifts
- Show the emotional reaction
- Re-anchor the viewer in the shared space

If a shot tries to do too many of these at once, it often becomes vague and replaceable.

## Hinge Shot Rule

Very short shots can be structurally useful when they act as hinges between larger shots.

A hinge shot is often around a fraction of a second to roughly one second and is used to:

- Redirect attention
- Hide a potentially harsh cut
- Turn motion into reaction
- Mark a shift from relation to subjectivity
- Bridge from a moving shot into a settled thought beat

Typical hinge material:

- Footfall
- Leash or hand detail
- Eyeline change
- Finger movement
- Door, object, or environment cue
- Brief cutaway reaction

Do not use a hinge shot as filler. Use it only when it changes what the next shot means.

## Reset Shot Rule

After several close reactions or inserts, re-establish the shared scene with a reset shot.

A reset shot may be:

- A two-character or group shot
- A doorway or corridor spatial shot
- A wider return to the environment
- A back or over-shoulder relation shot

The reset shot keeps the sequence from collapsing into disconnected portraits. It reminds the viewer where the characters are relative to each other and to the scene.

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

## Editing Tool Boundary Rule

An image-editing pass that starts from the previous ending frame is useful for continuity-preserving crops, inserts, and tighter coverage. It is not a substitute for true camera relocation.

Use an edit-style tool only when the next image should remain in the same camera family, for example:

- Crop from wide to half-body
- Turn the inherited frame into a leg, hand, leash, or prop insert
- Tighten to a head-and-shoulders reaction while preserving the same underlying scene state

Do not use an edit-style tool as proof of a side angle, reverse, or other real angle change. For that, generate the new angle in a camera-aware workflow such as RunningHub and verify the background perspective shift.

Practical rule:

- Same camera family and inherited geometry -> edit from the continuity frame
- New camera placement or claimed side angle -> use a camera-angle workflow, then validate angle read

## Action Carry-Through Rule

Two connected shots must overlap in action logic, not just in character identity.

If the first shot ends on walking, the next shot should usually inherit that walking for a brief beat before changing state. For example:

- Walk -> walk two more steps -> slow -> stop -> think
- Run -> continue run for a beat -> plant foot -> turn
- Reach -> continue reach -> contact -> react

Bad pattern:
- Shot 1: character walking
- Shot 2: character already fully stopped and posing to think

Better pattern:
- Shot 1: character walking
- Shot 2: character still carries the walking momentum for one beat, then naturally slows or stops inside the shot

The next shot must show the transition, not skip over it, unless the cut is intentionally stylized.

## Spatial Relationship Rule

Continuity also includes relative placement and physical logic between connected elements.

Check all of these when a subject interacts with a companion, prop, vehicle, leash, weapon, bag, or other attached object:

- Front/back ordering is consistent
- Occlusion order is believable
- Tension or slack direction makes sense
- Handedness and attachment point stay consistent
- The object does not jump across the body without a motivated transition

Bad pattern:

- Companion appears ahead of the body while the leash or attachment reads behind it
- The prop switches sides between shots without an action explaining it

If the model cannot hold these relationships cleanly in a bridge shot, reduce visibility and keep only the most stable parts that preserve the interaction logic.

## Failure Patterns To Catch

Reject or revise when you see:

- The face drifts into a different-looking version of the character even though costume and hair still match.
- Established elements disappear between shots without a motivated crop or exit.
- A companion subject becomes visually unstable because too much of it is shown in an insert or bridge shot.
- The close-up no longer carries the established scene logic.
- The next shot starts from a different action phase than the previous shot ended on.
- Two adjacent shots use nearly the same frontal coverage and create an obvious split-shot feeling.
- The second shot skips the transition beat and begins after the action has already changed state.
- A shot is described as three-quarter or side angle but still reads as frontal.
- A "side view" still uses frontal background geometry.
- An edited frame is being treated as proof of a real new camera angle.
- Spatial relationships between body, companion, and attached objects become physically inconsistent across the cut.
- A generated close-up is compositionally nice but cannot plausibly be the next camera setup from the prior frame.
- The next shot invents a cleaner or emptier environment than the previous shot established.
- A must-keep story detail disappears even though the later dialogue or action depends on it.

Do not reject only for these reasons:

- An optional descriptive flourish from the script is simplified away
- A non-essential micro gesture is omitted
- A cosmetic transition beat is compressed as long as the plot beat still reads cleanly

## Output Format

Before generation, produce a short continuity brief:

```text
cut_type:
must_keep_subjects:
identity_lock_refs:
must_keep_visible_elements:
must_keep_implied_elements:
must_keep_background_landmarks:
must_keep_story_details:
compressible_story_details:
optional_flavor_details:
can_crop_out:
can_exit_frame:
screen_direction:
camera_axis:
bridge_action:
companion_visibility_mode:
angle_read_test:
coverage_change_reason:
required_action_phase:
verdict:
why:
```

Use `verdict: block` when continuity is not yet strong enough to generate.

## References

Read [references/continuity-checklist.md](references/continuity-checklist.md) when you need the detailed checklist or examples of bad and good cuts.
