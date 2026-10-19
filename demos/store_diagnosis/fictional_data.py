"""所有记录、店铺、金额及时间均为虚构演示数据，不对应真实企业。

原始数量使用件，金额使用整数分，时间使用带+08:00偏移的ISO字符串。
"""

from types import MappingProxyType


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def copy_data(value):
    """将只读原始数据复制为独立的普通字典/列表，供验证和返回。"""
    if isinstance(value, MappingProxyType):
        return {key: copy_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [copy_data(item) for item in value]
    return value


STORE_ID = "SZ-DEMO-001"
AS_OF = "2026-09-18T20:00:00+08:00"
SNAPSHOT_AT = "2026-09-14T20:00:00+08:00"

CATALOG = freeze([
    {"product": "基础T恤", "evidence_id": "CAT-001", "baseline_units_sold": 180},
    {"product": "休闲衬衫", "evidence_id": "CAT-002", "baseline_units_sold": 120},
    {"product": "直筒长裤", "evidence_id": "CAT-003", "baseline_units_sold": 40},
])

SALES = freeze([
    {"evidence_id": "SALE-P1", "period_id": "P1", "start_at": "2026-09-01T00:00:00+08:00",
     "end_exclusive": "2026-09-08T00:00:00+08:00", "visitors": 2100,
     "orders": 630, "average_order_fen": 30000, "revenue_fen": 18900000},
    {"evidence_id": "SALE-P2", "period_id": "P2", "start_at": "2026-09-08T00:00:00+08:00",
     "end_exclusive": "2026-09-15T00:00:00+08:00", "visitors": 2100,
     "orders": 490, "average_order_fen": 28600, "revenue_fen": 14014000},
])

INVENTORY = freeze([
    {"evidence_id": "INV-001", "product": "基础T恤", "color": "白色", "size": "S", "quantity": 5, "stockout_since": None},
    {"evidence_id": "INV-002", "product": "基础T恤", "color": "白色", "size": "M", "quantity": 0, "stockout_since": "2026-09-09T10:00:00+08:00"},
    {"evidence_id": "INV-003", "product": "基础T恤", "color": "白色", "size": "L", "quantity": 0, "stockout_since": "2026-09-10T10:00:00+08:00"},
    {"evidence_id": "INV-004", "product": "基础T恤", "color": "白色", "size": "XL", "quantity": 2, "stockout_since": None},
    {"evidence_id": "INV-005", "product": "休闲衬衫", "color": "蓝色", "size": "M", "quantity": 0, "stockout_since": "2026-09-12T10:00:00+08:00"},
    {"evidence_id": "INV-006", "product": "休闲衬衫", "color": "蓝色", "size": "L", "quantity": 4, "stockout_since": None},
    {"evidence_id": "INV-007", "product": "直筒长裤", "color": "黑色", "size": "L", "quantity": 6, "stockout_since": None},
])

REPLENISHMENTS = freeze([
    {"evidence_id": "REP-001", "request_id": "REQ-001", "approval_id": "APR-001", "purchase_id": "PO-001", "receipt_id": "RCV-001",
     "product": "基础T恤", "requested_at": "2026-09-08T10:00:00+08:00", "approved_at": "2026-09-11T10:00:00+08:00",
     "purchased_at": "2026-09-12T10:00:00+08:00", "expected_arrival_at": "2026-09-12T10:00:00+08:00",
     "arrived_at": "2026-09-16T10:00:00+08:00", "items": [
         {"color": "白色", "size": "M", "requested_quantity": 30, "received_quantity": 30},
         {"color": "白色", "size": "L", "requested_quantity": 20, "received_quantity": 20}]},
    {"evidence_id": "REP-002", "request_id": "REQ-002", "approval_id": "APR-002", "purchase_id": "PO-002", "receipt_id": "RCV-002",
     "product": "休闲衬衫", "requested_at": "2026-09-11T10:00:00+08:00", "approved_at": "2026-09-12T10:00:00+08:00",
     "purchased_at": "2026-09-13T10:00:00+08:00", "expected_arrival_at": "2026-09-14T10:00:00+08:00",
     "arrived_at": "2026-09-17T10:00:00+08:00", "items": [
         {"color": "蓝色", "size": "M", "requested_quantity": 20, "received_quantity": 20}]},
])
