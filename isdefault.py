# isdefault.py

import os
import requests
from flask import Flask, render_template_string
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
API_KEY = os.environ.get("PRINTIFY_API_KEY")

# ---------- Option helpers (ID-based, robust) ----------

def _normalize_id(x):
    try:
        return str(x)
    except Exception:
        return x

def _build_valueid_lookup(product_options):
    """
    Build { value_id_str: {"kind": "size"|"color"|"other", "title": "2XL"} }
    using product_options[].values[].id/title and option 'type' or name.
    """
    lookup = {}
    for opt in product_options or []:
        name = (opt.get("name") or "").lower()
        typ = (opt.get("type") or "").lower()
        kind = "other"
        if typ == "size" or "size" in name:
            kind = "size"
        elif typ == "color" or "colour" in name or "color" in name:
            kind = "color"
        for v in opt.get("values", []) or []:
            vid = _normalize_id(v.get("id"))
            if vid:
                lookup[vid] = {"kind": kind, "title": v.get("title") or str(v.get("id"))}
    return lookup

def _parse_from_title_fallback(variant_title, product_options):
    """Fallback: infer size/color from variant.title by intersecting tokens with known value titles."""
    title = (variant_title or "")
    tokens = [t.strip() for part in title.split("/") for t in part.split("-")]
    tokens = [t for t in tokens if t]
    known_sizes, known_colors = set(), set()
    for opt in product_options or []:
        name = (opt.get("name") or "").lower()
        typ = (opt.get("type") or "").lower()
        for v in opt.get("values", []) or []:
            if typ == "size" or "size" in name:
                known_sizes.add(v.get("title"))
            elif typ == "color" or "colour" in name or "color" in name:
                known_colors.add(v.get("title"))
    size_title = next((t for t in tokens if t in known_sizes), None)
    color_title = next((t for t in tokens if t in known_colors), None)
    return size_title or "N/A", color_title or "N/A"

def extract_size_color_titles(variant, product_options):
    """
    Resolve (size_title, color_title) for a Product variant:
    - Prefer mapping each value-id in variant.options to product_options by ID
    - Works if options is a list (value IDs) or a dict (titles)
    - Falls back to parsing variant.title
    """
    size_title, color_title = None, None
    if not variant:
        return "N/A", "N/A"

    lookup = _build_valueid_lookup(product_options or [])
    opts = variant.get("options")

    # Case A: list of value IDs (common in Products API)
    if isinstance(opts, list):
        for raw_val in opts:
            vkey = _normalize_id(raw_val)
            meta = lookup.get(vkey)
            if not meta:
                continue
            if meta["kind"] == "size" and not size_title:
                size_title = meta["title"]
            elif meta["kind"] == "color" and not color_title:
                color_title = meta["title"]

    # Case B: dict (seen in some catalog objects)
    elif isinstance(opts, dict):
        for k, v in opts.items():
            key = (k or "").lower()
            val = str(v) if v is not None else ""
            if ("size" in key) and not size_title:
                size_title = val
            if ("color" in key or "colour" in key) and not color_title:
                color_title = val

    if not (size_title and color_title):
        s2, c2 = _parse_from_title_fallback(variant.get("title"), product_options or [])
        size_title = size_title or s2
        color_title = color_title or c2
    return size_title or "N/A", color_title or "N/A"

def get_human_readable_size(variant, product_options):
    if not variant:
        return "N/A"
    size_title, _ = extract_size_color_titles(variant, product_options or [])
    return size_title or "N/A"

# ---------- Data fetch ----------

def get_products_and_defaults():
    shops = requests.get(
        "https://api.printify.com/v1/shops.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    ).json()
    if not shops or not shops[0].get("id"):
        raise Exception("No shops found in your account.")
    shop_id = shops[0]["id"]

    resp = requests.get(
        f"https://api.printify.com/v1/shops/{shop_id}/products.json?limit=50",
        headers={"Authorization": f"Bearer {API_KEY}"}
    ).json()
    products = resp.get("data", [])

    rows = []
    for prod in products:
        details = requests.get(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{prod['id']}.json",
            headers={"Authorization": f"Bearer {API_KEY}"}
        ).json()
        product_options = details.get("options", []) or []
        variants = details.get("variants", []) or []

        default_variant = next((v for v in variants if v.get("is_default")), None)
        if not default_variant and variants:
            default_variant = variants[0]

        if default_variant:
            sz, col = extract_size_color_titles(default_variant, product_options)
            rows.append({
                "product_id": prod["id"],
                "title": prod.get("title", "Untitled"),
                "variant_id": default_variant.get("id", "N/A"),
                "size": sz,
                "color": col
            })
        else:
            rows.append({
                "product_id": prod["id"],
                "title": prod.get("title", "Untitled"),
                "variant_id": "N/A",
                "size": "N/A",
                "color": "N/A"
            })
    return rows

# ---------- Flask route ----------

@app.route("/")
def index():
    try:
        products = get_products_and_defaults()
    except Exception as e:
        return f"<b>Error:</b> {e}"
    html = '''
    <h2>Printify Product Default Variants</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Product Title</th>
            <th>Product ID</th>
            <th>Default Variant ID</th>
            <th>Size</th>
            <th>Color</th>
        </tr>
        {% for p in products %}
        <tr>
            <td>{{p.title}}</td>
            <td>{{p.product_id}}</td>
            <td>{{p.variant_id}}</td>
            <td>{{p.size}}</td>
            <td>{{p.color}}</td>
        </tr>
        {% endfor %}
    </table>
    '''
    return render_template_string(html, products=products)

if __name__ == "__main__":
    app.run(port=5001, debug=True)
