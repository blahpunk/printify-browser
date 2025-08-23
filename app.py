# app.py

import os
import requests
from flask import Flask, render_template_string, request, redirect, url_for, flash, get_flashed_messages, jsonify
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "not-so-secret")
API_KEY = os.environ.get("PRINTIFY_API_KEY")

shipping_cache = {}

def get_blueprint_map():
    resp = requests.get(
        "https://api.printify.com/v1/catalog/blueprints.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    resp.raise_for_status()
    data = resp.json()
    return {bp['id']: bp['title'] for bp in data}

BLUEPRINT_MAP = None

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
    """Fallback: infer from variant.title by intersecting tokens with known value titles."""
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

    # Case A: list of value IDs
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

    # Case B: dict (sometimes from catalog objects)
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

def get_large_variant(variants, product_options):
    large_titles = {"large", "l"}
    for v in variants or []:
        size = get_human_readable_size(v, product_options)
        if size and size.strip().lower() in large_titles:
            return v
    if variants:
        return variants[0]
    return None

# ---------- Core API helpers ----------

def get_shop_and_products():
    global BLUEPRINT_MAP
    if BLUEPRINT_MAP is None:
        BLUEPRINT_MAP = get_blueprint_map()
    shops = requests.get(
        "https://api.printify.com/v1/shops.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    ).json()
    if not shops or not shops[0].get("id"):
        raise Exception(f"No shops found in your account. Response: {shops}")
    shop_id = shops[0]["id"]

    products_resp = requests.get(
        f"https://api.printify.com/v1/shops/{shop_id}/products.json?limit=50",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    products = products_resp.json().get("data", [])
    if not products:
        raise Exception("No products found for this shop.")

    detailed = []
    used_blueprint_ids = set()

    for prod in products:
        prod_details = requests.get(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{prod['id']}.json",
            headers={"Authorization": f"Bearer {API_KEY}"}
        ).json()

        product_options = prod_details.get("options", []) or []
        variants = prod_details.get("variants", []) or []

        # annotate each variant with resolved size/color (per product)
        for var in variants:
            sz, col = extract_size_color_titles(var, product_options)
            var["__size_title"] = sz
            var["__color_title"] = col

        large_variant = get_large_variant(variants, product_options)
        large_size = get_human_readable_size(large_variant, product_options) if large_variant else "N/A"
        if large_variant and large_size.lower() == "large":
            print(f"[INFO] Product '{prod_details.get('title')}' — using variant '{large_variant.get('id')}' as KEY (Large, size={large_size}).")
        elif large_variant:
            print(f"[WARN] Product '{prod_details.get('title')}' — no Large variant; using FIRST variant '{large_variant.get('id')}', size={large_size}.")
        else:
            print(f"[ERROR] Product '{prod_details.get('title')}' — no variants found!")

        # One-line summary on card
        prod_details["default_size"] = large_size
        prod_details["variants"] = [large_variant] if large_variant else []

        # Full list for the expandable table
        prod_details["all_variants"] = variants or []

        # Provider/print area for shipping lookup
        prod_details["provider_id"] = (
            prod_details.get("print_provider_id")
            or prod_details.get("provider", {}).get("id")
            or (large_variant.get("print_provider_id") if large_variant else None)
        )
        prod_details["print_area_key"] = large_variant.get("print_area_key") if large_variant else None

        blueprint_id = prod_details.get("blueprint_id")
        garment_type = BLUEPRINT_MAP.get(blueprint_id, f"Blueprint {blueprint_id}")
        prod_details["garment_type"] = garment_type
        prod_details["type_display"] = garment_type
        used_blueprint_ids.add(blueprint_id)
        detailed.append(prod_details)

    found_types = sorted({p["garment_type"] for p in detailed})
    return shop_id, detailed, found_types

def get_all_variants(product_id, shop_id):
    r = requests.get(
        f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    prod = r.json()
    return prod.get("variants", [])

def get_variant_shipping_cost(provider_id, print_area_key, country_code="US"):
    key = (provider_id, print_area_key, country_code)
    if key in shipping_cache:
        return shipping_cache[key]
    if not provider_id or not print_area_key:
        return None
    url = f"https://api.printify.com/v1/shipping.json?country={country_code}&provider_id={provider_id}&print_area_key={print_area_key}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {API_KEY}"})
    if resp.status_code == 200:
        data = resp.json()
        if "standard" in data:
            price = data["standard"].get("cost", None)
            if price is not None:
                shipping_cache[key] = price
                return price
    shipping_cache[key] = None
    return None

# ---------- Pricing helpers ----------

def build_uniform_update(variants, uniform_retail):
    """Return payload list setting the same retail price for every variant."""
    uniform_cents = int(round(float(uniform_retail) * 100))
    updated = []
    for v in variants or []:
        updated.append({
            "id": v["id"],
            "price": uniform_cents,
            "is_enabled": v.get("is_enabled", True),
            "is_visible": v.get("is_visible", True)
        })
    return updated

def update_all_prices_based_on_large(product_id, shop_id, target_retail):
    """Existing per-cost pricing (non-flat)."""
    prod_resp = requests.get(
        f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    prod_data = prod_resp.json()
    product_options = prod_data.get("options", []) or []
    variants = prod_data.get("variants", []) or []
    large_variant = get_large_variant(variants, product_options)
    if not large_variant:
        return None, variants, [], product_options

    cost = large_variant.get("cost", 0) / 100
    retail = float(target_retail)
    margin = ((retail - cost) / retail) if retail > 0 else 0

    updated = []
    for v in variants:
        v_cost = v.get("cost", 0) / 100
        v_price = round(v_cost / (1 - margin) + 0.00001, 2) if margin < 1.0 else v_cost
        updated.append({
            "id": v["id"],
            "price": int(round(v_price * 100)),
            "is_enabled": v.get("is_enabled", True),
            "is_visible": v.get("is_visible", True)
        })

    patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json"
    payload = {"variants": updated}
    resp = requests.put(
        patch_url,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload
    )
    return resp, variants, updated, product_options

# ---------- Flask routes ----------

@app.route("/", methods=["GET"])
def index():
    messages = get_flashed_messages(with_categories=True)
    try:
        shop_id, detailed, found_types = get_shop_and_products()
    except Exception as e:
        return str(e), 400

    html = '''<!DOCTYPE html>
    <html>
    <head>
        <title>Printify Product Price Breakdown</title>
        <style>
            body { font-family: sans-serif; margin: 2em; background: #f9f9fb;}
            .prod { background: #fff; border-radius: 14px; margin-bottom: 2em; padding: 1.5em; box-shadow: 0 2px 8px #0001; position: relative;}
            .prod h2 { margin: 0 0 0.5em; }
            .default-size { font-size: 1em; font-weight: 600; color: #4c5799; margin-left: 0.5em;}
            table { width: 100%; border-collapse: collapse; table-layout: fixed;}
            th, td { padding: 0.4em 0.6em; text-align: center; vertical-align: middle;}
            th { background: #f0f0f7; }
            td { border-top: 1px solid #eee; }
            .margin-high { color: green; }
            .margin-med { color: orange; }
            .margin-low { color: red; }
            img { width: 80px; height: 80px; object-fit: contain; background: #f2f2f2; border-radius: 10px;}
            #filter-wrap { margin-bottom: 2em; }
            .editform { display: inline; }
            .edit-icons button {border:none;background:none;cursor:pointer;}
            .editbox { background:#eef; padding:1em; border-radius:8px; margin-bottom:1em;}
            .editlabel { font-weight: bold; }
            .updated-row { background: #e4fcd7; }
            .flash-success {padding:1em; background:#dff0d8; color:#3c763d; margin-bottom:1em; border-radius:8px;}
            .flash-error {padding:1em; background:#ffe1e1; color:#a32c2c; margin-bottom:1em; border-radius:8px;}
            .select-checkbox {position:absolute;top:16px;left:16px;zoom:1.3;}
            .scroll-table {max-height:320px;overflow:auto;border-radius:8px;box-shadow:0 1px 6px #0002;}
            .scroll-table table {line-height:2;}
            #bulk-edit-bar {display:none; margin-bottom: 2em; background: #222; color: #fff; padding: 1.2em 1.2em 0.9em 1.2em; border-radius: 1em; box-shadow: 0 2px 16px #0005;}
            #bulk-edit-bar input {margin-left:0.5em;margin-right:1em;}
            #bulk-edit-bar label {font-weight:600;}
            #bulk-edit-bar .editlabel {color:#6fa84f;}
            #bulk-edit-bar button {margin-left:1em;}
            #job-flash-messages {position:relative;}
            #close-job-msg { position: absolute; right: 12px; top: 12px; background: #ccc; color: #222; border: none; border-radius: 50%; width: 28px; height: 28px; font-size: 1.6em; line-height: 1; cursor:pointer; z-index: 10;}
            #bulk-publish-bar {display:none; margin-bottom:2em;}
            .expand-btn {margin-top:0.8em; border: 1px solid #ddd; background:#f8f8ff; padding:0.5em 0.8em; border-radius:8px; cursor:pointer;}
            .allvars-wrap {margin-top:0.8em; display:none;}
            .inline-note { color:#bbb; font-size:0.9em; display:block; margin-top:0.25em; }
            .flat-row { margin-left:1em; }
        </style>
    </head>
    <body>
        <h1>Printify Product Price Breakdown</h1>

        <div id="job-flash-messages">
            {% set has_msg = false %}
            {% for category, msg in messages %}
                {% if category == 'success' or category == 'error' %}
                    {% if not has_msg %}
                        {% set has_msg = true %}
                    {% endif %}
                    <div class="flash-{{category}}">{{ msg|safe }}</div>
                {% endif %}
            {% endfor %}
            {% if has_msg %}
                <button id="close-job-msg" title="Hide Results">&times;</button>
            {% endif %}
        </div>

        <div id="filter-wrap">
            <label for="gtype" style="font-weight:bold;">Filter by product type: </label>
            <select id="gtype">
                <option value="all">All</option>
                {% for g in found_types %}
                <option value="{{g}}">{{g}}</option>
                {% endfor %}
            </select>
            &nbsp; <label><input type="checkbox" id="select-all-cb"> Select All Visible</label>
        </div>

        <form id="bulk-edit-bar" method="POST" action="{{ url_for('bulk_edit') }}">
            <span><b>Bulk edit <span id="bulk_count">0</span> selected items</b></span>
            <input type="hidden" name="product_ids" id="bulk_products" value="">
            &nbsp;&nbsp;
            <span class="editlabel">Retail:</span>
            $<input type="number" step="0.01" min="0" name="retail_val" id="bulk_retail" value="" style="width:80px;">
            &nbsp;&nbsp; <b>or</b> &nbsp;&nbsp;
            <span class="editlabel">Profit:</span>
            $<input type="number" step="0.01" min="0" name="profit_val" id="bulk_profit" value="" style="width:80px;">
            &nbsp;&nbsp; <b>or</b> &nbsp;&nbsp;
            <span class="editlabel">Margin %:</span>
            <input type="number" step="1" min="0" max="99" name="percent_val" id="bulk_percent" value="" style="width:60px;">
            <span class="flat-row">
                <label><input type="checkbox" name="flat_prices" id="bulk_flat"> Flat prices</label>
            </span>
            &nbsp;&nbsp;
            <button type="submit">Save All</button>
            <button type="button" id="bulk-cancel">Cancel</button>
            <div style="margin-top:0.5em;color:#ccc;font-size:0.96em;">
                Set a retail price (all other variants follow margin, based on Large), a profit (adds $ to each cost), <b>or</b> a margin percentage (profit relative to cost, based on Large).<br>
                <span class="inline-note">If <b>Flat prices</b> is checked, one final retail is applied to every variant. For Profit or Margin %, the final retail is computed from the Large variant and used for all variants.</span>
            </div>
        </form>

        <div id="bulk-publish-bar">
            <button type="button" id="bulk-publish-btn">Publish Selected to Store</button>
            <span id="publish-status" style="margin-left:1em;color:#297;display:none;"></span>
        </div>

        {% for p in products %}
        <div class="prod" data-gtype="{{p.garment_type}}">
            <input class="select-checkbox" type="checkbox" value="{{p.id}}">
            <div style="display: flex; align-items: center; gap: 1em;">
                {% if p.images and p.images[0] %}
                <img src="{{ p.images[0].src }}">
                {% endif %}
                <div>
                    <h2>{{ p.title }} <span class="default-size">(Large-Ref Size: {{ p.default_size }})</span></h2>
                    <div style="color:#888;">{{ p.vendor }}</div>
                </div>
            </div>

            <div style="color:#666; font-size: 0.9em; margin-top: 0.5em;">Type: <b>{{p.type_display}}</b></div>

            <!-- Summary row (Large or first) -->
            <table>
                <thead>
                    <tr>
                        <th>Size</th>
                        <th>Color</th>
                        <th>Retail</th>
                        <th>Cost</th>
                        <th>Profit</th>
                        <th>Margin %</th>
                        <th>Shipping</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    {% for v in p.variants %}
                    {% if v %}
                    {% set prof = v.price - v.cost %}
                    {% set percent = ((prof / v.price) * 100) | round if v.price > 0 else 0 %}
                    <tr>
                        <td>{{ v.__size_title if v.__size_title is defined else 'N/A' }}</td>
                        <td>{{ v.__color_title if v.__color_title is defined else 'N/A' }}</td>
                        <td>${{ '%.2f' % (v.price / 100) }}</td>
                        <td><span id="cost_{{v.id}}">{{ '%.2f' % (v.cost / 100) }}</span></td>
                        <td>${{ '%.2f' % (prof / 100) }}</td>
                        <td>
                            <span class="{% if percent >= 40 %}margin-high{% elif percent >= 25 %}margin-med{% else %}margin-low{% endif %}">
                                {{ percent }}%
                            </span>
                        </td>
                        <td>
                            {% if v.get('shipping_cost') %}
                                ${{ '%.2f' % (v.get('shipping_cost') / 100) }}
                            {% else %}
                                N/A
                            {% endif %}
                        </td>
                        <td class="edit-icons">
                            <button onclick="showEdit('{{p.id}}')" title="Edit all variants">&#9998;</button>
                        </td>
                    </tr>
                    <tr id="editbox_{{p.id}}" class="editbox" style="display:none;">
                        <td colspan="8">
                            <form class="editform" method="POST" action="{{ url_for('edit_price_all') }}">
                                <input type="hidden" name="product_id" value="{{p.id}}">
                                <input type="hidden" name="variant_id" value="{{v.id}}">
                                <span class="editlabel">Retail:</span>
                                $<input type="number" step="0.01" min="0" name="new_price" id="retail_{{p.id}}"
                                    value="{{ '%.2f' % (v.price / 100) }}"
                                    oninput="updateFromRetail('{{p.id}}','cost_{{v.id}}','retail_{{p.id}}','profit_{{p.id}}','percent_{{p.id}}')">
                                &nbsp; &nbsp;
                                <span class="editlabel">Profit:</span>
                                $<input type="number" step="0.01" min="0" name="profit_val" id="profit_{{p.id}}"
                                    value="{{ '%.2f' % ((v.price - v.cost) / 100) }}"
                                    oninput="updateFromProfit('{{p.id}}','cost_{{v.id}}','retail_{{p.id}}','profit_{{p.id}}','percent_{{p.id}}')">
                                &nbsp; &nbsp;
                                <span class="editlabel">Margin %:</span>
                                <input type="number" step="1" min="0" max="99" name="percent_val" id="percent_{{p.id}}"
                                    value="{{ ((v.price-v.cost)/v.price*100)|round if v.price > 0 else 0 }}"
                                    oninput="updateFromPercent('{{p.id}}','cost_{{v.id}}','retail_{{p.id}}','profit_{{p.id}}','percent_{{p.id}}')">
                                &nbsp; &nbsp;
                                <label class="flat-row"><input type="checkbox" name="flat_prices" id="flat_{{p.id}}"> Flat prices</label>
                                &nbsp; &nbsp;
                                <button type="submit">Save</button>
                                <button type="button" onclick="hideEdit('{{p.id}}')">Cancel</button>
                                <br>
                                <span style="font-size:0.93em;color:#888;">
                                    <b>When saving, all variants will be updated:</b><br>
                                    • If you changed retail, all variants will update using that margin (based on Large).<br>
                                    • If you changed profit, all will get that profit added to their cost.<br>
                                    • If you changed margin %, all will be priced for that margin (based on Large).<br>
                                    (The field you changed most will be used.)<br>
                                    <span class="inline-note">If <b>Flat prices</b> is checked, one final retail is applied to every variant. For Profit or Margin %, the final retail is computed from the Large variant and used for all variants.</span>
                                </span>
                            </form>
                        </td>
                    </tr>
                    {% endif %}
                    {% endfor %}
                </tbody>
            </table>

            <!-- Expandable full variants table -->
            <button class="expand-btn" type="button" onclick="toggleAllVariants('{{p.id}}')" id="expand_btn_{{p.id}}">Show all variants</button>
            <div class="allvars-wrap" id="allvars_{{p.id}}">
                <div class="scroll-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Size</th>
                                <th>Color</th>
                                <th>Retail</th>
                                <th>Cost</th>
                                <th>Profit</th>
                                <th>Margin %</th>
                                <th>Shipping</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for av in p.all_variants %}
                            {% if av.is_enabled %}
                            {% set a_prof = av.price - av.cost %}
                            {% set a_percent = ((a_prof / av.price) * 100) | round if av.price > 0 else 0 %}
                            <tr>
                                <td>{{ av.__size_title if av.__size_title is defined else 'N/A' }}</td>
                                <td>{{ av.__color_title if av.__color_title is defined else 'N/A' }}</td>
                                <td>${{ '%.2f' % (av.price / 100) }}</td>
                                <td>${{ '%.2f' % (av.cost / 100) }}</td>
                                <td>${{ '%.2f' % (a_prof / 100) }}</td>
                                <td>
                                    <span class="{% if a_percent >= 40 %}margin-high{% elif a_percent >= 25 %}margin-med{% else %}margin-low{% endif %}">
                                        {{ a_percent }}%
                                    </span>
                                </td>
                                <td>
                                    {% if av.get('shipping_cost') %}
                                        ${{ '%.2f' % (av.get('shipping_cost') / 100) }}
                                    {% else %}
                                        N/A
                                    {% endif %}
                                </td>
                            </tr>
                            {% endif %}
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            <!-- End expandable -->
        </div>
        {% endfor %}

        <script>
        let selectedProducts = [];
        function updateBulkBar() {
            let bar = document.getElementById("bulk-edit-bar");
            if (selectedProducts.length > 0) {
                bar.style.display = 'block';
            } else {
                bar.style.display = 'none';
                document.getElementById("bulk_retail").value = "";
                document.getElementById("bulk_profit").value = "";
                document.getElementById("bulk_percent").value = "";
            }
            document.getElementById("bulk_count").innerText = selectedProducts.length;
            document.getElementById("bulk_products").value = selectedProducts.join(",");
        }
        function updatePublishBar() {
            let bar = document.getElementById("bulk-publish-bar");
            if (selectedProducts.length > 0) {
                bar.style.display = 'block';
            } else {
                bar.style.display = 'none';
                document.getElementById("publish-status").style.display = "none";
            }
        }
        function toggleProduct(id, checked) {
            if (checked) {
                if (!selectedProducts.includes(id)) selectedProducts.push(id);
            } else {
                selectedProducts = selectedProducts.filter(pid => pid !== id);
            }
            updateBulkBar();
            updatePublishBar();
        }
        function selectAllVisible(checked) {
            let products = document.querySelectorAll('.prod');
            products.forEach(prod => {
                if(prod.style.display !== "none") {
                    let cb = prod.querySelector('.select-checkbox');
                    cb.checked = checked;
                    toggleProduct(cb.value, checked);
                }
            });
        }
        function clearSelections() {
            selectedProducts = [];
            document.querySelectorAll('.select-checkbox').forEach(cb=>{ cb.checked=false; });
            updateBulkBar();
            updatePublishBar();
        }
        function filterByType() {
            var t = document.getElementById('gtype').value;
            document.querySelectorAll('.prod').forEach(function(p){
                var thisType = p.getAttribute('data-gtype');
                p.style.display = (!t || t=='all' || thisType==t) ? '' : 'none';
            });
        }
        function showEdit(id) { document.getElementById("editbox_" + id).style.display = ""; }
        function hideEdit(id) { document.getElementById("editbox_" + id).style.display = "none"; }
        function updateFromProfit(id, costId, retailId, profitId, percentId) {
            let cost = parseFloat(document.getElementById(costId).textContent);
            let profit = parseFloat(document.getElementById(profitId).value);
            let retail = cost + profit;
            document.getElementById(retailId).value = retail.toFixed(2);
            let percent = (profit / retail) * 100;
            document.getElementById(percentId).value = isFinite(percent) ? Math.round(percent) : 0;
        }
        function updateFromPercent(id, costId, retailId, profitId, percentId) {
            let cost = parseFloat(document.getElementById(costId).textContent);
            let percent = parseFloat(document.getElementById(percentId).value);
            let retail = cost / (1 - percent/100);
            let profit = retail - cost;
            document.getElementById(retailId).value = retail.toFixed(2);
            document.getElementById(profitId).value = profit.toFixed(2);
        }
        function updateFromRetail(id, costId, retailId, profitId, percentId) {
            let cost = parseFloat(document.getElementById(costId).textContent);
            let retail = parseFloat(document.getElementById(retailId).value);
            let profit = retail - cost;
            let percent = (profit / retail) * 100;
            document.getElementById(profitId).value = profit.toFixed(2);
            document.getElementById(percentId).value = isFinite(percent) ? Math.round(percent) : 0;
        }
        function toggleAllVariants(id){
            const el = document.getElementById('allvars_' + id);
            const btn = document.getElementById('expand_btn_' + id);
            if(el.style.display === 'none' || el.style.display === ''){
                el.style.display = 'block';
                if(btn) btn.textContent = 'Hide all variants';
            } else {
                el.style.display = 'none';
                if(btn) btn.textContent = 'Show all variants';
            }
        }
        document.addEventListener("DOMContentLoaded", function() {
            document.getElementById("gtype").addEventListener("change", function(){
                filterByType();
                clearSelections();
            });
            document.getElementById("select-all-cb").addEventListener("change", function() {
                selectAllVisible(this.checked);
            });
            document.getElementById("bulk-cancel").addEventListener("click", function() {
                clearSelections();
            });
            document.querySelectorAll('.select-checkbox').forEach(cb=>{
                cb.addEventListener("change", function() {
                    toggleProduct(cb.value, cb.checked);
                });
            });
            filterByType();
            updateBulkBar();
            updatePublishBar();
            let closeBtn = document.getElementById('close-job-msg');
            if (closeBtn) {
                closeBtn.addEventListener('click', function() {
                    document.getElementById('job-flash-messages').style.display = 'none';
                });
            }
            // Publish action
            document.getElementById("bulk-publish-btn").addEventListener("click", async function() {
                if(selectedProducts.length === 0) return;
                let status = document.getElementById("publish-status");
                status.style.display = "inline";
                status.textContent = "Publishing...";
                let resp = await fetch("{{ url_for('publish_selected') }}", {
                    method: "POST",
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ product_ids: selectedProducts })
                });
                let data = await resp.json();
                let msgs = data.results.map(
                    r => r.success
                        ? `✔️ ${r.title || r.id}: Published`
                        : `❌ ${r.title || r.id}: ${r.error || 'Failed'}`
                ).join(" | ");
                status.textContent = msgs;
            });
        });
        </script>
    </body>
    </html>'''

    # Attach shipping to both summary variant and all variants
    for prod in detailed:
        provider_id = prod.get("provider_id")
        print_area_key = prod.get("print_area_key")
        ship_cost = get_variant_shipping_cost(provider_id, print_area_key)
        for v in prod.get("variants", []):
            if v is not None:
                v["shipping_cost"] = ship_cost
        for av in prod.get("all_variants", []):
            av["shipping_cost"] = ship_cost

    return render_template_string(html, products=detailed, found_types=found_types, messages=messages)

@app.route("/bulk_edit", methods=["POST"])
def bulk_edit():
    product_ids = request.form.get("product_ids", "")
    retail_val = request.form.get("retail_val", "").strip()
    profit_val = request.form.get("profit_val", "").strip()
    percent_val = request.form.get("percent_val", "").strip()
    flat_prices = request.form.get("flat_prices") is not None  # checkbox present => True

    if not product_ids:
        flash("No products selected.", "error")
        return redirect(url_for("index"))
    ids = [pid for pid in product_ids.split(",") if pid]

    try:
        shop_id, detailed, _ = get_shop_and_products()
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("index"))

    product_lookup = {str(p['id']): p.get('title', str(p['id'])) for p in detailed}
    set_count = sum(1 for x in [retail_val, profit_val, percent_val] if x)
    if set_count != 1:
        flash("Set either Retail, Profit, or Margin %, not more than one.", "error")
        return redirect(url_for("index"))

    summary_lines = []

    for pid in ids:
        variants = []
        updated = []
        product_options = []

        # Fetch product (we need variants & Large sometimes)
        prod_resp = requests.get(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json",
            headers={"Authorization": f"Bearer {API_KEY}"}
        )
        prod_data = prod_resp.json()
        product_options = prod_data.get("options", []) or []
        variants = prod_data.get("variants", []) or []
        large_variant = get_large_variant(variants, product_options)
        large_cost = (large_variant.get("cost", 0) / 100) if large_variant else 0.0

        if retail_val:
            target_retail = float(retail_val)
            if flat_prices:
                updated = build_uniform_update(variants, target_retail)
                resp = requests.put(
                    f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json",
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    json={"variants": updated}
                )
            else:
                resp, variants, updated, product_options = update_all_prices_based_on_large(pid, shop_id, target_retail)
            msg_title = f"Set Large-variant to retail: ${target_retail:.2f} ({'Flat' if flat_prices else 'others follow margin'})"

        elif profit_val:
            try:
                value = float(profit_val)
            except Exception:
                flash("Invalid profit value.", "error")
                return redirect(url_for("index"))

            if flat_prices:
                target_retail = large_cost + value
                updated = build_uniform_update(variants, target_retail)
            else:
                updated = []
                for v in variants:
                    cost = v.get("cost", 0) / 100
                    price = cost + value
                    updated.append({
                        "id": v["id"],
                        "price": int(round(price * 100)),
                        "is_enabled": v.get("is_enabled", True),
                        "is_visible": v.get("is_visible", True)
                    })
            resp = requests.put(
                f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"variants": updated}
            )
            msg_title = f"Set all variants to profit: ${value:.2f} ({'Flat retail from Large' if flat_prices else 'per-variant'})"

        elif percent_val:
            try:
                value = float(percent_val)
                if value >= 100:
                    flash("Margin percent must be <100%.", "error")
                    return redirect(url_for("index"))
            except Exception:
                flash("Invalid percent value.", "error")
                return redirect(url_for("index"))

            margin = value / 100.0
            if flat_prices:
                target_retail = (large_cost / (1 - margin)) if margin < 1.0 else large_cost
                updated = build_uniform_update(variants, target_retail)
            else:
                updated = []
                for v in variants:
                    v_cost = v.get("cost", 0) / 100
                    v_price = round(v_cost / (1 - margin) + 0.00001, 2) if margin < 1.0 else v_cost
                    updated.append({
                        "id": v["id"],
                        "price": int(round(v_price * 100)),
                        "is_enabled": v.get("is_enabled", True),
                        "is_visible": v.get("is_visible", True)
                    })
            resp = requests.put(
                f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"variants": updated}
            )
            msg_title = f"Set all variants to margin: {round(value)}% ({'Flat retail from Large' if flat_prices else 'per-variant'})"

        else:
            flash("No pricing field set.", "error")
            return redirect(url_for("index"))

        product_title = product_lookup.get(str(pid), str(pid))

        if resp is None or resp.status_code != 200:
            try:
                err = resp.json()
                if isinstance(err, dict) and err.get('code') == 8251:
                    reason = err.get("errors", {}).get("reason", "")
                    summary_lines.append(
                        f"<b>{product_title} ({pid}): Failed to update:</b> {reason} "
                        "<br><span style='color:#c00;'>You likely have >100 enabled variants (may include hidden/archived). Disable some in Printify, then try again.</span><br>"
                    )
                    continue
            except Exception:
                err = resp.text if resp is not None else "Unknown error"
            summary_lines.append(f"<b>{product_title} ({pid}): Failed to update:</b> {err}<br>")
            continue

        # Ensure size/color labels are right in the confirmation
        for v in variants:
            sz, col = extract_size_color_titles(v, product_options or [])
            v["__size_title"] = sz
            v["__color_title"] = col

        confirm_rows = []
        for v in variants:
            new_row = next((u for u in updated if u["id"] == v["id"]), None)
            if new_row:
                cost = v.get("cost", 0) / 100
                price = new_row["price"] / 100
                profitx = price - cost
                marginx = (profitx / price * 100) if price > 0 else 0
                sz = v.get("__size_title", "N/A")
                col = v.get("__color_title", "N/A")
                confirm_rows.append(
                    f"<tr class='updated-row'><td>{sz}</td><td>{col}</td>"
                    f"<td>${price:.2f}</td><td>${cost:.2f}</td>"
                    f"<td>${profitx:.2f}</td><td>{round(marginx)}%</td></tr>"
                )

        summary_lines.append(
            f"<b>{msg_title}</b><br>"
            f"<b>{product_title} (Product ID: {pid})</b><br>"
            "<div class='scroll-table'><table style='width:100%;background:#f8fff8;'>"
            "<tr><th>Size</th><th>Color</th><th>Retail</th><th>Cost</th><th>Profit</th><th>Margin %</th></tr>"
            + "".join(confirm_rows) + "</table></div>"
        )

    flash("<br>".join(summary_lines), "success")
    return redirect(url_for("index"))

