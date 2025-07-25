import os
import requests
from flask import Flask, render_template_string, request, redirect, url_for, flash, get_flashed_messages
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "not-so-secret")
API_KEY = os.environ.get("PRINTIFY_API_KEY")

shipping_cache = {}

def get_blueprint_map():
    """Fetch all blueprints from Printify and build an id → name dict."""
    resp = requests.get(
        "https://api.printify.com/v1/catalog/blueprints.json",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    resp.raise_for_status()
    data = resp.json()
    return {bp['id']: bp['title'] for bp in data}

# Cache blueprint map for session
BLUEPRINT_MAP = None

def get_human_readable_size(variant, product_options):
    """Map variant.options (list of IDs) to human-readable size using product options metadata."""
    if not variant or "options" not in variant or not product_options:
        return "N/A"
    options = variant["options"]
    for idx, opt_meta in enumerate(product_options):
        # Accept both "type": "size" and "name" containing "size"
        if (opt_meta.get("type") == "size" or "size" in opt_meta.get("name", "").lower()) and idx < len(options):
            size_id = options[idx]
            for v in opt_meta.get("values", []):
                if v.get("id") == size_id:
                    return v.get("title", str(size_id))
    return "N/A"

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
        product_options = prod_details.get("options", [])
        variants = prod_details.get("variants", [])
        default_variant = None
        default_found = False
        for v in variants:
            if v.get("is_default"):
                default_variant = v
                default_found = True
                break
        if not default_variant and variants:
            default_variant = variants[0]
            default_found = False

        # Get human-readable size label for the default variant
        default_size = get_human_readable_size(default_variant, product_options)

        if default_found:
            print(f"[INFO] Product '{prod_details.get('title')}' — using variant '{default_variant.get('id')}' as DEFAULT (is_default=True, size={default_size}).")
        elif default_variant:
            print(f"[WARN] Product '{prod_details.get('title')}' — no variant marked is_default; using FIRST variant '{default_variant.get('id')}', size={default_size}.")
        else:
            print(f"[ERROR] Product '{prod_details.get('title')}' — no variants found!")

        prod_details["default_size"] = default_size

        prod_details["variants"] = [default_variant] if default_variant else []
        prod_details["provider_id"] = prod_details.get("provider", {}).get("id") or (
            default_variant.get("provider_id") if default_variant else None
        )
        prod_details["print_area_key"] = default_variant.get("print_area_key") if default_variant else None
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
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    if resp.status_code == 200:
        data = resp.json()
        if "standard" in data:
            price = data["standard"].get("cost", None)
            if price is not None:
                shipping_cache[key] = price
                return price
    shipping_cache[key] = None
    return None

def update_all_prices_bulk_retail(product_id, shop_id, target_retail):
    variants = get_all_variants(product_id, shop_id)
    if not variants:
        return None, variants, []
    default_variant = None
    for v in variants:
        if v.get("is_default"):
            default_variant = v
            break
    if not default_variant:
        default_variant = variants[0]
    cost = default_variant.get("cost", 0) / 100
    retail = float(target_retail)
    margin = ((retail - cost) / retail) if retail > 0 else 0
    updated = []
    for v in variants:
        v_cost = v.get("cost", 0) / 100
        v_price = round(v_cost / (1 - margin) + 0.00001, 2) if margin < 1.0 else v_cost
        variant_payload = {
            "id": v["id"],
            "price": int(round(v_price * 100)),
            "is_enabled": v.get("is_enabled", True),
            "is_visible": v.get("is_visible", True)
        }
        updated.append(variant_payload)
    patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json"
    payload = {"variants": updated}
    resp = requests.put(
        patch_url,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload
    )
    return resp, variants, updated

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
            .profit-pos { color: green; font-weight: bold;}
            .profit-neg { color: red; font-weight: bold;}
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
            &nbsp; &nbsp;
            <span class="editlabel">Retail:</span>
            $<input type="number" step="0.01" min="0" name="retail_val" id="bulk_retail" value="" style="width:80px;">
            &nbsp;&nbsp; <b>or</b> &nbsp;&nbsp;
            <span class="editlabel">Profit:</span>
            $<input type="number" step="0.01" min="0" name="profit_val" id="bulk_profit" value="" style="width:80px;">
            &nbsp;&nbsp; <b>or</b> &nbsp;&nbsp;
            <span class="editlabel">Margin %:</span>
            <input type="number" step="1" min="0" max="99" name="percent_val" id="bulk_percent" value="" style="width:60px;">
            &nbsp;&nbsp;
            <button type="submit">Save All</button>
            <button type="button" id="bulk-cancel">Cancel</button>
            <div style="margin-top:0.5em;color:#ccc;font-size:0.96em;">
                Set a retail price (all other variants follow margin), a profit (adds $ to each cost), <b>or</b> a margin percentage (profit relative to cost).<br>
                All variants for each product will update accordingly.
            </div>
        </form>
        {% for p in products %}
        <div class="prod" data-gtype="{{p.garment_type}}">
            <input class="select-checkbox" type="checkbox" value="{{p.id}}">
            <div style="display: flex; align-items: center; gap: 1em;">
                {% if p.images and p.images[0] %}
                <img src="{{ p.images[0].src }}">
                {% endif %}
                <div>
                    <h2>{{ p.title }} <span class="default-size">(Default Size: {{ p.default_size }})</span></h2>
                    <div style="color:#888;">{{ p.vendor }}</div>
                </div>
            </div>
            <div style="color:#666; font-size: 0.9em; margin-top: 0.5em;">Type: <b>{{p.type_display}}</b></div>
            <table>
                <thead>
                    <tr>
                        <th>Variant</th>
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
                    {% if v.price > 0 %}
                        {% set percent = ((prof / v.price) * 100) | round %}
                    {% else %}
                        {% set percent = 0 %}
                    {% endif %}
                    <tr>
                        <td>
                            {% for o in v.options %}{{ o }}{% if not loop.last %}, {% endif %}{% endfor %}
                        </td>
                        <td>${{ '%.2f' % (v.price / 100) }}</td>
                        <td><span id="cost_{{v.id}}">{{ '%.2f' % (v.cost / 100) }}</span></td>
                        <td>${{ '%.2f' % (prof / 100) }}</td>
                        <td>
                            <span class="
                                {% if percent >= 40 %}
                                    margin-high
                                {% elif percent >= 25 %}
                                    margin-med
                                {% else %}
                                    margin-low
                                {% endif %}
                            ">
                                {{ percent }}%
                            </span>
                        </td>
                        <td>
                            {% set shipping_cost = None %}
                            {% if p.provider_id and p.print_area_key %}
                                {% set shipping_cost = namespace(val=None) %}
                                {% set _ = shipping_cost.update({'val': v.get('shipping_cost')}) %}
                            {% endif %}
                            {% if shipping_cost and shipping_cost.val %}
                                ${{ '%.2f' % (shipping_cost.val / 100) }}
                            {% else %}
                                N/A
                            {% endif %}
                        </td>
                        <td class="edit-icons">
                            <button onclick="showEdit('{{p.id}}')" title="Edit all variants">&#9998;</button>
                        </td>
                    </tr>
                    <tr id="editbox_{{p.id}}" class="editbox" style="display:none;">
                        <td colspan="7">
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
                                    value="{% if v.price > 0 %}{{ ((v.price-v.cost)/v.price*100)|round }}{% else %}0{% endif %}"
                                    oninput="updateFromPercent('{{p.id}}','cost_{{v.id}}','retail_{{p.id}}','profit_{{p.id}}','percent_{{p.id}}')">
                                &nbsp; &nbsp;
                                <button type="submit">Save</button>
                                <button type="button" onclick="hideEdit('{{p.id}}')">Cancel</button>
                                <br>
                                <span style="font-size:0.93em;color:#888;">
                                    <b>When saving, all variants will be updated:</b><br>
                                    • If you changed retail, all variants will update using that margin.<br>
                                    • If you changed profit, all will get that profit added to their cost.<br>
                                    • If you changed margin %, all will be priced for that margin.<br>
                                    (The field you changed most will be used.)
                                </span>
                            </form>
                        </td>
                    </tr>
                    {% endif %}
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
        <script>
        // (JS unchanged)
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
        function toggleProduct(id, checked) {
            if (checked) {
                if (!selectedProducts.includes(id)) selectedProducts.push(id);
            } else {
                selectedProducts = selectedProducts.filter(pid => pid !== id);
            }
            updateBulkBar();
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
            let closeBtn = document.getElementById('close-job-msg');
            if (closeBtn) {
                closeBtn.addEventListener('click', function() {
                    document.getElementById('job-flash-messages').style.display = 'none';
                });
            }
        });
        </script>
    </body>
    </html>'''
    # Attach shipping cost per variant (only for default variant)
    for prod in detailed:
        for v in prod["variants"]:
            if v:
                provider_id = prod.get("provider_id")
                print_area_key = prod.get("print_area_key")
                v["shipping_cost"] = get_variant_shipping_cost(provider_id, print_area_key)
    return render_template_string(html, products=detailed, found_types=found_types, messages=messages)

# bulk_edit and edit_price_all endpoints remain unchanged

@app.route("/bulk_edit", methods=["POST"])
def bulk_edit():
    product_ids = request.form.get("product_ids", "")
    retail_val = request.form.get("retail_val", "").strip()
    profit_val = request.form.get("profit_val", "").strip()
    percent_val = request.form.get("percent_val", "").strip()
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
        if retail_val:
            resp, variants, updated = update_all_prices_bulk_retail(pid, shop_id, float(retail_val))
            msg_title = f"Set default variant to retail: ${float(retail_val):.2f} (others follow margin %)"
        else:
            mode = None
            value = None
            if profit_val:
                try:
                    value = float(profit_val)
                    mode = "profit"
                except Exception:
                    flash("Invalid profit value.", "error")
                    return redirect(url_for("index"))
            elif percent_val:
                try:
                    value = float(percent_val)
                    if value >= 100:
                        flash("Margin percent must be <100%.", "error")
                        return redirect(url_for("index"))
                    mode = "percent"
                except Exception:
                    flash("Invalid percent value.", "error")
                    return redirect(url_for("index"))
            resp, variants, updated = update_all_prices_bulk_retail(pid, shop_id, None)
            if mode == "profit":
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
                patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json"
                payload = {"variants": updated}
                resp = requests.put(
                    patch_url,
                    headers={
                        "Authorization": f"Bearer {API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
                msg_title = f"Set all variants to profit: ${value:.2f}"
            elif mode == "percent":
                updated = []
                margin = value / 100.0
                for v in variants:
                    cost = v.get("cost", 0) / 100
                    price = cost / (1 - margin) if margin < 1.0 else cost
                    updated.append({
                        "id": v["id"],
                        "price": int(round(price * 100)),
                        "is_enabled": v.get("is_enabled", True),
                        "is_visible": v.get("is_visible", True)
                    })
                patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{pid}.json"
                payload = {"variants": updated}
                resp = requests.put(
                    patch_url,
                    headers={
                        "Authorization": f"Bearer {API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
                msg_title = f"Set all variants to margin: {round(value)}%"
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
        confirm_rows = []
        for v in variants:
            new_row = next((u for u in updated if u["id"] == v["id"]), None)
            if new_row:
                cost = v.get("cost", 0) / 100
                price = new_row["price"] / 100
                profitx = price - cost
                marginx = (profitx / price * 100) if price > 0 else 0
                confirm_rows.append(
                    f"<tr class='updated-row'><td>{', '.join(str(x) for x in v.get('options', []))}</td>"
                    f"<td>${price:.2f}</td>"
                    f"<td>${cost:.2f}</td>"
                    f"<td>${profitx:.2f}</td>"
                    f"<td>{round(marginx)}%</td></tr>"
                )
        summary_lines.append(
            f"<b>{msg_title}</b><br>"
            f"<b>{product_title} (Product ID: {pid})</b><br>"
            "<div class='scroll-table'><table style='width:100%;background:#f8fff8;'>"
            "<tr><th>Variant</th><th>Retail</th><th>Cost</th><th>Profit</th><th>Margin %</th></tr>"
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
    try:
        shop_id, detailed, _ = get_shop_and_products()
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("index"))
    main_variant = get_all_variants(product_id, shop_id)[0]
    old_retail = main_variant.get("price", 0) / 100
    old_cost = main_variant.get("cost", 0) / 100
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
    if diff_retail >= diff_profit and diff_retail >= diff_percent:
        resp, variants, updated = update_all_prices_bulk_retail(product_id, shop_id, new_retail)
        msg_title = f"Set default variant to retail: ${new_retail:.2f} (others follow margin %)"
    elif diff_profit >= diff_retail and diff_profit >= diff_percent:
        value = new_profit
        updated = []
        variants = get_all_variants(product_id, shop_id)
        for v in variants:
            cost = v.get("cost", 0) / 100
            price = cost + value
            updated.append({
                "id": v["id"],
                "price": int(round(price * 100)),
                "is_enabled": v.get("is_enabled", True),
                "is_visible": v.get("is_visible", True)
            })
        patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json"
        payload = {"variants": updated}
        resp = requests.put(
            patch_url,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        msg_title = f"Set all variants to profit: ${value:.2f}"
    else:
        value = new_percent
        updated = []
        margin = value / 100.0
        variants = get_all_variants(product_id, shop_id)
        for v in variants:
            cost = v.get("cost", 0) / 100
            price = cost / (1 - margin) if margin < 1.0 else cost
            updated.append({
                "id": v["id"],
                "price": int(round(price * 100)),
                "is_enabled": v.get("is_enabled", True),
                "is_visible": v.get("is_visible", True)
            })
        patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json"
        payload = {"variants": updated}
        resp = requests.put(
            patch_url,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        msg_title = f"Set all variants to margin: {round(value)}%"
    if resp is None or resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        flash(f"Failed to update: {err}", "error")
        return redirect(url_for("index"))
    confirm_rows = []
    variants = get_all_variants(product_id, shop_id)
    for v in variants:
        new_row = next((u for u in updated if u["id"] == v["id"]), None)
        if new_row:
            cost = v.get("cost", 0) / 100
            price = new_row["price"] / 100
            profit = price - cost
            margin = (profit / price * 100) if price > 0 else 0
            confirm_rows.append(
                f"<tr class='updated-row'><td>{', '.join(str(x) for x in v.get('options', []))}</td>"
                f"<td>${price:.2f}</td>"
                f"<td>${cost:.2f}</td>"
                f"<td>${profit:.2f}</td>"
                f"<td>{round(margin)}%</td></tr>"
            )
    table = (
        f"<b>{msg_title}</b><br>"
        "<b>All variants updated. Changes are in Printify, not yet published in your store.</b>"
        "<div class='scroll-table'><table style='width:100%;background:#f8fff8;'>"
        "<tr><th>Variant</th><th>Retail</th><th>Cost</th><th>Profit</th><th>Margin %</th></tr>"
        + "".join(confirm_rows) + "</table></div>"
    )
    flash(table, "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(port=5000, debug=True)
