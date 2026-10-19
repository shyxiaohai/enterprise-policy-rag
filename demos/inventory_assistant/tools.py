"""只读查询工具，不提供扣减、预订、写文件或数据库功能。"""

from pydantic import ConfigDict, validate_call

from .fictional_inventory import INVENTORY
from .schemas import Size, Text


@validate_call(config=ConfigDict(strict=True))
def query_inventory(product: Text, color: Text, size: Size) -> dict:
    """查询虚构库存；验证失败直接抛出Pydantic ValidationError。"""
    key = (product, color, size)
    quantity = None
    if not any(item[0] == product for item in INVENTORY):
        status = "product_not_found"
    elif key not in INVENTORY:
        status = "variant_not_found"
    else:
        quantity = INVENTORY[key]
        status = "in_stock" if quantity > 0 else "out_of_stock"
    return {"status": status, "product": product, "color": color,
            "size": size, "quantity": quantity, "data_source": "fictional_demo"}
