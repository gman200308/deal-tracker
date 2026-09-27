import custom_stores

LISTING = """
<div onclick="location.assign('/warrior-crunch-bar#produit');"><div class="txt_pr_title_list" style="">Warrior crunch bar</div></div>
<div onclick="location.assign('/t-shirt-fitjoy#produit');"><div class="txt_pr_title_list">T-SHIRT FITJOY</div></div>
"""
PRODUCT = """
<script type="application/ld+json">{"@type": "Product", "name": "Warrior crunch bar",
 "description": "Barre croquante", "image": ["https://x/img.webp"]}</script>
<script>
var g_arr_product_code = new Array("WC1", "WC2");
var g_arr_variant = new Array("Boite (12 barres)\x01Fudge Brownie (exp 05/2027)", "1 x 64g (unité)\x01Key Lime &amp; Pie");
var g_int_product_id = 777;
var g_arr_price_selling = new Array("24.00", "2.50");
var g_arr_price_regular = new Array("60.00", "5.25");
var g_arr_price_other = new Array("55.00", "0");
var g_arr_backorder = new Array("0", "1");
</script>"""


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.encoding = text, status, None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if "/categorie/liquidation" in url and "/page/" not in url:
            return FakeResp(LISTING)
        if "/categorie/" in url:
            return FakeResp("")          # other categories / later pages: empty
        return FakeResp(PRODUCT)


CFG = {"polling": {"max_pages": 5, "request_timeout_seconds": 5, "delay_between_requests_seconds": 0}}


def test_proteinarabais_reader(monkeypatch):
    sess = FakeSession()
    store = {"name": "PaR", "base_url": "https://par.test"}
    keep = lambda title, ptype: "shirt" not in title.lower()
    [prod] = custom_stores.fetch_proteinarabais(sess, store, CFG, keep)

    assert not any("t-shirt" in u for u in sess.urls)      # filtered before fetching the page
    assert prod["id"] == 777 and prod["url"] == "https://par.test/warrior-crunch-bar"
    box, single = prod["variants"]
    assert box["title"] == "Boite (12 barres) / Fudge Brownie (exp 05/2027)"
    assert box["price"] == "24.00" and box["compare_at_price"] == "60.00" and box["available"]
    assert single["title"] == "1 x 64g (unité) / Key Lime & Pie"
    assert not single["available"]                          # backorder "1" = unavailable
    assert prod["images"][0]["src"] == "https://x/img.webp"


# --- Well.ca ---------------------------------------------------------------------------
def _well_card(pid, name, size, orig, new, soldout=False):
    button = ('<div class="btn btn-soldout disabled no_link"><span>Sold Out</span></div>' if soldout
              else '<a class="add_to_cart_button btn-cart"><span>Add to Cart</span></a>')
    orig_html = f'<span class="product_grid_original_price">${orig}</span>' if orig else ""
    return (f'<div class="product-item product-id-{pid}" data-product="{pid}">'
            f'<a href="https://well.ca/products/{name.lower().replace(" ", "-")}_{pid}.html?cat=6664"></a>'
            f'<img src="https://cdn/{pid}.png" />{button}'
            f'<script> productList.add({{"name":"{name}","id":"{pid}","price":{new},"brand":"X",'
            f'"category":"Fitness & Protein\/Protein Bars & Snacks\/Protein Bars"}}, $(".x")) </script>'
            f'<div class="product_grid_info_top_text_container">{name}</div>'
            f'<div class="product_grid_info_subtitle">{size}</div>'
            f'<div class="product_grid_price">{orig_html}<span class="product_grid_new_price">${new}</span></div></div>')


WELL_PAGE = ('<div id="categories_main_content" data-total="3">'
             + _well_card(1, "Built Bar Puffs Salted Caramel", "12 x 40 g", "59.99", "30.00")
             + _well_card(2, "Clif Bar Chocolate Chip", "68 g", None, "2.49")
             + _well_card(3, "Warrior Crunch White Choc", "64 g", "4.49", "2.00", soldout=True) + "</div>")


class WellSession(FakeSession):
    def get(self, url, timeout=None, params=None):
        self.urls.append(url)
        return FakeResp(WELL_PAGE)


def test_wellca_reader():
    sess = WellSession()
    prods = custom_stores.fetch_wellca(sess, {"name": "Well.ca", "base_url": "https://well.ca",
                                              "categories": ["fitness-protein-clearance_6664"]},
                                       CFG, lambda t, p: True)
    assert len(sess.urls) == 1               # stops as soon as data-total (3) products are read
    by_id = {p["id"]: p for p in prods}
    puffs = by_id[1]
    assert puffs["title"] == "Built Bar Puffs Salted Caramel (12 x 40 g)"
    assert puffs["url"] == "https://well.ca/products/built-bar-puffs-salted-caramel_1.html"
    assert puffs["product_type"].endswith("Protein Bars")
    v = puffs["variants"][0]
    assert (v["price"], v["compare_at_price"], v["available"]) == ("30.00", "59.99", True)
    assert by_id[2]["variants"][0]["compare_at_price"] is None
    assert by_id[3]["variants"][0]["available"] is False


# --- WooCommerce -----------------------------------------------------------------------
class WooSession(FakeSession):
    def __init__(self, maintenance=False):
        super().__init__()
        self.maintenance = maintenance

    def get(self, url, timeout=None, params=None):
        self.urls.append(url)
        if url.endswith(".test/"):
            return FakeResp("Site is undergoing maintenance" if self.maintenance else "<html>shop</html>")
        body = [{"id": 5, "name": "Quest &#8211; Peanut Butter Cups (2 Pack)", "permalink": "https://w.test/p/5",
                 "is_in_stock": True, "is_purchasable": True, "categories": [{"name": "Protein Bars"}],
                 "images": [{"src": "https://w.test/5.jpg"}],
                 "prices": {"price": "409", "regular_price": "525", "currency_minor_unit": 2}}]
        return FakeJSON(body)


class FakeJSON(FakeResp):
    def __init__(self, data):
        super().__init__("")
        self._data = data

    def json(self):
        return self._data


def test_woocommerce_reader():
    store = {"name": "W", "base_url": "https://w.test", "categories": ["protein-bars"]}
    [p] = custom_stores.fetch_woocommerce(WooSession(), store, CFG, lambda t, pt: True)
    assert p["title"] == "Quest – Peanut Butter Cups (2 Pack)"
    v = p["variants"][0]
    assert (v["price"], v["compare_at_price"], v["available"]) == ("4.09", "5.25", True)


def test_woocommerce_skips_store_in_maintenance():
    sess = WooSession(maintenance=True)
    store = {"name": "W", "base_url": "https://w.test"}
    assert custom_stores.fetch_woocommerce(sess, store, CFG, lambda t, pt: True) == []
    assert len(sess.urls) == 1               # no API calls while the shop is closed
