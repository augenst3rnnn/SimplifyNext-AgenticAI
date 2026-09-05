#!/usr/bin/env python3
"""Fail fast when the NaVILA PyTorch build cannot execute on the current GPU."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CudaReport:
    gpu: str
    capability: tuple[int, int]
    torch_version: str
    torch_cuda: str
    compiled_arches: tuple[str, ...]

    @property
    def capability_name(self) -> str:
        return f"sm_{self.capability[0]}{self.capability[1]}"

    def summary(self) -> str:
        arches = " ".join(self.compiled_arches) or "unknown"
        return (
            f"gpu={self.gpu}; capability={self.capability_name}; "
            f"torch={self.torch_version}; torch_cuda={self.torch_cuda}; "
            f"compiled_arches={arches}"
        )


def _version_tuple(value: str | None) -> tuple[int, int]:
    if not value:
        return (0, 0)
    parts = value.split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return (0, 0)


def inspect_and_test_cuda(torch_module: Any) -> CudaReport:
    """Return the active CUDA stack after executing real CUDA kernels."""

    cuda = torch_module.cuda
    if not cuda.is_available():
        raise RuntimeError(
            "torch.cuda.is_available() is false; check the NVIDIA driver and "
            "install the CUDA-enabled NaVILA environment"
        )

    device_index = cuda.current_device()
    capability = tuple(cuda.get_device_capability(device_index))
    torch_cuda = str(getattr(torch_module.version, "cuda", None) or "none")
    report = CudaReport(
        gpu=str(cuda.get_device_name(device_index)),
        capability=(int(capability[0]), int(capability[1])),
        torch_version=str(torch_module.__version__),
        torch_cuda=torch_cuda,
        compiled_arches=tuple(cuda.get_arch_list()),
    )

    if report.capability >= (10, 0) and _version_tuple(torch_cuda) < (12, 8):
        raise RuntimeError(
            "Blackwell requires a PyTorch wheel built with CUDA 12.8 or newer; "
            f"this environment reports CUDA {torch_cuda}. {report.summary()}"
        )

    try:
        vector = torch_module.ones(
            256, device=f"cuda:{device_index}", dtype=torch_module.float32
        )
        value = ((vector * vector) + 1).sum()
        value.item()
        cuda.synchronize(device_index)
    except Exception as exc:
        raise RuntimeError(
            "the installed PyTorch CUDA kernels cannot execute on "
            f"{report.capability_name}: {exc}. {report.summary()}"
        ) from exc

    try:
        from flash_attn import flash_attn_func

        query = torch_module.randn(
            (1, 8, 4, 64),
            device=f"cuda:{device_index}",
            dtype=torch_module.float16,
        )
        flash_attn_func(query, query, query, causal=False).sum().item()
        cuda.synchronize(device_index)
    except Exception as exc:
        raise RuntimeError(
            "the installed FlashAttention CUDA kernels cannot execute on "
            f"{report.capability_name}: {exc}. {report.summary()}"
        ) from exc

    return report


def main() -> int:
    try:
        import torch

        report = inspect_and_test_cuda(torch)
    except Exception as exc:
        print("NaVILA CUDA preflight failed before model loading.", file=sys.stderr)
        print(f"Reason: {exc}", file=sys.stderr)
        print("Repair the dedicated NaVILA environment with:", file=sys.stderr)
        print(f"  {PROJECT_ROOT}/scripts/setup_navila_env.sh", file=sys.stderr)
        return 2

    print(f"NaVILA CUDA preflight passed: {report.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
