from datetime import date

import deal_tracker as dt

CFG = dt.load_config(dt.ROOT / "config.toml")
TH = CFG["thresholds"]


def deal(**kw):
    base = dict(store="S", product_id=1, variant_id=2, product_title="Bar", variant_title="Default Title",
                url="u", price=20.0, compare_at=50.0, discount_pct=60.0, count=12, count_known=True,
                per_bar=1.67, best_before=date(2026, 11, 30), date_source="variant", days_left=60)
    base.update(kw)
    return dt.Deal(**base)


def test_qualifies_rules():
    assert dt.qualifies(deal(), TH)[0]
    assert not dt.qualifies(deal(per_bar=2.51), TH)[0]
    assert not dt.qualifies(deal(days_left=6), TH)[0]
    assert dt.qualifies(deal(days_left=7), TH)[0]
    # undated items need a real markdown
    assert not dt.qualifies(deal(best_before=None, days_left=None, discount_pct=0), TH)[0]
    assert dt.qualifies(deal(best_before=None, days_left=None, discount_pct=40), TH)[0]


def test_alert_reasons():
    d = deal()
    assert dt.alert_reason(d, {}, TH) == "new"
    assert dt.alert_reason(d, {d.key: {"price": 25.0}}, TH).startswith("price drop")
    assert dt.alert_reason(d, {d.key: {"price": 20.0}}, TH) is None
    assert dt.alert_reason(d, {d.key: {"price": 20.0, "in_stock": False}}, TH) == "back in stock"


def test_health_warns_once_and_recovers():
    h = {}
    fail = {"S": {"ok": False, "error": "boom"}}
    for i in range(CFG["health"]["consecutive_failures"] - 1):
        msgs, h = dt.check_health(h, fail, CFG)
        assert msgs == []
    msgs, h = dt.check_health(h, fail, CFG)
    assert len(msgs) == 1 and "needs attention" in msgs[0]["title"]
    msgs, h = dt.check_health(h, fail, CFG)
    assert msgs == []                                   # no repeat while still broken
    msgs, h = dt.check_health(h, {"S": {"ok": True, "products": 500}}, CFG)
    assert len(msgs) == 1 and "back to normal" in msgs[0]["title"]


def test_health_warns_on_product_count_collapse():
    h = {"S": {"fail_streak": 0, "usual_products": 800, "warned": False}}
    msgs, h = dt.check_health(h, {"S": {"ok": True, "products": 30}}, CFG)
    assert len(msgs) == 1 and "30 products" in msgs[0]["message"]
