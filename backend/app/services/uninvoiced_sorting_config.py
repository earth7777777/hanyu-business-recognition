from __future__ import annotations

UNINVOICED_EXPORT_SORTING_CONFIG_KEY = "uninvoiced_export_sorting"

DEFAULT_UNINVOICED_EXPORT_SORTING: dict = {
    "customer_sort": "amount_desc_with_sort_groups",
    "customer_sort_groups": [
        {"name": "巨星系", "keywords": ["巨星", "联合电气"], "priority": 1},
        {"name": "热威系", "keywords": ["热威"], "priority": 2},
    ],
    "order_sort": "fully_outbound_then_order_date_desc_then_urgent_amount_desc",
    "product_sort": "kingdee_entry_line_no",
}
