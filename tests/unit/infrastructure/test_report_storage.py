"""Archivos reales y fallos acotados en los límites de escritura del sistema."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.errors import ReportFileMissing
from app.infrastructure.report_storage import LocalReportStorage


def test_write_creates_root_and_resolves_complete_relative_report(tmp_path):
    root = tmp_path / "new" / "reports"
    storage = LocalReportStorage(root)
    assert not root.exists()
    report_id = uuid4()
    content = "sale_id,customer,total,currency\n1,Cliente ñ,10.00,PEN\n".encode()
    ref = storage.write(report_id, content)
    assert ref == f"report-{report_id}.csv"
    assert not Path(ref).is_absolute()
    assert storage.resolve(ref) == root / ref
    assert storage.resolve(ref).read_bytes() == content
    assert list(root.iterdir()) == [root / ref]


@pytest.mark.parametrize("previous", [None, b"previous complete report\n"])
def test_replacement_publishes_only_closed_complete_file(
    tmp_path, monkeypatch, previous
):
    storage = LocalReportStorage(tmp_path)
    report_id = uuid4()
    ref = f"report-{report_id}.csv"
    target = tmp_path / ref
    if previous is not None:
        target.write_bytes(previous)
    content = b"complete new report\n" * 100
    replace = os.replace
    observed = []
    temporaries = []
    create_temporary = tempfile.NamedTemporaryFile

    def record_temporary(*args, **kwargs):
        temporary = create_temporary(*args, **kwargs)
        temporaries.append(temporary)
        return temporary

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", record_temporary)

    def observe_replace(source, destination):
        source = Path(source)
        assert source.parent == tmp_path
        assert source != target
        assert source.read_bytes() == content
        assert temporaries[-1].closed
        if previous is None:
            with pytest.raises(ReportFileMissing):
                storage.resolve(ref)
        else:
            assert storage.resolve(ref).read_bytes() == previous
        observed.append(source)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", observe_replace)
    assert storage.write(report_id, content) == ref
    assert len(observed) == 1
    assert target.read_bytes() == content
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["write", "replace"])
@pytest.mark.parametrize("previous", [None, b"previous complete report\n"])
def test_io_failure_cleans_temporary_and_preserves_final(
    tmp_path, monkeypatch, failure, previous
):
    storage = LocalReportStorage(tmp_path)
    report_id = uuid4()
    target = tmp_path / f"report-{report_id}.csv"
    if previous is not None:
        target.write_bytes(previous)

    if failure == "write":
        create_temporary = tempfile.NamedTemporaryFile

        def failing_temporary(*args, **kwargs):
            temporary = create_temporary(*args, **kwargs)

            def partial_write(content):
                temporary.file.write(content[:3])
                raise OSError("private disk failure")

            temporary.write = partial_write
            return temporary

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_temporary)
    else:

        def failing_replace(source, destination):
            assert Path(source).read_bytes() == b"new complete report\n"
            raise OSError("private replace failure")

        monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        storage.write(report_id, b"new complete report\n")
    if previous is None:
        assert not target.exists()
        assert not list(tmp_path.iterdir())
    else:
        assert target.read_bytes() == previous
        assert list(tmp_path.iterdir()) == [target]


def test_missing_report_has_public_error_and_private_cause(tmp_path):
    with pytest.raises(ReportFileMissing) as raised:
        LocalReportStorage(tmp_path).resolve(f"report-{uuid4()}.csv")
    assert raised.value.code == "report_file_missing"
    assert str(tmp_path) not in str(raised.value)
    assert isinstance(raised.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "report.csv",
        "report-not-a-uuid.csv",
        ".report-draft.tmp",
        "../report-00000000-0000-0000-0000-000000000000.csv",
        "nested/report-00000000-0000-0000-0000-000000000000.csv",
        "/report-00000000-0000-0000-0000-000000000000.csv",
        "report-00000000-0000-0000-0000-000000000000.csv.tmp",
        "report-00000000-0000-0000-0000-000000000000.csv\n",
        "report-AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA.csv",
    ],
)
def test_resolve_rejects_noncanonical_or_unsafe_references(tmp_path, ref):
    with pytest.raises(ValueError) as raised:
        LocalReportStorage(tmp_path).resolve(ref)
    assert str(tmp_path) not in str(raised.value)


def test_resolve_rejects_symlink_outside_reports_root(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    private_file = tmp_path / "private.csv"
    private_file.write_bytes(b"private contents")
    ref = f"report-{uuid4()}.csv"
    (root / ref).symlink_to(private_file)
    with pytest.raises(ValueError) as raised:
        LocalReportStorage(root).resolve(ref)
    assert str(private_file) not in str(raised.value)


def test_resolve_rejects_directory_named_as_report(tmp_path):
    ref = f"report-{uuid4()}.csv"
    (tmp_path / ref).mkdir()
    with pytest.raises(ValueError):
        LocalReportStorage(tmp_path).resolve(ref)
