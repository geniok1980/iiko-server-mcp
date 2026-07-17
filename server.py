#!/usr/bin/env python3
"""
iikoServer MCP Server — Model Context Protocol server for iiko restaurant management system.

Exposes iikoServer REST API as MCP tools over stdio.
Configure via environment variables: IIKO_URL, IIKO_LOGIN, IIKO_PASS.
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

__version__ = "0.1.0"

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("iiko-server-mcp")

# ─── Configuration ───────────────────────────────────────────────────────────

IIKO_URL = os.environ.get("IIKO_URL", "").rstrip("/")
IIKO_LOGIN = os.environ.get("IIKO_LOGIN", "")
IIKO_PASS = os.environ.get("IIKO_PASS", "")
IIKO_VERIFY_SSL = os.environ.get("IIKO_VERIFY_SSL", "false").lower() in (
    "true", "1", "yes",
)

# Venue mapping (optional — for tools like get_venues_revenue, get_staff_meals).
# Format: comma-separated pairs. Example:
#   IIKO_VENUE_MAP="Venue A=4,Venue B=5"
_vm_raw = os.environ.get("IIKO_VENUE_MAP", "")
VENUE_MAP: dict[int, str] = {}  # kassa_num → name
if _vm_raw:
    for pair in _vm_raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            name, num = pair.split("=", 1)
            VENUE_MAP[int(num.strip())] = name.strip()


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ─── HTTP Client & Auth ─────────────────────────────────────────────────────

class IikoClient:
    """Async HTTP wrapper with auto-auth and 401 retry."""

    def __init__(self):
        self.client: httpx.AsyncClient | None = None
        self.token: str | None = None

    async def start(self):
        if not IIKO_URL:
            raise RuntimeError(
                "IIKO_URL environment variable is required. "
                "Example: https://your-restaurant.iiko.it:443"
            )
        self.client = httpx.AsyncClient(
            verify=IIKO_VERIFY_SSL,
            timeout=60.0,
        )
        await self._auth()

    async def stop(self):
        if self.token and self.client:
            try:
                await self.client.get(
                    f"{IIKO_URL}/resto/api/logout",
                    params={"key": self.token},
                )
                logger.info("Logged out from iikoServer")
            except Exception:
                logger.warning("Logout failed", exc_info=True)
        if self.client:
            await self.client.aclose()

    async def _auth(self):
        if not IIKO_LOGIN or not IIKO_PASS:
            raise RuntimeError(
                "IIKO_LOGIN and IIKO_PASS environment variables are required"
            )
        pass_hash = sha1_hex(IIKO_PASS)
        resp = await self.client.get(
            f"{IIKO_URL}/resto/api/auth",
            params={"login": IIKO_LOGIN, "pass": pass_hash},
        )
        resp.raise_for_status()
        self.token = resp.text.strip()
        logger.info("Authenticated, token=%s...", self.token[:8])

    async def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: Any = None,
        retry=True,
    ) -> Any:
        """Make an authenticated request. Returns parsed JSON or raw text."""
        url = f"{IIKO_URL}{path}"
        if params is None:
            params = {}
        params["key"] = self.token

        if method.upper() == "GET":
            resp = await self.client.get(url, params=params)
        elif method.upper() == "POST":
            resp = await self.client.post(url, params=params, json=json_body)
        else:
            resp = await self.client.request(
                method, url, params=params, json=json_body,
            )

        if resp.status_code == 401 and retry:
            logger.info("Got 401, re-authenticating...")
            await self._auth()
            return await self.request(
                method, path, params=params, json_body=json_body, retry=False,
            )

        if resp.status_code >= 400:
            return {
                "error": f"HTTP {resp.status_code}",
                "body": resp.text[:2000],
            }

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            try:
                return resp.json()
            except Exception:
                return resp.text
        return resp.text


iiko = IikoClient()

# ─── Tool Definitions ───────────────────────────────────────────────────────

TOOLS: list[dict] = []


def tool(name: str, description: str, input_schema: dict | None = None):
    """Decorator to register a tool handler."""

    def decorator(func):
        schema = input_schema or {"type": "object", "properties": {}}
        TOOLS.append({
            "name": name,
            "description": description,
            "input_schema": schema,
            "handler": func,
        })
        return func

    return decorator


# ─── System ──────────────────────────────────────────────────────────────────


@tool("ping", "Health check. Returns iikoServer URL, auth state, and token info.")
async def ping_handler(args):
    status = "authenticated" if iiko.token else "not authenticated"
    return {
        "url": IIKO_URL,
        "status": status,
        "token_prefix": (iiko.token or "")[:8] + "..." if iiko.token else None,
    }


ENDPOINT_CATALOG = [
    {"category": "System", "tools": ["ping", "list_endpoints"]},
    {
        "category": "Products/Nomenclature",
        "tools": [
            "get_products", "get_product_groups", "get_product_sizes",
            "get_nomenclature", "get_corporate_structure",
            "get_corporate_groups", "search_products",
        ],
    },
    {
        "category": "Employees",
        "tools": [
            "get_employees", "get_employee", "get_employee_roles", "get_persons",
        ],
    },
    {
        "category": "Entities",
        "tools": [
            "get_entity_types", "get_entities", "get_entity_by_id",
            "get_stores", "get_contractors",
        ],
    },
    {
        "category": "Documents",
        "tools": [
            "get_incoming_invoices", "get_outgoing_invoices",
            "get_incoming_invoice_by_id", "get_outgoing_invoice_by_id",
            "get_waste_documents", "get_menu_changes", "get_menu_change_by_id",
        ],
    },
    {
        "category": "Recipes (Assembly Charts)",
        "tools": [
            "get_assembly_charts", "get_assembly_chart_by_id",
            "save_assembly_chart",
        ],
    },
    {
        "category": "Cash & Payments",
        "tools": [
            "get_payment_types", "get_cash_shifts", "get_cash_shift",
            "get_payments", "get_payment", "create_encashment",
        ],
    },
    {
        "category": "Prices",
        "tools": ["get_price_categories", "get_price_category"],
    },
    {
        "category": "Reports & OLAP",
        "tools": [
            "get_olap_columns", "run_olap_report", "get_olap_presets",
            "run_olap_by_preset", "run_custom_report",
            "get_venues_revenue", "get_staff_meals", "get_top_dishes",
            "compare_revenue", "get_checks",
        ],
    },
    {"category": "Raw", "tools": ["raw_request"]},
]


@tool(
    "list_endpoints",
    "List all available iikoServer MCP tools grouped by category.",
)
async def list_endpoints_handler(args):
    result = []
    for group in ENDPOINT_CATALOG:
        lines = []
        for tname in group["tools"]:
            entry = next((t for t in TOOLS if t["name"] == tname), None)
            if entry:
                lines.append(f"  - {tname}: {entry['description']}")
        result.append(f"### {group['category']}\n" + "\n".join(lines))
    return "\n\n".join(result)


# ─── Products / Nomenclature ────────────────────────────────────────────────


@tool(
    "get_products",
    "Get all products from iikoServer. GET /resto/api/v2/entities/products/list",
)
async def get_products_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/products/list")


@tool(
    "get_product_groups",
    "Get all product groups (categories). "
    "GET /resto/api/v2/entities/products/group/list",
)
async def get_product_groups_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/products/group/list")


@tool(
    "get_product_sizes",
    "Get product sizes/units. "
    "GET /resto/api/v2/entities/products/sizes/list",
)
async def get_product_sizes_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/products/sizes/list")


@tool(
    "get_nomenclature",
    "Get full nomenclature (products with extra details). Alias for get_products.",
)
async def get_nomenclature_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/products/list")


@tool(
    "get_corporate_structure",
    "Get corporate departments structure. "
    "GET /resto/api/corporation/departments/",
)
async def get_corporate_structure_handler(args):
    return await iiko.request("GET", "/resto/api/corporation/departments/")


@tool(
    "get_corporate_groups",
    "Get corporate groups. GET /resto/api/corporation/groups/",
)
async def get_corporate_groups_handler(args):
    return await iiko.request("GET", "/resto/api/corporation/groups/")


@tool(
    "search_products",
    "Search products by name (case-insensitive substring match). "
    "Returns matching products.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to match against product names",
            },
        },
        "required": ["query"],
    },
)
async def search_products_handler(args):
    query = args.get("query", "").lower()
    if not query:
        return {"error": "query parameter is required"}
    all_products = await iiko.request(
        "GET", "/resto/api/v2/entities/products/list",
    )
    if isinstance(all_products, dict) and "error" in all_products:
        return all_products
    if not isinstance(all_products, list):
        return {"error": "Unexpected response format", "data": all_products}
    matches = [
        p for p in all_products
        if query in (p.get("name", "") or "").lower()
    ]
    return {"count": len(matches), "products": matches}


# ─── Employees ───────────────────────────────────────────────────────────────


@tool("get_employees", "Get all employees. GET /resto/api/employees")
async def get_employees_handler(args):
    return await iiko.request("GET", "/resto/api/employees")


@tool(
    "get_employee",
    "Get a single employee by UUID. GET /resto/api/employees/byId/{uuid}",
    {
        "type": "object",
        "properties": {
            "uuid": {"type": "string", "description": "Employee UUID"},
        },
        "required": ["uuid"],
    },
)
async def get_employee_handler(args):
    uuid = args.get("uuid", "")
    if not uuid:
        return {"error": "uuid parameter is required"}
    return await iiko.request("GET", f"/resto/api/employees/byId/{uuid}")


@tool("get_employee_roles", "Get employee roles. GET /resto/api/employees/roles")
async def get_employee_roles_handler(args):
    return await iiko.request("GET", "/resto/api/employees/roles")


@tool("get_persons", "Get all persons (users). GET /resto/api/persons")
async def get_persons_handler(args):
    return await iiko.request("GET", "/resto/api/persons")


# ─── Entities ────────────────────────────────────────────────────────────────


@tool(
    "get_entity_types",
    "List available entity types. GET /resto/api/v2/entities/list",
)
async def get_entity_types_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/list")


@tool(
    "get_entities",
    "Get entities of a given type. GET /resto/api/v2/entities/{entityType}",
    {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "description": "Entity type name (e.g. 'contractors', 'priceCategories')",
            },
        },
        "required": ["entity_type"],
    },
)
async def get_entities_handler(args):
    et = args.get("entity_type", "")
    if not et:
        return {"error": "entity_type parameter is required"}
    return await iiko.request("GET", f"/resto/api/v2/entities/{et}")


@tool(
    "get_entity_by_id",
    "Get a single entity by type and ID. "
    "GET /resto/api/v2/entities/{entityType}/{id}",
    {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "description": "Entity type name"},
            "id": {"type": "string", "description": "Entity UUID"},
        },
        "required": ["entity_type", "id"],
    },
)
async def get_entity_by_id_handler(args):
    et = args.get("entity_type", "")
    eid = args.get("id", "")
    if not et or not eid:
        return {"error": "entity_type and id parameters are required"}
    return await iiko.request("GET", f"/resto/api/v2/entities/{et}/{eid}")


@tool(
    "get_stores",
    "Get all stores (warehouses). GET /resto/api/corporation/stores/",
)
async def get_stores_handler(args):
    return await iiko.request("GET", "/resto/api/corporation/stores/")


@tool(
    "get_contractors",
    "Get all contractors (suppliers/counterparties). "
    "GET /resto/api/v2/entities/contractors",
)
async def get_contractors_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/contractors")


# ─── Documents ───────────────────────────────────────────────────────────────


@tool(
    "get_incoming_invoices",
    "Get incoming invoices for a date range. Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["from_date", "to_date"],
    },
)
async def get_incoming_invoices_handler(args):
    return await iiko.request(
        "GET",
        "/resto/api/documents/export/incomingInvoice",
        params={"from": args["from_date"], "to": args["to_date"]},
    )


@tool(
    "get_outgoing_invoices",
    "Get outgoing invoices for a date range. Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["from_date", "to_date"],
    },
)
async def get_outgoing_invoices_handler(args):
    return await iiko.request(
        "GET",
        "/resto/api/documents/export/outgoingInvoice",
        params={"from": args["from_date"], "to": args["to_date"]},
    )


@tool(
    "get_incoming_invoice_by_id",
    "Get a single incoming invoice by document ID.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Document UUID",
            },
        },
        "required": ["document_id"],
    },
)
async def get_incoming_invoice_by_id_handler(args):
    doc_id = args.get("document_id", "")
    if not doc_id:
        return {"error": "document_id is required"}
    return await iiko.request(
        "GET",
        f"/resto/api/documents/export/incomingInvoice/byId/{doc_id}",
    )


@tool(
    "get_outgoing_invoice_by_id",
    "Get a single outgoing invoice by document ID.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Document UUID",
            },
        },
        "required": ["document_id"],
    },
)
async def get_outgoing_invoice_by_id_handler(args):
    doc_id = args.get("document_id", "")
    if not doc_id:
        return {"error": "document_id is required"}
    return await iiko.request(
        "GET",
        f"/resto/api/documents/export/outgoingInvoice/byId/{doc_id}",
    )


@tool(
    "get_waste_documents",
    "Get waste/write-off documents for a date range. "
    "Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["from_date", "to_date"],
    },
)
async def get_waste_documents_handler(args):
    return await iiko.request(
        "GET",
        "/resto/api/documents/export/waste",
        params={"from": args["from_date"], "to": args["to_date"]},
    )


@tool(
    "get_menu_changes",
    "Get menu change documents. GET /resto/api/v2/documents/menuChange",
)
async def get_menu_changes_handler(args):
    return await iiko.request("GET", "/resto/api/v2/documents/menuChange")


@tool(
    "get_menu_change_by_id",
    "Get a single menu change document by ID.",
    {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Menu change document UUID",
            },
        },
        "required": ["id"],
    },
)
async def get_menu_change_by_id_handler(args):
    doc_id = args.get("id", "")
    if not doc_id:
        return {"error": "id parameter is required"}
    return await iiko.request(
        "GET",
        "/resto/api/v2/documents/menuChange/byId",
        params={"id": doc_id},
    )


# ─── Recipes / Assembly Charts ──────────────────────────────────────────────


@tool(
    "get_assembly_charts",
    "Get all assembly charts (tech cards / recipes). "
    "GET /resto/api/v2/assemblyCharts/getAll",
)
async def get_assembly_charts_handler(args):
    return await iiko.request("GET", "/resto/api/v2/assemblyCharts/getAll")


@tool(
    "get_assembly_chart_by_id",
    "Get a single assembly chart (tech card) by ID. "
    "GET /resto/api/v2/assemblyCharts/byId",
    {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Assembly chart UUID"},
        },
        "required": ["id"],
    },
)
async def get_assembly_chart_by_id_handler(args):
    chart_id = args.get("id", "")
    if not chart_id:
        return {"error": "id parameter is required"}
    return await iiko.request(
        "GET",
        "/resto/api/v2/assemblyCharts/byId",
        params={"id": chart_id},
    )


@tool(
    "save_assembly_chart",
    "Create or update an assembly chart (tech card). "
    "POST /resto/api/v2/assemblyCharts/save. "
    "Body must include: id (UUID), name, groupId, productId, "
    "outputProductId, amount, outputAmount, "
    "and nested items with productId, amount, storeId.",
    {
        "type": "object",
        "properties": {
            "body": {
                "type": "object",
                "description": "Assembly chart JSON body",
            },
        },
        "required": ["body"],
    },
)
async def save_assembly_chart_handler(args):
    body = args.get("body", {})
    if not body:
        return {"error": "body parameter is required"}
    return await iiko.request(
        "POST",
        "/resto/api/v2/assemblyCharts/save",
        json_body=body,
    )


# ─── Cash & Payments ────────────────────────────────────────────────────────


@tool(
    "get_payment_types",
    "Get payment types. GET /resto/api/v2/paymentTypes",
)
async def get_payment_types_handler(args):
    return await iiko.request("GET", "/resto/api/v2/paymentTypes")


@tool(
    "get_cash_shifts",
    "Get cash shifts list. GET /resto/api/v2/cashshifts/list",
)
async def get_cash_shifts_handler(args):
    return await iiko.request("GET", "/resto/api/v2/cashshifts/list")


@tool(
    "get_cash_shift",
    "Get a single cash shift by session ID.",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Cash shift session UUID",
            },
        },
        "required": ["session_id"],
    },
)
async def get_cash_shift_handler(args):
    sid = args.get("session_id", "")
    if not sid:
        return {"error": "session_id is required"}
    return await iiko.request("GET", f"/resto/api/v2/cashshifts/byId/{sid}")


@tool(
    "get_payments",
    "Get payments for a cash shift session.",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Cash shift session UUID",
            },
        },
        "required": ["session_id"],
    },
)
async def get_payments_handler(args):
    sid = args.get("session_id", "")
    if not sid:
        return {"error": "session_id is required"}
    return await iiko.request(
        "GET",
        f"/resto/api/v2/cashshifts/payments/list/{sid}",
    )


@tool(
    "get_payment",
    "Get a single payment by session ID and payment ID.",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Cash shift session UUID",
            },
            "payment_id": {
                "type": "string",
                "description": "Payment UUID",
            },
        },
        "required": ["session_id", "payment_id"],
    },
)
async def get_payment_handler(args):
    sid = args.get("session_id", "")
    pid = args.get("payment_id", "")
    if not sid or not pid:
        return {"error": "session_id and payment_id are required"}
    return await iiko.request(
        "GET",
        "/resto/api/v2/cashshifts/payments/byId",
        params={"sessionId": sid, "id": pid},
    )


@tool(
    "create_encashment",
    "Create an encashment (cash in/out) entry. "
    "POST /resto/api/v2/cashshifts/save",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Cash shift session UUID",
            },
            "sum": {
                "type": "number",
                "description": "Encashment amount (negative for cash out)",
            },
        },
        "required": ["session_id", "sum"],
    },
)
async def create_encashment_handler(args):
    sid = args.get("session_id", "")
    enc_sum = args.get("sum")
    if not sid or enc_sum is None:
        return {"error": "session_id and sum are required"}
    body = {"sessionId": sid, "sum": enc_sum}
    return await iiko.request(
        "POST",
        "/resto/api/v2/cashshifts/save",
        json_body=body,
    )


# ─── Prices ──────────────────────────────────────────────────────────────────


@tool(
    "get_price_categories",
    "Get all price categories. "
    "GET /resto/api/v2/entities/priceCategories",
)
async def get_price_categories_handler(args):
    return await iiko.request("GET", "/resto/api/v2/entities/priceCategories")


@tool(
    "get_price_category",
    "Get a single price category by ID.",
    {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Price category UUID"},
        },
        "required": ["id"],
    },
)
async def get_price_category_handler(args):
    cid = args.get("id", "")
    if not cid:
        return {"error": "id parameter is required"}
    return await iiko.request(
        "GET",
        "/resto/api/v2/entities/priceCategories/byId",
        params={"id": cid},
    )


# ─── Reports & OLAP ─────────────────────────────────────────────────────────


@tool(
    "get_olap_columns",
    "Get available OLAP report columns for a report type.",
    {
        "type": "object",
        "properties": {
            "report_type": {
                "type": "string",
                "description": "Report type (e.g. 'SALES', 'PROCUREMENT')",
            },
        },
        "required": ["report_type"],
    },
)
async def get_olap_columns_handler(args):
    rt = args.get("report_type", "")
    if not rt:
        return {"error": "report_type is required"}
    return await iiko.request(
        "GET",
        "/resto/api/v2/reports/olap/columns",
        params={"reportType": rt},
    )


@tool(
    "run_olap_report",
    "Run an OLAP report. POST /resto/api/v2/reports/olap. "
    "Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "report_type": {
                "type": "string",
                "description": "Report type (e.g. 'SALES')",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column names to include",
            },
            "filters": {
                "type": "object",
                "description": "Filter key-value pairs",
                "additionalProperties": True,
            },
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["report_type", "columns", "from_date", "to_date"],
    },
)
async def run_olap_report_handler(args):
    body = {
        "reportType": args["report_type"],
        "columns": args["columns"],
        "filters": args.get("filters", {}),
        "dateFrom": args["from_date"],
        "dateTo": args["to_date"],
    }
    return await iiko.request(
        "POST", "/resto/api/v2/reports/olap", json_body=body,
    )


@tool(
    "get_olap_presets",
    "Get saved OLAP report presets. "
    "GET /resto/api/v2/reports/olap/presets",
)
async def get_olap_presets_handler(args):
    return await iiko.request("GET", "/resto/api/v2/reports/olap/presets")


@tool(
    "run_olap_by_preset",
    "Run an OLAP report by preset ID. Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "preset_id": {
                "type": "string",
                "description": "Preset UUID",
            },
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["preset_id", "from_date", "to_date"],
    },
)
async def run_olap_by_preset_handler(args):
    pid = args.get("preset_id", "")
    if not pid:
        return {"error": "preset_id is required"}
    return await iiko.request(
        "GET",
        f"/resto/api/v2/reports/olap/byPresetId/{pid}",
        params={"from": args["from_date"], "to": args["to_date"]},
    )


@tool(
    "run_custom_report",
    "Run a custom report by name. Dates in DD.MM.YYYY format.",
    {
        "type": "object",
        "properties": {
            "report_name": {
                "type": "string",
                "description": "Custom report name",
            },
            "from_date": {
                "type": "string",
                "description": "Start date DD.MM.YYYY",
            },
            "to_date": {
                "type": "string",
                "description": "End date DD.MM.YYYY",
            },
        },
        "required": ["report_name", "from_date", "to_date"],
    },
)
async def run_custom_report_handler(args):
    rname = args.get("report_name", "")
    if not rname:
        return {"error": "report_name is required"}
    return await iiko.request(
        "GET",
        "/api/0/reports/custom",
        params={
            "report": rname,
            "from": args["from_date"],
            "to": args["to_date"],
        },
    )


# ─── Business-level tools ────────────────────────────────────────────────────

_MSK = timezone(timedelta(hours=3))


def _today_msk() -> str:
    return datetime.now(_MSK).strftime("%Y-%m-%d")

def _next_day(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return (dt + timedelta(days=1)).strftime("%Y-%m-%d")


def _date_filter(date_str: str) -> dict:
    """Build a standard DateRange filter for a single day."""
    return {
        "filterType": "DateRange",
        "from": f"{date_str}T00:00:00",
        "to": f"{_next_day(date_str)}T00:00:00",
        "includeLow": True,
        "includeHigh": False,
        "periodType": "CUSTOM",
    }


def _base_filters(date_str: str) -> dict:
    """Base filters: date range + exclude deleted orders/items."""
    return {
        "OpenDate.Typed": _date_filter(date_str),
        "DeletedWithWriteoff": {
            "filterType": "IncludeValues",
            "values": ["NOT_DELETED"],
        },
        "OrderDeleted": {
            "filterType": "IncludeValues",
            "values": ["NOT_DELETED"],
        },
    }


def _kassa_name(num: int) -> str:
    """Resolve cash register number to a human-readable name via VENUE_MAP."""
    return VENUE_MAP.get(num, f"Kassa {num}")


# ─────────────────────────────────────────────────────────────────────────────


@tool(
    "get_venues_revenue",
    "Get daily revenue for all venues grouped by cash register. "
    "Set IIKO_VENUE_MAP env var to map register numbers to names "
    "(e.g. 'Cafe A=4,Cafe B=5').",
    {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date YYYY-MM-DD. Defaults to today (Moscow time).",
            },
        },
    },
)
async def get_venues_revenue_handler(args):
    date_str = args.get("date") or _today_msk()

    body = {
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": _base_filters(date_str),
        "groupByRowFields": ["CashRegisterName.Number"],
        "reportType": "SALES",
    }

    result = await iiko.request(
        "POST", "/resto/api/v2/reports/olap", json_body=body,
    )
    if isinstance(result, dict) and "data" in result:
        venues = {}
        total = 0
        for d in result["data"]:
            num = d.get("CashRegisterName.Number")
            rev = d.get("DishDiscountSumInt", 0)
            name = _kassa_name(num)
            venues[name] = {"kassa": num, "revenue": rev}
            total += rev
        return {"date": date_str, "venues": venues, "total": total}
    return result


@tool(
    "get_staff_meals",
    "Get staff meals (free / без оплаты) for a date. "
    "Returns by-venue breakdown. "
    "Set IIKO_VENUE_MAP to map register numbers to names.",
    {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date YYYY-MM-DD. Defaults to today.",
            },
        },
    },
)
async def get_staff_meals_handler(args):
    date_str = args.get("date") or _today_msk()
    filters = _base_filters(date_str)
    filters["PayTypes"] = {
        "filterType": "IncludeValues",
        "values": ["(без оплаты)"],
    }

    body = {
        "aggregateFields": ["DishAmountInt"],
        "filters": filters,
        "groupByRowFields": ["CashRegisterName.Number", "WriteoffReason"],
        "reportType": "SALES",
    }

    result = await iiko.request(
        "POST", "/resto/api/v2/reports/olap", json_body=body,
    )
    if isinstance(result, dict) and "data" in result:
        by_venue: dict[str, dict[str, int]] = {}
        for d in result["data"]:
            name = _kassa_name(d.get("CashRegisterName.Number") or 0)
            reason = (d.get("WriteoffReason") or "?").lower()
            count = d.get("DishAmountInt", 0)
            by_venue.setdefault(name, {})
            by_venue[name][reason] = by_venue[name].get(reason, 0) + count
        return {"date": date_str, "venues": by_venue}
    return result


@tool(
    "get_top_dishes",
    "Get top-selling dishes for a date. Sorted by revenue.",
    {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date YYYY-MM-DD. Defaults to today.",
            },
            "kassa": {
                "type": "integer",
                "description": "Cash register number to filter. Omit for all.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10).",
            },
        },
    },
)
async def get_top_dishes_handler(args):
    date_str = args.get("date") or _today_msk()
    limit = args.get("limit", 10)
    kassa = args.get("kassa")

    filters = _base_filters(date_str)
    if kassa is not None:
        filters["CashRegisterName.Number"] = {
            "filterType": "IncludeValues",
            "values": [kassa],
        }

    body = {
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": filters,
        "groupByRowFields": ["DishName"],
        "reportType": "SALES",
    }

    result = await iiko.request(
        "POST", "/resto/api/v2/reports/olap", json_body=body,
    )
    if isinstance(result, dict) and "data" in result:
        sorted_dishes = sorted(
            result["data"],
            key=lambda d: d.get("DishDiscountSumInt", 0),
            reverse=True,
        )[:limit]
        return {
            "date": date_str,
            "kassa_filter": kassa or "all",
            "top": [
                {
                    "dish": d["DishName"],
                    "revenue": d["DishDiscountSumInt"],
                }
                for d in sorted_dishes
            ],
        }
    return result


@tool(
    "compare_revenue",
    "Compare revenue between two dates for all venues.",
    {
        "type": "object",
        "properties": {
            "date1": {
                "type": "string",
                "description": "First date YYYY-MM-DD. Defaults to today.",
            },
            "date2": {
                "type": "string",
                "description": "Second date YYYY-MM-DD. Defaults to yesterday.",
            },
        },
    },
)
async def compare_revenue_handler(args):
    today = datetime.now(_MSK)
    date1_str = args.get("date1") or today.strftime("%Y-%m-%d")
    date2_str = args.get("date2") or (
        today - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    async def revenue_for_date(date_str):
        body = {
            "aggregateFields": ["DishDiscountSumInt"],
            "filters": _base_filters(date_str),
            "groupByRowFields": ["CashRegisterName.Number"],
            "reportType": "SALES",
        }
        r = await iiko.request(
            "POST", "/resto/api/v2/reports/olap", json_body=body,
        )
        if isinstance(r, dict) and "data" in r:
            result = {}
            total = 0
            for d in r["data"]:
                num = d.get("CashRegisterName.Number")
                rev = d.get("DishDiscountSumInt", 0)
                name = _kassa_name(num)
                result[name] = rev
                total += rev
            result["total"] = total
            return result
        return {}

    r1 = await revenue_for_date(date1_str)
    r2 = await revenue_for_date(date2_str)

    def diff_pct(a, b):
        if b == 0:
            return "+∞%" if a > 0 else "0%"
        pct = (a - b) / b * 100
        return f"{'+' if pct > 0 else ''}{pct:.1f}%"

    diff = {}
    for key in sorted(set(r1) | set(r2)):
        if key == "total":
            continue
        diff[key] = diff_pct(r1.get(key, 0), r2.get(key, 0))
    diff["total"] = diff_pct(r1.get("total", 0), r2.get("total", 0))

    return {
        "date1": date1_str,
        "date2": date2_str,
        "date1_revenue": r1,
        "date2_revenue": r2,
        "diff": diff,
    }


@tool(
    "get_checks",
    "Get checks (orders) for a venue by date. "
    "Returns OrderNum, CloseTime, amount, payment type, masked card number.",
    {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date YYYY-MM-DD. Defaults to today.",
            },
            "kassa": {
                "type": "integer",
                "description": "Cash register number (required).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 200).",
            },
        },
        "required": ["kassa"],
    },
)
async def get_checks_handler(args):
    date_str = args.get("date") or _today_msk()
    kassa = args["kassa"]
    limit = args.get("limit", 200)

    filters = _base_filters(date_str)
    filters["CashRegisterName.Number"] = {
        "filterType": "IncludeValues",
        "values": [kassa],
    }

    body = {
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": filters,
        "groupByRowFields": ["OrderNum", "PayTypes", "CardNumber", "CloseTime"],
        "reportType": "SALES",
    }

    result = await iiko.request(
        "POST", "/resto/api/v2/reports/olap", json_body=body,
    )
    if isinstance(result, dict) and "data" in result:
        checks = result["data"][:limit]
        total = sum(c.get("DishDiscountSumInt", 0) for c in checks)
        return {
            "date": date_str,
            "venue": _kassa_name(kassa),
            "kassa": kassa,
            "count": len(checks),
            "total": total,
            "checks": [
                {
                    "order": c["OrderNum"],
                    "time": (c.get("CloseTime") or "")[:16],
                    "amount": c.get("DishDiscountSumInt", 0),
                    "payment_type": c.get("PayTypes", ""),
                    "card": c.get("CardNumber", ""),
                }
                for c in checks
            ],
        }
    return result


# ─── Raw ─────────────────────────────────────────────────────────────────────


@tool(
    "raw_request",
    "Make a raw authenticated request to any iikoServer endpoint.",
    {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": "HTTP method (GET, POST, PUT, DELETE)",
            },
            "path": {
                "type": "string",
                "description": "API path, e.g. '/resto/api/employees'",
            },
            "params": {
                "type": "object",
                "description": "Query parameters (key is added automatically)",
                "additionalProperties": True,
            },
            "body": {
                "type": "object",
                "description": "Request body for POST/PUT",
                "additionalProperties": True,
            },
        },
        "required": ["method", "path"],
    },
)
async def raw_request_handler(args):
    method = args.get("method", "GET").upper()
    path = args.get("path", "")
    params = args.get("params", {})
    body = args.get("body", None)
    if not path:
        return {"error": "path is required"}
    return await iiko.request(method, path, params=params, json_body=body)


# ─── MCP Server ─────────────────────────────────────────────────────────────


def serialize_result(data: Any) -> str:
    """Serialize tool result to JSON string."""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(data)


async def run_server():
    server = Server("iiko-server-mcp")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["input_schema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        entry = next((t for t in TOOLS if t["name"] == name), None)
        if not entry:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = await entry["handler"](arguments or {})
            return [TextContent(type="text", text=serialize_result(result))]
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return [TextContent(type="text", text=f"Error: {e}")]

    # Auth on startup
    try:
        await iiko.start()
        logger.info("iiko MCP server ready")
    except Exception as e:
        logger.error("Failed to authenticate: %s", e)

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await iiko.stop()


if __name__ == "__main__":
    asyncio.run(run_server())
