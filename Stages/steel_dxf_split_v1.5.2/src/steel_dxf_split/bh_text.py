from __future__ import annotations


def canonical_bh_label(part_number: str, role: str, index: int | None = None, quantity: int = 1) -> str:
    if role == "web":
        base = f"p={part_number}腹"
    elif role == "flange":
        flange_role = {1: "上翼", 2: "下翼"}.get(index, "翼")
        base = f"p={part_number}{flange_role}"
    else:
        raise ValueError(f"Unsupported BH role: {role}")
    if index is not None and (role != "flange" or index not in {1, 2}):
        base += f"-{index}"
    return base
