"""Thin client for the CRM sandbox API. The only file that talks to the CRM."""
import requests

import config


class CRMError(Exception):
    """The CRM API returned an error."""


class CRMClient:
    def __init__(self, token=None, api_url=None):
        self.api_url = api_url or config.API_URL
        token = token or config.API_TOKEN
        if not token:
            raise CRMError("CRM_API_TOKEN is not set. Copy .env.example to .env and add your token.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, self.api_url + path, timeout=30, **kwargs)
        if not response.ok:
            raise CRMError(f"{method} {path} returned {response.status_code}: {response.text[:300]}")
        return response.json()

    def _list_all(self, path, **filters):
        """Walk every page of a list endpoint."""
        rows, page = [], 1
        while True:
            body = self._request("GET", path, params={**filters, "page": page, "page_size": 100})
            rows += body["data"]
            if not body["data"] or len(rows) >= body["total"]:
                return rows
            page += 1

    def list_accounts(self, **filters):
        return self._list_all("/accounts", **filters)

    def list_contacts(self, **filters):
        return self._list_all("/contacts", **filters)

    def get_account(self, account_id):
        return self._request("GET", f"/accounts/{account_id}")

    def update_account(self, account_id, fields):
        """Send only the fields that change."""
        return self._request("PATCH", f"/accounts/{account_id}", json=fields)

    def create_account(self, fields):
        """Returns the new account, including its account_id."""
        body = self._request("POST", "/accounts", json=fields)
        return body if "account_id" in body else body.get("data", body)
