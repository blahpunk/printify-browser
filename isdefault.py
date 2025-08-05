import os
import requests
from flask import Flask, render_template_string
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
API_KEY = os.environ.get("PRINTIFY_API_KEY")

def get_human_readable_size(variant, product_options):
    if not variant or "options" not in variant or not product_options:
        return "N/A"
    options = variant["options"]
    for idx, opt_meta in enumerate(product_options):
        if (opt_meta.get("type") == "size" or "size" in opt_meta.get("name", "").lower()) and idx < len(options):
            size_id = options[idx]
            for v in opt_meta.get("values", []):
                if v.get("id") == size_id:
                    return v.get("title", str(size_id))
    return "N/A"

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
        product_options = details.get("options", [])
        variants = details.get("variants", [])
        default_variant = next((v for v in variants if v.get("is_default")), None)
        if not default_variant and variants:
            default_variant = variants[0]
        size = get_human_readable_size(default_variant, product_options) if default_variant else "N/A"
        rows.append({
            "product_id": prod["id"],
            "title": prod.get("title", "Untitled"),
            "variant_id": default_variant["id"] if default_variant else "N/A",
            "size": size,
            "variant_options": default_variant.get("options", []) if default_variant else []
        })
    return rows

@app.route("/")
def index():
    try:
        products = get_products_and_defaults()
    except Exception as e:
        return f"<b>Error:</b> {e}"
    html = '''
    <h2>Printify Product Default Variants</h2>
    <table border=1 cellpadding=6 cellspacing=0>
        <tr>
            <th>Product Title</th>
            <th>Product ID</th>
            <th>Default Variant ID</th>
            <th>Default Size</th>
            <th>Options Raw</th>
        </tr>
        {% for p in products %}
        <tr>
            <td>{{p.title}}</td>
            <td>{{p.product_id}}</td>
            <td>{{p.variant_id}}</td>
            <td>{{p.size}}</td>
            <td>{{p.variant_options}}</td>
        </tr>
        {% endfor %}
    </table>
    '''
    return render_template_string(html, products=products)

if __name__ == "__main__":
    app.run(port=5001, debug=True)