@app.route("/edit_price_all", methods=["POST"])
def edit_price_all():
    product_id = request.form.get("product_id")
    new_price = request.form.get("new_price")
    profit_val = request.form.get("profit_val")
    percent_val = request.form.get("percent_val")
    flat_prices = request.form.get("flat_prices") is not None  # checkbox present => True

    try:
        shop_id, _, _ = get_shop_and_products()
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("index"))

    prod_resp = requests.get(
        f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    prod_data = prod_resp.json()
    product_options = prod_data.get("options", []) or []
    variants = prod_data.get("variants", []) or []

    # annotate for confirmation table
    for var in variants:
        sz, col = extract_size_color_titles(var, product_options)
        var["__size_title"] = sz
        var["__color_title"] = col

    large_variant = get_large_variant(variants, product_options)
    if not large_variant:
        flash("No Large or fallback variant found.", "error")
        return redirect(url_for("index"))

    old_retail = large_variant.get("price", 0) / 100
    old_cost = large_variant.get("cost", 0) / 100
    old_profit = old_retail - old_cost
    old_percent = (old_profit / old_retail * 100) if old_retail > 0 else 0

    try:
        new_retail = float(new_price)
        new_profit = float(profit_val)
        new_percent = float(percent_val)
    except Exception:
        flash("Invalid field values.", "error")
        return redirect(url_for("index"))

    diff_retail = abs(new_retail - old_retail)
    diff_profit = abs(new_profit - old_profit)
    diff_percent = abs(new_percent - old_percent)

    # Choose the field that changed most
    if diff_retail >= diff_profit and diff_retail >= diff_percent:
        if flat_prices:
            updated = build_uniform_update(variants, new_retail)
            resp = requests.put(
                f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={"variants": updated}
            )
        else:
            resp, variants2, updated, _ = update_all_prices_based_on_large(product_id, shop_id, new_retail)
            if variants2:
                variants = variants2
        msg_title = f"Set Large-variant to retail: ${new_retail:.2f} ({'Flat' if flat_prices else 'others follow margin'})"

    elif diff_profit >= diff_retail and diff_profit >= diff_percent:
        value = new_profit
        if flat_prices:
            target_retail = old_cost + value  # compute from Large
            updated = build_uniform_update(variants, target_retail)
        else:
            updated = []
            for v in variants:
                cost = v.get("cost", 0) / 100
                price = cost + value
                updated.append({
                    "id": v["id"],
                    "price": int(round(price * 100)),
                    "is_enabled": v.get("is_enabled", True),
                    "is_visible": v.get("is_visible", True)
                })
        resp = requests.put(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"variants": updated}
        )
        msg_title = f"Set all variants to profit: ${value:.2f} ({'Flat retail from Large' if flat_prices else 'per-variant'})"

    else:
        value = new_percent
        margin = value / 100.0
        if value >= 100:
            flash("Margin percent must be <100%.", "error")
            return redirect(url_for("index"))
        if flat_prices:
            target_retail = (old_cost / (1 - margin)) if margin < 1.0 else old_cost  # compute from Large
            updated = build_uniform_update(variants, target_retail)
        else:
            updated = []
            for v in variants:
                v_cost = v.get("cost", 0) / 100
                v_price = round(v_cost / (1 - margin) + 0.00001, 2) if margin < 1.0 else v_cost
                updated.append({
                    "id": v["id"],
                    "price": int(round(v_price * 100)),
                    "is_enabled": v.get("is_enabled", True),
                    "is_visible": v.get("is_visible", True)
                })
        resp = requests.put(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"variants": updated}
        )
        msg_title = f"Set all variants to margin: {round(value)}% ({'Flat retail from Large' if flat_prices else 'per-variant'})"

    if resp is None or resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        flash(f"Failed to update: {err}", "error")
        return redirect(url_for("index"))

    # Re-annotate just in case
    for v in variants:
        sz, col = extract_size_color_titles(v, product_options)
        v["__size_title"] = sz
        v["__color_title"] = col

    confirm_rows = []
    for v in variants:
        new_row = next((u for u in updated if u["id"] == v["id"]), None)
        if new_row:
            cost = v.get("cost", 0) / 100
            price = new_row["price"] / 100
            profit = price - cost
            margin = (profit / price * 100) if price > 0 else 0
            sz = v.get("__size_title", "N/A")
            col = v.get("__color_title", "N/A")
            confirm_rows.append(
                f"<tr class='updated-row'><td>{sz}</td><td>{col}</td>"
                f"<td>${price:.2f}</td><td>${cost:.2f}</td>"
                f"<td>${profit:.2f}</td><td>{round(margin)}%</td></tr>"
            )

    table = (
        f"<b>{msg_title}</b><br>"
        "<b>All variants updated. Changes are in Printify, not yet published in your store.</b>"
        "<div class='scroll-table'><table style='width:100%;background:#f8fff8;'>"
        "<tr><th>Size</th><th>Color</th><th>Retail</th><th>Cost</th><th>Profit</th><th>Margin %</th></tr>"
        + "".join(confirm_rows) + "</table></div>"
    )
    flash(table, "success")
    return redirect(url_for("index"))

