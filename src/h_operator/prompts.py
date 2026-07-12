"""Deterministic, read-only H instructions for the four UI checkpoints."""

from __future__ import annotations

from .contracts import CheckpointCommand, OperatorStep


def build_checkpoint_prompt(command: CheckpointCommand) -> str:
    target = {
        OperatorStep.OPEN_PROJECT: (
            f'Open only "{command.project_file}" and verify the visible project name is '
            f'exactly "{command.project}".'
        ),
        OperatorStep.LOAD_FLOW: (
            f'In project "{command.project}", select only Load Flow study case '
            f'"{command.study_case}", run it in ETAP, and show its result view.'
        ),
        OperatorStep.COORDINATION: (
            f'In project "{command.project}", open only the existing protection coordination '
            f'view "{command.view}" and show it.'
        ),
        OperatorStep.ARC_FLASH: (
            f'In project "{command.project}", select only AC Arc Flash study case '
            f'"{command.study_case}", run it in ETAP, and show its result view.'
        ),
    }[command.step]
    return " ".join((
        f"Execute exactly one checkpoint: {command.step.value}.",
        target,
        "Do not edit the electrical model, study settings, cases, equipment, or files.",
        "Use visible UI actions only.",
        "Do not run any other study and do not interpret, approve, or recommend engineering results.",
        "If any identity, case, or view does not match exactly, stop and return failed.",
        "Claim success only when a final visible observation confirms the expected state.",
        f'Return observed_identity exactly as the visible label "{command.expected_observed_identity}"; '
        "set visible_confirmation true only after that final observation.",
        "Report only UI identity and completion evidence; do not infer any engineering result value.",
        "Capture one PNG screenshot of the visible final state and return only the required structured answer.",
    ))
