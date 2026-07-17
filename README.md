# iikoServer MCP Server

MCP (Model Context Protocol) server exposing the [iiko](https://iiko.ru) restaurant management system REST API as 42+ LLM-callable tools over stdio.

## Features

- **42+ tools** — products, employees, orders, payments, OLAP reports, assembly charts
- **Auto-auth** — SHA1 password hashing + session cookie, re-authenticates on 401
- **Graceful shutdown** — sends logout to release iiko license slot
- **Self-signed cert support** — `IIKO_VERIFY_SSL=false` by default
- **Venue mapping** — optional `IIKO_VENUE_MAP` to tag cash register numbers with names
- **Business-level helpers** — `get_checks`, `get_venues_revenue`, `get_staff_meals`, `get_top_dishes`, `compare_revenue`
- **Docker-ready** — Python 3.12-slim, stdio protocol

## Quick Start

### Local (pip)

```bash
git clone https://github.com/your-org/iiko-server-mcp.git
cd iiko-server-mcp
pip install -r requirements.txt
```

```bash
IIKO_URL=https://your-restaurant.iiko.it:443 \
IIKO_LOGIN=your_login \
IIKO_PASS=your_password \
python3 server.py
```

### Docker

```bash
docker build -t iiko-server-mcp .
docker run -i --rm \
  -e IIKO_URL=https://your-restaurant.iiko.it:443 \
  -e IIKO_LOGIN=your_login \
  -e IIKO_PASS=your_password \
  iiko-server-mcp
```

**Important:** MCP servers use **stdio** (not HTTP). The container must run with `-i` (interactive) so the MCP client can communicate via stdin/stdout. Do not use `-d` (detached).

## Configuration

Copy `.env.example` to `.env` and fill in real values:

| Variable | Required | Default | Description |
|---|---|---|---|
| `IIKO_URL` | **yes** | — | iikoServer base URL, e.g. `https://my-restaurant.iiko.it:443` |
| `IIKO_LOGIN` | **yes** | — | Login username |
| `IIKO_PASS` | **yes** | — | Plain password (SHA1-hashed before sending) |
| `IIKO_VENUE_MAP` | no | — | `"Venue A=4,Venue B=5"` — names for cash register numbers |
| `IIKO_VERIFY_SSL` | no | `false` | Set to `true` if using a CA-signed certificate |

## MCP Client Configuration

### Hermes Agent

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  iiko-server:
    command: python3
    args: ["/path/to/iiko-server-mcp/server.py"]
    env:
      IIKO_URL: "https://your-restaurant.iiko.it:443"
      IIKO_LOGIN: "your_login"
      IIKO_PASS: "your_password"
      IIKO_VENUE_MAP: "Cafe A=4,Cafe B=5"
```

For Docker:

```yaml
mcp_servers:
  iiko-server:
    command: docker
    args: ["run", "-i", "--rm",
           "-e", "IIKO_URL=https://your-restaurant.iiko.it:443",
           "-e", "IIKO_LOGIN=your_login",
           "-e", "IIKO_PASS=your_password",
           "iiko-server-mcp"]
```

### Claude Desktop

```json
{
  "mcpServers": {
    "iiko-server": {
      "command": "python3",
      "args": ["/path/to/iiko-server-mcp/server.py"],
      "env": {
        "IIKO_URL": "https://your-restaurant.iiko.it:443",
        "IIKO_LOGIN": "your_login",
        "IIKO_PASS": "your_password"
      }
    }
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "iiko-server": {
      "command": "python3",
      "args": ["/path/to/iiko-server-mcp/server.py"],
      "env": {
        "IIKO_URL": "https://your-restaurant.iiko.it:443",
        "IIKO_LOGIN": "your_login",
        "IIKO_PASS": "your_password"
      }
    }
  }
}
```

## Available Tools

### System
- **ping** — Health check, server URL + auth state
- **list_endpoints** — All 42 tools with descriptions

### Products / Nomenclature
- **get_products** / **get_nomenclature** — All products
- **get_product_groups** — Product categories with hierarchy
- **get_product_sizes** — Sizes/units
- **get_corporate_structure** — Departments structure
- **get_corporate_groups** — Corporate groups
- **search_products** — Substring search by name

### Employees
- **get_employees** / **get_employee** / **get_employee_roles** / **get_persons**

### Entities
- **get_entity_types** / **get_entities** / **get_entity_by_id**
- **get_stores** — Warehouses
- **get_contractors** — Suppliers

### Documents
- **get_incoming_invoices** / **get_outgoing_invoices** — by date range (DD.MM.YYYY)
- **get_incoming_invoice_by_id** / **get_outgoing_invoice_by_id**
- **get_waste_documents** — Write-off documents
- **get_menu_changes** / **get_menu_change_by_id**

### Recipes (Assembly Charts)
- **get_assembly_charts** — All tech cards
- **get_assembly_chart_by_id** — Single chart
- **save_assembly_chart** — Create/update (⚠️ modifies production data)

### Cash & Payments
- **get_payment_types**
- **get_cash_shifts** / **get_cash_shift** — by session UUID
- **get_payments** / **get_payment** — per session
- **create_encashment** — Cash in/out (⚠️ modifies production data)

### Prices
- **get_price_categories** / **get_price_category**

### Reports & OLAP
- **get_olap_columns** — Schema for a report type (e.g. `SALES`)
- **run_olap_report** — Raw OLAP with custom columns/filters (DD.MM.YYYY)
- **get_olap_presets** / **run_olap_by_preset** — Saved presets
- **run_custom_report** — Named custom reports (DD.MM.YYYY)
- **get_venues_revenue** — Daily revenue by venue (YYYY-MM-DD)
- **get_staff_meals** — Free staff meals per venue (YYYY-MM-DD)
- **get_top_dishes** — Top N dishes by revenue (YYYY-MM-DD)
- **compare_revenue** — Day-over-day revenue comparison
- **get_checks** — Individual orders with payment type & masked card (YYYY-MM-DD)

### Raw
- **raw_request** — Direct authenticated HTTP call to any iikoServer endpoint

## Date Format

| Tool Group | Format | Example |
|---|---|---|
| Business helpers (`get_venues_revenue`, `get_checks`, etc.) | **YYYY-MM-DD** | `2026-07-17` |
| Legacy OLAP (`run_olap_report`, `run_olap_by_preset`) | **DD.MM.YYYY** | `17.07.2026` |

All business tools default to **Moscow time** (UTC+3) if no date is provided.

## Known Limitations

- **`get_cash_shifts`** — requires a date parameter; currently returns 409 if called without one
- **`get_payment_types`** — returns 404 on some iikoServer versions (endpoint may not exist)
- **Staff meal detection** — filters by `"(без оплаты)"` payment type label; verify this matches your iiko configuration
- **`CardTypeName`** — may return `"(нет карты)"` depending on acquirer integration
- **Self-signed certs** — default `verify=False`; set `IIKO_VERIFY_SSL=true` for CA-signed certificates

## Development

```bash
# Syntax check
python3 -m py_compile server.py

# Run locally with test env
IIKO_URL=... IIKO_LOGIN=... IIKO_PASS=... python3 server.py

# Build Docker
docker build -t iiko-server-mcp .
```

### Adding a Tool

1. Write a handler with the `@tool("name", "description", input_schema)` decorator
2. Add `"name"` to the appropriate category in `ENDPOINT_CATALOG`
3. Python will auto-register it — no other wiring needed

## License

MIT — see [LICENSE](LICENSE)
