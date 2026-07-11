"""Step 10: Invoke the vendored multi_split framework.

Saves the workbook, invokes split_profile_excel(), and re-opens the
workbook pointing at the newly created sheet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

from multi_split import split_profile_excel

log = logging.getLogger(__name__)


def step_10_multi_split(wb, output_file: Path, output_sheet: str | None = None):
    """Call the multi_split framework to split BH/BOX/BT/I profiles.

    Saves *wb* to *output_file*, invokes split_profile_excel(), and
    re-opens the workbook.  Returns (new_wb, result_sheet_name).
    """
    wb.save(output_file)
    wb.close()

    kwargs = dict(
        excel_path=str(output_file),
        sheet_name="整理表",
        spec_col="规格",
        width_col="宽度",
        qty_col="数量",
        part_type_col="类型",
        modes=["BH", "BT", "BOX", "I", "PL"],  # full profile suite
    )
    if output_sheet is not None:
        kwargs["output_sheet"] = output_sheet

    result_sheet = split_profile_excel(**kwargs)
    log.info("Step 10: multi_split complete → sheet '%s'.", result_sheet)

    wb = openpyxl.load_workbook(output_file)
    return wb, result_sheet
