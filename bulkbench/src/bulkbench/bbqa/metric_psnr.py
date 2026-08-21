"""PSNR metric implementation."""

import os
import itertools
from pathlib import Path, PurePath
from benchstats.common import LoggingConsole
from benchstats.compare import poolBenchmarks
from typing import Any, TypedDict

from ..parser_JSON import get_benchmark_sources, _ALT_DELIMITER, parse_filter
from ..bulkbench import EAGER_GROUP_PREFIX, _RESULT_IMAGE_SUFFIXES, _RESULT_VIDEO_SUFFIXES


def _is_eager_alternative(alt: str) -> bool:
    """Determines if a benchmark alternative was made in an eager mode.

    By construction, get_benchmark_sources() makes benchmark alternative
    names from directory names and benchmark group name (if not requested by a user
    to be in a benchmark entity id with a `filter=1`/--args=1 argument) is going to always be
    the last part of the alternative name. We just need to split it correctly and check if it
    starts with the eager group prefix.
    """
    parts = PurePath(alt).parts
    return parts[-1].startswith(EAGER_GROUP_PREFIX)


def _split_references_and_runs(
    pool: dict[str, dict[str, str]],
    logger: LoggingConsole,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    references = {}
    runs = {}
    for k, v in pool.items():
        dref = {}
        drun = {}
        for a, d in v.items():
            if _is_eager_alternative(a):
                dref[a] = d
            else:
                drun[a] = d
        if dref:
            references[k] = dref
        if drun:
            runs[k] = drun
    logger.debug("_split_references_and_runs references:", references)
    logger.debug("_split_references_and_runs runs:", runs)
    return references, runs


def _check_refs_runs(
    references: dict[str, dict[str, str]], runs: dict[str, dict[str, str]], logger: LoggingConsole
) -> int:
    if not references:
        logger.error(
            "No reference data found. You can generate it with `eager_in_patches` field "
            "set correctly in a configs file of a `bulkbench` project for a group you're interested "
            "in, or you can make such "
            "references manually and store them under respective `eager_*` groups result directories."
        )
        return 2
    if not runs:
        logger.error("No run data found. Did you run a `bulkbench` project?")
        return 3

    unmatched_refs = references.keys() - runs.keys()
    if unmatched_refs:
        logger.warning(
            "These references don't have corresponding run data to be compared with and will be ignored:",
            {k: references[k] for k in unmatched_refs},
        )
    unmatched_runs = runs.keys() - references.keys()
    if unmatched_runs:
        logger.warning(
            "These runs don't have corresponding reference data to be compared with and will be ignored:",
            {k: runs[k] for k in unmatched_runs},
        )
    return 0


class MediaFiles(TypedDict):
    """Describe paths to available media files in a directory."""

    dir: Path
    images: frozenset[str]
    videos: frozenset[str]


def _get_media_files(
    references: dict[str, dict[str, str]], runs: dict[str, dict[str, str]], logger: LoggingConsole
) -> tuple[dict[str, dict[str, MediaFiles]], dict[str, dict[str, MediaFiles]]]:
    """Replaces a path to a directory in inner dict values with a description of available
    media files in it according to _RESULT_IMAGE_SUFFIXES and _RESULT_VIDEO_SUFFIXES.
    If not a single media file is found, the corresponding key is removed and a warning
    is logged. Empty high-level keys are also removed.
    """

    def collect(
        sources: dict[str, dict[str, str]], source_name: str
    ) -> dict[str, dict[str, MediaFiles]]:
        media_sources: dict[str, dict[str, MediaFiles]] = {}

        for benchmark, alternatives in sources.items():
            media_alternatives: dict[str, MediaFiles] = {}

            for alternative, directory in alternatives.items():
                try:
                    path = Path(directory)
                    if not path.is_dir():
                        raise ValueError("path is not a directory")

                    images: set[str] = set()
                    videos: set[str] = set()
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if not entry.is_file():
                                continue

                            suffix = Path(entry.name).suffix
                            if suffix in _RESULT_IMAGE_SUFFIXES:
                                images = images.add(entry.name)
                            elif suffix in _RESULT_VIDEO_SUFFIXES:
                                videos = videos.add(entry.name)
                except (OSError, TypeError, ValueError) as error:
                    logger.error(
                        f"Invalid {source_name} media directory for "
                        f"{benchmark!r}/{alternative!r}: {directory!r}: {error}"
                    )
                    continue

                if not images and not videos:
                    logger.warning(
                        f"No media files found in {source_name} directory for "
                        f"{benchmark!r}/{alternative!r}: {directory!r}"
                    )
                    continue

                media_alternatives[alternative] = {
                    "dir": path,
                    "images": frozenset(images),
                    "videos": frozenset(videos),
                }

            if media_alternatives:
                media_sources[benchmark] = media_alternatives

        return media_sources

    return collect(references, "eager_"), collect(runs, "run")


class PSNRResult(TypedDict):
    """Describe a PSNR for a given "distorted" file using some reference file"""

    distorted_file: Path
    psnr: float
    comparison_name: str


def _process_files(
    references: dict[str, dict[str, MediaFiles]],
    runs: dict[str, dict[str, MediaFiles]],
    logger: LoggingConsole,
) -> dict[str, list[PSNRResult]]:
    """Compute PSNR for all media files in a collection.
    Returns a dictionary reference_file_name -> list[PSNRResult]
    """
    results: dict[str, list[PSNRResult]] = {}

    def compute_psnr_all(cmp_prefix:str, Ref_media: MediaFiles, distorted_media: MediaFiles) -> dict[str, list[PSNRResult]]:
        return {}

    for model_name in sorted(references.keys()):
        alternatives = references[model_name]
        if len(alternatives) <= 1:
            logger.warning(
                "Benchmark '%s%s' has no alternatives. Skipping it.",
                model_name,
                next(iter(alternatives.keys())) if len(alternatives) == 1 else "???",
            )
            continue

        # first do mutual PSNR for all references
        for A_name, B_name in itertools.combinations(sorted(alternatives.keys()), 2):
            assert isinstance(A_name, str) and isinstance(B_name, str)
            assert A_name != B_name
            A_media = alternatives[A_name]
            B_media = alternatives[B_name]

            cmp_prefix = f"{model_name} {_ALT_DELIMITER} {A_name} vs {B_name}"
            results.update(compute_psnr_all(cmp_prefix, A_media, B_media))

            cmp_prefix = f"{model_name} {_ALT_DELIMITER} {B_name} vs {A_name}"
            results.update(compute_psnr_all(cmp_prefix, B_media, A_media))


def metric_psnr(logger: LoggingConsole, results_dir: Path, args: list[str] | None = None) -> int:
    """Compute PSNR between images and videos in the results directory using eager mode resuls "
    "as reference."""
    if args:
        assert len(args) == 1, "PSNR metric supports only one argument"
        filter = args[0]
    else:
        filter = None

    filter_indices = parse_filter(filter)
    if filter_indices is not None and 1 in filter_indices:
        logger.warning(
            "By letting --args to have 1, you're requesting a benchmark group name "
            "(second closest parent of a `timings.json` file in a model config run results) to be "
            "a part of benchmarking entity id, instead of an alternative id. This will break "
            "this tool assumptions and will most likely fail later."
        )

    # there should be no duplicate by construction.
    src = dict(
        get_benchmark_sources(results_dir, filter_indices, debug_log=logger, ignore_eager=False)
    )
    pool, _ = poolBenchmarks(_ALT_DELIMITER, src, None, logger)

    references, runs = _split_references_and_runs(pool, logger)
    if (ret := _check_refs_runs(references, runs, logger)) != 0:
        return ret

    references, runs = _get_media_files(references, runs, logger)
    logger.debug("_get_media_files references:", references)
    logger.debug("_get_media_files runs:", runs)

    coll = _process_files(references, runs, logger)
    logger.info("PSNR collection:", coll)

    return 0
