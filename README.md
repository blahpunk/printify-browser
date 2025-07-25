# Printify Product Price Manager

![App Screenshot](screenshots/Screenshot_2025-07-24_22-27-24)

A Flask-based web dashboard for viewing, filtering, and **bulk updating retail prices** of your Printify products with instant feedback and live product-type filtering.
Supports price updates by retail, margin %, or profit—while preserving your product/variant setup in Printify. Designed for efficiency and quick oversight of all your store’s key garments.

---

## Features

* **Automatic detection of garment/product types**: Filter products by actual types found in your Printify store (e.g., All-Over Print Hoodie, Premium Tee, etc.)
* **Bulk select and edit**: Set new profit, margin %, or retail for multiple products at once
* **One-click price editing** for all variants within a product, safely preserving all other options (no accidental variant changes)
* **Visual profit/margin breakdown** for each product
* **Live sync with Printify**: No need to manually maintain a product/blueprint type list; works directly from your Printify data
* **Responsive, modern UI**

---

## Getting Started

### 1. Clone the Repository

```sh
git clone https://github.com/blahpunk/printify-price-manager.git
cd printify-price-manager
```

### 2. Create and Configure Your `.env` File

Copy `.env-sample` to `.env` and set your credentials:

```sh
cp .env-sample .env
```

Open `.env` and set:

* `PRINTIFY_API_KEY`    *Your Printify API access token (see below)*
* `FLASK_SECRET_KEY`    *Any secret string for session encryption (can be random)*

Example:

```dotenv
PRINTIFY_API_KEY=your_printify_api_key_here
FLASK_SECRET_KEY=a_really_secret_key
```

*If you’re not familiar with `.env` files, they are simple text files containing key-value pairs used for local app settings.
Never commit your real `.env` file to a public repo.*

---

### 3. Get Your Printify API Key

* Log in to your [Printify account](https://printify.com/)
* Go to **My Account → API tokens** ([API Console](https://printify.com/app/account/api))
* Create a new API token with read and write permissions
* Copy the key and paste it as `PRINTIFY_API_KEY` in your `.env` file

---

### 4. Install Requirements

```sh
pip install -r requirements.txt
```

---

### 5. Run the Application

```sh
python app.py
```

Open your browser to [http://localhost:5000](http://localhost:5000)
You’ll see a dashboard showing your products, prices, and detected product types.

---

## Customization

* The product type/category filter is generated dynamically from your actual Printify product data. You do **not** need to manually edit a type list for filtering.
* If you wish to manually override names or add additional mappings, you may extend the `BLUEPRINT_MAP` dictionary in `app.py` to supply your preferred labels for specific blueprint IDs, but the app works out-of-the-box using live product names.
* All other UI and workflow logic is customizable in `app.py` as needed.

---

## Security Note

* Never commit your real `.env` with API keys to public repositories.
* This tool is intended for use by Printify store owners or admins only.

---

## Screenshots

![App Screenshot](screenshots/Screenshot_2025-07-24_22-27-24.png)

---

## License

MIT License (c) 2025
See [LICENSE](LICENSE) for details.

---

### Feedback & Issues

Open a GitHub Issue or contact the maintainer for support or feature requests.
