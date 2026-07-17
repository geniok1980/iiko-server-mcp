# Agent Context: iiko-server-mcp

MCP (Model Context Protocol) server that wraps the iikoServer REST API as 42+ tools over stdio.

## Architecture

```
MCP Client (Claude/Hermes/Cursor)
  └─ stdio ─► Server.__init__("iiko-server-mcp")
                └─ IikoClient (httpx.AsyncClient)
                     └─ HTTPS ─► iikoServer REST API
```

- **Auth**: SHA1(password) → `GET /resto/api/auth?login=...&pass=...` → cookie `key`
- **Auto re-auth**: 401 triggers `_auth()` retry (once)
- **SSL**: `IIKO_VERIFY_SSL=false` by default (iiko often uses self-signed certs)
- **Logout**: graceful `GET /resto/api/logout?key=...` on shutdown

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `IIKO_URL` | **yes** | — | iikoServer base URL (e.g. `https://your-restaurant.iiko.it:443`) |
| `IIKO_LOGIN` | **yes** | — | Login username |
| `IIKO_PASS` | **yes** | — | Plain password (SHA1 hashed internally before sending) |
| `IIKO_VENUE_MAP` | no | — | `"Name=4,Name=5"` mapping of cash register numbers to venue names |
| `IIKO_VERIFY_SSL` | no | `false` | Set to `true` for CA-signed certs |

## Tool Categories (42+ tools)

| Category | Tools |
|---|---|
| **System** | `ping`, `list_endpoints` |
| **Products/Nomenclature** | `get_products`, `get_product_groups`, `get_product_sizes`, `get_nomenclature`, `get_corporate_structure`, `get_corporate_groups`, `search_products` |
| **Employees** | `get_employees`, `get_employee`, `get_employee_roles`, `get_persons` |
| **Entities** | `get_entity_types`, `get_entities`, `get_entity_by_id`, `get_stores`, `get_contractors` |
| **Documents** | `get_incoming_invoices`, `get_outgoing_invoices`, `get_incoming_invoice_by_id`, `get_outgoing_invoice_by_id`, `get_waste_documents`, `get_menu_changes`, `get_menu_change_by_id` |
| **Recipes** | `get_assembly_charts`, `get_assembly_chart_by_id`, `save_assembly_chart` |
| **Cash & Payments** | `get_payment_types`, `get_cash_shifts`, `get_cash_shift`, `get_payments`, `get_payment`, `create_encashment` |
| **Prices** | `get_price_categories`, `get_price_category` |
| **Reports & OLAP** | `get_olap_columns`, `run_olap_report`, `get_olap_presets`, `run_olap_by_preset`, `run_custom_report`, `get_venues_revenue`, `get_staff_meals`, `get_top_dishes`, `compare_revenue`, `get_checks` |
| **Raw** | `raw_request` |

## Date Format Convention

- **Business tools** (`get_venues_revenue`, `get_staff_meals`, `get_top_dishes`, `compare_revenue`, `get_checks`): **YYYY-MM-DD** (Python-friendly, auto defaults to Moscow time)
- **Legacy OLAP tools** (`run_olap_report`, `run_olap_by_preset`, `run_custom_report`): **DD.MM.YYYY** (iiko native format)

## Adding a New Tool

1. Define handler:
```python
@tool("my_new_tool", "Description.", {
    "type": "object",
    "properties": {"param": {"type": "string"}},
    "required": ["param"],
})
async def my_new_tool_handler(args):
    ...
```

2. Add `"my_new_tool"` to the appropriate category in `ENDPOINT_CATALOG`.

3. Recompile: `python3 -m py_compile server.py`

## OLAP Report Pattern

Business tools use the v2 OLAP format with `filterType`-wrapped filters:

```python
body = {
    "reportType": "SALES",
    "aggregateFields": ["DishDiscountSumInt"],
    "groupByRowFields": ["CashRegisterName.Number"],
    "filters": {
        "OpenDate.Typed": {
            "filterType": "DateRange",
            "from": "2026-07-17T00:00:00",
            "to": "2026-07-18T00:00:00",
            "includeLow": True,
            "includeHigh": False,
            "periodType": "CUSTOM",
        },
        "DeletedWithWriteoff": {
            "filterType": "IncludeValues",
            "values": ["NOT_DELETED"],
        },
    },
}
```

Helper functions `_date_filter()`, `_base_filters()`, `_today_msk()`, `_next_day()` are available at module level.

## Security Notes

- **`save_assembly_chart`**: Writes production data — assembly charts (TTK/tech cards)
- **`create_encashment`**: Creates cash in/out entries on live shifts
- **`raw_request`**: Can execute any authenticated HTTP call to iikoServer
- Consider using read-only credentials for agent access when possible
