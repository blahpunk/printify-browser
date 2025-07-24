import os
import re
import requests
from flask import Flask, render_template_string, request, redirect, url_for, flash
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "not-so-secret")
API_KEY = os.environ.get("PRINTIFY_API_KEY")

GARMENT_TYPES = [
    "All-Over Print Hoodie",
    "Crewneck Sweatshirt",
    "Art Tee",
    "Pullover Art Hoodie",
    "Lightweight Hooded Tee",
    "All-over Print Tee",
]

def extract_garment_type(title):
    for gtype in GARMENT_TYPES:
        if re.search(re.escape(gtype), title, re.IGNORECASE):
            return gtype
    return "Other"

def get_shop_and_products():
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
    for prod in products:
        prod_details = requests.get(
            f"https://api.printify.com/v1/shops/{shop_id}/products/{prod['id']}.json",
            headers={"Authorization": f"Bearer {API_KEY}"}
        ).json()
        if "variants" in prod_details:
            default_variants = [v for v in prod_details["variants"] if v.get("is_default")]
            if default_variants:
                prod_details["variants"] = default_variants
            else:
                prod_details["variants"] = prod_details["variants"][:1]
        prod_details["garment_type"] = extract_garment_type(prod_details.get("title", ""))
        detailed.append(prod_details)
    found_types = sorted({p["garment_type"] for p in detailed if p["garment_type"] in GARMENT_TYPES})
    return shop_id, detailed, found_types

@app.route("/", methods=["GET"])
def index():
    msg = request.args.get("msg")
    try:
        shop_id, detailed, found_types = get_shop_and_products()
    except Exception as e:
        return str(e), 400

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Printify Product Price Breakdown</title>
        <style>
            body { font-family: sans-serif; margin: 2em; background: #f9f9fb;}
            .prod { background: #fff; border-radius: 14px; margin-bottom: 2em; padding: 1.5em; box-shadow: 0 2px 8px #0001;}
            .prod h2 { margin: 0 0 0.5em; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 0.4em 0.6em; }
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
        </style>
        <script>
        function filterByType() {
            var t = document.getElementById('gtype').value;
            document.querySelectorAll('.prod').forEach(function(p){
                var thisType = p.getAttribute('data-gtype');
                p.style.display = (!t || t=='all' || thisType==t) ? '' : 'none';
            });
        }
        function showEdit(id) {
            document.getElementById("editbox_" + id).style.display = "";
        }
        function hideEdit(id) {
            document.getElementById("editbox_" + id).style.display = "none";
        }
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
        </script>
    </head>
    <body>
        <h1>Printify Product Price Breakdown</h1>
        {% if msg %}
            <div style="padding:1em; background:#dff0d8; color:#3c763d; margin-bottom:1em; border-radius:8px;">{{ msg }}</div>
        {% endif %}
        <div id="filter-wrap">
            <label for="gtype" style="font-weight:bold;">Filter by garment type: </label>
            <select id="gtype" onchange="filterByType()">
                <option value="all">All</option>
                {% for g in found_types %}
                <option value="{{g}}">{{g}}</option>
                {% endfor %}
            </select>
        </div>
        {% for p in products %}
        <div class="prod" data-gtype="{{p.garment_type}}">
            <div style="display: flex; align-items: center; gap: 1em;">
                {% if p.images and p.images[0] %}
                <img src="{{ p.images[0].src }}">
                {% endif %}
                <div>
                    <h2>{{ p.title }}</h2>
                    <div style="color:#888;">{{ p.vendor }}</div>
                </div>
            </div>
            <div style="color:#666; font-size: 0.9em; margin-top: 0.5em;">Type: <b>{{p.garment_type}}</b></div>
            <table>
                <thead>
                    <tr>
                        <th>Variant</th>
                        <th>Retail</th>
                        <th>Cost</th>
                        <th>Profit</th>
                        <th>Margin %</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    {% for v in p.variants %}
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
                        <td class="edit-icons">
                            <button onclick="showEdit('{{v.id}}')" title="Edit price/profit/margin">&#9998;</button>
                        </td>
                    </tr>
                    <tr id="editbox_{{v.id}}" class="editbox" style="display:none;">
                        <td colspan="6">
                            <form class="editform" method="POST" action="{{ url_for('edit_price') }}">
                                <input type="hidden" name="product_id" value="{{p.id}}">
                                <input type="hidden" name="variant_id" value="{{v.id}}">
                                <span class="editlabel">Retail:</span>
                                $<input type="number" step="0.01" min="0" name="new_price" id="retail_{{v.id}}"
                                    value="{{ '%.2f' % (v.price / 100) }}"
                                    oninput="updateFromRetail('{{v.id}}','cost_{{v.id}}','retail_{{v.id}}','profit_{{v.id}}','percent_{{v.id}}')">
                                &nbsp; &nbsp;
                                <span class="editlabel">Profit:</span>
                                $<input type="number" step="0.01" min="0" id="profit_{{v.id}}"
                                    value="{{ '%.2f' % ((v.price - v.cost) / 100) }}"
                                    oninput="updateFromProfit('{{v.id}}','cost_{{v.id}}','retail_{{v.id}}','profit_{{v.id}}','percent_{{v.id}}')">
                                &nbsp; &nbsp;
                                <span class="editlabel">Margin %:</span>
                                <input type="number" step="1" min="0" max="99" id="percent_{{v.id}}"
                                    value="{% if v.price > 0 %}{{ ((v.price-v.cost)/v.price*100)|round }}{% else %}0{% endif %}"
                                    oninput="updateFromPercent('{{v.id}}','cost_{{v.id}}','retail_{{v.id}}','profit_{{v.id}}','percent_{{v.id}}')">
                                &nbsp; &nbsp;
                                <button type="submit">Save</button>
                                <button type="button" onclick="hideEdit('{{v.id}}')">Cancel</button>
                                <br>
                                <span style="font-size:0.93em;color:#888;">Retail price is what is updated in Printify. You can set by any value; the rest will auto-calculate.</span>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
        <script>
        filterByType();
        </script>
    </body>
    </html>
    '''
    return render_template_string(html, products=detailed, found_types=found_types, msg=msg)

@app.route("/edit_price", methods=["POST"])
def edit_price():
    product_id = request.form.get("product_id")
    variant_id = request.form.get("variant_id")
    new_price = request.form.get("new_price")
    try:
        shop_id, _, _ = get_shop_and_products()
    except Exception as e:
        return str(e), 400
    if not (product_id and variant_id and new_price):
        return redirect(url_for("index", msg="Missing required info."))

    try:
        price_cents = int(round(float(new_price) * 100))
    except Exception:
        return redirect(url_for("index", msg="Invalid price."))

    patch_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{product_id}.json"
    payload = {
        "variants": [
            {"id": int(variant_id), "price": price_cents}
        ]
    }
    resp = requests.patch(
        patch_url,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        return redirect(url_for("index", msg=f"Failed to update price: {err}"))
    return redirect(url_for("index", msg=f"Retail price updated to ${float(new_price):.2f} for product/variant {product_id}/{variant_id}. This does NOT publish the product."))

if __name__ == "__main__":
    app.run(port=5000, debug=True)
