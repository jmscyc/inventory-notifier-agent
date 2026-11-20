import pytest
from app.inventory_tools import check_product_inventory, send_telegram_notification
from app.firestore_tools import list_tracked_products, get_tracked_product, add_or_update_tracked_product


def test_inventory_check_example():
    res = check_product_inventory("https://example.com")
    assert "in_stock" in res
    assert "title" in res
    assert res["in_stock"] is True


def test_telegram_mock_mode():
    res = send_telegram_notification("測試通知訊息")
    assert res["success"] is True
    assert res["mode"] == "mock"
    assert "模擬發送成功" in res["detail"]


def test_firestore_crud():
    item = add_or_update_tracked_product(
        product_id="test-item-unit",
        name="Test Item Unit",
        target_url="https://example.com/test",
        category="Testing",
        price="$10.00",
        in_stock=True,
    )
    assert item["success"] is True

    fetched = get_tracked_product("test-item-unit")
    assert fetched["name"] == "Test Item Unit"

    all_items = list_tracked_products(category="Testing")
    assert any(i["id"] == "test-item-unit" for i in all_items)
