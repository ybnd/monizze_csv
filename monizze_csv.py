import csv
from getpass import getpass
from json import loads
from logging import Logger, getLogger
from random import randint
from typing import Tuple, List, Set

from keyring import get_password, set_password, delete_password
from playwright.sync_api import sync_playwright, Page, TimeoutError

log: Logger = getLogger("monizze_csv")


class MonizzeTransaction:
    date: str
    voucher: str
    amount: float
    detail: str

    def __hash__(self):
        return hash(self.date) \
               ^ hash(self.voucher) \
               ^ hash(self.amount) \
               ^ hash(self.detail)

    def __eq__(self, other):
        return hash(self) == hash(other)


class MonizzeClient:
    endpoint: str = "https://my.monizze.be/en/"
    _page: Page
    _token: str
    _csrf: str
    _logged_in: bool

    CSRF_XPATH: str = "//head/meta[@name='csrf-token']/@content"
    TOKEN_XPATH: str = "//form[@id='login-form']/input[@name='_token']/@value"
    KEYRING_KEY: str = __file__

    def __init__(self, page: Page):
        self._page = page
        self._page.on("request", self._handle_request)
        self._page.on("response", self._handle_response)
        self._logged_in = False

    def _handle_request(self, r):
        print(f"Request: {r}")

    def _handle_response(self, r):
        print(f"Response: {r}")

    def _get_or_prompt_credentials(
            self, email: str, clear: bool
    ) -> Tuple[str, str]:
        if clear:
            delete_password(self.KEYRING_KEY, email)

        password = get_password(self.KEYRING_KEY, email)

        if password is None:
            password = getpass(prompt=f"Email:    {email}\nPassword: ")
            set_password(self.KEYRING_KEY, email, password)

        return email, password

    def login(self, email: str, clear: bool = False):
        email, password = self._get_or_prompt_credentials(email, clear)

        self._page.goto(self.endpoint + "login", wait_until="networkidle")

        try:
            self._page.click("button#onetrust-accept-btn-handler", timeout=50)
        except TimeoutError:
            pass

        self._page.wait_for_timeout(1000)
        self._page.fill("input#email", email)
        self._page.fill("input#password", password)
        self._page.click("input[type=\"submit\"]", delay=randint(5,50), force=True)
        self._page.wait_for_timeout(1000)

    def get_history(self) -> List[MonizzeTransaction]:
        history: Set[MonizzeTransaction] = set()

        def _handle_history(r):
            data = loads(r.value.body())["data"]
            for voucher, entries in data.items():
                for entry in entries:
                    transaction = MonizzeTransaction()
                    transaction.voucher = voucher
                    transaction.date = entry["date"]
                    transaction.amount = float(entry["amount"])
                    transaction.detail = entry["detail"]
                    history.add(transaction)

        with self._page.expect_response("**/voucher/history") as r:
            self._page.goto(self.endpoint + "history")

        _handle_history(r)

        keep_paging = True

        while keep_paging:
            with self._page.expect_response("**/voucher/history/*/*/*") as r:
                try:
                    self._page.click("tfoot > tr > td > a:last-of-type", timeout=250)
                except TimeoutError:
                    keep_paging = False
                    continue

            len_t0 = len(history)
            _handle_history(r)
            if len(history) == len_t0:
                keep_paging = False

        return sorted(history, key=lambda transaction: transaction.date)


def save_csv(history: List[MonizzeTransaction]) -> None:
    with open("/home/ybnd/monizze.csv", "w+") as f:
        writer = csv.writer(
            f, delimiter=",", quotechar="\"", quoting=csv.QUOTE_ALL
        )
        writer.writerow(["Date", "Monizze Voucher", "Amount", "Detail"])

        for transaction in history:
            writer.writerow([
                transaction.date,
                transaction.voucher,
                transaction.amount,
                transaction.detail
            ])


if __name__ == '__main__':
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        mc = MonizzeClient(page)
        mc.login("ybnd@tuta.io")
        save_csv(mc.get_history())


        page.context.close()
        browser.close()