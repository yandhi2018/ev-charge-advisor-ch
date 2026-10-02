"""HTTP-клиент с повторными попытками (перенесено из evcharge-ch-forecast и доработано).

Экспоненциальная пауза, учёт 429 и заголовка Retry-After, повтор при таймаутах и 5xx.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "ev-charge-advisor-ch/0.1 (student project, RTU MIREA)"


def make_session(total: int = 5, backoff: float = 2.0) -> requests.Session:
    retry = Retry(
        total=total,
        connect=3,
        read=3,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    return session


def get(url: str, timeout: tuple[float, float], params: dict | None = None,
        session: requests.Session | None = None) -> requests.Response:
    """GET с повторами; при окончательной неудаче — исключение requests.HTTPError / ConnectionError."""
    s = session or make_session()
    response = s.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response
