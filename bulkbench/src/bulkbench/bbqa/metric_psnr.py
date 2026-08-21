"""PSNR metric implementation."""

import itertools
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import TypedDict

from benchstats.common import LoggingConsole
from benchstats.compare import poolBenchmarks
from skimage.io import imread
from skimage.metrics import peak_signal_noise_ratio

from ..bulkbench import EAGER_GROUP_PREFIX, _RESULT_IMAGE_SUFFIXES, _RESULT_VIDEO_SUFFIXES
from ..parser_JSON import _ALT_DELIMITER, get_benchmark_sources, parse_filter


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
                                images.add(entry.name)
                            elif suffix in _RESULT_VIDEO_SUFFIXES:
                                videos.add(entry.name)
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

    distorted_file: str  # relative to the top_dir
    psnr: float
    comparison_pfx: str


def _compute_psnr_images(ref_img: Path, distorted_img: Path, logger: LoggingConsole) -> float:
    """Compute PSNR for a given reference and distorted image."""
    logger.trace(f"Computing PSNR for image '{ref_img}' and '{distorted_img}'")
    try:
        reference = imread(ref_img)
        distorted = imread(distorted_img)
        if reference.shape != distorted.shape:
            logger.error(
                f"Cannot compute PSNR for images '{ref_img}' and '{distorted_img}': "
                f"image shapes differ ({reference.shape} != {distorted.shape})"
            )
            return float("nan")

        return float(peak_signal_noise_ratio(reference, distorted))
    except Exception as error:  # noqa: BLE001 - metric failures are reported as NaN
        logger.error(
            f"Failed to compute PSNR for images '{ref_img}' and '{distorted_img}': {error}"
        )
        return float("nan")


_RE_FFMPEG_PARSER = re.compile(
    r"\baverage:(inf|nan|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\b", re.IGNORECASE
)


def _compute_psnr_videos(ref_vid: Path, distorted_vid: Path, logger: LoggingConsole) -> float:
    """Compute PSNR for a given reference and distorted video."""
    logger.trace(f"Computing PSNR for video '{ref_vid}' and '{distorted_vid}'")

    def probe(video: Path) -> tuple[int, int, int]:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=width,height,nb_read_frames",
            "-of",
            "json",
            str(video),
        ]
        completed = subprocess.run(command, capture_output=True, check=True, text=True)
        streams = json.loads(completed.stdout).get("streams", [])
        if len(streams) != 1:
            raise ValueError(f"expected one primary video stream, found {len(streams)}")

        stream = streams[0]
        try:
            return int(stream["width"]), int(stream["height"]), int(stream["nb_read_frames"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"incomplete video stream information: {stream!r}") from error

    try:
        ref_info = probe(ref_vid)
        distorted_info = probe(distorted_vid)
        if ref_info != distorted_info:
            logger.error(
                f"Cannot compute PSNR for videos '{ref_vid}' and '{distorted_vid}': "
                "video dimensions or decoded frame counts differ "
                f"({ref_info} != {distorted_info})"
            )
            return float("nan")

        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            str(ref_vid),
            "-i",
            str(distorted_vid),
            "-lavfi",
            "[0:v:0][1:v:0]psnr",
            "-f",
            "null",
            "-",
        ]
        completed = subprocess.run(command, capture_output=True, check=True, text=True)
        matches = _RE_FFMPEG_PARSER.findall(completed.stderr)
        if not matches:
            raise ValueError("FFmpeg output did not contain an overall average PSNR")

        return float(matches[-1])
    except Exception as error:  # noqa: BLE001 - metric failures are reported as NaN
        logger.error(
            f"Failed to compute PSNR for videos '{ref_vid}' and '{distorted_vid}': {error}"
        )
        return float("nan")


_RE_COMPILE_FLAG = re.compile(r"_tc_(True|False)_")


def _strip_compile_flag(fname: str) -> str:
    """Strip the compile flag from a file name.
    Not verifying if it's there since this would narrow possible comparison modes"""
    return _RE_COMPILE_FLAG.sub(r"", fname)


