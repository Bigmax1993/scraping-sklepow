from unittest.mock import Mock, patch
from types import SimpleNamespace

from selenium.webdriver.common.by import By

from scraper import (
    build_driver,
    is_captcha_page,
    is_running_in_jupyter,
    transfer_cookies,
    wait_for_user_confirmation,
)

import pytest

pytestmark = pytest.mark.unit


def test_is_captcha_page_detects_sorry_url():
    driver = Mock()
    driver.current_url = "https://www.google.com/sorry/index?continue=maps"
    driver.title = "Google"
    driver.find_elements.return_value = []

    assert is_captcha_page(driver) is True


def test_is_captcha_page_detects_robot_text():
    driver = Mock()
    driver.current_url = "https://www.google.com/maps"
    driver.title = "Google Maps"

    def find_elements_side_effect(by, xpath):
        if by == By.XPATH and "i am not a robot" in xpath:
            return [Mock()]
        return []

    driver.find_elements.side_effect = find_elements_side_effect

    assert is_captcha_page(driver) is True


def test_is_captcha_page_returns_false_for_normal_page():
    driver = Mock()
    driver.current_url = "https://www.google.com/maps/search/rewe"
    driver.title = "Google Maps"
    driver.find_elements.return_value = []

    assert is_captcha_page(driver) is False


def test_transfer_cookies_adds_each_cookie():
    source_driver = Mock()
    source_driver.get_cookies.return_value = [
        {"name": "a", "value": "1"},
        {"name": "b", "value": "2"},
    ]
    target_driver = Mock()

    transfer_cookies(source_driver, target_driver)

    assert target_driver.add_cookie.call_count == 2
    target_driver.add_cookie.assert_any_call({"name": "a", "value": "1"})
    target_driver.add_cookie.assert_any_call({"name": "b", "value": "2"})


@patch("scraper.webdriver.Chrome")
@patch("scraper.Service")
@patch("scraper.ChromeDriverManager")
def test_build_driver_headless_sets_expected_flags(
    mock_cdm,
    mock_service,
    mock_chrome,
):
    mock_cdm.return_value.install.return_value = "chromedriver.exe"

    build_driver(headless=True)

    passed_options = mock_chrome.call_args.kwargs["options"]
    args = passed_options.arguments
    assert "--headless=new" in args
    assert "--window-size=1920,1080" in args
    assert "--disable-blink-features=AutomationControlled" in args
    mock_service.assert_called_once_with("chromedriver.exe")


@patch.dict("sys.modules", {}, clear=False)
def test_is_running_in_jupyter_true_for_zmq_shell():
    shell = Mock()
    shell.__class__.__name__ = "ZMQInteractiveShell"
    fake_ipython = SimpleNamespace(get_ipython=lambda: shell)
    with patch.dict("sys.modules", {"IPython": fake_ipython}, clear=False):
        assert is_running_in_jupyter() is True


@patch.dict("sys.modules", {}, clear=False)
def test_is_running_in_jupyter_false_for_other_shell():
    shell = Mock()
    shell.__class__.__name__ = "TerminalInteractiveShell"
    fake_ipython = SimpleNamespace(get_ipython=lambda: shell)
    with patch.dict("sys.modules", {"IPython": fake_ipython}, clear=False):
        assert is_running_in_jupyter() is False


@patch.dict("sys.modules", {"IPython": None}, clear=False)
def test_is_running_in_jupyter_false_when_ipython_missing():
    assert is_running_in_jupyter() is False


@patch("builtins.input", return_value="ok")
def test_wait_for_user_confirmation_calls_input_once(_):
    wait_for_user_confirmation("Potwierdź", jupyter_mode=False)
