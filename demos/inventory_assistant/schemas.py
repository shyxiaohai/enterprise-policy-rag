"""Pydantic v2参数约定；非法参数不是缺货。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                        min_length=1, max_length=80)]
Size = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                        pattern=r"^(S|M|L|XL)$")]


class InventoryQuery(BaseModel):
    """供字典输入及未来工具参数使用；字段均必填，不接受额外字段。"""

    model_config = ConfigDict(strict=True, extra="forbid")
    product: Text
    color: Text
    size: Size
