"""三个确定性只读工具；无模型、网络、写入或因果判断。"""

from decimal import Decimal, ROUND_HALF_UP

from pydantic import ConfigDict, validate_call

from . import fictional_data as data
from .schemas import (InventoryQuery, InventoryResponse, ReplenishmentQuery,
                      ReplenishmentRecord, ReplenishmentResponse, SalesQuery,
                      SalesRecord, SalesResponse, Size, Text, moment)


class QueryFailure(RuntimeError):
    """数据损坏或读取/计算失败；与不存在、参数错误明确区分。"""


def rounded(value):
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def base(store_id):
    return dict(store_id=store_id, data_source="fictional_demo", as_of=data.AS_OF, evidence_ids=[])


def finish(model, result):
    return model.model_validate(result).model_dump(mode="json")


@validate_call(config=ConfigDict(strict=True))
def query_sales(store_id: Text = data.STORE_ID, baseline: Text = "P1", comparison: Text = "P2") -> dict:
    """查询两个等长、不重叠、按时间先后的固定期间，返回计算事实。"""
    args = SalesQuery(store_id=store_id, baseline=baseline, comparison=comparison)
    try:
        result = dict(base(store_id), status="ok", periods=[], changes=None, hot_products=[],
                      hot_product_period_id="P1", basis={
                          "conversion_rate_pct": "订单数 / 客流 × 100",
                          "revenue_fen": "订单数 × 客单价（分）",
                          "change": "比较期减基期；营业额变化率=差额/基期营业额×100",
                          "conversion_change_pp": "比较期成交率减基期成交率，单位百分点",
                          "rounding": "百分数与天数四舍五入至4位小数；差值由未舍入值计算",
                          "scope": "单店线下完整7日，起点包含、终点不包含；客流=进店人次，订单=完成成交单（演示无退款）；金额单位分",
                          "hot_products": "固定按P1商品销量列示，单位件；非全店明细，不是额外订单数"})
        if store_id != data.STORE_ID:
            result["status"] = "store_not_found"
        else:
            records = {p["period_id"]: SalesRecord.model_validate(data.copy_data(p)) for p in data.SALES}
            if baseline not in records or comparison not in records:
                result["status"] = "period_not_found"
            else:
                first, second = records[baseline], records[comparison]
                if (moment(first.end_exclusive) > moment(second.start_at)
                        or moment(first.end_exclusive) - moment(first.start_at) != moment(second.end_exclusive) - moment(second.start_at)):
                    raise ValueError("期间不可比")
                rates = [Decimal(p.orders) / p.visitors * 100 for p in (first, second)]
                result["periods"] = [dict(p.model_dump(), conversion_rate_pct=rounded(rate))
                                     for p, rate in zip((first, second), rates)]
                result["changes"] = dict(visitors_change=second.visitors-first.visitors,
                    orders_change=second.orders-first.orders,
                    average_order_change_fen=second.average_order_fen-first.average_order_fen,
                    revenue_change_fen=second.revenue_fen-first.revenue_fen,
                    revenue_change_pct=rounded(Decimal(second.revenue_fen-first.revenue_fen)/first.revenue_fen*100),
                    conversion_change_pp=rounded(rates[1]-rates[0]))
                result["hot_products"] = data.copy_data(data.CATALOG)
                result["evidence_ids"] = [p.evidence_id for p in (first, second)] + [p["evidence_id"] for p in data.CATALOG]
        return finish(SalesResponse, result)
    except Exception:
        raise QueryFailure("销售数据读取或校验失败") from None


@validate_call(config=ConfigDict(strict=True))
def query_inventory(product: Text, color: Text, size: Size, store_id: Text = data.STORE_ID) -> dict:
    """按单个商品颜色尺码查询固定历史快照；不是实时库存。"""
    InventoryQuery(product=product, color=color, size=size, store_id=store_id)
    try:
        result = dict(base(store_id), status="store_not_found", product=product, color=color, size=size,
                      quantity=None, record=None, snapshot_at=data.SNAPSHOT_AT)
        if store_id == data.STORE_ID:
            if not any(p["product"] == product for p in data.CATALOG):
                result["status"] = "product_not_found"
            else:
                record = next((p for p in data.INVENTORY if (p["product"], p["color"], p["size"]) == (product, color, size)), None)
                result["status"] = "variant_not_found"
                if record is not None:
                    result.update(status="in_stock" if record["quantity"] > 0 else "out_of_stock",
                                  quantity=record["quantity"], record=data.copy_data(record), evidence_ids=[record["evidence_id"]])
        return finish(InventoryResponse, result)
    except Exception:
        raise QueryFailure("库存数据读取或校验失败") from None


@validate_call(config=ConfigDict(strict=True))
def query_replenishment(product: Text, store_id: Text = data.STORE_ID) -> dict:
    """返回指定商品的补货流程事实和时间差，不判断审批是否导致销售下降。"""
    ReplenishmentQuery(product=product, store_id=store_id)
    try:
        result = dict(base(store_id), status="store_not_found", product=product, records=[], basis={
            "approval_wait_days": "(审批时间-申请时间)/24小时",
            "arrival_delay_days": "max(实际到货时间-预计到货时间,0)/24小时",
            "rounding": "四舍五入至4位小数；到货记录截至as_of；不代表库存快照已更新"})
        if store_id == data.STORE_ID:
            result["status"] = "product_not_found"
            if any(p["product"] == product for p in data.CATALOG):
                result["status"] = "no_records"
                for raw in data.REPLENISHMENTS:
                    if raw["product"] != product:
                        continue
                    record = ReplenishmentRecord.model_validate(data.copy_data(raw))
                    if moment(record.arrived_at) > moment(data.AS_OF):
                        raise ValueError("记录晚于资料截止时间")
                    wait = Decimal(str((moment(record.approved_at)-moment(record.requested_at)).total_seconds()))/86400
                    delay = max(Decimal(0), Decimal(str((moment(record.arrived_at)-moment(record.expected_arrival_at)).total_seconds()))/86400)
                    result["records"].append(dict(record.model_dump(), approval_wait_days=rounded(wait), arrival_delay_days=rounded(delay)))
                    result["evidence_ids"].append(record.evidence_id)
                if result["records"]:
                    result["status"] = "ok"
        return finish(ReplenishmentResponse, result)
    except Exception:
        raise QueryFailure("补货数据读取或校验失败") from None
