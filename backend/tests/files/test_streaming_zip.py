from __future__ import annotations

import zipfile
from io import BytesIO

from app.modules.files.streaming_zip import StorageZipMember, iter_storage_zip


class _StreamingStorage:
    def __init__(self) -> None:
        self.read_chunks = 0
        self.objects = {
            ("bucket", "classified"): [b"classified-", b"dxf"],
            ("bucket", "excel"): [b"excel-", b"stage1"],
        }

    def iter_file(self, bucket: str, storage_key: str):
        for chunk in self.objects[(bucket, storage_key)]:
            self.read_chunks += 1
            yield chunk


def test_streaming_zip_yields_header_before_reading_objects_and_keeps_exact_names():
    storage = _StreamingStorage()
    chunks = iter_storage_zip(
        storage,
        [
            StorageZipMember("bucket", "classified", "原DXF/A_拆板前.dxf"),
            StorageZipMember("bucket", "excel", "产出Excel/项目阶段一.xlsx"),
        ],
    )

    first = next(chunks)

    assert first.startswith(b"PK")
    assert storage.read_chunks == 0

    payload = b"".join([first, *chunks])
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert archive.namelist() == [
            "原DXF/A_拆板前.dxf",
            "产出Excel/项目阶段一.xlsx",
        ]
        assert archive.read("原DXF/A_拆板前.dxf") == b"classified-dxf"
        assert archive.read("产出Excel/项目阶段一.xlsx") == b"excel-stage1"
