from __future__ import annotations

import os
import shutil
from pathlib import Path

from factoholic_script_engine.config import EngineConfig
from factoholic_script_engine.database import Database
from factoholic_script_engine.engine import ScriptEngine
from factoholic_script_engine.indexer import DatasetIndexer
from factoholic_script_engine.parsing import parse_markdown


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "0001_Bo5azL9dpMM.md"


def config_for(dataset: Path, state: Path) -> EngineConfig:
    return EngineConfig(
        dataset_dir=dataset,
        state_dir=state,
        instructions_file=ROOT / "MASTER_INSTRUCTIONS.md",
        workflow_file=ROOT / "WORKFLOW_CONTRACT.md",
    )


def test_parser_reads_actual_markdown_and_separates_analysis() -> None:
    parsed = parse_markdown(SOURCE.read_text(encoding="utf-8"))
    assert parsed.title == "World's Most Expensive School 😱"
    assert parsed.rank == 1
    assert parsed.duration_seconds == 56
    assert parsed.transcript_status == "available"
    assert len(parsed.captions) == 25
    assert parsed.captions[0].timestamp == "00:00:02.639"
    assert "सबसे महंगा स्कूल" in parsed.captions[0].text
    assert parsed.analysis_text and "HOOK" in parsed.analysis_text
    assert parsed.parsing_status == "ok"


def test_incremental_indexing_uses_copied_actual_source_and_does_not_touch_source(tmp_path: Path) -> None:
    dataset = tmp_path / "sample"
    dataset.mkdir()
    copied = dataset / SOURCE.name
    original_hash = __import__("hashlib").sha256(SOURCE.read_bytes()).hexdigest()
    shutil.copy2(SOURCE, copied)
    database = Database(tmp_path / "state" / "sample.sqlite3")
    indexer = DatasetIndexer(config_for(dataset, tmp_path / "state"), database)

    first = indexer.sync()
    second = indexer.sync()
    assert first["discovered_markdown_files"] == 1
    assert first["added"] == 1
    assert second["unchanged"] == 1
    with database.connect() as connection:
        row = connection.execute("SELECT filename,caption_count,content_hash,file_status FROM transcripts").fetchone()
    assert row["filename"] == SOURCE.name
    assert row["caption_count"] == 25
    assert row["content_hash"] == original_hash
    assert row["file_status"] == "available"

    previous_mtime = copied.stat().st_mtime_ns
    os.utime(copied, ns=(previous_mtime + 2_000_000_000, previous_mtime + 2_000_000_000))
    metadata_only = indexer.sync()
    assert metadata_only["unchanged"] == 1

    original = copied.read_bytes()
    modified = original.replace(b"World's Most Expensive School", b"World's Most Exclusive School", 1)
    assert len(modified) == len(original) and modified != original
    copied.write_bytes(modified)
    os.utime(copied, ns=(previous_mtime, previous_mtime))
    content_changed = indexer.sync()
    assert content_changed["updated"] == 1
    with database.connect() as connection:
        row = connection.execute("SELECT title,content_hash FROM transcripts").fetchone()
    assert row["title"] == "World's Most Exclusive School 😱"
    assert row["content_hash"] == __import__("hashlib").sha256(modified).hexdigest()


def test_duplicate_stems_get_stable_distinct_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "sample"
    (dataset / "part-a").mkdir(parents=True)
    (dataset / "part-b").mkdir()
    source = SOURCE.read_text(encoding="utf-8")
    (dataset / "part-a" / "same.md").write_text(source, encoding="utf-8")
    (dataset / "part-b" / "same.md").write_text(source.replace("Expensive School", "Exclusive School"), encoding="utf-8")
    database = Database(tmp_path / "state" / "sample.sqlite3")
    indexer = DatasetIndexer(config_for(dataset, tmp_path / "state"), database)

    first = indexer.sync()
    with database.connect() as connection:
        before = {row["relative_path"]: row["transcript_id"] for row in connection.execute("SELECT relative_path,transcript_id FROM transcripts")}
    second = indexer.sync()
    with database.connect() as connection:
        after = {row["relative_path"]: row["transcript_id"] for row in connection.execute("SELECT relative_path,transcript_id FROM transcripts")}
    assert first["added"] == 2
    assert second["unchanged"] == 2
    assert before == after
    assert len(set(after.values())) == 2


def test_overview_lists_malformed_incomplete_duplicate_and_missing_files(tmp_path: Path) -> None:
    dataset = tmp_path / "sample"
    dataset.mkdir()
    sample = SOURCE.read_text(encoding="utf-8")
    (dataset / "complete-a.md").write_text(sample, encoding="utf-8")
    (dataset / "complete-b.md").write_text(sample, encoding="utf-8")
    (dataset / "missing.md").write_text(sample.replace("Expensive School", "Exclusive School"), encoding="utf-8")
    (dataset / "malformed.md").write_text("# Broken\n\n- **Transcript Status:** available\n\n## Transcript\n- `00:00:99` invalid time\n- `00:00:01` valid caption\n", encoding="utf-8")
    (dataset / "incomplete.md").write_text("# Incomplete\n\n- **Transcript Status:** unavailable\n\n## Transcript\n", encoding="utf-8")
    engine = ScriptEngine(config_for(dataset, tmp_path / "state"))
    (dataset / "missing.md").unlink()

    overview = engine.get_dataset_overview()
    details = overview["file_details"]
    assert [item["relative_path"] for item in details["missing_files"]] == ["missing.md"]
    assert {item["relative_path"] for item in details["malformed_files"]} == {"incomplete.md", "malformed.md"}
    assert [item["relative_path"] for item in details["incomplete_or_unavailable_transcripts"]] == ["incomplete.md"]
    assert {item["filename"] for item in details["duplicate_content_groups"][0]["files"]} == {"complete-a.md", "complete-b.md"}
    assert overview["counts"]["duplicate_content_groups"] == 1
    assert overview["counts"]["missing_files"] == 1


def test_actual_corpus_overview_and_caption_search(full_engine: ScriptEngine) -> None:
    engine = full_engine
    overview = engine.get_dataset_overview(refresh=False)
    assert overview["counts"]["available_files"] == 1000
    assert overview["counts"]["missing_files"] == 0
    assert overview["counts"]["unreadable_files"] == 0
    assert overview["counts"]["malformed_files"] == 0
    assert overview["counts"]["files_with_existing_analysis"] == 1000
    assert overview["filename_numbering"]["gaps"] == [576, 619, 665, 670, 684, 711, 779, 960, 977, 986, 990]
    search = engine.search_references("expensive school", limit=5)
    assert search["returned"] >= 1
    assert search["results"][0]["transcript_id"] == SOURCE.stem
    caption_search = engine.search_language_patterns("सबसे महंगा स्कूल", exact_phrase=True, limit=5)
    assert caption_search["returned"] >= 1
    assert caption_search["results"][0]["filename"] == SOURCE.name
    assert caption_search["results"][0]["timestamp"] == "00:00:02.639"