def _compute_psnr_all(
    cmp_prefix: str,
    ref_media: MediaFiles,
    distorted_media: MediaFiles,
    top_dir: Path,
    logger: LoggingConsole,
) -> dict[str, list[PSNRResult]]:
    """Compute PSNR for the given reference and distorted media files."""
    logger.trace(
        f"Computing PSNR for '{cmp_prefix}'. Reference: {ref_media}. Distorted: {distorted_media}."
    )

    _impl: dict[str, Callable[[Path, Path, LoggingConsole], float]] = {
        "images": _compute_psnr_images,
        "videos": _compute_psnr_videos,
    }

    results: dict[str, list[PSNRResult]] = {}

    def _do(what: str):
        ref, dist = ref_media[what], distorted_media[what]
        if len(ref) == 0 and len(dist) == 0:
            return

        psnr_func = _impl[what]

        # the core runner adds a compile flag to the file name, so we have to strip it
        stripped_ref = {_strip_compile_flag(file): file for file in ref}
        assert len(stripped_ref) == len(ref), (
            "Stripping the compile flag should not change the number of files"
        )
        stripped_dist = {_strip_compile_flag(file): file for file in dist}
        assert len(stripped_dist) == len(dist), (
            "Stripping the compile flag should not change the number of files"
        )

        cmn = stripped_ref.keys() & stripped_dist.keys()
        if len(cmn) < len(stripped_ref):
            logger.warning(
                f"Reference {what} '{stripped_ref.keys() - stripped_dist.keys()}' not found in distorted media. "
                "(note that the compile flag `_tc_(True|False)_` was stripped from names)"
            )
        if len(cmn) < len(stripped_dist):
            logger.warning(
                f"Distorted {what} '{stripped_dist.keys() - stripped_ref.keys()}' not found in reference media."
                "(note that the compile flag `_tc_(True|False)_` was stripped from names)"
            )
        for file in cmn:
            ref_file = ref_media["dir"] / stripped_ref[file]
            dist_file = distorted_media["dir"] / stripped_dist[file]
            psnr = psnr_func(ref_file, dist_file, logger)

            res_rel = ref_file.relative_to(top_dir)
            dist_rel = dist_file.relative_to(top_dir)
            assert res_rel not in results
            results[str(res_rel)] = PSNRResult(
                distorted_file=str(dist_rel), psnr=psnr, comparison_pfx=cmp_prefix
            )

    _do("images")
    _do("videos")

    if not results:
        logger.warning(f"No PSNR results computed for '{cmp_prefix}', no matching files found")

    return results


def _process_files(
    references: dict[str, dict[str, MediaFiles]],
    runs: dict[str, dict[str, MediaFiles]],
    top_dir: Path,
    logger: LoggingConsole,
) -> dict[str, list[PSNRResult]]:
    """Compute PSNR for all media files in a collection.
    Returns a dictionary reference_file_name -> list[PSNRResult]
    """
    results: dict[str, list[PSNRResult]] = {}

    def _do_compute(
        cmp_prefix: str, Ref_media: MediaFiles, distorted_media: MediaFiles
    ) -> dict[str, list[PSNRResult]]:
        nonlocal results  # not necessary, but documents the intention to modify it
        new_r = _compute_psnr_all(cmp_prefix, Ref_media, distorted_media, top_dir, logger)
        for r in new_r:
            if r in results:
                results[r].extend(new_r[r])
            else:
                results[r] = new_r[r]

    def _make_cmp_prefix(model_name: str, ref_name: str, run_name: str) -> str:
        # return f"{model_name} {_ALT_DELIMITER} {ref_name} vs {run_name}:"
        return model_name

    for model_name in sorted(references.keys()):
        ref_alternatives = references[model_name]
        if len(ref_alternatives) == 0:
            logger.warning("Benchmark '", model_name, "' has no references! Skipping it.")
            continue

        if len(ref_alternatives) > 1:
            # first do mutual PSNR for all references
            for A_name, B_name in itertools.combinations(sorted(ref_alternatives.keys()), 2):
                assert isinstance(A_name, str) and isinstance(B_name, str)
                assert A_name != B_name
                A_media = ref_alternatives[A_name]
                B_media = ref_alternatives[B_name]

                cmp_prefix = _make_cmp_prefix(model_name, A_name, B_name)
                _do_compute(cmp_prefix, A_media, B_media)

                cmp_prefix = _make_cmp_prefix(model_name, B_name, A_name)
                _do_compute(cmp_prefix, B_media, A_media)

        # next use the ref for all its runs
        if model_name not in runs:
            logger.warning(
                f"Benchmark '{model_name}' has no runs to compare references to. Skipping it."
            )
            continue

        run_alternatives = runs[model_name]
        for ref_name in sorted(ref_alternatives.keys()):
            ref_media = ref_alternatives[ref_name]
            for run_name in sorted(run_alternatives.keys()):
                run_media = run_alternatives[run_name]

                cmp_prefix = _make_cmp_prefix(model_name, ref_name, run_name)
                _do_compute(cmp_prefix, ref_media, run_media)
    return results


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

    coll = _process_files(references, runs, results_dir, logger)
    logger.info("Computed PSNR collection:", coll)

    return 0
