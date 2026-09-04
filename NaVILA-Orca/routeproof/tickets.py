"""Local facilities-ticket receipt used by the RouteProof prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from PIL import Image


@dataclass(frozen=True, slots=True)
class TicketReceipt:
    ticket_id: str
    ticket_path: str
    status: str = "OPEN"

    def as_dict(self) -> dict[str, str]:
        return {
            "ticket_id": self.ticket_id,
            "ticket_path": self.ticket_path,
            "status": self.status,
        }


class LocalFacilitiesTicketStore:
    """Write evidence images and a JSON ticket to the run output directory."""

    def __init__(self, output_directory: str | Path) -> None:
        self.output_directory = Path(output_directory).expanduser().resolve()
        self.evidence_directory = self.output_directory / "evidence"
        self.ticket_directory = self.output_directory / "tickets"

    def save_evidence(
        self,
        *,
        route_id: str,
        image: Image.Image,
        step_id: int,
    ) -> str:
        self.evidence_directory.mkdir(parents=True, exist_ok=True)
        route_slug = _slug(route_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.evidence_directory / (
            f"{stamp}_{route_slug}_step_{int(step_id):06d}.jpg"
        )
        image.convert("RGB").save(path, format="JPEG", quality=92)
        return str(path)

    def create_no_safe_route_ticket(
        self,
        *,
        destination: str,
        requester: str,
        attempts: Sequence[Mapping[str, Any]],
        position_xyz: Sequence[float],
        obstacle_label: str,
        reason: str,
        evidence_paths: Sequence[str],
    ) -> TicketReceipt:
        self.ticket_directory.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(timezone.utc)
        ticket_id = (
            f"RP-{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8].upper()}"
        )
        payload = {
            "ticket_id": ticket_id,
            "status": "OPEN",
            "category": "ACCESSIBLE_ROUTE_BLOCKED",
            "created_at": created_at.isoformat(),
            "destination": destination,
            "requester": requester,
            "summary": "No approved accessible route could be verified",
            "reason": reason,
            "latest_obstacle": obstacle_label,
            "latest_robot_position_xyz": [float(value) for value in position_xyz],
            "attempts": list(attempts),
            "evidence": list(evidence_paths),
        }
        path = self.ticket_directory / f"{ticket_id.lower()}.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return TicketReceipt(ticket_id=ticket_id, ticket_path=str(path))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip()).strip("-")
    return slug or "route"
