from __future__ import annotations

from typing import Any, Sequence, Tuple

def get_kernels() -> Sequence[Tuple[str, str, Any]]: ...
def get_local_attention_shape(b: int, n: int, k: int, w: int) -> Sequence[int]: ...
def get_local_attention_data(b: int, n: int, k: int, w: int) -> Any: ...
