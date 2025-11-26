import os
import re
import time
from urllib.parse import urlparse

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

AMAZON_RAPIDAPI_KEY = os.getenv(
    "AMAZON_RAPIDAPI_KEY", "eed1eb9a41msh4b52a1c92cdca8cp1f1142jsn137cd6d55801"
)
GOOGLE_RAPIDAPI_KEY = os.getenv(
    "GOOGLE_RAPIDAPI_KEY", "53ab7bc106mshab922e7e38938a8p1e6d0ejsn2d4534d61481"
)

AMAZON_HOST = "real-time-amazon-data.p.rapidapi.com"
GOOGLE_HOST = "google-search74.p.rapidapi.com"

CACHE_TTL = 60
_cache_store = {}

ECOMMERCE_KEYWORDS = [
    "amazon.",
    "flipkart.",
    "myntra.",
    "ajio.",
    "tatacliq",
    "nykaa",
    "meesho",
    "reliance",
    "snapdeal",
    "croma",
    "shopclues",
    "limeroad",
    "shoppersstop",
    "pantaloons",
    "nike.",
    "adidas.",
]


def make_cache_key(params: dict) -> str:
    return "|".join(f"{k}={params.get(k,'')}" for k in sorted(params.keys()))


def get_cached(key: str):
    rec = _cache_store.get(key)
    if not rec:
        return None
    ts, data = rec
    if time.time() - ts > CACHE_TTL:
        del _cache_store[key]
        return None
    return data


def set_cache(key: str, data):
    _cache_store[key] = (time.time(), data)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.amazon.in" + url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    domain = (parsed.netloc or "").lower().replace("www.", "")
    return domain


def ensure_domain(product: dict):
    domain = product.get("domain") or extract_domain(product.get("url", ""))
    if not domain and product.get("source"):
        source = product["source"].lower().strip()
        if "." in source:
            domain = source.replace("www.", "")
        else:
            domain = f"{source}.com"
    if domain:
        product["domain"] = domain
        product.setdefault("source", domain)
    return product


