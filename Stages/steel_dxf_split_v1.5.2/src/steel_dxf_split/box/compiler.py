from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analysis import run_analysis
from .assembly import AssemblySearchResult
from .contracts import BoxSourceContract, BoxSourceLimits
from .frontend import run_frontend
from .manufacturing import freeze_manufacturing
from .manufacturing_ir import BoxManufacturingIR
from .metadata import BoxMetadata
from .proofs import ProofReport
from .solve import run_solve
from .source_ir import SourceDocumentIR
from .validation import run_validation


@dataclass(frozen=True, slots=True)
class BoxCoreCompilation:
    source: SourceDocumentIR
    metadata: BoxMetadata
    search: AssemblySearchResult
    manufacturing: BoxManufacturingIR
    proof_report: ProofReport
    validation: dict[str, object]

    @property
    def fingerprint(self) -> str:
        return self.manufacturing.fingerprint


@dataclass(frozen=True, slots=True)
class BoxCompileConfig:
    output_dir: Path
    source_contract: BoxSourceContract
    report_path: Path | None = None
    require_auto_accept: bool = False
    release_attestation_path: Path | None = None
    source_limits: BoxSourceLimits = BoxSourceLimits()


@dataclass(frozen=True, slots=True)
class BoxCompilationResult:
    production_path: Path | None
    review_path: Path | None
    report_path: Path
    report: dict[str, object]
    core: BoxCoreCompilation


def compile_box_core(
    input_path: str | Path,
    source_contract: BoxSourceContract,
    *,
    source_limits: BoxSourceLimits = BoxSourceLimits(),
) -> BoxCoreCompilation:
    """Compile one BOX source through the Project 2 passes only."""

    source_contract.validate()
    source = run_frontend(input_path, limits=source_limits)
    metadata = run_analysis(source)
    search = run_solve(source, metadata)
    manufacturing = freeze_manufacturing(search)
    validation = run_validation(manufacturing)
    return BoxCoreCompilation(
        source=source,
        metadata=metadata,
        search=search,
        manufacturing=manufacturing,
        proof_report=search.best.proof_report,
        validation=validation,
    )


def compile_box(
    input_path: str | Path,
    *,
    config: BoxCompileConfig,
) -> BoxCompilationResult:
    """Compile and atomically deliver one BOX drawing."""

    from .delivery import deliver_box_compilation
    from .release import load_verified_box_release_attestation

    release_attestation = load_verified_box_release_attestation(
        config.release_attestation_path
    )
    core = compile_box_core(
        input_path,
        config.source_contract,
        source_limits=config.source_limits,
    )
    delivered = deliver_box_compilation(
        core,
        config=config,
        release_attestation=release_attestation,
    )
    return BoxCompilationResult(
        production_path=delivered.production_path,
        review_path=delivered.review_path,
        report_path=delivered.report_path,
        report=delivered.report,
        core=core,
    )