@app.route("/publish_selected", methods=["POST"])
def publish_selected():
    data = request.get_json()
    product_ids = data.get("product_ids", [])

    try:
        shop_id, detailed, _ = get_shop_and_products()
    except Exception as e:
        return jsonify({"results": [{"id": None, "success": False, "error": str(e)}]}), 500

    id_title = {str(p["id"]): p.get("title", "") for p in detailed}
    results = []
    for pid in product_ids:
        try:
            publish_resp = requests.post(
                f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}/publish.json",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={
                    "title": False,
                    "description": False,
                    "images": False,
                    "variants": False,
                    "tags": False,
                    "keyFeatures": False,
                    "shipping_template": False,
                    "retail_price": True
                }
            )
            if publish_resp.status_code == 200:
                results.append({"id": pid, "title": id_title.get(str(pid), ""), "success": True})
            else:
                err_msg = publish_resp.json() if publish_resp.content else publish_resp.text
                results.append({"id": pid, "title": id_title.get(str(pid), ""), "success": False, "error": str(err_msg)})
        except Exception as ex:
            results.append({"id": pid, "title": id_title.get(str(pid), ""), "success": False, "error": str(ex)})
    return jsonify({"results": results})

if __name__ == "__main__":
    app.run(port=5000, debug=True)
