import csv
from getpass import getpass
from json import loads
from random import randint
from typing import Tuple, List, Set
from argparse import ArgumentParser
from datetime import datetime

from keyring import get_password, set_password, delete_password
from playwright.sync_api import sync_playwright, Page, TimeoutError

KEYRING_KEY: str = __file__


class MonizzeTransaction:
    date: str
    voucher: str
    amount: float
    detail: str

    def __init__(self, date: str, voucher: str, amount: str, detail: str):
        self.date = date
        self.voucher = voucher
        self.amount = float(amount)
        self.detail = detail

    def __hash__(self):
        return hash(self.date) \
               ^ hash(self.voucher) \
               ^ hash(self.amount) \
               ^ hash(self.detail)

    def __eq__(self, other):
        return hash(self) == hash(other)

    @property
    def row(self) -> List[str]:
        return [
            self.date,
            self.voucher,
            self.amount,
            self.detail,
        ]

class MonizzeClient:
    endpoint: str = "https://my.monizze.be/en/"
    _page: Page

    def __init__(self, page: Page):
        self._page = page

    def _get_or_prompt_credentials(
            self, email: str, clear: bool
    ) -> Tuple[str, str]:
        password = get_password(KEYRING_KEY, email)

        if password is None:
            password = getpass(prompt=f"Email:    {email}\nPassword: ")
            set_password(KEYRING_KEY, email, password)
            print("Password saved to keyring")
        else:
            print("Got password from keyring")

        return email, password

    def login(self, email: str, clear: bool = False):
        email, password = self._get_or_prompt_credentials(email, clear)

        print(f"Logging in to Monizze...")
        self._page.goto(self.endpoint + "login", wait_until="networkidle")

        try:
            self._page.click("button#onetrust-accept-btn-handler", timeout=50)
        except TimeoutError:
            pass

        self._page.wait_for_timeout(1000)
        self._page.fill("input#email", email)
        self._page.fill("input#password", password)
        self._page.click(
            "input[type=\"submit\"]", delay=randint(5,50), force=True
        )

    def get_history(self) -> List[MonizzeTransaction]:
        print("Retrieving transaction history...")
        history: Set[MonizzeTransaction] = set()

        with self._page.expect_response("**/voucher/history") as r:
            self._page.goto(self.endpoint + "history")

        self._add_to_history(r, history)

        keep_paging = True

        while keep_paging:
            with self._page.expect_response("**/voucher/history/*/*/*") as r:
                try:
                    self._page.click(
                        "tfoot > tr > td > a:last-of-type", timeout=250
                    )
                except TimeoutError:
                    keep_paging = False
                    continue

            len_t0 = len(history)
            self._add_to_history(r, history)
            if len(history) == len_t0:
                keep_paging = False

        return sorted(history, key=lambda transaction: transaction.date)

    def _add_to_history(self, r, history: Set[MonizzeTransaction]):
        data = loads(r.value.body())["data"]
        for voucher, entries in data.items():
            for entry in entries:
                transaction = MonizzeTransaction(
                    entry["date"], voucher, entry["amount"], entry["detail"]
                )
                history.add(transaction)


def before(t0: str, t1: str) -> bool:
    return datetime.fromisoformat(t0) < datetime.fromisoformat(t1)


def save_csv(path: str, history: List[MonizzeTransaction]) -> None:
    # Monizze seems to only show transactions for the last year or so
    # -> keep any transactions older than the ones we've just retrieved
    oldest = history[0].date
    old: List[MonizzeTransaction] = []
    try:
        with open(path, "r") as f:
            reader = csv.reader(
                f, delimiter=",", quotechar="\"", quoting=csv.QUOTE_ALL
            )
            next(reader) # skip header
            older_than_current = True
            while older_than_current:
                row = next(reader)
                if before(row[0], oldest):
                    old.append(MonizzeTransaction(*row))
                else:
                    older_than_current = False

            if len(old) > 0:
                print(
                    f"Monizze only reported transactions starting from {oldest}; "
                    f"keeping {len(old)} older transaction(s) already present in CSV file."
                )
    except FileNotFoundError:
        # No such CSV file? nothing to do here!
        pass

    print("Saving to CSV...")
    with open(path, "w+", newline="") as f:
        writer = csv.writer(
            f, delimiter=",", quotechar="\"", quoting=csv.QUOTE_ALL
        )
        writer.writerow(["Date", "Monizze Voucher", "Amount", "Detail"])

        for transaction in old + history:
            writer.writerow(transaction.row)


if __name__ == '__main__':
    parser = ArgumentParser(
        prog="monizze-csv",
        description="scrape transaction history from Monizze and save as CSV."
    )
    parser.add_argument(
        "-e", "--email", type=str, required=True,
        help="the email address with which to log in"
    )
    parser.add_argument(
        "-o", "--output-path", type=str,
        help="where to save the CSV"
    )
    parser.add_argument(
        "-c", "--clear", action='store_true',
        help="clear the stored password for this email address "
             "from the keyring"
    )
    args = parser.parse_args()

    if args.clear:
        delete_password(KEYRING_KEY, args.email)
        print("Password cleared from keyring.")
    else:
        if not args.output_path:
            raise SystemExit("No output path provided!")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            mc = MonizzeClient(page)
            mc.login(args.email)
            save_csv(args.output_path, mc.get_history())

            page.context.close()
            browser.close()
            print("Done.")