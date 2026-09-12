from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import numpy as np

from ..models import Event, Inference
from .kuka_parser import collision_time_seconds, parse_collision_indices


class KukaCollisionHints:
    """Evidence-backed hints specific to Part I of the KUKA collision dataset."""

    event_label = "collision"

    signal_names = {
        "CmdTrq": "commanded_joint_torque",
        "Grt": "gravity_torque",
        "MsrExtTrq": "measured_external_joint_torque",
        "MsrFrc": "measured_cartesian_force_torque",
        "MsrTrq": "measured_joint_torque",
        "PosMsr": "measured_joint_position",
        "Jcb": "jacobian",
        "Mass": "mass_matrix",
        "rt_tout": "time",
        "JK_moments": "event_sample_indices",
    }

    primary_plot_variable = "MsrExtTrq"

    def parse_run_metadata(self, run_dir: Path) -> dict[str, Any]:
        readme = run_dir / "ReadMe.txt"
        result: dict[str, Any] = {"metadata_source": readme.name if readme.exists() else None}
        if not readme.exists():
            return result
        text = readme.read_text(encoding="utf-8", errors="replace")
        executable = re.search(r"^Executable:\s*(.+)$", text, re.MULTILINE)
        start = re.search(r"^Start time:\s*(.+)$", text, re.MULTILINE)
        collision_block = text.split("Collision(ball):", 1)[1] if "Collision(ball):" in text else ""
        result.update(
            executable=executable.group(1).strip() if executable else None,
            start_time=start.group(1).strip() if start else None,
            collision_times=re.findall(r"\b\d{2}:\d{2}:\d{2}\b", collision_block),
            collision_object="ball" if "Collision(ball):" in text else None,
        )
        return result

    def events(self, run_dir: Path, time_axis: np.ndarray | None) -> list[Event]:
        path = run_dir / "JK_moments.mat"
        if not path.exists():
            return []
        raw = parse_collision_indices(
            path,
            n_samples=int(time_axis.size) if time_axis is not None else None,
        )
        result: list[Event] = []
        for position, raw_index in enumerate(raw):
            # MATLAB indices are one-based. Preserve the original and use index-1 only for lookup.
            index = int(raw_index)
            event_time = collision_time_seconds(time_axis, index) if time_axis is not None else None
            result.append(
                Event(
                    event_id=f"{run_dir.name}:event:{position + 1:03d}",
                    observed_index=index,
                    inferred_time_seconds=event_time,
                    interpretation=Inference(
                        value=self.event_label,
                        confidence=0.99,
                        evidence=[
                            "run ReadMe.txt labels matching wall-clock entries as Collision(ball)",
                            "GenMoments.m assigns the same values to JK_moments",
                        ],
                    ),
                )
            )
        return result

    def semantic_claims(self) -> list[dict[str, Any]]:
        return [
            {
                "subject": "rows_after_time_in_8_row_torque_matrices",
                "value": "seven_robot_joint_channels",
                "confidence": 0.99,
                "evidence": [
                    "dataset documentation describes KUKA joint torque sensors",
                    "MATLAB example plots row 1 against rows 2:8",
                    "all six torque/position matrices have 8 rows and an identical time first row",
                ],
            },
            {
                "subject": "JK_moments",
                "value": "one_based_collision_sample_indices",
                "confidence": 0.99,
                "evidence": [
                    "GenMoments.m defines JK_moments",
                    "indices converted at 1 kHz align with ReadMe.txt collision wall-clock times",
                ],
            },
            {
                "subject": "record_boundary",
                "value": "timestamped_directory",
                "confidence": 0.98,
                "evidence": [
                    "each directory has one shared 232-second time axis and one event list",
                    "each directory ReadMe.txt provides one start time",
                ],
            },
        ]

    def unknowns(self) -> list[str]:
        return [
            "The robot model/serial or other hardware identifier is not present in Batch 01 files.",
            "Physical units are not explicitly encoded in the MAT files; torque/force/position units rely on external documentation.",
            "The 42- and 49-value flattening order for Jacobian and mass-matrix rows is not documented in the batch.",
            "The experimental date/year and timezone are not present in per-run metadata.",
            "The intended prediction target and evaluation protocol are not specified by the raw batch.",
        ]


class KukaContactPart2Hints(KukaCollisionHints):
    """Part II semantics over the same shared KUKA MAT layout."""

    event_label = "intentional_contact"

    def parse_run_metadata(self, run_dir: Path) -> dict[str, Any]:
        result = super().parse_run_metadata(run_dir)
        result.update(
            event_semantics=self.event_label,
            event_semantics_source="Part II dataset identity",
            raw_readme_event_label="Collision(ball)" if result.get("collision_object") else None,
        )
        return result

    def semantic_claims(self) -> list[dict[str, Any]]:
        claims = super().semantic_claims()
        claims[1] = {
            "subject": "JK_moments",
            "value": "one_based_intentional_contact_sample_indices",
            "confidence": 0.95,
            "evidence": [
                "Part II dataset identity describes intentional contacts",
                "GenMoments.m defines JK_moments as the source marker vector",
                "indices are one-based and are converted against the 1 kHz time axis",
            ],
        }
        return claims

    def unknowns(self) -> list[str]:
        return super().unknowns() + [
            "The downloaded per-run ReadMe.txt still labels its marker list Collision(ball); "
            "the raw batch alone does not explain this conflict with the Part II intentional-contact identity."
        ]
