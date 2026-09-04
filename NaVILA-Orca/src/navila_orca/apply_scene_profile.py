"""Apply a named NaVILA MuJoCo physics profile to an open OrcaLab scene.

This is intentionally separate from the navigation runner: authors can switch
the OrcaLab scene and re-apply the same global physics contract before a Go2
actor or a camera has been added to that layout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from typing import Any, Sequence

from .orcalab_runtime.scene_options import resolve_scene_options_profile


PROFILE_FIELDS = (
    "timestep",
    "integrator",
    "gravity",
    "density",
    "viscosity",
    "wind",
    "iterations",
    "ls_iterations",
    "noslip_iterations",
    "ccd_iterations",
    "sdf_initpoints",
    "sdf_iterations",
    "tolerance",
    "ls_tolerance",
    "noslip_tolerance",
    "ccd_tolerance",
)


def _scene_options(profile: str) -> Any:
    """Load the project-owned canonical scene profile."""
    return resolve_scene_options_profile(profile)


def _selected_options(response: Any) -> dict[str, Any]:
    """Return only values owned by a scene profile, in a stable JSON form."""

    snapshot: dict[str, Any] = {}
    for name in PROFILE_FIELDS:
        value = getattr(response, name)
        snapshot[name] = list(value) if name in {"gravity", "wind"} else value
    return snapshot


def _assert_applied(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    mismatches: list[str] = []
    for name, wanted in expected.items():
        got = actual[name]
        if isinstance(wanted, (tuple, list)):
            if len(got) != len(wanted) or any(
                abs(float(left) - float(right)) > 1.0e-9
                for left, right in zip(got, wanted, strict=True)
            ):
                mismatches.append(f"{name}={got!r}, expected {list(wanted)!r}")
        elif isinstance(wanted, float):
            if abs(float(got) - wanted) > 1.0e-9:
                mismatches.append(f"{name}={got!r}, expected {wanted!r}")
        elif got != wanted:
            mismatches.append(f"{name}={got!r}, expected {wanted!r}")
    if mismatches:
        raise RuntimeError(
            "OrcaLab rejected part of the scene profile: " + "; ".join(mismatches)
        )


def _is_applied(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    try:
        _assert_applied(actual, expected)
    except RuntimeError:
        return False
    return True


async def apply_scene_profile(address: str, profile: str) -> dict[str, Any]:
    """Set and verify global MuJoCo options of the remote, already-open scene."""

    import grpc
    from orca_gym.protos import mjc_message_pb2, mjc_message_pb2_grpc

    options = _scene_options(profile)
    expected = asdict(options)
    channel = grpc.aio.insecure_channel(address)
    try:
        stub = mjc_message_pb2_grpc.GrpcServiceStub(channel)
        try:
            before = await stub.QueryOptConfig(mjc_message_pb2.QueryOptConfigRequest())
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                raise RuntimeError(
                    f"OrcaGym runtime is not listening at {address}. In OrcaLab, start "
                    "the current scene in no-simulation/external mode first; that mode "
                    "publishes the scene and exposes the configured OrcaGym port."
                ) from exc
            raise
        before_values = _selected_options(before)
        if _is_applied(before_values, expected):
            return {
                "address": address,
                "profile": profile,
                "before": before_values,
                "applied": before_values,
                "changed": False,
            }
        request = mjc_message_pb2.SetOptConfigRequest(
            timestep=options.timestep,
            impratio=before.impratio,
            tolerance=options.tolerance,
            ls_tolerance=options.ls_tolerance,
            noslip_tolerance=options.noslip_tolerance,
            ccd_tolerance=options.ccd_tolerance,
            gravity=list(options.gravity),
            wind=list(options.wind),
            magnetic=list(before.magnetic),
            density=options.density,
            viscosity=options.viscosity,
            o_margin=before.o_margin,
            o_solref=list(before.o_solref),
            o_solimp=list(before.o_solimp),
            o_friction=list(before.o_friction),
            integrator=options.integrator,
            cone=before.cone,
            jacobian=before.jacobian,
            solver=before.solver,
            iterations=options.iterations,
            ls_iterations=options.ls_iterations,
            noslip_iterations=options.noslip_iterations,
            ccd_iterations=options.ccd_iterations,
            disableflags=before.disableflags,
            enableflags=before.enableflags,
            disableactuator=before.disableactuator,
            sdf_initpoints=options.sdf_initpoints,
            sdf_iterations=options.sdf_iterations,
        )
        await stub.SetOptConfig(request)
        after = await stub.QueryOptConfig(mjc_message_pb2.QueryOptConfigRequest())
    finally:
        await channel.close()

    after_values = _selected_options(after)
    _assert_applied(after_values, expected)
    return {
        "address": address,
        "profile": profile,
        "before": before_values,
        "applied": after_values,
        "changed": True,
    }


async def watch_scene_profile(
    address: str,
    profile: str,
    *,
    interval: float,
    parent_pid: int | None = None,
) -> None:
    """Keep a profile applied while a GUI switches/restarts scene runtimes."""

    if interval <= 0:
        raise ValueError("interval must be positive")
    runtime_was_available = False
    while True:
        if parent_pid is not None:
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                return
        try:
            result = await apply_scene_profile(address, profile)
        except RuntimeError as exc:
            # The runtime is expected to disappear briefly during a scene switch.
            if runtime_was_available:
                print(f"[orcalab-profile] waiting for runtime: {exc}", flush=True)
            runtime_was_available = False
        else:
            runtime_was_available = True
            if result["changed"]:
                print(
                    "[orcalab-profile] applied "
                    f"{profile} to the newly available scene at {address}",
                    flush=True,
                )
        await asyncio.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply a NaVILA MuJoCo profile to the currently open OrcaLab scene."
    )
    parser.add_argument("--orcagym-address", default="127.0.0.1:50051")
    parser.add_argument(
        "--scene-profile",
        choices=("orca-train", "orca-runtime"),
        default="orca-train",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep applying the profile after OrcaLab scene/runtime switches",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=1.0,
        help="seconds between runtime checks in --watch mode (default: 1)",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        help="exit watch mode when this GUI process exits",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.watch:
            asyncio.run(
                watch_scene_profile(
                    args.orcagym_address,
                    args.scene_profile,
                    interval=args.watch_interval,
                    parent_pid=args.parent_pid,
                )
            )
            return 0
        result = asyncio.run(apply_scene_profile(args.orcagym_address, args.scene_profile))
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
