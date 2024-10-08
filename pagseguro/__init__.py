# coding: utf-8
import logging
import requests

from .config import Config
from .utils import is_valid_email, is_valid_cpf, is_valid_cnpj
from .parsers import (
    PagSeguroNotificationResponse,
    PagSeguroPreApprovalNotificationResponse,
    PagSeguroPreApprovalCancel,
    PagSeguroCheckoutSession,
    PagSeguroPreApprovalPayment,
    PagSeguroCheckoutResponse,
    PagSeguroTransactionSearchResult,
    PagSeguroPreApproval,
    PagSeguroPreApprovalSearch,
)

logger = logging.getLogger()


class PagSeguro(object):
    """Pag Seguro V2 wrapper"""

    PAC = 1
    SEDEX = 2
    NONE = 3

    def __init__(self, token, public_key=None, email=None, data=None, config=None):

        config = config or {}
        if not type(config) == dict:
            raise Exception("Malformed config dict param")

        self.config = Config(**config)
        self.headers = {
            "accept": "*/*",
            "Authorization": "Bearer %s" % token,
            "Content-type": "application/json",
        }
        self.public_key = public_key
        self.data = {}
        self.token = token
        self.email = email

        if data and isinstance(data, dict):
            self.data.update(data)

        self.items = []
        self.sender = {}
        self.shipping = {}
        self._reference = ""
        self.extra_amount = None
        self.redirect_url = None
        self.charge = None
        self.notification_url = None
        self.abandon_url = None
        self.credit_card = {}
        self.pre_approval = {}
        self.checkout_session = None
        self.payment = {}
        self.holder = {}
        self.subscription = {}

    def build_checkout_params(self, **kwargs):
        """build a dict with params"""
        params = kwargs or {}

        if self.reference:
            params["reference_id"] = self.reference
        if self.sender:
            params["customer"] = {}
            customer = params["customer"]
            customer["name"] = self.sender.get("name")
            customer["email"] = is_valid_email(self.sender.get("email"))
            customer["tax_id"] = (
                is_valid_cnpj(self.sender.get("cnpj"))
                if is_valid_cnpj(self.sender.get("cnpj"))
                else is_valid_cpf(self.sender.get("cpf"))
            )
            customer["phones"] = [
                {
                    "type": "MOBILE",
                    "country": "55",
                    "area": self.sender.get("area_code"),
                    "number": self.sender.get("phone"),
                }
            ]

        if self.config.USE_SHIPPING:
            if self.shipping:
                params["shipping"] = {"address": {}}
                shipping = params["shipping"]["address"]
                shipping["shippingType"] = self.shipping.get("type")

                shipping["street"] = self.shipping.get("street")
                shipping["number"] = self.shipping.get("number")
                shipping["complement"] = self.shipping.get("complement", "")
                shipping["locality"] = self.shipping.get("district")
                shipping["city"] = self.shipping.get("city")
                shipping["region_code"] = self.shipping.get("state")
                shipping["country"] = self.shipping.get("country", "BRA")
                shipping["postal_code"] = self.shipping.get("postal_code")

        if self.extra_amount:
            params["extraAmount"] = self.extra_amount

        params["reference"] = self.reference
        # params["receiverEmail"] = self.data["email"]

        if self.redirect_url:
            params["redirectURL"] = self.redirect_url

        if self.notification_url:
            params["notification_urls"] = [self.notification_url]

        if self.abandon_url:
            params["notifcation_urls"] = [self.abandon_url]

        if self.items:
            params["items"] = []

        for i, item in enumerate(self.items, 1):
            item_params = {}
            item_params["reference_id"] = item.get("id")
            item_params["name"] = item.get("name")
            item_params["quantity"] = item.get("quantity")
            item_params["unit_amount"] = item.get("amount")
            params["items"].append(item_params)

        if self.payment:
            params["charges"] = []
            charge = {}
            charge["amount"] = self.payment.get("amount")
            charge["payment_method"] = self.payment.get("method")
            params["charges"].append(charge)
            if self.payment["method"] == "BOLETO":
                charge["payment_method"]["holder"] = {}
                charge["payment_method"]["holder"]["name"] = self.sender.get("name")
                charge["payment_method"]["holder"]["tax_id"] = (
                    is_valid_cnpj(self.sender.get("cnpj"))
                    if is_valid_cnpj(self.sender.get("cnpj"))
                    else is_valid_cpf(self.sender.get("cpf"))
                )
                charge["payment_method"]["holder"]["email"] = self.sender.get("email")
                charge["payment_method"]["holder"]["address"] = params["shipping"][
                    "address"
                ]

        print(self.payment)
        self.data.update(params)
        self.clean_none_params()

    def build_subscription(self, **kwargs):
        """build a dict with params"""
        self.build_checkout_params(**kwargs)

        params = None
        params = kwargs or {}
        if self.subscription.get("plan_rference_id", None):
            response = self.get_plan(reference_id=self.subscription["plan_rference_id"])
            params["plan"] = {"id": response.get("plans", [])[0]["id"]}
        else:
            params["plan"] = {"id": self.subscription["plan_id"]}

        if self.subscription.get("customer_id", None):
            params["customer"] = {"id": self.subscription["customer_id"]}
        elif self.subscription.get("customer_reference_id", None):
            response = self.get_subscriber(
                reference_id=self.subscription["customer_reference_id"]
            )
            params["customer"] = response.get("customers", [])[0]
        else:
            billing_info = {
                "card": self.data["charge"]["payment_method"]["card"],
                "type": "CREDIT_CARD",
            }
            params["customer"] = self.data["customer"]
            params["customer"]["billing_info"] = billing_info
        params["payment_method"] = {
            "type": "CREDIT_CARD",
            "card": {
                "security_code": self.data["charge"]["payment_method"]["card"][
                    "security_code"
                ],
            },
        }

        params["amount"] = self.data["charge"]["amount"]
        params["best_invoice_date"] = self.subscription["best_invoice_date"]

        params["reference_id"] = self.subscription["reference_id"]

        self.data = {}
        self.data.update(params)
        self.clean_none_params()

    def clean_none_params(self):
        self.data = {k: v for k, v in self.data.items() if v or isinstance(v, bool)}

    @property
    def reference_prefix(self):
        return self.config.REFERENCE_PREFIX or "%s"

    @reference_prefix.setter
    def reference_prefix(self, value):
        self.config.REFERENCE_PREFIX = (value or "") + "%s"

    @property
    def reference(self):
        return self.reference_prefix % self._reference

    @reference.setter
    def reference(self, value):
        if not isinstance(value, str):
            value = str(value)
        if value.startswith(self.reference_prefix):
            value = value[len(self.reference_prefix) :]
        self._reference = value

    def get(self, url):
        """do a get transaction"""
        return requests.get(url, params=self.data, headers=self.config.HEADERS)

    def post(self, url):
        """do a post request"""
        return requests.post(url, json=self.data, headers=self.headers)

    def checkout(self, transparent=False, **kwargs):
        """create a pagseguro checkout"""
        self.build_checkout_params(**kwargs)
        response = self.post(url=self.config.ORDER_URL)

        return response

    def transparent_checkout_session(self):
        response = self.post(url=self.config.SESSION_CHECKOUT_URL)
        return PagSeguroCheckoutSession(response.content, config=self.config).session_id

    def check_notification(self, code):
        """check a notification by its code"""
        params = {"email": self.email, "token": self.token}
        response = requests.get(url=self.config.NOTIFICATION_URL % code, params=params)
        return PagSeguroNotificationResponse(response.content, self.config)

    def check_pre_approval_notification(self, code):
        """check a notification by its code"""
        response = self.get(url=self.config.PRE_APPROVAL_NOTIFICATION_URL % code)
        return PagSeguroPreApprovalNotificationResponse(response.content, self.config)

    def pre_approval_ask_payment(self, **kwargs):
        """ask form a subscribe payment"""
        self.build_pre_approval_payment_params(**kwargs)
        response = self.post(url=self.config.PRE_APPROVAL_PAYMENT_URL)
        return PagSeguroPreApprovalPayment(response.content, self.config)

    def pre_approval_cancel(self, code):
        """cancel a subscribe"""
        response = self.get(url=self.config.PRE_APPROVAL_CANCEL_URL % code)
        return PagSeguroPreApprovalCancel(response.content, self.config)

    def check_transaction(self, code):
        """check a transaction by its code"""
        response = self.get(url=self.config.TRANSACTION_URL % code)
        return PagSeguroNotificationResponse(response.content, self.config)

    def query_transactions(self, initial_date, final_date, page=None, max_results=None):
        """query transaction by date range"""
        last_page = False
        results = []
        while last_page is False:
            search_result = self._consume_query_transactions(
                initial_date, final_date, page, max_results
            )
            results.extend(search_result.transactions)
            if (
                search_result.current_page is None
                or search_result.total_pages is None
                or search_result.current_page == search_result.total_pages
            ):
                last_page = True
            else:
                page = search_result.current_page + 1

        return results

    def _consume_query_transactions(
        self, initial_date, final_date, page=None, max_results=None
    ):
        querystring = {
            "initialDate": initial_date.strftime("%Y-%m-%dT%H:%M"),
            "finalDate": final_date.strftime("%Y-%m-%dT%H:%M"),
            "page": page,
            "maxPageResults": max_results,
        }
        self.data.update(querystring)
        self.clean_none_params()
        response = self.get(url=self.config.QUERY_TRANSACTION_URL)
        return PagSeguroTransactionSearchResult(response.content, self.config)

    def query_pre_approvals(
        self, initial_date, final_date, page=None, max_results=None
    ):
        """query pre-approvals by date range"""
        last_page = False
        results = []
        while last_page is False:
            search_result = self._consume_query_pre_approvals(
                initial_date, final_date, page, max_results
            )
            results.extend(search_result.pre_approvals)
            if (
                search_result.current_page is None
                or search_result.total_pages is None
                or search_result.current_page == search_result.total_pages
            ):
                last_page = True
            else:
                page = search_result.current_page + 1

        return results

    def _consume_query_pre_approvals(
        self, initial_date, final_date, page=None, max_results=None
    ):
        querystring = {
            "initialDate": initial_date.strftime("%Y-%m-%dT%H:%M"),
            "finalDate": final_date.strftime("%Y-%m-%dT%H:%M"),
            "page": page,
            "maxPageResults": max_results,
        }

        self.data.update(querystring)
        self.clean_none_params()

        response = self.get(url=self.config.QUERY_PRE_APPROVAL_URL)
        return PagSeguroPreApprovalSearch(response.content, self.config)

    def query_pre_approvals_by_code(self, code):
        """query pre-approvals by code"""
        result = self._consume_query_pre_approvals_by_code(code)
        return result

    def _consume_query_pre_approvals_by_code(self, code):

        response = self.get(url="%s/%s" % (self.config.QUERY_PRE_APPROVAL_URL, code))
        return PagSeguroPreApproval(response.content, self.config)

    def add_item(self, **kwargs):
        self.items.append(kwargs)

    def create_subscriber(self, **kwargs):
        self.build_checkout_params(**kwargs)
        response = self.post(url=self.config.SUBSCRIBER_URL)
        return response

    def get_subscriber(self, pag_id=None, reference_id=None):
        url = self.config.SUBSCRIBER_URL
        if reference_id:
            url += "?reference_id=%s" % reference_id
        response = self.get(url=url)
        return response

    def delete_subscriber(self, code):
        response = self.delete(url=self.config.SUBSCRIBER_URL + code)
        return response

    def create_plan(self, plan):
        response = self.post(url=self.config.PLAN_URL, data=plan)
        return response

    def get_plan(self, plan_id=None, reference_id=None):
        url = self.config.PLAN_URL
        if reference_id:
            url = self.config.PLAN_URL + "?reference_id=%s" % reference_id
        response = self.get(url=self.config.PLAN_URL)
        return response

    def delete_plan(self, code):
        response = self.delete(url=self.config.PLAN_URL + code)
        return response

    def list_plans(self):
        response = self.get(url=self.config.PLAN_URL)
        return response

    def create_signature(self, signature=None):
        if signature:
            self.data = signature
        else:
            self.build_subscription()
        response = self.post(url=self.config.SUBSCRIPTION_URL, data=self.data)
        return response