def google_search(query: str, limit=12):
    params = {
        "query": query,
        "limit": str(limit),
        "related_keywords": "false",
    }
    cache_key = "google|" + make_cache_key(params)
    cached = get_cached(cache_key)
    if cached:
        return cached

    url = f"https://{GOOGLE_HOST}/"
    headers = {
        "x-rapidapi-key": GOOGLE_RAPIDAPI_KEY,
        "x-rapidapi-host": GOOGLE_HOST,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    set_cache(cache_key, data)
    return data


def amazon_search(query: str, country="IN"):
    params = {"query": query, "page": "1", "country": country}
    cache_key = "amazon|" + make_cache_key(params)
    cached = get_cached(cache_key)
    if cached:
        return cached

    url = f"https://{AMAZON_HOST}/search"
    headers = {
        "x-rapidapi-key": AMAZON_RAPIDAPI_KEY,
        "x-rapidapi-host": AMAZON_HOST,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    set_cache(cache_key, data)
    return data


def map_google_item(item: dict):
    price_raw = item.get("price") or item.get("price_string")
    price = None
    price_text = None
    if isinstance(price_raw, (int, float)):
        price = float(price_raw)
        price_text = f"₹{price:,.0f}"
    elif isinstance(price_raw, str):
        price_text = price_raw.strip()
        digits = re.sub(r"[^\d.]", "", price_text)
        if digits:
            try:
                price = float(digits)
            except ValueError:
                price = None

    product = {
        "title": item.get("title") or item.get("name") or "",
        "price": price,
        "price_text": price_text,
        "url": normalize_url(item.get("link") or item.get("url") or ""),
        "image": normalize_url(
            item.get("thumbnail") or item.get("thumbnail_highres") or item.get("image") or ""
        ),
        "source": item.get("source") or item.get("displayed_url") or "",
    }
    return ensure_domain(product)


def map_amazon_item(item: dict):
    price_raw = (
        item.get("price")
        or item.get("product_price")
        or item.get("product_minimum_offer_price")
        or item.get("price_current")
    )
    price = None
    price_text = None
    if isinstance(price_raw, (int, float)):
        price = float(price_raw)
        price_text = f"₹{price:,.0f}"
    elif isinstance(price_raw, str):
        price_text = price_raw.strip()
        digits = re.sub(r"[^\d.]", "", price_text)
        if digits:
            try:
                price = float(digits)
            except ValueError:
                price = None

    product = {
        "title": item.get("title") or item.get("product_title") or "",
        "price": price,
        "price_text": price_text,
        "url": normalize_url(
            item.get("url")
            or item.get("product_link")
            or item.get("detail_page_url")
            or item.get("product_url")
            or ""
        ),
        "image": normalize_url(
            item.get("image")
            or item.get("thumbnail")
            or item.get("product_photo")
            or ""
        ),
        "source": "Amazon.in",
    }
    return ensure_domain(product)


def parse_budget(budget: str):
    budget = (budget or "").replace(",", "")
    nums = re.findall(r"\d+", budget)
    if len(nums) >= 2:
        low, high = sorted([float(nums[0]), float(nums[1])])
        return low, high
    if len(nums) == 1:
        val = float(nums[0])
        return val, val
    return None, None


def filter_by_budget(products, low, high):
    if low is None:
        return products
    filtered = []
    for p in products:
        price = p.get("price")
        if price is None:
            continue
        max_allowed = high * 1.2 if high else low * 1.2
        min_allowed = low * 0.8
        if price < min_allowed or price > max_allowed:
            continue
        filtered.append(p)
    return filtered or products


def enforce_domain_mix(products, desired=6, per_domain_limit=2):
    """
    Try to keep at most `per_domain_limit` items per domain, but relax the rule
    if we still don't have enough products.
    """
    final = []
    counts = {}

    def add_items(limit=None):
        for prod in products:
            domain = prod.get("domain") or ""
            if limit is not None and counts.get(domain, 0) >= limit:
                continue
            if prod in final:
                continue
            final.append(prod)
            counts[domain] = counts.get(domain, 0) + 1
            if len(final) >= desired:
                return True
        return False

    add_items(per_domain_limit)
    if len(final) < desired:
        add_items(None)
    return final


def fetch_recommendations(query, color, gender, budget, country="IN"):
    gender = gender.strip().lower()
    base_query = query.strip()
    if color and color.lower() not in base_query.lower():
        base_query = f"{base_query} {color}"
    if gender in ("male", "man", "men"):
        base_query = f"{base_query} men"
    elif gender in ("female", "woman", "women"):
        base_query = f"{base_query} women"

    budget_low, budget_high = parse_budget(budget)

    products = []
    google_resp = None
    try:
        google_resp = google_search(base_query, limit=18)
    except Exception as exc:
        st.warning(f"Google search failed: {exc}")

    if google_resp:
        google_items = []
        if isinstance(google_resp.get("shopping_results"), list):
            google_items.extend(google_resp["shopping_results"])
        if isinstance(google_resp.get("results"), list):
            for res in google_resp["results"]:
                url = res.get("url", "")
                if "amazon" in url.lower():
                    continue
                if not any(keyword in url.lower() for keyword in ECOMMERCE_KEYWORDS):
                    continue
                google_items.append(res)
        google_products = [map_google_item(it) for it in google_items]
        google_products = filter_by_budget(google_products, budget_low, budget_high)
        products.extend(google_products)

    if len(products) < 6:
        try:
            amazon_resp = amazon_search(base_query, country=country)
            item_list = None
            for key in ("results", "products", "items", "search_results"):
                if key in amazon_resp and isinstance(amazon_resp[key], list):
                    item_list = amazon_resp[key]
                    break
            if not item_list and isinstance(amazon_resp, dict) and "data" in amazon_resp:
                data = amazon_resp["data"]
                if isinstance(data, dict) and "products" in data:
                    item_list = data["products"]
            if item_list:
                amazon_products = [map_amazon_item(it) for it in item_list]
                amazon_products = filter_by_budget(amazon_products, budget_low, budget_high)
                products.extend(amazon_products)
        except Exception as exc:
            st.error(f"Amazon search failed: {exc}")

    if not products:
        return []

    seen = set()
    unique_products = []
    for p in products:
        key = (p.get("domain"), p.get("title"))
        if key in seen:
            continue
        unique_products.append(p)
        seen.add(key)

    # sort by price proximity if budget given, else leave order
    if budget_low is not None:
        target = budget_high if budget_high else budget_low
        unique_products.sort(
            key=lambda x: abs((x.get("price") or target) - target)
        )

    final = enforce_domain_mix(unique_products, desired=6, per_domain_limit=2)
    if len(final) < 6:
        final = unique_products[:6]
    return final[:6]


def main():
    st.set_page_config(page_title="AI Fashion Recommender", layout="wide")
    st.title("AI Fashion Recommender")
    st.write("Find products across multiple ecommerce stores with RapidAPI.")

    col1, col2 = st.columns(2)
    with col1:
        items = st.text_input("Items / Keywords", "black shoes")
        category = st.text_input("Category", "Footwear")
        color = st.text_input("Color", "")
        budget = st.text_input("Budget (₹ or range)", "3000")
    with col2:
        gender = st.selectbox(
            "Gender", ["Prefer not to say", "Male", "Female", "Unisex"], index=0
        )
        size = st.text_input("Size", "")
        country = st.selectbox("Marketplace Country", ["IN", "US", "UK"], index=0)

    if st.button("Get recommendations"):
        query = items or category
        if not query:
            st.warning("Please enter items or category.")
            return
        with st.spinner("Searching Google Shopping and Amazon..."):
            products = fetch_recommendations(query, color, gender, budget, country)

        if not products:
            st.error("No products found for those filters.")
            return

        st.success(f"Showing {len(products)} products")
        cols = st.columns(3)
        for idx, prod in enumerate(products):
            col = cols[idx % 3]
            with col:
                if prod.get("image"):
                    col.image(prod["image"], use_column_width=True)
                else:
                    col.image(
                        "https://via.placeholder.com/600x400?text=No+Image",
                        use_column_width=True,
                    )
                col.markdown(f"**{prod.get('title') or 'Untitled'}**")
                if prod.get("price_text"):
                    col.markdown(f"**Price:** {prod['price_text']}")
                elif prod.get("price"):
                    col.markdown(f"**Price:** ₹{prod['price']:.0f}")
                if prod.get("source"):
                    col.caption(f"Source: {prod['source']}")
                if prod.get("url"):
                    col.markdown(f"[View product]({prod['url']})", unsafe_allow_html=True)


if __name__ == "__main__":
    main()

