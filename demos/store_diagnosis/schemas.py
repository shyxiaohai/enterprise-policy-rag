"""Pydantic v2输入、事实记录和工具响应结构。"""

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80)]
Size = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, pattern=r"^(S|M|L|XL)$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]


def moment(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("时间必须包含时区偏移")
    return parsed


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SalesQuery(StrictModel):
    store_id: Text = "SZ-DEMO-001"
    baseline: Text = "P1"
    comparison: Text = "P2"

    @model_validator(mode="after")
    def distinct(self):
        if self.baseline == self.comparison:
            raise ValueError("必须选择两个不同期间")
        if self.baseline == "P2" and self.comparison == "P1":
            raise ValueError("P1须为基期，P2须为比较期")
        return self


class InventoryQuery(StrictModel):
    product: Text
    color: Text
    size: Size
    store_id: Text = "SZ-DEMO-001"


class ReplenishmentQuery(StrictModel):
    product: Text
    store_id: Text = "SZ-DEMO-001"


class SalesRecord(StrictModel):
    evidence_id: Text
    period_id: Text
    start_at: Text
    end_exclusive: Text
    visitors: Positive
    orders: Count
    average_order_fen: Count
    revenue_fen: Count

    @model_validator(mode="after")
    def coherent(self):
        if self.orders > self.visitors or self.revenue_fen != self.orders * self.average_order_fen:
            raise ValueError("销售口径不一致")
        if moment(self.start_at) >= moment(self.end_exclusive):
            raise ValueError("期间起止不合法")
        return self


class SalesMetrics(SalesRecord):
    conversion_rate_pct: float


class Changes(StrictModel):
    visitors_change: int
    orders_change: int
    average_order_change_fen: int
    revenue_change_fen: int
    revenue_change_pct: float
    conversion_change_pp: float


class HotProduct(StrictModel):
    product: Text
    evidence_id: Text
    baseline_units_sold: Count


class ResponseBase(StrictModel):
    store_id: Text
    data_source: Literal["fictional_demo"]
    as_of: Text
    evidence_ids: list[Text]


class SalesResponse(ResponseBase):
    status: Literal["ok", "store_not_found", "period_not_found"]
    periods: list[SalesMetrics]
    changes: Optional[Changes]
    hot_products: list[HotProduct]
    hot_product_period_id: Text
    basis: dict[str, str]


class InventoryRecord(StrictModel):
    evidence_id: Text
    product: Text
    color: Text
    size: Size
    quantity: Count
    stockout_since: Optional[Text]


class InventoryResponse(ResponseBase):
    status: Literal["in_stock", "out_of_stock", "product_not_found", "variant_not_found", "store_not_found"]
    product: Text
    color: Text
    size: Size
    quantity: Optional[Count]
    record: Optional[InventoryRecord]
    snapshot_at: Text

    @model_validator(mode="after")
    def coherent(self):
        snapshot = moment(self.snapshot_at)
        if snapshot > moment(self.as_of):
            raise ValueError("库存快照晚于资料截止时间")
        if self.status in ("in_stock", "out_of_stock"):
            if self.record is None or self.quantity != self.record.quantity:
                raise ValueError("库存结果缺少一致记录")
            if (self.quantity > 0) != (self.status == "in_stock"):
                raise ValueError("库存状态错误")
            if self.record.stockout_since is not None and moment(self.record.stockout_since) > snapshot:
                raise ValueError("缺货起点晚于快照")
        elif self.record is not None or self.quantity is not None:
            raise ValueError("无记录不能返回库存数量")
        return self


class ReplenishmentItem(StrictModel):
    color: Text
    size: Size
    requested_quantity: Positive
    received_quantity: Count

    @model_validator(mode="after")
    def coherent(self):
        if self.received_quantity > self.requested_quantity:
            raise ValueError("到货量超过申请量")
        return self


class ReplenishmentRecord(StrictModel):
    evidence_id: Text
    request_id: Text
    approval_id: Text
    purchase_id: Text
    receipt_id: Text
    product: Text
    requested_at: Text
    approved_at: Text
    purchased_at: Text
    expected_arrival_at: Text
    arrived_at: Text
    items: list[ReplenishmentItem]

    @model_validator(mode="after")
    def coherent(self):
        times = [moment(getattr(self, key)) for key in
                 ("requested_at", "approved_at", "purchased_at", "arrived_at")]
        if times != sorted(times) or not self.items:
            raise ValueError("补货流程节点不一致")
        if moment(self.expected_arrival_at) < times[0]:
            raise ValueError("预计到货早于申请")
        return self


class ReplenishmentMetrics(ReplenishmentRecord):
    approval_wait_days: float
    arrival_delay_days: float


class ReplenishmentResponse(ResponseBase):
    status: Literal["ok", "store_not_found", "product_not_found", "no_records"]
    product: Text
    records: list[ReplenishmentMetrics]
    basis: dict[str, str]
