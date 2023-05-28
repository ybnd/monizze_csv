import csv
import re
import uuid
from getpass import getpass
from json import loads
from random import randint
from typing import Tuple, List, Set
from argparse import ArgumentParser
from datetime import datetime
from enum import Enum

from keyring import get_password, set_password, delete_password
from playwright.sync_api import sync_playwright, Playwright, Browser, Page, TimeoutError, Route, Response

KEYRING_KEY: str = __file__


class ANSI(str, Enum):
    end = "\033[0m"
    bold = "\033[01m"
    black = "\033[30m"
    red = "\033[31m"
    orange = "\033[93m"


def style(text: str, *ansi: ANSI) -> str:
    return "".join(ansi) + text + ANSI.end


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

    _browser: Browser
    _page: Page

    _abort: bool

    re_domain = re.compile(r"^https://((my|happy).monizze.be|www.(google|gstatic).com/recaptcha).*$")

    ALLOWED_DOMAINS = (
        "https://my.monizze.be",
        "https://happy.monizze.be",
        "https://www.google.com/recaptcha",
        "https://www.gstatic.com/recaptcha",
    )

    _assets: int
    _3party: int

    _verbose: bool = False

    def __init__(self, playwright: Playwright, verbose: bool = False):
        print("Starting Monizze session")
        self._verbose = verbose

        self._browser = playwright.chromium.launch()
        context = self._browser.new_context(
            user_agent=str(uuid.uuid4())
        )
        self._page = context.new_page()
        self._abort = False
        self._assets = 0
        self._3party = 0

        self.page.route("**/*", self._block_routes)
        self.page.on("response", self._handle_response)

    @property
    def page(self) -> Page:
        if self._abort:
            print(style(f"Failed to retrieve Monizze data!", ANSI.bold, ANSI.red))
            exit(1)
        else:
            return self._page

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

        print(f"Logging in...")
        self.page.goto(self.endpoint + "login", wait_until="networkidle")

        try:
            self.page.click("button#onetrust-accept-btn-handler", timeout=50)
        except TimeoutError:
            pass

        self.page.wait_for_timeout(500)
        self.page.fill("input#email", email)
        self.page.fill("input#password", password)

        try:
            with self.page.expect_response("**/my-monizze/user", timeout=5000):
                self.page.click(
                    "input[type=\"submit\"]", delay=randint(5, 50)
                )
        except TimeoutError:
            print(style(
                f"Timed out while logging in (hit reCAPTCHA or HTML was changed)",
                ANSI.bold, ANSI.red,
            ))
            self._abort = True

    def get_history(self) -> List[MonizzeTransaction]:
        print("Retrieving transaction history...")
        history: Set[MonizzeTransaction] = set()

        with self.page.expect_response("**/voucher/history") as r:
            self.page.goto(self.endpoint + "history")

        self._add_to_history(r, history)

        keep_paging = True

        while keep_paging:
            with self.page.expect_response("**/voucher/history/*/*/*") as r:
                try:
                    self.page.click(
                        "tfoot > tr > td > a:last-of-type", timeout=250
                    )
                except TimeoutError:
                    keep_paging = False
                    continue

            len_t0 = len(history)
            self._add_to_history(r, history)
            if len(history) == len_t0:
                keep_paging = False

        self._close()

        return sorted(history, key=lambda transaction: transaction.date)

    def _close(self):
        self._browser.close()

        if self._assets > 0:
            print(style(f"Blocked {self._assets} request(s) for assets", ANSI.black))
        if self._assets > 0:
            print(style(f"Blocked {self._3party} third-party request(s)", ANSI.black))

    def abort(self):
        self._abort = True

    def _add_to_history(self, r, history: Set[MonizzeTransaction]):
        data = loads(r.value.body())["data"]
        for voucher, entries in data.items():
            for entry in entries:
                transaction = MonizzeTransaction(
                    entry["date"], voucher, entry["amount"], entry["detail"]
                )
                history.add(transaction)

    def _block_routes(self, r: Route) -> None:
        if not any(r.request.url.startswith(d) for d in self.ALLOWED_DOMAINS):
            if self._verbose:
                print(style(
                    f"Blocked 3rd party request: {r.request.method} {r.request.url}",
                    ANSI.black,
                ))
            self._3party += 1
            r.abort()
        elif r.request.resource_type in ("stylesheet", "image", "font"):
            if self._verbose:
                print(style(
                    f"Blocked asset request:     {r.request.method} {r.request.url}",
                    ANSI.black,
                ))
            self._assets += 1
            r.abort()
        else:
            if self._verbose:
                print(style(
                    f" → {r.request.method} {r.request.url}",
                    ANSI.black,
                ))
            r.continue_()

    def _handle_response(self, r: Response) -> None:
        if self._verbose:
            print(style(
                f" ← HTTP {r.status} ~ {r.request.method} {r.request.url}",
                ANSI.black,
            ))
        if r.status in (500, 400, 401, 403, 429):
            print(style(
                f" × HTTP {r.status} ~ {r.request.url}",
                ANSI.bold, ANSI.red
            ))
            self._abort = True


def before(t0: str, t1: str) -> bool:
    return datetime.fromisoformat(t0) < datetime.fromisoformat(t1)


def save_csv(path: str, history: List[MonizzeTransaction]) -> None:
    # Monizze seems to only show transactions for the last year or so
    # -> keep any transactions older than the ones we've just retrieved
    oldest = datetime.fromisoformat(history[0].date)
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
                dt = datetime.fromisoformat(row[0])
                if dt < oldest:
                    old.append(MonizzeTransaction(*row))
                else:
                    older_than_current = False

            if len(old) > 0:
                print(style(
                    f"Monizze only remembers transactions starting from {oldest.date()}; "
                    f"keeping {len(old)} older transaction(s) in the CSV file.",
                    ANSI.bold, ANSI.orange
                ))
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
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="show detailed messages"
    )
    args = parser.parse_args()

    if args.clear:
        delete_password(KEYRING_KEY, args.email)
        print("Password cleared from keyring.")
    else:
        if not args.output_path:
            print("No output path specified (-o/--output-path is required unless running with --clear)")
            exit(1)

        with sync_playwright() as p:
            mc = MonizzeClient(p, args.verbose)
            mc.login(args.email)
            save_csv(args.output_path, mc.get_history())
            print("Done.")